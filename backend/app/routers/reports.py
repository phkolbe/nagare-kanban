"""The Reports area (24/11/2026, wave 1 of the plan in the vault: ``plano-area-de-reports``).

Only in the standalone Nagare (decision R4): a request signed by the Pampufa is refused.
Reading and exporting need ``read_reports`` (whoever reads metrics, R21) and the ``reports``
flag (Business plan, R20); the portfolio also needs ``reports_portfolio`` (Enterprise).

* ``GET /reports/templates`` — the catalogue the person can open.
* ``GET /reports/{template}`` — the report live, as the Document (see
  :mod:`backend.app.reports.documento`).
* ``GET /reports/{template}/export?format=xlsx|csv`` — the same Document as a spreadsheet.

Common query: ``period`` (``week``, ``month``, ``custom``), ``date`` (a day inside the
week or month; default the last complete one), ``from`` and ``to`` (``custom``), plus the
scope: ``board_id``, ``project_id`` or ``project_ids`` (comma separated).

Wave 2 (25/11/2026): issued reports and client links (see :mod:`backend.app.reports.emitidos`).

* ``POST /reports/issued`` — issue a report (``issue_reports``): the Document is frozen.
* ``GET /reports/issued`` and ``GET /reports/issued/{id}`` — the history and one copy.
* ``GET /reports/issued/{id}/export`` — the frozen copy as a spreadsheet.
* ``POST /reports/issued/{id}/links`` and ``DELETE /reports/issued/{id}/links/{link_id}``
  — create (the token comes back once) and revoke a client link (``share_reports``).

The public page of a link is ``GET /public/reports/{token}`` (:mod:`.public`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import iso, utcnow
from ..deps import (
    CurrentUser,
    check_flag,
    effective_flags,
    get_session,
    require_capability,
    require_flag,
    require_standalone,
)
from ..errors import ApiError, BadRequest, NotFound
from ..models import Project, ReportIssue, ReportLink, User, as_dict
from ..reports.documento import PERIOD_KINDS, resolve_period
from ..reports.emitidos import (
    DEFAULT_LINK_DAYS,
    MAX_LINK_DAYS,
    link_projects,
    link_state,
    new_token,
)
from ..reports.modelos import TEMPLATES, ReportRequest, template_or_400
from ..reports.planilha import file_name, to_csv, to_xlsx
from ..settings import zone
from ..tenancy import in_product, product_for_new_rows
from ..texts import locale, set_locale

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
    dependencies=[Depends(require_standalone), Depends(require_flag("reports"))],
)

__all__ = ["router"]

_CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


def _day(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError as exc:
        raise BadRequest(
            f"“{name}” is not a date (YYYY-MM-DD).", code="report_bad_date", details={name: value}
        ) from exc


def report_query(
    period: str = Query("week", description="week, month or custom"),
    day: str | None = Query(None, alias="date", description="A day inside the week or month."),
    frm: str | None = Query(None, alias="from", description="First day (custom)."),
    to: str | None = Query(None, description="Last day (custom)."),
    board_id: str | None = Query(None),
    project_id: str | None = Query(None),
    project_ids: str | None = Query(None, description="Comma-separated project ids."),
) -> dict[str, Any]:
    if period not in PERIOD_KINDS:
        raise BadRequest(
            "The period is week, month or custom.",
            code="report_bad_period",
            details={"period": period},
        )
    return {
        "period": period,
        "ref": _day(day, "date"),
        "frm": _day(frm, "from"),
        "to": _day(to, "to"),
        "board_id": board_id or None,
        "project_id": project_id or None,
        "project_ids": [p for p in (project_ids or "").split(",") if p.strip()],
    }


async def _build(
    s: AsyncSession, template_id: str, query: dict[str, Any], user: dict[str, Any]
) -> dict[str, Any]:
    template = template_or_400(template_id)
    if template.flag != "reports":
        await check_flag(s, template.flag)
    tz = await s.run_sync(zone)
    today = datetime.now(UTC).astimezone(tz).date()
    try:
        period = resolve_period(
            query["period"],
            today=today,
            zone=tz,
            ref=query["ref"],
            frm=query["frm"],
            to=query["to"],
        )
    except ValueError as exc:
        raise BadRequest(str(exc), code="report_bad_period") from exc
    request = ReportRequest(
        period=period,
        user=user,
        board_id=query["board_id"],
        project_id=query["project_id"],
        project_ids=query["project_ids"],
    )
    return await template.build(s, request)


@router.get("/templates", summary="The reports the person can open")
async def list_templates(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> dict[str, Any]:
    flags = await effective_flags(s)
    return {
        "items": [
            {**t.card(), "available": bool(flags.get(t.flag, True))} for t in TEMPLATES.values()
        ]
    }


# ---------------------------------------------------------------------------
# issued reports and client links (wave 2, 25/11/2026)
# ---------------------------------------------------------------------------


class IssueIn(BaseModel):
    """``POST /reports/issued``: the template and the same parameters as the live report."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    template: str
    period: str = "week"
    date: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    board_id: str | None = None
    project_id: str | None = None
    #: a list or a comma-separated string, like the query of the live report
    project_ids: list[str] | str | None = None


