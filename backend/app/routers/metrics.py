"""Flow metrics endpoints (SPEC §5 "Metrics"; formulas in SPEC §7).

Every route here is a thin HTTP shell: :mod:`backend.app.flow.metrics` and
:mod:`backend.app.flow.kmm` already contain every formula, percentile and chart
envelope. This module's whole job is to turn query parameters into a
:class:`~backend.app.flow.metrics.MetricsContext` once per request and hand it
to the right pure function.

Two rules from SPEC §5 shape every route:

* **Read access is universal.** Every role in the workspace — down to
  ``viewer`` and ``simulator`` — can read metrics; only *writing* boards and
  cards is restricted. ``require_capability("read_boards_metrics")`` encodes
  exactly that (SPEC §4).
* **Feature flags gate by tier, not by role.** ``metrics_basic`` covers the
  three foundational numbers (summary, throughput, distribution);
  ``metrics_advanced`` covers everything that needs a real analytics tier
  (CFD, scatter, aging, flow efficiency, blockers, demand, Little's Law);
  ``kmm_dashboard`` gates the maturity ladder on its own, since a workspace can
  buy Business without wanting the KMM view turned on for every board.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, get_board_or_404, get_session, require_capability, require_flag
from ..flow.kmm import compute_kmm
from ..flow.metrics import (
    MetricsContext,
)
from ..flow.metrics import (
    aging as aging_metric,
)
from ..flow.metrics import (
    blockers as blockers_metric,
)
from ..flow.metrics import (
    cfd as cfd_metric,
)
from ..flow.metrics import (
    demand as demand_metric,
)
from ..flow.metrics import (
    distribution as distribution_metric,
)
from ..flow.metrics import (
    flow_efficiency as flow_efficiency_metric,
)
from ..flow.metrics import (
    littles_law as littles_law_metric,
)
from ..flow.metrics import (
    scatter as scatter_metric,
)
from ..flow.metrics import (
    summary as summary_metric,
)
from ..flow.metrics import (
    throughput as throughput_metric,
)
from ..schemas import KmmOut, SeriesOut, SummaryOut

router = APIRouter(tags=["metrics"])

__all__ = ["router"]


# ---------------------------------------------------------------------------
# shared query parameters (SPEC §5: "all accept ?from=&to=&project_id=&
# service_class=&segment=")
# ---------------------------------------------------------------------------


def common_filters(
    frm: str | None = Query(None, alias="from", description="ISO-8601 lower bound (inclusive)."),
    to: str | None = Query(None, description="ISO-8601 upper bound (inclusive)."),
    project_id: str | None = Query(None, description="Restrict to one project."),
    service_class: str | None = Query(
        None,
        pattern="^(expedite|standard|fixed_date|intangible)$",
        description="Restrict to one class of service.",
    ),
    segment: str = Query(
        "unified",
        pattern="^(upstream|downstream|unified)$",
        description="Which half of the flow to analyse.",
    ),
) -> dict[str, Any]:
    """The five filters every metrics endpoint honours (SPEC §5, §7)."""
    return {
        "frm": frm,
        "to": to,
        "project_id": project_id,
        "service_class": service_class,
        "segment": segment,
    }


async def _ctx(s: AsyncSession, board_id: str, filters: dict[str, Any]) -> MetricsContext:
    """Resolve the board and build the one snapshot every metric reads from.

    The metric engine is synchronous and pure; it reads the board once, through
    the session's synchronous face, and computes from memory afterwards.
    """
    board = await get_board_or_404(s, board_id)
    return await s.run_sync(lambda session: MetricsContext(session, board, **filters))


# ---------------------------------------------------------------------------
# metrics_basic — summary, throughput, distribution
# ---------------------------------------------------------------------------


@router.get(
    "/boards/{id}/metrics/summary",
    response_model=SummaryOut,
    summary="Headline KPI set",
    dependencies=[Depends(require_flag("metrics_basic"))],
)
async def get_summary(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Every SPEC §7.1 KPI, each with its trend, sample size and confidence flag."""
    return summary_metric(await _ctx(s, id, filters))


