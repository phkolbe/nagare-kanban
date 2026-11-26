"""Registered forecasts (wave 2 of the Reports, 25/11/2026; decision R15).

The **Registrar previsão** button of the Monte Carlo panel (Metrics) keeps the forecast on
the screen, and the REP-09 report (forecast against actual) checks it later against the real
deliveries. Only in the standalone Nagare, like the rest of the Reports, with the ``reports``
flag.

* ``POST /forecasts/records`` — register a forecast (``issue_reports`` and the
  ``monte_carlo`` flag). The server recomputes it with the panel's own function, sample pool
  and seed (:func:`~backend.app.routers.metrics.board_forecast`), so what is kept is what the
  panel showed, not numbers typed by the client.
* ``GET /forecasts/records`` — the registered forecasts (``read_reports``), newest first.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..deps import (
    CurrentUser,
    check_flag,
    get_board_or_404,
    get_session,
    require_capability,
    require_flag,
    require_standalone,
)
from ..errors import NotFound
from ..models import ForecastRecord, Project, User, as_dict
from ..tenancy import in_product, product_for_new_rows
from .metrics import board_forecast

router = APIRouter(
    prefix="/forecasts",
    tags=["forecasts"],
    dependencies=[Depends(require_standalone), Depends(require_flag("reports"))],
)

__all__ = ["router"]


class ForecastRecordIn(BaseModel):
    """The panel's question, as ``GET /boards/{id}/metrics/forecast`` takes it."""

    model_config = ConfigDict(extra="forbid")

    board_id: str
    #: the project the forecast was given for (optional); REP-09 then counts its deliveries
    project_id: str | None = None
    question: Literal["when", "how_many"]
    backlog_size: int = Field(default=10, ge=1, le=10_000)
    days: int = Field(default=28, ge=1, le=3_650)
    sample_weeks: int | None = Field(default=None, ge=1, le=520)


_FIELDS = (
    "id",
    "product_id",
    "board_id",
    "project_id",
    "question",
    "params",
    "percentiles",
    "dates",
    "start_date",
    "target_date",
    "low_confidence",
    "created_at",
)


def _out(row: ForecastRecord, people: dict[str, str]) -> dict[str, Any]:
    data = as_dict(row) or {}
    out = {key: data.get(key) for key in _FIELDS}
    who = data.get("created_by")
    out["created_by"] = {"id": who, "name": people.get(who, "—")} if who else None
    return out


@router.post("/records", status_code=201, summary="Register the forecast the panel shows")
async def register_forecast(
    payload: ForecastRecordIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("issue_reports")),
) -> dict[str, Any]:
    await check_flag(s, "monte_carlo")
    board = await get_board_or_404(s, payload.board_id)
    if payload.project_id:
        project = await s.scalar(
            select(Project.id).where(Project.id == payload.project_id, *in_product(Project))
        )
        if project is None:
            raise NotFound("Project", payload.project_id, code="project_not_found")
    result = await board_forecast(
        s, board, payload.question, payload.backlog_size, payload.days, payload.sample_weeks
    )
    params: dict[str, Any] = {
        "sample_weeks": payload.sample_weeks,
        "trials": result.get("trials"),
        "seed": result.get("seed"),
    }
    if payload.question == "when":
        params["backlog_size"] = payload.backlog_size
    else:
        params["days"] = payload.days
    row = ForecastRecord(
        product_id=product_for_new_rows(),
        board_id=board["id"],
        project_id=payload.project_id or None,
        question=payload.question,
        params=params,
        percentiles=dict(result.get("percentiles") or {}),
        dates=dict(result.get("dates") or {}),
        start_date=result["start_date"],
        target_date=result.get("target_date"),
        low_confidence=bool(result.get("low_confidence")),
        created_by=user["id"],
        created_at=utcnow(),
    )
    s.add(row)
    await s.commit()
    out = _out(row, {user["id"]: user["name"]})
    out["sentence"] = result.get("sentence")
    return out


@router.get("/records", summary="The registered forecasts, newest first")
async def list_forecasts(
    board_id: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_reports")),
) -> dict[str, Any]:
    conditions = [*in_product(ForecastRecord)]
    if board_id:
        board = await get_board_or_404(s, board_id)
        conditions.append(ForecastRecord.board_id == board["id"])
    if project_id:
        conditions.append(ForecastRecord.project_id == project_id)
    total = await s.scalar(select(func.count()).select_from(ForecastRecord).where(*conditions))
    rows = (
        await s.scalars(
            select(ForecastRecord)
            .where(*conditions)
            .order_by(ForecastRecord.created_at.desc(), ForecastRecord.id.desc())
            .limit(limit)
        )
    ).all()
    wanted = [r.created_by for r in rows if r.created_by]
    people = (
        {u.id: u.name for u in await s.scalars(select(User).where(User.id.in_(wanted)))}
        if wanted
        else {}
    )
    return {"items": [_out(r, people) for r in rows], "total": int(total or 0)}