class LinkIn(BaseModel):
    """``POST /reports/issued/{id}/links``: how many days the link lasts (R19)."""

    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=DEFAULT_LINK_DAYS, ge=1, le=MAX_LINK_DAYS)


async def _people(s: AsyncSession, ids: set[str | None]) -> dict[str, str]:
    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    return {u.id: u.name for u in await s.scalars(select(User).where(User.id.in_(wanted)))}


def _person(people: dict[str, str], user_id: str | None) -> dict[str, Any] | None:
    return {"id": user_id, "name": people.get(user_id, "—")} if user_id else None


def _shareable(template: str, document: dict[str, Any]) -> bool:
    try:
        link_projects(template, document)
    except ApiError:
        return False
    return True


def _issue_out(
    row: ReportIssue,
    people: dict[str, str],
    *,
    active_links: int | None = None,
    full: bool = False,
) -> dict[str, Any]:
    data = as_dict(row) or {}
    document = data.get("document") or {}
    period = document.get("period") or {}
    out: dict[str, Any] = {
        "id": data["id"],
        "product_id": data["product_id"],
        "template": data["template"],
        "code": data["code"],
        "title": data["title"],
        "subtitle": data.get("subtitle"),
        "params": data.get("params") or {},
        "period": {
            "kind": data["period_kind"],
            "from": data["period_from"],
            "to": data["period_to"],
            "label": period.get("label"),
        },
        "format_version": data["format_version"],
        "locale": data["locale"],
        "issued_at": data["issued_at"],
        "issued_by": _person(people, data.get("issued_by")),
        "shareable": _shareable(data["template"], document),
    }
    if active_links is not None:
        out["active_links"] = active_links
    if full:
        out["document"] = document
    return out


def _link_out(
    row: ReportLink, people: dict[str, str], projects: dict[str, str], now: datetime
) -> dict[str, Any]:
    data = as_dict(row) or {}
    return {
        "id": data["id"],
        "issue_id": data["issue_id"],
        "projects": [
            {"id": pid, "name": projects.get(str(pid), "—")} for pid in data["project_ids"] or []
        ],
        "expires_at": data["expires_at"],
        "created_at": data["created_at"],
        "created_by": _person(people, data.get("created_by")),
        "revoked_at": data.get("revoked_at"),
        "revoked_by": _person(people, data.get("revoked_by")),
        "access_count": int(data.get("access_count") or 0),
        "last_access_at": data.get("last_access_at"),
        "state": link_state(data, now),
    }


async def _issue_or_404(s: AsyncSession, issue_id: str) -> ReportIssue:
    row = await s.scalar(
        select(ReportIssue).where(ReportIssue.id == issue_id, *in_product(ReportIssue))
    )
    if row is None:
        raise NotFound("Issued report", issue_id, code="report_issue_not_found")
    return row


async def _links_of(s: AsyncSession, issue_id: str) -> list[ReportLink]:
    return list(
        (
            await s.scalars(
                select(ReportLink)
                .where(ReportLink.issue_id == issue_id, *in_product(ReportLink))
                .order_by(ReportLink.created_at.desc(), ReportLink.id.desc())
            )
        ).all()
    )


async def _project_names(s: AsyncSession) -> dict[str, str]:
    return {p.id: p.name for p in await s.scalars(select(Project).where(*in_product(Project)))}


@router.post("/issued", status_code=201, summary="Issue a report: freeze its Document")
async def issue_report(
    payload: IssueIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("issue_reports")),
) -> dict[str, Any]:
    project_ids = payload.project_ids
    if isinstance(project_ids, list):
        project_ids = ",".join(project_ids)
    query = report_query(
        period=payload.period,
        day=payload.date,
        frm=payload.from_,
        to=payload.to,
        board_id=payload.board_id,
        project_id=payload.project_id,
        project_ids=project_ids,
    )
    document = await _build(s, payload.template, query, user)
    row = ReportIssue(
        product_id=product_for_new_rows(),
        template=document["template"],
        code=document["code"],
        title=str(document["title"])[:200],
        subtitle=(str(document["subtitle"])[:300] if document.get("subtitle") else None),
        params=document.get("params") or {},
        period_kind=document["period"]["kind"],
        period_from=document["period"]["from"],
        period_to=document["period"]["to"],
        format_version=int(document["format_version"]),
        locale=locale(),
        document=document,
        issued_by=user["id"],
        issued_at=document.get("generated_at") or utcnow(),
    )
    s.add(row)
    await s.commit()
    return _issue_out(row, {user["id"]: user["name"]}, active_links=0, full=True)


