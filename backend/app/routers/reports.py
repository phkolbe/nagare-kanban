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
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import (
    CurrentUser,
    check_flag,
    effective_flags,
    get_session,
    require_capability,
    require_flag,
    require_standalone,
)
from ..errors import BadRequest
from ..reports.documento import PERIOD_KINDS, resolve_period
from ..reports.modelos import TEMPLATES, ReportRequest, template_or_400
from ..reports.planilha import file_name, to_csv, to_xlsx
from ..settings import zone

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
