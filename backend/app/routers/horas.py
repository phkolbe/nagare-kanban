"""Hours on cards (GJ-4) and the people / project / client reading of a period (D45).

- ``GET/POST /cards/{id}/time`` and ``DELETE /time/{id}``: whoever moves cards logs their own
  hours; an administrator can remove anyone's.
- ``GET /metrics/people`` (``?from&to``): per person, per project and per client in the
  current product: cards delivered, on time x late (fixed date, the due day in the product's
  time zone), work in progress, demand and hours.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import new_id, utcnow
from ..deps import CurrentUser, get_session, require_capability
from ..errors import Forbidden, NotFound
from ..flow.metrics import end_of_day
from ..models import Card, Project, TimeEntry, User, as_dict
from ..settings import zone
from ..sqltypes import from_iso
from ..tenancy import in_product, product_for_new_rows

router = APIRouter(tags=["hours"])


class TimeIn(BaseModel):
    worked_on: date
    minutes: int = Field(gt=0, le=1440)
    description: str | None = Field(default=None, max_length=2000)


def _entry_out(row: dict[str, Any], card: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": row["id"],
        "card_id": row["card_id"],
        "card_key": (card or {}).get("key"),
        "card_title": (card or {}).get("title"),
        "project_id": row.get("project_id"),
        "user_id": row.get("user_id"),
        "user_name": row["user_name"],
        "worked_on": row["worked_on"],
        "minutes": row["minutes"],
        "description": row.get("description"),
        "created_at": row.get("created_at"),
    }


async def _card(s: AsyncSession, card_id: str) -> dict[str, Any]:
    card = as_dict(await s.scalar(select(Card).where(Card.id == card_id, *in_product(Card))))
    if card is None:
        raise NotFound("card_not_found", "Card not found.")
    return card


@router.get("/cards/{id}/time", summary="Hours logged on a card")
async def list_time(
    id: str,
    s: AsyncSession = Depends(get_session),
    _user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    card = await _card(s, id)
    rows = await s.scalars(
        select(TimeEntry)
        .where(TimeEntry.card_id == card["id"])
        .order_by(TimeEntry.worked_on.desc(), TimeEntry.created_at.desc())
    )
    items = [_entry_out(as_dict(r) or {}, card) for r in rows]
    return {"items": items, "total_minutes": sum(i["minutes"] for i in items)}


@router.post("/cards/{id}/time", status_code=status.HTTP_201_CREATED, summary="Log hours on a card")
async def log_time(
    id: str,
    payload: TimeIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    card = await _card(s, id)
    entry = TimeEntry(
        id=new_id(),
        product_id=product_for_new_rows(),
        card_id=card["id"],
        project_id=card.get("project_id"),
        user_id=user.get("id"),
        user_name=str(user.get("name") or user.get("email") or "?"),
        worked_on=payload.worked_on.isoformat(),
        minutes=payload.minutes,
        description=(payload.description or "").strip() or None,
        created_at=utcnow(),
    )
    s.add(entry)
    await s.commit()
    return _entry_out(as_dict(entry) or {}, card)


@router.delete("/time/{id}", summary="Remove an hour entry (your own, or any as administrator)")
async def delete_time(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    entry = await s.scalar(select(TimeEntry).where(TimeEntry.id == id, *in_product(TimeEntry)))
    if entry is None:
        raise NotFound("time_not_found", "Hour entry not found.")
    if entry.user_id != user.get("id") and user.get("role") not in {"master", "administrator"}:
        raise Forbidden(
            "Only who logged the hours (or an administrator) removes them.", code="not_yours"
        )
    await s.delete(entry)
    await s.commit()
    return {"ok": True}


# --------------------------------------------------------------------------- D45


def _period(frm: date | None, to: date | None) -> tuple[date, date]:
    hoje = datetime.now(UTC).date()
    fim = to or hoje
    inicio = frm or fim - timedelta(days=29)
    return (inicio, fim) if inicio <= fim else (fim, inicio)


async def people_projects_clients(
    s: AsyncSession, frm: date | None, to: date | None
) -> dict[str, Any]:
    """The D45 reading of the current product in a period (``from`` and ``to`` inclusive)."""
    inicio, fim = _period(frm, to)
    tz = await s.run_sync(zone)
    desde = datetime.combine(inicio, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    ate = datetime.combine(fim + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(UTC)

    projects = {p.id: p for p in await s.scalars(select(Project).where(*in_product(Project)))}
    names = {u.id: u.name for u in await s.scalars(select(User))}
    cards = [
        as_dict(c) or {}
        for c in await s.scalars(select(Card).where(Card.archived.is_(False), *in_product(Card)))
    ]
    entries = [
        as_dict(e) or {}
        for e in await s.scalars(
            select(TimeEntry).where(
                TimeEntry.worked_on >= inicio.isoformat(),
                TimeEntry.worked_on <= fim.isoformat(),
                *in_product(TimeEntry),
            )
        )
    ]

    def novo() -> dict[str, Any]:
        return {"delivered": 0, "on_time": 0, "late": 0, "wip": 0, "arrivals": 0, "minutes": 0}

    pessoas: dict[str, dict[str, Any]] = {}
    projetos: dict[str, dict[str, Any]] = {}
    for c in cards:
        entregue = from_iso(c.get("delivered_at"))
        criado = from_iso(c.get("created_at"))
        quem = c.get("assignee_id") or ""
        proj = c.get("project_id") or ""
        alvos = [pessoas.setdefault(quem, novo()), projetos.setdefault(proj, novo())]
        for alvo in alvos:
            if entregue is None and c.get("committed_at"):
                alvo["wip"] += 1
            if criado is not None and desde <= criado < ate:
                alvo["arrivals"] += 1
            if entregue is not None and desde <= entregue < ate:
                alvo["delivered"] += 1
                if c.get("service_class") == "fixed_date" and c.get("due_date"):
                    prazo = end_of_day(c["due_date"], tz)
                    alvo["on_time" if prazo and entregue <= prazo else "late"] += 1
    for e in entries:
        pessoas.setdefault(e.get("user_id") or "", novo())["minutes"] += int(e["minutes"])
        projetos.setdefault(e.get("project_id") or "", novo())["minutes"] += int(e["minutes"])

    def linha_pessoa(uid: str, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": uid or None,
            "name": names.get(uid) or (uid if uid else "Sem responsável"),
            **v,
        }

    def linha_projeto(pid: str, v: dict[str, Any]) -> dict[str, Any]:
        p = projects.get(pid)
        return {
            "project_id": pid or None,
            "name": p.name if p else "Sem projeto",
            "key": p.key if p else None,
            "client_name": p.client_name if p else None,
            **v,
        }

    clientes: dict[str, dict[str, Any]] = {}
    for pid, v in projetos.items():
        p = projects.get(pid)
        nome = (p.client_name if p else None) or "Sem cliente"
        alvo = clientes.setdefault(nome, {**novo(), "projects": 0})
        alvo["projects"] += 1
        for k in novo():
            alvo[k] += v[k]

    def tem(v: dict[str, Any]) -> bool:
        return any(v[k] for k in novo())

    return {
        "from": inicio.isoformat(),
        "to": fim.isoformat(),
        "people": sorted(
            (linha_pessoa(k, v) for k, v in pessoas.items() if tem(v)), key=lambda r: -r["minutes"]
        ),
        "projects": sorted(
            (linha_projeto(k, v) for k, v in projetos.items() if tem(v)),
            key=lambda r: -r["minutes"],
        ),
        "clients": sorted(
            ({"client_name": k, **v} for k, v in clientes.items() if tem(v)),
            key=lambda r: -r["minutes"],
        ),
        "total_minutes": sum(int(e["minutes"]) for e in entries),
    }


@router.get("/metrics/people", summary="People, projects and clients in a period (D45)")
async def people_metrics(
    frm: date | None = Query(None, alias="from"),
    to: date | None = Query(None),
    s: AsyncSession = Depends(get_session),
    _user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    return await people_projects_clients(s, frm, to)


async def minutes_by_project(
    s: AsyncSession, project_ids: list[str], frm: date, to: date
) -> list[dict[str, Any]]:
    """Hour entries of these projects in the period, with the card (for billing, GJ-4)."""
    if not project_ids:
        return []
    rows = (
        await s.execute(
            select(TimeEntry, Card.key, Card.title)
            .join(Card, Card.id == TimeEntry.card_id)
            .where(
                TimeEntry.project_id.in_(project_ids),
                TimeEntry.worked_on >= frm.isoformat(),
                TimeEntry.worked_on <= to.isoformat(),
                *in_product(TimeEntry),
            )
            .order_by(TimeEntry.worked_on, TimeEntry.created_at)
        )
    ).all()
    return [_entry_out(as_dict(e) or {}, {"key": key, "title": title}) for e, key, title in rows]


async def total_minutes(s: AsyncSession, frm: date, to: date) -> int:
    return int(
        await s.scalar(
            select(func.coalesce(func.sum(TimeEntry.minutes), 0)).where(
                TimeEntry.worked_on >= frm.isoformat(),
                TimeEntry.worked_on <= to.isoformat(),
                *in_product(TimeEntry),
            )
        )
        or 0
    )