@router.get("/issued", summary="The issued reports, newest first")
async def list_issued(
    template: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> dict[str, Any]:
    conditions = [*in_product(ReportIssue)]
    if template:
        conditions.append(ReportIssue.template == template)
    total = await s.scalar(select(func.count()).select_from(ReportIssue).where(*conditions))
    rows = (
        await s.scalars(
            select(ReportIssue)
            .where(*conditions)
            .order_by(ReportIssue.issued_at.desc(), ReportIssue.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    now = iso(datetime.now(UTC))
    active: dict[str, int] = {}
    if rows:
        for issue_id, count in (
            await s.execute(
                select(ReportLink.issue_id, func.count())
                .where(
                    ReportLink.issue_id.in_([r.id for r in rows]),
                    ReportLink.revoked_at.is_(None),
                    ReportLink.expires_at > now,
                    *in_product(ReportLink),
                )
                .group_by(ReportLink.issue_id)
            )
        ).all():
            active[str(issue_id)] = int(count)
    people = await _people(s, {r.issued_by for r in rows})
    return {
        "items": [_issue_out(r, people, active_links=active.get(r.id, 0)) for r in rows],
        "total": int(total or 0),
    }


@router.get("/issued/{id}", summary="One issued report, with its frozen Document")
async def get_issued(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> dict[str, Any]:
    row = await _issue_or_404(s, id)
    # The links (who created them, how often they were opened) only for whoever can share.
    links = await _links_of(s, row.id) if user.has("share_reports") else []
    people = await _people(
        s, {row.issued_by} | {x.created_by for x in links} | {x.revoked_by for x in links}
    )
    now = datetime.now(UTC)
    projects = await _project_names(s) if links else {}
    out = _issue_out(
        row,
        people,
        active_links=sum(1 for x in links if link_state(as_dict(x) or {}, now) == "active"),
        full=True,
    )
    out["links"] = [_link_out(x, people, projects, now) for x in links]
    return out


@router.get("/issued/{id}/export", summary="An issued report as a spreadsheet (xlsx or csv)")
async def export_issued(
    id: str,
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> Response:
    row = await _issue_or_404(s, id)
    # The sheet's own labels in the language the frozen texts were written in.
    set_locale(row.locale)
    document = row.document
    content = to_xlsx(document) if format == "xlsx" else to_csv(document)
    name = file_name(document, format)
    return Response(
        content=content,
        media_type=_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/issued/{id}/links", status_code=201, summary="Create a client link (shown once)")
async def create_link(
    id: str,
    payload: LinkIn | None = None,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("share_reports")),
) -> dict[str, Any]:
    row = await _issue_or_404(s, id)
    project_ids = link_projects(row.template, row.document)
    days = (payload or LinkIn()).days
    token, token_hash = new_token()
    now = datetime.now(UTC)
    link = ReportLink(
        product_id=row.product_id,
        issue_id=row.id,
        token_hash=token_hash,
        project_ids=project_ids,
        expires_at=iso(now + timedelta(days=days)),
        created_by=user["id"],
        created_at=iso(now),
    )
    s.add(link)
    await s.commit()
    out = _link_out(link, {user["id"]: user["name"]}, await _project_names(s), now)
    # The token exists only in this answer: the database keeps its SHA-256.
    out["token"] = token
    out["path"] = f"/r/{token}"
    return out


@router.delete("/issued/{id}/links/{link_id}", summary="Revoke a client link")
async def revoke_link(
    id: str,
    link_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("share_reports")),
) -> dict[str, Any]:
    row = await _issue_or_404(s, id)
    link = await s.scalar(
        select(ReportLink).where(
            ReportLink.id == link_id, ReportLink.issue_id == row.id, *in_product(ReportLink)
        )
    )
    if link is None:
        raise NotFound("Report link", link_id, code="report_link_not_found")
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        link.revoked_by = user["id"]
        await s.commit()
    people = await _people(s, {link.created_by, link.revoked_by})
    return _link_out(link, people, await _project_names(s), datetime.now(UTC))


@router.get("/{template}", summary="One report, live, as a Document")
async def get_report(
    template: str,
    query: dict[str, Any] = Depends(report_query),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> dict[str, Any]:
    return await _build(s, template, query, user)


@router.get("/{template}/export", summary="One report as a spreadsheet (xlsx or csv)")
async def export_report(
    template: str,
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    query: dict[str, Any] = Depends(report_query),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> Response:
    document = await _build(s, template, query, user)
    content = to_xlsx(document) if format == "xlsx" else to_csv(document)
    name = file_name(document, format)
    return Response(
        content=content,
        media_type=_CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