@router.get(
    "/boards/{id}/metrics/throughput",
    response_model=SeriesOut,
    summary="Delivered counts per period, with rolling average",
    dependencies=[Depends(require_flag("metrics_basic"))],
)
async def get_throughput(
    id: str,
    period: str = Query("week", pattern="^(day|week|month)$"),
    rolling: int = Query(4, ge=1, le=26, description="Rolling-average window, in periods."),
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Delivered items per day/week/month plus a rolling average and variability read."""
    return throughput_metric(await _ctx(s, id, filters), period=period, rolling=rolling)


@router.get(
    "/boards/{id}/metrics/distribution",
    response_model=SeriesOut,
    summary="Lead-time histogram with percentile markers",
    dependencies=[Depends(require_flag("metrics_basic"))],
)
async def get_distribution(
    id: str,
    metric: str = Query("customer", pattern="^(customer|system|discovery)$"),
    bin_days: float | None = Query(
        None, gt=0, description="Fixed bin width; auto-sized when omitted."
    ),
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """A lead-time histogram (auto-binned by default) with 50/70/85/95 markers."""
    return distribution_metric(await _ctx(s, id, filters), bin_days=bin_days, metric=metric)


# ---------------------------------------------------------------------------
# metrics_advanced — cfd, scatter, aging, flow-efficiency, blockers, demand,
# littles-law
# ---------------------------------------------------------------------------


@router.get(
    "/boards/{id}/metrics/cfd",
    response_model=SeriesOut,
    summary="Cumulative flow diagram",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_cfd(
    id: str,
    interval: str = Query("auto", pattern="^(auto|day|week)$"),
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """One stacked-area series per column, ordered upstream to downstream."""
    return cfd_metric(await _ctx(s, id, filters), interval=interval)


@router.get(
    "/boards/{id}/metrics/scatter",
    response_model=SeriesOut,
    summary="Lead-time scatter with percentile and SLE reference lines",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_scatter(
    id: str,
    metric: str = Query("customer", pattern="^(customer|system|discovery)$"),
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """One dot per delivered card against its delivery date, plus reference lines."""
    return scatter_metric(await _ctx(s, id, filters), metric=metric)


@router.get(
    "/boards/{id}/metrics/aging",
    response_model=SeriesOut,
    summary="In-flight cards against the percentile bands they have crossed",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_aging(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Every in-flight card plotted by column and age, banded against SLE percentiles."""
    return aging_metric(await _ctx(s, id, filters))


@router.get(
    "/boards/{id}/metrics/flow-efficiency",
    response_model=SeriesOut,
    summary="Touch/wait split, aggregate and per column",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_flow_efficiency(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Aggregate flow efficiency plus the per-column wait-time Pareto."""
    return flow_efficiency_metric(await _ctx(s, id, filters))


@router.get(
    "/boards/{id}/metrics/blockers",
    response_model=SeriesOut,
    summary="Blocked time, count and Pareto by category",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_blockers(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Blocked days by category, ranked, with the worst-affected cards."""
    return blockers_metric(await _ctx(s, id, filters))


@router.get(
    "/boards/{id}/metrics/demand",
    response_model=SeriesOut,
    summary="Class-of-service mix, demand type, failure demand and due-date performance",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_demand(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Demand analysis: service-class mix, demand type, per-project split, flow debt."""
    return demand_metric(await _ctx(s, id, filters))


@router.get(
    "/boards/{id}/metrics/littles-law",
    response_model=SeriesOut,
    summary="WIP / throughput / lead-time coherence",
    dependencies=[Depends(require_flag("metrics_advanced"))],
)
async def get_littles_law(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Observed WIP against throughput × lead time, with a stability verdict."""
    return littles_law_metric(await _ctx(s, id, filters))


# ---------------------------------------------------------------------------
# kmm_dashboard — the maturity ladder
# ---------------------------------------------------------------------------


@router.get(
    "/boards/{id}/metrics/kmm",
    response_model=KmmOut,
    summary="Kanban Maturity Model readiness (ML0..ML6)",
    dependencies=[Depends(require_flag("kmm_dashboard"))],
)
async def get_kmm(
    id: str,
    filters: dict[str, Any] = Depends(common_filters),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Per-level metric availability, current level and concrete next actions."""
    return compute_kmm(await _ctx(s, id, filters))
