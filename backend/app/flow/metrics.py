"""Every flow metric in SPEC §7.1, computed from movement history.

Design rules this module follows, because a flow dashboard that lies is worse
than no dashboard:

1. **Nothing is estimated from the card's current column.** Durations come from
   :mod:`backend.app.flow.intervals`, i.e. from ``card_movements``.
2. **Percentiles use linear interpolation** between order statistics (the
   ``R type 7`` / ``numpy`` default). Kanban capability claims live or die on the
   85th percentile, and nearest-rank quantisation visibly distorts it on the
   small samples real teams have.
3. **Small samples are labelled, not hidden.** Every KPI carries
   ``sample_size`` and sets ``low_confidence`` below eight observations
   (SPEC §7.1) instead of quietly rendering a confident-looking number.
4. **One load, many metrics.** :class:`MetricsContext` reads columns, cards,
   movements and blockers once; every function below is pure arithmetic over
   that snapshot.
5. **Filters are honoured everywhere.** ``from``/``to``/``project_id``/
   ``service_class``/``segment`` and the board's ``count_envelope_in_metrics``
   toggle change the numbers, never the response shape.

Every series-shaped result uses one envelope so the charts agent can render any
of them with the same code::

    {"series": [{"key", "label", "color_role", "points": [{"x", "y", ...}]}],
     "meta": {...}}
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import parse_iso
from ..models import Blocker, Board, Card, Project, SimulationRun
from .intervals import (
    ACTIVITY,
    DAY,
    DONE,
    ENVELOPE,
    QUEUE,
    CardFlow,
    Interval,
    LeadTimes,
    TouchWait,
    board_flows,
    first_reach_times,
    lead_times,
    overlap_seconds,
    touch_wait,
)

__all__ = [
    "LOW_CONFIDENCE_SAMPLE",
    "PERCENTILES",
    "MetricsContext",
    "aging",
    "blockers",
    "cfd",
    "demand",
    "distribution",
    "flow_efficiency",
    "littles_law",
    "percentile",
    "percentile_set",
    "scatter",
    "summary",
    "throughput",
]

#: The four percentiles SPEC §7.1 requires wherever a distribution exists.
PERCENTILES: tuple[int, ...] = (50, 70, 85, 95)

#: Below this many observations a KPI is flagged ``low_confidence`` (SPEC §7.1).
LOW_CONFIDENCE_SAMPLE = 8

DEFAULT_WINDOW_DAYS = 90

#: Hard ceiling on points in a time series, so a five-year window cannot make the
#: browser render 1 800 stacked polygons.
MAX_SERIES_POINTS = 400

_SERVICE_CLASS_ROLE = {
    "expedite": "sc-expedite",
    "standard": "sc-standard",
    "fixed_date": "sc-fixed-date",
    "intangible": "sc-intangible",
}

_SERVICE_CLASS_LABEL = {
    "expedite": "Expedite",
    "standard": "Standard",
    "fixed_date": "Fixed date",
    "intangible": "Intangible",
}

_DEMAND_TYPE_LABEL = {
    "value": "Value demand",
    "failure": "Failure demand",
    "maintenance": "Maintenance",
}

_DEMAND_TYPE_ROLE = {"value": "success", "failure": "danger", "maintenance": "info"}

_COLUMN_KIND_ROLE = {
    ENVELOPE: "envelope",
    QUEUE: "queue",
    ACTIVITY: "activity",
    DONE: "done",
}


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float | None:
    """The ``p``-th percentile with linear interpolation between order statistics.

    Matches ``numpy.percentile(..., method="linear")`` and R's type-7 quantile::

        rank = p/100 × (n − 1)
        result = v[⌊rank⌋] + (rank − ⌊rank⌋) × (v[⌈rank⌉] − v[⌊rank⌋])

    Nearest-rank would round an 85th percentile of a 10-item sample to the 9th
    value, overstating capability by a whole order statistic — which is exactly
    the number teams turn into a service level expectation.
    """
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    count = len(ordered)
    if count == 1:
        return ordered[0]
    p = min(100.0, max(0.0, float(p)))
    rank = (p / 100.0) * (count - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] + weight * (ordered[high] - ordered[low])


def percentile_set(
    values: Sequence[float], points: Sequence[int] = PERCENTILES
) -> dict[str, float]:
    """``{"p50": …, "p70": …, "p85": …, "p95": …}`` (empty when there is no data)."""
    if not values:
        return {}
    ordered = sorted(float(v) for v in values)
    out: dict[str, float] = {}
    for point in points:
        computed = percentile(ordered, point)
        if computed is not None:
            out[f"p{point}"] = round(computed, 4)
    return out


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(float(v) for v in values) / len(values)


def _stdev(values: Sequence[float]) -> float | None:
    """Sample standard deviation; ``None`` below two observations."""
    count = len(values)
    if count < 2:
        return None
    average = sum(float(v) for v in values) / count
    variance = sum((float(v) - average) ** 2 for v in values) / (count - 1)
    return math.sqrt(variance)


def _cov(values: Sequence[float]) -> float | None:
    """Coefficient of variation — the scale-free measure of flow variability."""
    average = _mean(values)
    deviation = _stdev(values)
    if average is None or deviation is None or average == 0:
        return None
    return deviation / average


def _r(value: Any, digits: int = 3) -> float | None:
    """Round for transport, turning NaN/inf into ``None`` so the JSON stays valid."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _pct(part: float, whole: float) -> float | None:
    """``part / whole × 100`` with an explicit zero-denominator guard."""
    if not whole:
        return None
    return part / whole * 100.0


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _parse_bound(value: str | datetime | None, *, end_of_day: bool) -> datetime | None:
    """Parse a ``from``/``to`` filter, widening a bare date to the whole day."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _utc(value)
    text = str(value).strip()
    parsed = parse_iso(text)
    if parsed is None:
        return None
    date_only = len(text) == 10 and text.count("-") == 2
    if date_only and end_of_day:
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return _utc(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_key(moment: datetime) -> str:
    return _utc(moment).strftime("%Y-%m-%d")


def _period_start(moment: datetime, period: str) -> datetime:
    """Bucket an instant into the start of its day / ISO week / month (UTC)."""
    anchored = _utc(moment).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return anchored - timedelta(days=anchored.weekday())  # ISO weeks start Monday
    if period == "month":
        return anchored.replace(day=1)
    return anchored


def _next_period(moment: datetime, period: str) -> datetime:
    if period == "week":
        return moment + timedelta(days=7)
    if period == "month":
        year, month = moment.year, moment.month
        return (
            moment.replace(year=year + 1, month=1)
            if month == 12
            else moment.replace(month=month + 1)
        )
    return moment + timedelta(days=1)


def _period_range(start: datetime, end: datetime, period: str) -> list[datetime]:
    """Every bucket start from ``start`` to ``end`` inclusive."""
    cursor = _period_start(start, period)
    out: list[datetime] = []
    guard = 0
    while cursor <= end and guard < 5000:
        out.append(cursor)
        cursor = _next_period(cursor, period)
        guard += 1
    return out


# ---------------------------------------------------------------------------
# per-card snapshot
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CardRecord:
    """One card with everything the metrics need, computed exactly once."""

    card: dict[str, Any]
    intervals: list[Interval]
    lead: LeadTimes
    split: TouchWait
    created_at: datetime | None
    start_at: datetime | None
    committed_at: datetime | None
    delivered_at: datetime | None
    due_date: datetime | None

    @property
    def id(self) -> str:
        return str(self.card.get("id"))

    @property
    def is_delivered(self) -> bool:
        return self.delivered_at is not None

    def in_flight_at(self, moment: datetime) -> bool:
        """Committed but not yet delivered as of ``moment`` (SPEC §7.1 WIP)."""
        if self.committed_at is None or self.committed_at > moment:
            return False
        return self.delivered_at is None or self.delivered_at > moment

    def started_at(self, moment: datetime) -> bool:
        """Present in the flow (not sealed, not delivered) as of ``moment``."""
        if self.start_at is None or self.start_at > moment:
            return False
        return self.delivered_at is None or self.delivered_at > moment

    def age_days(self, moment: datetime) -> float | None:
        """Aging: ``now − committed_at``, falling back to the flow start upstream."""
        anchor = self.committed_at or self.start_at
        if anchor is None:
            return None
        return max(0.0, (moment - anchor).total_seconds()) / DAY


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


class MetricsContext:
    """One filtered snapshot of a board, shared by every metric function.

    Everything is read in the constructor — columns, cards, movements, blockers,
    projects and the two workspace counts the maturity model needs — so the
    context is plain data afterwards. Routers build it inside
    ``AsyncSession.run_sync`` and then compute with no database in sight.
    """

    def __init__(
        self,
        db: Session,
        board: Mapping[str, Any],
        *,
        frm: str | datetime | None = None,
        to: str | datetime | None = None,
        project_id: str | None = None,
        service_class: str | None = None,
        segment: str = "unified",
        include_envelope: bool | None = None,
        now: str | datetime | None = None,
    ) -> None:
        self.board = dict(board)
        self.board_id = str(board["id"])

        self.now = _utc(parse_iso(now) or datetime.now(UTC)) if now else datetime.now(UTC)
        self.segment = segment if segment in {"upstream", "downstream", "unified"} else "unified"
        self.project_id = project_id or None
        self.service_class = service_class or None
        self.include_envelope = (
            bool(board.get("count_envelope_in_metrics"))
            if include_envelope is None
            else bool(include_envelope)
        )

        window_end = _parse_bound(to, end_of_day=True) or self.now
        window_start = _parse_bound(frm, end_of_day=False) or (
            window_end - timedelta(days=DEFAULT_WINDOW_DAYS)
        )
        if window_start > window_end:
            window_start, window_end = window_end, window_start
        self.window_start = window_start
        self.window_end = window_end
        #: Analysis instant. A window that ends in the past makes every "current"
        #: metric an as-of reading rather than a mix of past and present.
        self.as_of = min(window_end, self.now)
        span = window_end - window_start
        self.window_days = max(span.total_seconds() / DAY, 1.0 / 24.0)
        self.previous_end = window_start
        self.previous_start = window_start - span

        # -- board structure ------------------------------------------------
        flows, columns = board_flows(
            db,
            self.board_id,
            now=self.as_of,
            project_id=self.project_id,
            service_class=self.service_class,
        )
        self.columns_by_id: dict[str, dict[str, Any]] = columns
        self.all_columns: list[dict[str, Any]] = sorted(
            columns.values(), key=lambda row: (int(row.get("position") or 0), row.get("name") or "")
        )
        self.commitment_column = next(
            (c for c in self.all_columns if c.get("is_commitment_point")), None
        )
        self.delivery_column = next(
            (c for c in self.all_columns if c.get("is_delivery_point")),
            next((c for c in reversed(self.all_columns) if c.get("kind") == DONE), None),
        )
        self.flow_columns: list[dict[str, Any]] = [
            column for column in self.all_columns if self._column_in_scope(column)
        ]
        self.flow_column_ids: list[str] = [c["id"] for c in self.flow_columns]

        # -- per-card snapshot ----------------------------------------------
        self.records: list[CardRecord] = [self._record(flow) for flow in flows]
        self.records_by_id: dict[str, CardRecord] = {r.id: r for r in self.records}
        self._blockers: list[dict[str, Any]] = self._load_blockers(db)
        self._projects: dict[str, dict[str, Any]] = {
            project.id: {
                "id": project.id,
                "name": project.name,
                "key": project.key,
                "color": project.color,
                "status": project.status,
                "target_date": project.target_date,
            }
            for project in db.scalars(select(Project))
        }
        #: Read by the maturity model (KMM): operational boards in the workspace, and
        #: WIP experiments run against this board or a clone of it.
        self.operational_board_count = int(
            db.scalar(
                select(func.count()).where(Board.kind == "operational", Board.archived.is_(False))
            )
            or 0
        )
        self.wip_experiment_count = int(
            db.scalar(
                select(func.count())
                .select_from(SimulationRun)
                .join(Board, Board.id == SimulationRun.board_id)
                .where(
                    SimulationRun.kind == "wip_experiment",
                    or_(Board.id == self.board_id, Board.source_board_id == self.board_id),
                )
            )
            or 0
        )

    # -- construction helpers ------------------------------------------------

    def _column_in_scope(self, column: Mapping[str, Any]) -> bool:
        """Is this column part of the filtered flow the caller asked about?"""
        if column.get("kind") == ENVELOPE and not self.include_envelope:
            return False
        if self.segment == "unified":
            return True
        return column.get("segment") == self.segment

    def _scoped_intervals(self, flow: CardFlow) -> list[Interval]:
        """Intervals that count toward touch/wait under the current filters."""
        out: list[Interval] = []
        for interval in flow.intervals:
            if interval.kind == ENVELOPE and not self.include_envelope:
                continue
            if self.segment != "unified" and interval.segment != self.segment:
                continue
            out.append(interval)
        return out

    def _record(self, flow: CardFlow) -> CardRecord:
        card = flow.card
        lead = lead_times(card, self.include_envelope)
        split = touch_wait(card, self._scoped_intervals(flow), self.include_envelope)
        return CardRecord(
            card=card,
            intervals=flow.intervals,
            lead=lead,
            split=split,
            created_at=parse_iso(card.get("created_at")),
            start_at=parse_iso(lead.start_at),
            committed_at=parse_iso(card.get("committed_at")),
            delivered_at=parse_iso(card.get("delivered_at")),
            due_date=parse_iso(card.get("due_date")),
        )

    # -- loaded data ---------------------------------------------------------

    def _load_blockers(self, db: Session) -> list[dict[str, Any]]:
        """Blockers on this board's (filtered) cards, one query."""
        query = (
            select(
                Blocker.id,
                Blocker.card_id,
                Blocker.reason,
                Blocker.category,
                Blocker.started_at,
                Blocker.ended_at,
                Card.key.label("card_key"),
                Card.title.label("card_title"),
                Card.service_class,
                Card.project_id,
            )
            .join(Card, Card.id == Blocker.card_id)
            .where(Card.board_id == self.board_id, Card.archived.is_(False))
        )
        if self.project_id:
            query = query.where(Card.project_id == self.project_id)
        if self.service_class:
            query = query.where(Card.service_class == self.service_class)
        rows = db.execute(query.order_by(Blocker.started_at, Blocker.id))
        return [dict(row._mapping) for row in rows]

    @property
    def blocker_rows(self) -> list[dict[str, Any]]:
        """Blockers on this board's (filtered) cards."""
        return self._blockers

    @property
    def projects(self) -> dict[str, dict[str, Any]]:
        """Projects referenced by this board's cards, keyed by id."""
        return self._projects

    # -- cohorts -------------------------------------------------------------

    def delivered_between(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[CardRecord]:
        """Cards whose ``delivered_at`` falls in ``(start, end]``."""
        start = self.window_start if start is None else start
        end = self.window_end if end is None else end
        return [
            record
            for record in self.records
            if record.delivered_at is not None and start <= record.delivered_at <= end
        ]

    def arrived_between(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[CardRecord]:
        """Cards whose flow clock started in ``(start, end]`` — the arrival rate."""
        start = self.window_start if start is None else start
        end = self.window_end if end is None else end
        return [
            record
            for record in self.records
            if record.start_at is not None and start <= record.start_at <= end
        ]

    def in_flight_at(self, moment: datetime | None = None) -> list[CardRecord]:
        """WIP: committed and not delivered as of ``moment`` (SPEC §7.1)."""
        moment = self.as_of if moment is None else moment
        return [record for record in self.records if record.in_flight_at(moment)]

    def active_at(self, moment: datetime | None = None) -> list[CardRecord]:
        """Every card present in the flow — upstream options included."""
        moment = self.as_of if moment is None else moment
        out: list[CardRecord] = []
        for record in self.records:
            if record.card.get("envelope_state") == "sealed" and not self.include_envelope:
                continue
            if not record.started_at(moment) and not (
                record.card.get("envelope_state") == "sealed"
                and self.include_envelope
                and record.created_at is not None
                and record.created_at <= moment
            ):
                continue
            out.append(record)
        return out

    def daily_wip(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[tuple[datetime, int]]:
        """Daily WIP samples — the basis for the average WIP in Little's Law."""
        start = self.window_start if start is None else start
        end = min(self.window_end if end is None else end, self.now)
        if end < start:
            return []
        total_days = max(1, int((end - start).total_seconds() // DAY) + 1)
        step = max(1, math.ceil(total_days / MAX_SERIES_POINTS))
        samples: list[tuple[datetime, int]] = []
        cursor = start
        while cursor <= end:
            samples.append((cursor, len(self.in_flight_at(cursor))))
            cursor += timedelta(days=step)
        if samples and samples[-1][0] < end:
            samples.append((end, len(self.in_flight_at(end))))
        return samples

    # -- shared descriptors --------------------------------------------------

    def filters(self) -> dict[str, Any]:
        """Echo of the filters that produced this snapshot."""
        return {
            "from": _iso(self.window_start),
            "to": _iso(self.window_end),
            "as_of": _iso(self.as_of),
            "project_id": self.project_id,
            "service_class": self.service_class,
            "segment": self.segment,
            "include_envelope": self.include_envelope,
            "window_days": _r(self.window_days, 2),
        }

    def envelope(
        self,
        kind: str,
        series: list[dict[str, Any]],
        *,
        meta: dict[str, Any] | None = None,
        percentiles: dict[str, float] | None = None,
        categories: list[Any] | None = None,
        x_label: str | None = None,
        y_label: str | None = None,
        unit: str | None = None,
        sample_size: int = 0,
    ) -> dict[str, Any]:
        """The one chart-shaped response every series endpoint returns."""
        payload: dict[str, Any] = {
            "board_id": self.board_id,
            "board_name": self.board.get("name"),
            "kind": kind,
            "series": series,
            "categories": categories or [],
            "percentiles": percentiles or {},
            "x_label": x_label,
            "y_label": y_label,
            "unit": unit,
            "sample_size": sample_size,
            "low_confidence": sample_size < LOW_CONFIDENCE_SAMPLE,
            "meta": {**(meta or {}), "filters": self.filters()},
            "generated_at": _iso(self.now),
        }
        return payload


def _series(
    key: str,
    label: str,
    color_role: str,
    points: list[dict[str, Any]],
    **meta: Any,
) -> dict[str, Any]:
    """One renderable series. ``color_role`` is a design-token role, not a hex."""
    entry: dict[str, Any] = {
        "key": key,
        "label": label,
        "color_role": color_role,
        "points": points,
    }
    if meta:
        entry.update(meta)
    return entry


def _metric(
    value: float | int | None,
    unit: str,
    *,
    label: str,
    description: str,
    sample_size: int = 0,
    previous: float | int | None = None,
    higher_is_better: bool | None = True,
    digits: int = 2,
) -> dict[str, Any]:
    """The uniform KPI envelope of SPEC §7.1.

    ``trend_pct`` is the relative change against the previous window of equal
    length; ``higher_is_better`` lets the UI colour that change without
    hard-coding a list of metric names.
    """
    rounded = _r(value, digits)
    previous_rounded = _r(previous, digits)
    trend: float | None = None
    if rounded is not None and previous_rounded not in (None, 0):
        trend = (rounded - previous_rounded) / abs(previous_rounded) * 100.0
    return {
        "value": rounded,
        "unit": unit,
        "trend_pct": _r(trend, 1),
        "sample_size": int(sample_size),
        "previous_value": previous_rounded,
        "low_confidence": int(sample_size) < LOW_CONFIDENCE_SAMPLE,
        "label": label,
        "description": description,
        "higher_is_better": higher_is_better,
    }


# ---------------------------------------------------------------------------
# 1. summary
# ---------------------------------------------------------------------------


def _window_aggregate(
    ctx: MetricsContext, start: datetime, end: datetime, as_of: datetime
) -> dict[str, Any]:
    """Every raw number the summary needs, for one window. Reused for the trend."""
    delivered = ctx.delivered_between(start, end)
    arrivals = ctx.arrived_between(start, end)

    lead_customer = [r.lead.customer_days for r in delivered if r.lead.customer_days is not None]
    lead_system = [r.lead.system_days for r in delivered if r.lead.system_days is not None]
    lead_discovery = [r.lead.discovery_days for r in delivered if r.lead.discovery_days is not None]

    touch = sum(r.split.touch_seconds for r in delivered)
    wait = sum(r.split.wait_seconds for r in delivered)
    envelope = sum(r.split.envelope_seconds for r in delivered)
    denominator = touch + wait + (envelope if ctx.include_envelope else 0.0)

    weeks = _period_range(start, end, "week")
    weekly_counts = [
        sum(
            1
            for r in delivered
            if r.delivered_at is not None and week <= r.delivered_at < _next_period(week, "week")
        )
        for week in weeks
    ]

    in_flight = [r for r in ctx.records if r.in_flight_at(as_of)]
    ages = [age for age in (r.age_days(as_of) for r in in_flight) if age is not None]
    wip_samples = [count for _, count in ctx.daily_wip(start, min(end, ctx.now))]

    sle_days = ctx.board.get("sle_days")
    sle_ok = sum(1 for value in lead_customer if value <= float(sle_days)) if sle_days else 0

    fixed_date = [
        r for r in delivered if r.card.get("service_class") == "fixed_date" and r.due_date
    ]
    on_time = sum(
        1 for r in fixed_date if r.delivered_at is not None and r.delivered_at <= r.due_date
    )
    failure = sum(1 for r in delivered if r.card.get("demand_type") == "failure")

    blocked_seconds = 0.0
    blocked_cards: set[str] = set()
    for row in ctx.blocker_rows:
        started = parse_iso(row.get("started_at"))
        ended = parse_iso(row.get("ended_at")) or as_of
        overlap = overlap_seconds(started, ended, start, min(end, as_of))
        if overlap > 0:
            blocked_seconds += overlap
            blocked_cards.add(str(row["card_id"]))

    lead_seconds_total = sum(r.lead.customer for r in delivered if r.lead.customer is not None)
    blocked_within_lead = 0.0
    delivered_ids = {r.id for r in delivered}
    for row in ctx.blocker_rows:
        if str(row["card_id"]) not in delivered_ids:
            continue
        record = ctx.records_by_id.get(str(row["card_id"]))
        if record is None:
            continue
        blocked_within_lead += overlap_seconds(
            parse_iso(row.get("started_at")),
            parse_iso(row.get("ended_at")) or as_of,
            record.start_at,
            record.delivered_at,
        )

    window_days = max((end - start).total_seconds() / DAY, 1.0 / 24.0)

    return {
        "delivered": delivered,
        "delivered_count": len(delivered),
        "arrivals": len(arrivals),
        "lead_customer": lead_customer,
        "lead_system": lead_system,
        "lead_discovery": lead_discovery,
        "touch_seconds": touch,
        "wait_seconds": wait,
        "envelope_seconds": envelope,
        "flow_efficiency": _pct(touch, denominator),
        "weekly_counts": weekly_counts,
        "throughput_per_week": len(delivered) / (window_days / 7.0),
        "wip": len(in_flight),
        "avg_wip": _mean(wip_samples),
        "ages": ages,
        "sle_ok": sle_ok,
        "sle_pct": _pct(sle_ok, len(lead_customer)),
        "fixed_date_total": len(fixed_date),
        "due_date_pct": _pct(on_time, len(fixed_date)),
        "failure_pct": _pct(failure, len(delivered)),
        "blocked_days": blocked_seconds / DAY,
        "blocked_cards": len(blocked_cards),
        "blocked_lead_pct": _pct(blocked_within_lead, lead_seconds_total),
        "window_days": window_days,
    }


def summary(ctx: MetricsContext) -> dict[str, Any]:
    """The headline KPI set of SPEC §7.1, each with its trend and sample size."""
    current = _window_aggregate(ctx, ctx.window_start, ctx.window_end, ctx.as_of)
    previous = _window_aggregate(ctx, ctx.previous_start, ctx.previous_end, ctx.previous_end)

    delivered_n = current["delivered_count"]
    customer = current["lead_customer"]
    system = current["lead_system"]
    discovery = current["lead_discovery"]
    percentiles = percentile_set(customer)
    system_percentiles = percentile_set(system)

    weekly = current["weekly_counts"]
    variability = _cov(weekly)
    avg_wip = current["avg_wip"]
    throughput_per_day = delivered_n / current["window_days"] if current["window_days"] else 0.0
    mean_lead_system = _mean(system)
    predicted_wip = throughput_per_day * mean_lead_system if mean_lead_system is not None else None

    metrics: dict[str, dict[str, Any]] = {
        "customer_lead_time": _metric(
            _mean(customer),
            "days",
            label="Customer lead time",
            description=(
                "Mean elapsed time from the moment demand entered the flow to delivery"
                + (" (sealed-envelope time counted)." if ctx.include_envelope else ".")
            ),
            sample_size=len(customer),
            previous=_mean(previous["lead_customer"]),
            higher_is_better=False,
        ),
        "customer_lead_time_p85": _metric(
            percentiles.get("p85"),
            "days",
            label="Lead time (85th pct)",
            description="85% of delivered work finished within this many days.",
            sample_size=len(customer),
            previous=percentile_set(previous["lead_customer"]).get("p85"),
            higher_is_better=False,
        ),
        "system_lead_time": _metric(
            _mean(system),
            "days",
            label="System lead time",
            description="Mean cycle time from the commitment point to delivery.",
            sample_size=len(system),
            previous=_mean(previous["lead_system"]),
            higher_is_better=False,
        ),
        "discovery_lead_time": _metric(
            _mean(discovery),
            "days",
            label="Discovery lead time",
            description="Mean time demand spends upstream before the team commits to it.",
            sample_size=len(discovery),
            previous=_mean(previous["lead_discovery"]),
            higher_is_better=False,
        ),
        "touch_time": _metric(
            current["touch_seconds"] / DAY / delivered_n if delivered_n else None,
            "days",
            label="Touch time",
            description="Mean time per delivered item spent in activity columns.",
            sample_size=delivered_n,
            previous=(
                previous["touch_seconds"] / DAY / previous["delivered_count"]
                if previous["delivered_count"]
                else None
            ),
            higher_is_better=None,
        ),
        "wait_time": _metric(
            current["wait_seconds"] / DAY / delivered_n if delivered_n else None,
            "days",
            label="Wait time",
            description="Mean time per delivered item spent parked in queue columns.",
            sample_size=delivered_n,
            previous=(
                previous["wait_seconds"] / DAY / previous["delivered_count"]
                if previous["delivered_count"]
                else None
            ),
            higher_is_better=False,
        ),
        "flow_efficiency": _metric(
            current["flow_efficiency"],
            "percent",
            label="Flow efficiency",
            description="Touch time as a share of total lead time. Most teams start near 15%.",
            sample_size=delivered_n,
            previous=previous["flow_efficiency"],
            higher_is_better=True,
        ),
        "throughput": _metric(
            current["throughput_per_week"],
            "cards/week",
            label="Throughput",
            description="Delivered items per week across the selected window.",
            sample_size=delivered_n,
            previous=previous["throughput_per_week"],
            higher_is_better=True,
        ),
        "delivered": _metric(
            delivered_n,
            "count",
            label="Delivered",
            description="Items that reached the delivery point inside the window.",
            sample_size=delivered_n,
            previous=previous["delivered_count"],
            higher_is_better=True,
            digits=0,
        ),
        "arrival_rate": _metric(
            current["arrivals"] / (current["window_days"] / 7.0),
            "cards/week",
            label="Arrival rate",
            description="New demand entering the flow per week. Compare with throughput.",
            sample_size=current["arrivals"],
            previous=previous["arrivals"] / (previous["window_days"] / 7.0),
            higher_is_better=None,
        ),
        "net_flow": _metric(
            current["arrivals"] - delivered_n,
            "count",
            label="Net flow",
            description="Arrivals minus departures. Sustained positive net flow grows the queue.",
            sample_size=current["arrivals"] + delivered_n,
            previous=previous["arrivals"] - previous["delivered_count"],
            higher_is_better=False,
            digits=0,
        ),
        "wip": _metric(
            current["wip"],
            "count",
            label="Work in progress",
            description="Committed items not yet delivered, as of the end of the window.",
            sample_size=current["wip"],
            previous=previous["wip"],
            higher_is_better=False,
            digits=0,
        ),
        "avg_wip": _metric(
            avg_wip,
            "count",
            label="Average WIP",
            description="Mean daily work in progress across the window.",
            sample_size=current["wip"],
            previous=previous["avg_wip"],
            higher_is_better=False,
        ),
        "average_age": _metric(
            _mean(current["ages"]),
            "days",
            label="Average age",
            description="Mean age of items currently in flight, measured from commitment.",
            sample_size=len(current["ages"]),
            previous=_mean(previous["ages"]),
            higher_is_better=False,
        ),
        "oldest_age": _metric(
            max(current["ages"]) if current["ages"] else None,
            "days",
            label="Oldest item",
            description="Age of the longest-running item in flight. The first thing to unblock.",
            sample_size=len(current["ages"]),
            previous=max(previous["ages"]) if previous["ages"] else None,
            higher_is_better=False,
        ),
        "sle_attainment": _metric(
            current["sle_pct"],
            "percent",
            label="SLE attainment",
            description=(
                f"Share of delivered work inside the {ctx.board.get('sle_days')}-day service "
                f"level expectation (target {ctx.board.get('sle_percentile')}%)."
                if ctx.board.get("sle_days")
                else "No service level expectation is set on this board."
            ),
            sample_size=len(customer),
            previous=previous["sle_pct"],
            higher_is_better=True,
        ),
        "due_date_performance": _metric(
            current["due_date_pct"],
            "percent",
            label="Due-date performance",
            description="Fixed-date items delivered on or before their due date.",
            sample_size=current["fixed_date_total"],
            previous=previous["due_date_pct"],
            higher_is_better=True,
        ),
        "failure_demand": _metric(
            current["failure_pct"],
            "percent",
            label="Failure demand",
            description="Delivered items that existed only because something else failed.",
            sample_size=delivered_n,
            previous=previous["failure_pct"],
            higher_is_better=False,
        ),
        "blocked_time": _metric(
            current["blocked_lead_pct"],
            "percent",
            label="Lead time blocked",
            description="Share of delivered items' lead time spent explicitly blocked.",
            sample_size=delivered_n,
            previous=previous["blocked_lead_pct"],
            higher_is_better=False,
        ),
        "blocked_cards": _metric(
            current["blocked_cards"],
            "count",
            label="Cards blocked",
            description="Distinct cards that were blocked at some point in the window.",
            sample_size=current["blocked_cards"],
            previous=previous["blocked_cards"],
            higher_is_better=False,
            digits=0,
        ),
        "throughput_variability": _metric(
            variability,
            "ratio",
            label="Throughput variability",
            description=(
                "Coefficient of variation of weekly throughput. Below 0.5 the flow is "
                "predictable enough to forecast without heroics."
            ),
            sample_size=len(weekly),
            previous=_cov(previous["weekly_counts"]),
            higher_is_better=False,
        ),
        "littles_law_coherence": _metric(
            (avg_wip / predicted_wip) if avg_wip is not None and predicted_wip else None,
            "ratio",
            label="Little's Law coherence",
            description=(
                "Observed average WIP divided by throughput × cycle time. A value near "
                "1.0 means the system is stable and the metrics can be trusted."
            ),
            sample_size=delivered_n,
            previous=None,
            higher_is_better=None,
        ),
    }

    return {
        "board_id": ctx.board_id,
        "board_name": ctx.board.get("name"),
        "from": _iso(ctx.window_start),
        "to": _iso(ctx.window_end),
        "metrics": metrics,
        "percentiles": percentiles,
        "system_percentiles": system_percentiles,
        "filters": ctx.filters(),
        "sample_size": delivered_n,
        "low_confidence": delivered_n < LOW_CONFIDENCE_SAMPLE,
        "generated_at": _iso(ctx.now),
    }


# ---------------------------------------------------------------------------
# 2. cumulative flow diagram
# ---------------------------------------------------------------------------


def cfd(ctx: MetricsContext, interval: str = "auto") -> dict[str, Any]:
    """Cumulative flow: one series per column, ordered upstream → downstream.

    Each point carries two numbers:

    ``cumulative``
        how many cards had reached this column *or any column downstream of it*
        by that instant — the classic CFD curve, monotone non-decreasing.
    ``y``
        the band thickness, ``cumulative[i] − cumulative[i+1]``: the number of
        cards sitting in that column. Stack these from the most downstream
        column upward and you reproduce the cumulative curves exactly.

    Rendering (``meta.render``): draw one filled area per series from zero up to
    ``cumulative``, painting the series in array order. Each area covers the
    previous one, so the visible band for a column is precisely its ``y``.
    """
    columns = ctx.flow_columns
    if not columns:
        return ctx.envelope(
            "cfd",
            [],
            meta={"render": "areas", "reason": "no_columns"},
            x_label="Date",
            y_label="Cards",
            unit="count",
        )

    span_days = (ctx.window_end - ctx.window_start).total_seconds() / DAY
    if interval not in {"day", "week"}:
        interval = "day" if span_days <= 180 else "week"

    reach = first_reach_times(
        [CardFlow(card=r.card, intervals=r.intervals) for r in ctx.records],
        ctx.flow_column_ids,
    )

    stamps = _period_range(ctx.window_start, min(ctx.window_end, ctx.now), interval)
    if not stamps:
        stamps = [ctx.window_start]
    if len(stamps) > MAX_SERIES_POINTS:
        stride = math.ceil(len(stamps) / MAX_SERIES_POINTS)
        stamps = stamps[::stride]
    # Sample at the *end* of each bucket so a day's arrivals are included in it.
    samples = [
        min(_next_period(stamp, interval) - timedelta(seconds=1), ctx.window_end)
        for stamp in stamps
    ]

    commitment_index = _index_of(ctx.flow_column_ids, ctx.commitment_column)
    delivery_index = _index_of(ctx.flow_column_ids, ctx.delivery_column)

    cumulative_by_column: list[list[int]] = [
        [bisect_right(reach[i], moment) for moment in samples] for i in range(len(columns))
    ]

    series: list[dict[str, Any]] = []
    last = len(columns) - 1
    for index, column in enumerate(columns):
        points: list[dict[str, Any]] = []
        for slot, stamp in enumerate(stamps):
            cumulative = cumulative_by_column[index][slot]
            downstream = cumulative_by_column[index + 1][slot] if index < last else 0
            points.append(
                {
                    "x": _day_key(stamp),
                    "y": max(0, cumulative - downstream),
                    "cumulative": cumulative,
                    "t": _iso(stamp),
                }
            )
        series.append(
            _series(
                str(column["id"]),
                str(column["name"]),
                _COLUMN_KIND_ROLE.get(str(column.get("kind")), "neutral"),
                points,
                column_kind=column.get("kind"),
                segment=column.get("segment"),
                position=column.get("position"),
                ramp=round(index / last, 4) if last else 0.0,
                is_commitment_point=bool(column.get("is_commitment_point")),
                is_delivery_point=bool(column.get("is_delivery_point")),
            )
        )

    # Derived reads: the vertical gap is WIP, the horizontal gap is lead time.
    derived: list[dict[str, Any]] = []
    for slot, stamp in enumerate(stamps):
        moment = samples[slot]
        if commitment_index is None or delivery_index is None:
            approx_wip = None
            approx_lead = None
        else:
            entered = cumulative_by_column[commitment_index][slot]
            left = cumulative_by_column[delivery_index][slot]
            approx_wip = max(0, entered - left)
            approx_lead = _horizontal_gap(reach[commitment_index], left, moment)
        derived.append(
            {
                "x": _day_key(stamp),
                "t": _iso(stamp),
                "wip": approx_wip,
                "approx_lead_time_days": _r(approx_lead, 2),
                "arrived": cumulative_by_column[0][slot] if cumulative_by_column else 0,
                "delivered": (
                    cumulative_by_column[delivery_index][slot] if delivery_index is not None else 0
                ),
            }
        )

    wip_values = [point["wip"] for point in derived if point["wip"] is not None]
    lead_values = [
        point["approx_lead_time_days"]
        for point in derived
        if point["approx_lead_time_days"] is not None
    ]

    return ctx.envelope(
        "cfd",
        series,
        categories=[
            {
                "id": column["id"],
                "name": column["name"],
                "kind": column.get("kind"),
                "segment": column.get("segment"),
            }
            for column in columns
        ],
        meta={
            "interval": interval,
            "render": (
                "Draw one filled area per series from y=0 to point.cumulative in array "
                "order (upstream first); each area covers the previous, so the visible "
                "band equals point.y — the number of cards in that column."
            ),
            "stack_from": list(reversed(ctx.flow_column_ids)),
            "commitment_column_id": ctx.commitment_column["id"] if ctx.commitment_column else None,
            "delivery_column_id": ctx.delivery_column["id"] if ctx.delivery_column else None,
            "derived": derived,
            "approx_wip_avg": _r(_mean(wip_values), 2),
            "approx_lead_time_avg_days": _r(_mean(lead_values), 2),
            "total_cards": len(ctx.records),
        },
        x_label="Date",
        y_label="Cards",
        unit="count",
        sample_size=len(ctx.records),
    )


def _index_of(ordered_ids: Sequence[str], column: Mapping[str, Any] | None) -> int | None:
    if column is None:
        return None
    try:
        return ordered_ids.index(str(column["id"]))
    except ValueError:
        return None


def _horizontal_gap(
    arrival_times: Sequence[datetime], departures: int, moment: datetime
) -> float | None:
    """Approximate lead time as the horizontal gap between the CFD curves.

    The ``departures``-th item to leave is the ``departures``-th item that
    entered (flow is conserved), so the horizontal distance from that item's
    arrival to ``moment`` is the lead time the system is currently delivering.
    """
    if departures <= 0 or departures > len(arrival_times):
        return None
    arrived_at = arrival_times[departures - 1]
    return max(0.0, (moment - arrived_at).total_seconds()) / DAY


# ---------------------------------------------------------------------------
# 3. lead-time scatter
# ---------------------------------------------------------------------------


_SCATTER_METRICS = {
    "customer": ("customer_days", "Customer lead time"),
    "system": ("system_days", "System lead time"),
    "discovery": ("discovery_days", "Discovery lead time"),
}


def scatter(ctx: MetricsContext, metric: str = "customer") -> dict[str, Any]:
    """One dot per delivered card plus the percentile and SLE reference lines."""
    metric = metric if metric in _SCATTER_METRICS else "customer"
    attribute, label = _SCATTER_METRICS[metric]

    delivered = sorted(ctx.delivered_between(), key=lambda record: record.delivered_at or ctx.now)
    points: list[dict[str, Any]] = []
    values: list[float] = []
    for record in delivered:
        value = getattr(record.lead, attribute)
        if value is None:
            continue
        project = ctx.projects.get(str(record.card.get("project_id") or ""))
        values.append(value)
        points.append(
            {
                "x": _iso(record.delivered_at),
                "y": _r(value, 3),
                "date": _day_key(record.delivered_at) if record.delivered_at else None,
                "card_id": record.id,
                "key": record.card.get("key"),
                "title": record.card.get("title"),
                "service_class": record.card.get("service_class"),
                "demand_type": record.card.get("demand_type"),
                "value_area": record.card.get("value_area"),
                "size": record.card.get("size"),
                "project_id": record.card.get("project_id"),
                "project": project.get("name") if project else None,
                "project_key": project.get("key") if project else None,
                "color_role": _SERVICE_CLASS_ROLE.get(
                    str(record.card.get("service_class")), "accent"
                ),
                "customer_days": record.lead.customer_days,
                "system_days": record.lead.system_days,
                "discovery_days": record.lead.discovery_days,
                "flow_efficiency": _r(record.split.flow_efficiency, 1),
                "blocked": bool(record.card.get("is_blocked")),
            }
        )

    percentiles = percentile_set(values)
    series = [_series("delivered", label, "accent", points, mark="dot", metric=metric)]

    # Percentile lines span the window so the chart can draw them as flat rules.
    x_from = _iso(ctx.window_start)
    x_to = _iso(min(ctx.window_end, ctx.now))
    role_for = {"p50": "info", "p70": "success", "p85": "warning", "p95": "danger"}
    for name, value in percentiles.items():
        series.append(
            _series(
                name,
                f"{name[1:]}th percentile — {value:.1f}d",
                role_for.get(name, "muted"),
                [{"x": x_from, "y": value}, {"x": x_to, "y": value}],
                mark="rule",
                dashed=name not in {"p85"},
            )
        )
    sle_days = ctx.board.get("sle_days")
    if sle_days:
        series.append(
            _series(
                "sle",
                f"SLE — {ctx.board.get('sle_percentile') or 85}% in {sle_days}d",
                "info",
                [{"x": x_from, "y": float(sle_days)}, {"x": x_to, "y": float(sle_days)}],
                mark="rule",
                dashed=True,
            )
        )

    within_sle = (
        sum(1 for value in values if value <= float(sle_days)) if sle_days and values else 0
    )
    return ctx.envelope(
        "scatter",
        series,
        percentiles=percentiles,
        meta={
            "metric": metric,
            "metric_label": label,
            "mean_days": _r(_mean(values), 2),
            "max_days": _r(max(values), 2) if values else None,
            "min_days": _r(min(values), 2) if values else None,
            "sle_days": sle_days,
            "sle_percentile": ctx.board.get("sle_percentile"),
            "sle_attainment_pct": _r(_pct(within_sle, len(values)), 1),
            "available_metrics": list(_SCATTER_METRICS),
        },
        x_label="Delivered",
        y_label=f"{label} (days)",
        unit="days",
        sample_size=len(values),
    )


# ---------------------------------------------------------------------------
# 4. throughput
# ---------------------------------------------------------------------------


def throughput(ctx: MetricsContext, period: str = "week", rolling: int = 4) -> dict[str, Any]:
    """Delivered counts per period with a rolling average and a CoV of variability."""
    period = period if period in {"day", "week", "month"} else "week"
    rolling = max(1, min(int(rolling or 4), 26))

    delivered = ctx.delivered_between()
    buckets = _period_range(ctx.window_start, min(ctx.window_end, ctx.now), period)
    if not buckets:
        buckets = [_period_start(ctx.window_start, period)]

    counts: list[int] = []
    per_bucket_classes: list[dict[str, int]] = []
    for start in buckets:
        end = _next_period(start, period)
        inside = [
            record
            for record in delivered
            if record.delivered_at is not None and start <= record.delivered_at < end
        ]
        counts.append(len(inside))
        classes: dict[str, int] = {}
        for record in inside:
            key = str(record.card.get("service_class") or "standard")
            classes[key] = classes.get(key, 0) + 1
        per_bucket_classes.append(classes)

    points: list[dict[str, Any]] = []
    rolling_points: list[dict[str, Any]] = []
    for index, start in enumerate(buckets):
        window = counts[max(0, index - rolling + 1) : index + 1]
        average = _mean(window)
        points.append(
            {
                "x": _day_key(start),
                "y": counts[index],
                "t": _iso(start),
                "period_end": _iso(_next_period(start, period) - timedelta(seconds=1)),
                "by_service_class": per_bucket_classes[index],
            }
        )
        rolling_points.append({"x": _day_key(start), "y": _r(average, 2), "t": _iso(start)})

    variability = _cov(counts)
    return ctx.envelope(
        "throughput",
        [
            _series("throughput", f"Delivered per {period}", "accent", points, mark="bar"),
            _series(
                "rolling",
                f"{rolling}-{period} rolling average",
                "info",
                rolling_points,
                mark="line",
            ),
        ],
        percentiles=percentile_set(counts),
        categories=[_day_key(start) for start in buckets],
        meta={
            "period": period,
            "rolling_window": rolling,
            "total": sum(counts),
            "periods": len(counts),
            "mean": _r(_mean(counts), 2),
            "median": _r(percentile(counts, 50), 2),
            "stdev": _r(_stdev(counts), 3),
            "cov": _r(variability, 3),
            "variability_label": _variability_label(variability),
            "max": max(counts) if counts else 0,
            "min": min(counts) if counts else 0,
            "zero_periods": sum(1 for value in counts if value == 0),
        },
        x_label=period.title(),
        y_label="Delivered",
        unit="count",
        sample_size=sum(counts),
    )


def _variability_label(cov: float | None) -> str:
    """Plain words for a coefficient of variation, the way a coach would say it."""
    if cov is None:
        return "Not enough periods to judge variability."
    if cov < 0.35:
        return "Highly predictable — forecasts from this history will be tight."
    if cov < 0.75:
        return "Normal variability — probabilistic forecasting works well here."
    if cov < 1.2:
        return "Lumpy delivery — expect wide forecast ranges until batch sizes drop."
    return "Erratic — investigate batching, blockers and unplanned work before forecasting."


# ---------------------------------------------------------------------------
# 5. aging work in progress
# ---------------------------------------------------------------------------


def aging(ctx: MetricsContext) -> dict[str, Any]:
    """Every in-flight card plotted against the percentile bands it has crossed."""
    delivered = ctx.delivered_between()
    system_values = [r.lead.system_days for r in delivered if r.lead.system_days is not None]
    customer_values = [r.lead.customer_days for r in delivered if r.lead.customer_days is not None]
    system_bands = percentile_set(system_values)
    customer_bands = percentile_set(customer_values)

    moment = ctx.as_of
    columns = {column["id"]: column for column in ctx.flow_columns}
    points: list[dict[str, Any]] = []
    for record in ctx.active_at(moment):
        interval = _interval_at(record.intervals, moment)
        column_id = interval.column_id if interval else str(record.card.get("column_id"))
        column = columns.get(column_id)
        if column is None:
            continue
        if column.get("kind") == DONE:
            continue
        age = record.age_days(moment)
        if age is None:
            continue
        committed = record.committed_at is not None and record.committed_at <= moment
        bands = system_bands if committed else customer_bands
        project = ctx.projects.get(str(record.card.get("project_id") or ""))
        days_in_column = (
            max(0.0, (moment - (parse_iso(interval.entered_at) or moment)).total_seconds()) / DAY
            if interval
            else None
        )
        points.append(
            {
                "x": column_id,
                "y": _r(age, 2),
                "column_id": column_id,
                "column_name": column.get("name"),
                "column_kind": column.get("kind"),
                "segment": column.get("segment"),
                "position": column.get("position"),
                "card_id": record.id,
                "key": record.card.get("key"),
                "title": record.card.get("title"),
                "service_class": record.card.get("service_class"),
                "color_role": _SERVICE_CLASS_ROLE.get(
                    str(record.card.get("service_class")), "accent"
                ),
                "project_id": record.card.get("project_id"),
                "project": project.get("name") if project else None,
                "project_key": project.get("key") if project else None,
                "assignee_id": record.card.get("assignee_id"),
                "is_blocked": bool(record.card.get("is_blocked")),
                "days_in_flight": _r(age, 2),
                "days_in_column": _r(days_in_column, 2),
                "committed": committed,
                "band": _band_for(age, bands),
                "band_scale": "system" if committed else "customer",
                "over_sle": (
                    bool(ctx.board.get("sle_days")) and age > float(ctx.board["sle_days"])
                ),
            }
        )

    points.sort(key=lambda point: (point["position"] or 0, -(point["y"] or 0)))
    band_series = _band_series(system_bands, ctx)
    ages = [point["y"] for point in points if point["y"] is not None]

    return ctx.envelope(
        "aging",
        [_series("cards", "In-flight cards", "accent", points, mark="dot"), *band_series],
        percentiles=system_bands,
        categories=[
            {
                "id": column["id"],
                "name": column["name"],
                "kind": column.get("kind"),
                "segment": column.get("segment"),
                "count": sum(1 for point in points if point["column_id"] == column["id"]),
                "wip_max": column.get("wip_max"),
                "wip_min": column.get("wip_min"),
            }
            for column in ctx.flow_columns
            if column.get("kind") != DONE
        ],
        meta={
            "bands": system_bands,
            "customer_bands": customer_bands,
            "band_basis": "system lead time of items delivered in this window",
            "sle_days": ctx.board.get("sle_days"),
            "sle_percentile": ctx.board.get("sle_percentile"),
            "as_of": _iso(moment),
            "wip": len([point for point in points if point["committed"]]),
            "mean_age_days": _r(_mean(ages), 2),
            "median_age_days": _r(percentile(ages, 50), 2),
            "oldest": max(points, key=lambda point: point["y"] or 0) if points else None,
            "over_p85": sum(1 for point in points if point["band"] in {"p85", "p95"}),
            "blocked": sum(1 for point in points if point["is_blocked"]),
        },
        x_label="Column",
        y_label="Age (days)",
        unit="days",
        sample_size=len(points),
    )


def _interval_at(intervals: Sequence[Interval], moment: datetime) -> Interval | None:
    found: Interval | None = None
    for interval in intervals:
        entered = parse_iso(interval.entered_at)
        if entered is None or entered > moment:
            continue
        exited = parse_iso(interval.exited_at) if interval.exited_at else None
        if exited is None or exited > moment or interval.is_terminal:
            found = interval
    return found


def _band_for(age: float, bands: Mapping[str, float]) -> str | None:
    """The highest percentile band this age has crossed."""
    crossed: str | None = None
    for name in ("p50", "p70", "p85", "p95"):
        value = bands.get(name)
        if value is not None and age >= value:
            crossed = name
    return crossed


def _band_series(bands: Mapping[str, float], ctx: MetricsContext) -> list[dict[str, Any]]:
    role_for = {"p50": "info", "p70": "success", "p85": "warning", "p95": "danger"}
    out: list[dict[str, Any]] = []
    for name in ("p50", "p70", "p85", "p95"):
        value = bands.get(name)
        if value is None:
            continue
        out.append(
            _series(
                name,
                f"{name[1:]}th percentile — {value:.1f}d",
                role_for[name],
                [
                    {"x": column["id"], "y": value}
                    for column in ctx.flow_columns
                    if column.get("kind") != DONE
                ],
                mark="band",
            )
        )
    return out


# ---------------------------------------------------------------------------
# 6. flow efficiency
# ---------------------------------------------------------------------------


def flow_efficiency(ctx: MetricsContext) -> dict[str, Any]:
    """Touch/wait split: aggregate, per card, and per column.

    The per-column breakdown is the actionable half. An aggregate of 18% tells a
    team it has a problem; ``Build Done: 41% of all wait time`` tells it where.
    """
    delivered = ctx.delivered_between()
    touch_total = sum(record.split.touch_seconds for record in delivered)
    wait_total = sum(record.split.wait_seconds for record in delivered)
    envelope_total = sum(record.split.envelope_seconds for record in delivered)
    counted_envelope = envelope_total if ctx.include_envelope else 0.0
    grand_total = touch_total + wait_total + counted_envelope

    per_card: list[dict[str, Any]] = []
    for record in sorted(delivered, key=lambda r: r.delivered_at or ctx.now):
        split = record.split
        per_card.append(
            {
                "card_id": record.id,
                "key": record.card.get("key"),
                "title": record.card.get("title"),
                "service_class": record.card.get("service_class"),
                "delivered_at": _iso(record.delivered_at),
                "touch_days": _r(split.touch_seconds / DAY, 3),
                "wait_days": _r(split.wait_seconds / DAY, 3),
                "envelope_days": _r(split.envelope_seconds / DAY, 3),
                "lead_time_days": record.lead.customer_days,
                "flow_efficiency": _r(split.flow_efficiency, 2),
            }
        )

    # -- per column ---------------------------------------------------------
    by_column: dict[str, dict[str, Any]] = {}
    for record in delivered:
        for column_id, bucket in record.split.by_column.items():
            entry = by_column.setdefault(
                column_id,
                {
                    "column_id": column_id,
                    "column_name": bucket["column_name"],
                    "kind": bucket["kind"],
                    "segment": bucket["segment"],
                    "position": bucket["position"],
                    "seconds": 0.0,
                    "visits": 0,
                    "cards": 0,
                },
            )
            entry["seconds"] += bucket["seconds"]
            entry["visits"] += bucket["visits"]
            entry["cards"] += 1

    ordered = sorted(by_column.values(), key=lambda entry: entry["position"])
    wait_by_column = sorted(
        (entry for entry in ordered if entry["kind"] in {QUEUE, ENVELOPE}),
        key=lambda entry: entry["seconds"],
        reverse=True,
    )
    cumulative = 0.0
    pareto: list[dict[str, Any]] = []
    wait_denominator = sum(entry["seconds"] for entry in wait_by_column) or 0.0
    for entry in wait_by_column:
        cumulative += entry["seconds"]
        pareto.append(
            {
                "column_id": entry["column_id"],
                "column_name": entry["column_name"],
                "kind": entry["kind"],
                "days": _r(entry["seconds"] / DAY, 2),
                "share_pct": _r(_pct(entry["seconds"], wait_denominator), 1),
                "cumulative_pct": _r(_pct(cumulative, wait_denominator), 1),
            }
        )

    column_points: list[dict[str, Any]] = []
    for entry in ordered:
        column_points.append(
            {
                "x": entry["column_name"],
                "y": _r(entry["seconds"] / DAY / max(1, entry["cards"]), 3),
                "column_id": entry["column_id"],
                "kind": entry["kind"],
                "segment": entry["segment"],
                "total_days": _r(entry["seconds"] / DAY, 2),
                "cards": entry["cards"],
                "visits": entry["visits"],
                "share_of_lead_time_pct": _r(_pct(entry["seconds"], grand_total), 1),
                "color_role": _COLUMN_KIND_ROLE.get(entry["kind"], "neutral"),
            }
        )

    efficiencies = [
        record.split.flow_efficiency
        for record in delivered
        if record.split.flow_efficiency is not None
    ]

    split_series = [
        _series(
            "touch",
            "Touch time",
            "success",
            [
                {
                    "x": "flow",
                    "y": _r(touch_total / DAY, 2),
                    "pct": _r(_pct(touch_total, grand_total), 1),
                }
            ],
            mark="bar",
        ),
        _series(
            "wait",
            "Wait time",
            "warning",
            [
                {
                    "x": "flow",
                    "y": _r(wait_total / DAY, 2),
                    "pct": _r(_pct(wait_total, grand_total), 1),
                }
            ],
            mark="bar",
        ),
    ]
    if ctx.include_envelope:
        split_series.append(
            _series(
                "envelope",
                "Sealed envelope",
                "muted",
                [
                    {
                        "x": "flow",
                        "y": _r(envelope_total / DAY, 2),
                        "pct": _r(_pct(envelope_total, grand_total), 1),
                    }
                ],
                mark="bar",
            )
        )
    split_series.append(
        _series(
            "by_column",
            "Average days per card by column",
            "neutral",
            column_points,
            mark="bar",
            stacked=False,
        )
    )

    return ctx.envelope(
        "flow-efficiency",
        split_series,
        percentiles=percentile_set(efficiencies),
        categories=[entry["column_name"] for entry in ordered],
        meta={
            "flow_efficiency": _r(_pct(touch_total, grand_total), 2),
            "median_card_efficiency": _r(percentile(efficiencies, 50), 2),
            "touch_days": _r(touch_total / DAY, 2),
            "wait_days": _r(wait_total / DAY, 2),
            "envelope_days": _r(envelope_total / DAY, 2),
            "total_days": _r(grand_total / DAY, 2),
            "include_envelope": ctx.include_envelope,
            "by_column": [
                {
                    **entry,
                    "days": _r(entry["seconds"] / DAY, 2),
                    "avg_days_per_card": _r(entry["seconds"] / DAY / max(1, entry["cards"]), 3),
                    "share_of_lead_time_pct": _r(_pct(entry["seconds"], grand_total), 1),
                    "seconds": _r(entry["seconds"], 0),
                }
                for entry in ordered
            ],
            "wait_pareto": pareto,
            "worst_queue": pareto[0] if pareto else None,
            "per_card": per_card,
            "interpretation": _efficiency_label(_pct(touch_total, grand_total)),
        },
        x_label="Column",
        y_label="Days",
        unit="days",
        sample_size=len(delivered),
    )


def _efficiency_label(value: float | None) -> str:
    if value is None:
        return "No delivered work in this window, so there is no efficiency to report."
    if value >= 40:
        return "Excellent — very little of the lead time is spent waiting."
    if value >= 25:
        return "Healthy for knowledge work; most teams sit below this."
    if value >= 15:
        return "Typical. The lever is queue removal, not working faster."
    return "Queue-dominated: over 85% of lead time is waiting. Attack the biggest queue first."


# ---------------------------------------------------------------------------
# 7. lead-time distribution
# ---------------------------------------------------------------------------


def distribution(
    ctx: MetricsContext, bin_days: float | None = None, metric: str = "customer"
) -> dict[str, Any]:
    """Lead-time histogram with a configurable bin width and percentile markers."""
    metric = metric if metric in _SCATTER_METRICS else "customer"
    attribute, label = _SCATTER_METRICS[metric]
    values = [
        value
        for value in (getattr(record.lead, attribute) for record in ctx.delivered_between())
        if value is not None
    ]

    width = float(bin_days) if bin_days and float(bin_days) > 0 else _auto_bin_width(values)
    percentiles = percentile_set(values)

    points: list[dict[str, Any]] = []
    if values:
        highest = max(values)
        bins = max(1, math.ceil((highest + 1e-9) / width))
        bins = min(bins, 120)  # a histogram nobody can read is not a histogram
        width = max(width, (highest + 1e-9) / bins)
        counts = [0] * bins
        for value in values:
            index = min(bins - 1, int(value // width))
            counts[index] += 1
        for index, count in enumerate(counts):
            low = index * width
            high = low + width
            points.append(
                {
                    "x": _r(low, 3),
                    "x_end": _r(high, 3),
                    "y": count,
                    "label": f"{low:g}–{high:g}d",
                    "pct": _r(_pct(count, len(values)), 1),
                    "cumulative_pct": _r(_pct(sum(counts[: index + 1]), len(values)), 1),
                }
            )

    sle_days = ctx.board.get("sle_days")
    within = sum(1 for value in values if sle_days and value <= float(sle_days))
    series = [_series("count", f"{label} distribution", "accent", points, mark="bar")]
    role_for = {"p50": "info", "p70": "success", "p85": "warning", "p95": "danger"}
    for name, value in percentiles.items():
        series.append(
            _series(
                name,
                f"{name[1:]}th percentile — {value:.1f}d",
                role_for.get(name, "muted"),
                [{"x": value, "y": None}],
                mark="rule",
                orientation="vertical",
            )
        )

    tail_ratio = None
    if percentiles.get("p50"):
        tail_ratio = percentiles["p95"] / percentiles["p50"] if percentiles.get("p95") else None

    return ctx.envelope(
        "distribution",
        series,
        percentiles=percentiles,
        meta={
            "metric": metric,
            "metric_label": label,
            "bin_days": _r(width, 3),
            "bins": len(points),
            "mean_days": _r(_mean(values), 2),
            "median_days": _r(percentiles.get("p50"), 2),
            "min_days": _r(min(values), 2) if values else None,
            "max_days": _r(max(values), 2) if values else None,
            "stdev_days": _r(_stdev(values), 3),
            "cov": _r(_cov(values), 3),
            "tail_ratio_p95_p50": _r(tail_ratio, 2),
            "sle_days": sle_days,
            "sle_percentile": ctx.board.get("sle_percentile"),
            "sle_attainment_pct": _r(_pct(within, len(values)), 1),
            "shape": _shape_label(tail_ratio),
        },
        x_label=f"{label} (days)",
        y_label="Items",
        unit="count",
        sample_size=len(values),
    )


def _auto_bin_width(values: Sequence[float]) -> float:
    """Freedman–Diaconis bin width, snapped to a width a human would choose."""
    if len(values) < 4:
        return 1.0
    q1 = percentile(values, 25) or 0.0
    q3 = percentile(values, 75) or 0.0
    iqr = max(0.0, q3 - q1)
    if iqr <= 0:
        return 1.0
    raw = 2 * iqr / (len(values) ** (1 / 3))
    for candidate in (0.5, 1, 2, 3, 5, 7, 10, 14, 21, 28):
        if raw <= candidate:
            return float(candidate)
    return 30.0


def _shape_label(tail_ratio: float | None) -> str:
    if tail_ratio is None:
        return "Not enough delivered work to describe the shape of the distribution."
    if tail_ratio < 2:
        return "Tight distribution — the process behaves consistently across item types."
    if tail_ratio < 3.5:
        return "Typical right-skewed Kanban distribution. Forecast with percentiles, not averages."
    return "Long tail: the slowest 5% take several times the median. Look for blocked or oversized items."


# ---------------------------------------------------------------------------
# 8. blockers
# ---------------------------------------------------------------------------


def blockers(ctx: MetricsContext) -> dict[str, Any]:
    """Blocked time, count, mean duration, Pareto by category, % of lead time."""
    window_end = min(ctx.window_end, ctx.as_of)
    entries: list[dict[str, Any]] = []
    for row in ctx.blocker_rows:
        started = parse_iso(row.get("started_at"))
        ended = parse_iso(row.get("ended_at"))
        effective_end = ended or ctx.as_of
        overlap = overlap_seconds(started, effective_end, ctx.window_start, window_end)
        if overlap <= 0:
            continue
        entries.append(
            {
                "id": row["id"],
                "card_id": row["card_id"],
                "card_key": row.get("card_key"),
                "card_title": row.get("card_title"),
                "service_class": row.get("service_class"),
                "category": row.get("category") or "other",
                "reason": row.get("reason"),
                "started_at": row.get("started_at"),
                "ended_at": row.get("ended_at"),
                "is_open": ended is None,
                "days": overlap / DAY,
                "total_days": (
                    max(0.0, (effective_end - started).total_seconds()) / DAY if started else 0.0
                ),
            }
        )

    total_days = sum(entry["days"] for entry in entries)
    durations = [entry["days"] for entry in entries]
    affected = {entry["card_id"] for entry in entries}
    open_now = [entry for entry in entries if entry["is_open"]]

    by_category: dict[str, dict[str, Any]] = {}
    for entry in entries:
        bucket = by_category.setdefault(
            entry["category"], {"category": entry["category"], "count": 0, "days": 0.0}
        )
        bucket["count"] += 1
        bucket["days"] += entry["days"]
    ranked = sorted(by_category.values(), key=lambda bucket: bucket["days"], reverse=True)
    cumulative = 0.0
    pareto: list[dict[str, Any]] = []
    for bucket in ranked:
        cumulative += bucket["days"]
        pareto.append(
            {
                "category": bucket["category"],
                "label": str(bucket["category"]).replace("_", " ").title(),
                "count": bucket["count"],
                "days": _r(bucket["days"], 2),
                "mean_days": _r(bucket["days"] / bucket["count"], 2),
                "share_pct": _r(_pct(bucket["days"], total_days), 1),
                "cumulative_pct": _r(_pct(cumulative, total_days), 1),
            }
        )

    # Share of delivered lead time spent blocked.
    delivered = ctx.delivered_between()
    delivered_ids = {record.id for record in delivered}
    lead_seconds = sum(r.lead.customer for r in delivered if r.lead.customer is not None)
    blocked_in_lead = 0.0
    for row in ctx.blocker_rows:
        card_id = str(row["card_id"])
        if card_id not in delivered_ids:
            continue
        record = ctx.records_by_id.get(card_id)
        if record is None:
            continue
        blocked_in_lead += overlap_seconds(
            parse_iso(row.get("started_at")),
            parse_iso(row.get("ended_at")) or ctx.as_of,
            record.start_at,
            record.delivered_at,
        )

    per_card: dict[str, dict[str, Any]] = {}
    for entry in entries:
        bucket = per_card.setdefault(
            entry["card_id"],
            {
                "card_id": entry["card_id"],
                "key": entry["card_key"],
                "title": entry["card_title"],
                "days": 0.0,
                "count": 0,
                "open": False,
            },
        )
        bucket["days"] += entry["days"]
        bucket["count"] += 1
        bucket["open"] = bucket["open"] or entry["is_open"]
    worst = sorted(per_card.values(), key=lambda bucket: bucket["days"], reverse=True)[:10]
    for bucket in worst:
        bucket["days"] = _r(bucket["days"], 2)

    return ctx.envelope(
        "blockers",
        [
            _series(
                "blocked_days",
                "Blocked days by category",
                "danger",
                [
                    {
                        "x": bucket["label"],
                        "y": bucket["days"],
                        "count": bucket["count"],
                        "category": bucket["category"],
                    }
                    for bucket in pareto
                ],
                mark="bar",
            ),
            _series(
                "cumulative",
                "Cumulative share",
                "muted",
                [{"x": bucket["label"], "y": bucket["cumulative_pct"]} for bucket in pareto],
                mark="line",
                axis="right",
                unit="percent",
            ),
        ],
        categories=[bucket["label"] for bucket in pareto],
        meta={
            "total_blocked_days": _r(total_days, 2),
            "blocker_count": len(entries),
            "cards_affected": len(affected),
            "mean_duration_days": _r(_mean(durations), 2),
            "median_duration_days": _r(percentile(durations, 50), 2),
            "p85_duration_days": _r(percentile(durations, 85), 2),
            "currently_blocked": len(open_now),
            "currently_blocked_days": _r(sum(e["days"] for e in open_now), 2),
            "pct_of_lead_time_blocked": _r(_pct(blocked_in_lead, lead_seconds), 2),
            "pareto": pareto,
            "top_blocked_cards": worst,
            "open_blockers": sorted(
                (
                    {
                        **entry,
                        "days": _r(entry["days"], 2),
                        "total_days": _r(entry["total_days"], 2),
                    }
                    for entry in open_now
                ),
                key=lambda entry: entry["days"] or 0,
                reverse=True,
            ),
        },
        x_label="Category",
        y_label="Blocked days",
        unit="days",
        sample_size=len(entries),
    )


# ---------------------------------------------------------------------------
# 9. demand analysis
# ---------------------------------------------------------------------------


def demand(ctx: MetricsContext) -> dict[str, Any]:
    """Class-of-service mix, failure demand, per-project mix, due-date performance."""
    delivered = ctx.delivered_between()
    arrivals = ctx.arrived_between()

    def mix(records: Sequence[CardRecord], field_name: str) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for record in records:
            key = str(record.card.get(field_name) or "unknown")
            counts[key] = counts.get(key, 0) + 1
        total = sum(counts.values())
        return [
            {
                "key": key,
                "label": _SERVICE_CLASS_LABEL.get(key)
                or _DEMAND_TYPE_LABEL.get(key)
                or key.replace("_", " ").title(),
                "count": count,
                "pct": _r(_pct(count, total), 1),
            }
            for key, count in sorted(counts.items(), key=lambda item: -item[1])
        ]

    delivered_classes = mix(delivered, "service_class")
    arrival_classes = mix(arrivals, "service_class")
    demand_types = mix(delivered, "demand_type")
    value_areas = mix(delivered, "value_area")

    # Lead time by class of service — how much flow debt expedite is creating.
    by_class: list[dict[str, Any]] = []
    for name in ("expedite", "standard", "fixed_date", "intangible"):
        values = [
            record.lead.customer_days
            for record in delivered
            if record.card.get("service_class") == name and record.lead.customer_days is not None
        ]
        if not values:
            continue
        by_class.append(
            {
                "service_class": name,
                "label": _SERVICE_CLASS_LABEL[name],
                "count": len(values),
                "mean_days": _r(_mean(values), 2),
                "p50_days": _r(percentile(values, 50), 2),
                "p85_days": _r(percentile(values, 85), 2),
                "color_role": _SERVICE_CLASS_ROLE[name],
            }
        )

    # Per-project mix.
    project_rows: dict[str, dict[str, Any]] = {}
    for record in ctx.records:
        project_id = str(record.card.get("project_id") or "")
        project = ctx.projects.get(project_id)
        bucket = project_rows.setdefault(
            project_id,
            {
                "project_id": project_id or None,
                "name": project.get("name") if project else "Unassigned",
                "key": project.get("key") if project else None,
                "color": project.get("color") if project else None,
                "status": project.get("status") if project else None,
                "created": 0,
                "delivered": 0,
                "wip": 0,
                "lead_times": [],
            },
        )
        if record.start_at is not None and ctx.window_start <= record.start_at <= ctx.window_end:
            bucket["created"] += 1
        if (
            record.delivered_at is not None
            and ctx.window_start <= record.delivered_at <= ctx.window_end
        ):
            bucket["delivered"] += 1
            if record.lead.customer_days is not None:
                bucket["lead_times"].append(record.lead.customer_days)
        if record.in_flight_at(ctx.as_of):
            bucket["wip"] += 1

    delivered_total = len(delivered)
    per_project: list[dict[str, Any]] = []
    for bucket in sorted(project_rows.values(), key=lambda row: -row["delivered"]):
        values = bucket.pop("lead_times")
        if not (bucket["created"] or bucket["delivered"] or bucket["wip"]):
            continue
        per_project.append(
            {
                **bucket,
                "share_pct": _r(_pct(bucket["delivered"], delivered_total), 1),
                "mean_lead_time_days": _r(_mean(values), 2),
                "p85_lead_time_days": _r(percentile(values, 85), 2),
            }
        )

    # Due-date performance for fixed-date work.
    fixed = [
        record
        for record in delivered
        if record.card.get("service_class") == "fixed_date" and record.due_date
    ]
    on_time = [r for r in fixed if r.delivered_at is not None and r.delivered_at <= r.due_date]
    late = [r for r in fixed if r not in on_time]
    lateness = [
        (r.delivered_at - r.due_date).total_seconds() / DAY
        for r in late
        if r.delivered_at is not None and r.due_date is not None
    ]

    failure = sum(1 for record in delivered if record.card.get("demand_type") == "failure")

    series = [
        _series(
            "service_class",
            "Class of service (delivered)",
            "accent",
            [
                {
                    "x": row["label"],
                    "y": row["count"],
                    "pct": row["pct"],
                    "key": row["key"],
                    "color_role": _SERVICE_CLASS_ROLE.get(row["key"], "neutral"),
                }
                for row in delivered_classes
            ],
            mark="arc",
        ),
        _series(
            "demand_type",
            "Demand type (delivered)",
            "info",
            [
                {
                    "x": row["label"],
                    "y": row["count"],
                    "pct": row["pct"],
                    "key": row["key"],
                    "color_role": _DEMAND_TYPE_ROLE.get(row["key"], "neutral"),
                }
                for row in demand_types
            ],
            mark="arc",
        ),
        _series(
            "per_project",
            "Delivered by project",
            "neutral",
            [
                {
                    "x": row["key"] or row["name"],
                    "y": row["delivered"],
                    "project_id": row["project_id"],
                    "label": row["name"],
                    "wip": row["wip"],
                    "created": row["created"],
                }
                for row in per_project
            ],
            mark="bar",
        ),
    ]

    return ctx.envelope(
        "demand",
        series,
        meta={
            "delivered_total": delivered_total,
            "arrivals_total": len(arrivals),
            "service_class_mix": delivered_classes,
            "arrival_service_class_mix": arrival_classes,
            "demand_type_mix": demand_types,
            "value_area_mix": value_areas,
            "failure_demand_pct": _r(_pct(failure, delivered_total), 1),
            "value_demand_pct": _r(
                _pct(
                    sum(1 for r in delivered if r.card.get("demand_type") == "value"),
                    delivered_total,
                ),
                1,
            ),
            "maintenance_demand_pct": _r(
                _pct(
                    sum(1 for r in delivered if r.card.get("demand_type") == "maintenance"),
                    delivered_total,
                ),
                1,
            ),
            "lead_time_by_class": by_class,
            "flow_debt": _flow_debt(by_class),
            "per_project": per_project,
            "due_date_performance": {
                "total": len(fixed),
                "on_time": len(on_time),
                "late": len(late),
                "pct": _r(_pct(len(on_time), len(fixed)), 1),
                "mean_lateness_days": _r(_mean(lateness), 2),
                "worst_lateness_days": _r(max(lateness), 2) if lateness else None,
            },
        },
        x_label="Class",
        y_label="Items",
        unit="count",
        sample_size=delivered_total,
    )


def _flow_debt(by_class: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Expedite vs standard lead time — the price the standard class is paying.

    Anderson calls the lead time the ordinary class loses to interruptions *flow
    debt*: expedite work is only free if nothing else slows down, and this ratio
    is the receipt.
    """
    expedite = next((row for row in by_class if row["service_class"] == "expedite"), None)
    standard = next((row for row in by_class if row["service_class"] == "standard"), None)
    if not expedite or not standard or not standard.get("p85_days"):
        return {"available": False, "ratio": None, "note": "Needs delivered work in both classes."}
    ratio = (expedite.get("p85_days") or 0) / standard["p85_days"]
    return {
        "available": True,
        "expedite_p85_days": expedite.get("p85_days"),
        "standard_p85_days": standard.get("p85_days"),
        "ratio": _r(ratio, 3),
        "expedite_share_pct": None,
        "note": (
            "Expedite work clears far faster than standard work; the standard class is "
            "absorbing the interruption cost."
            if ratio < 0.6
            else "Expedite and standard classes finish at comparable speed — the expedite "
            "lane is not distorting the flow."
        ),
    }


# ---------------------------------------------------------------------------
# 10. Little's Law
# ---------------------------------------------------------------------------


def littles_law(ctx: MetricsContext) -> dict[str, Any]:
    """WIP / throughput / lead time coherence — is the system stable enough to trust?

    Little's Law holds for a stable system: ``WIP = throughput × lead time``. When
    the observed average WIP and the product of the other two disagree, the
    system is not in a steady state (arrivals ≠ departures, or items are entering
    and leaving without being recorded) and every forecast built on it is soft.
    """
    delivered = ctx.delivered_between()
    system_values = [r.lead.system_days for r in delivered if r.lead.system_days is not None]
    customer_values = [r.lead.customer_days for r in delivered if r.lead.customer_days is not None]

    samples = ctx.daily_wip()
    wip_values = [count for _, count in samples]
    avg_wip = _mean(wip_values)
    window_days = ctx.window_days
    throughput_per_day = len(delivered) / window_days if window_days else 0.0
    mean_system = _mean(system_values)

    predicted_wip = throughput_per_day * mean_system if mean_system is not None else None
    implied_lead = avg_wip / throughput_per_day if avg_wip and throughput_per_day else None
    coherence = avg_wip / predicted_wip if avg_wip is not None and predicted_wip else None

    arrivals = len(ctx.arrived_between())
    conservation = arrivals - len(delivered)

    series = [
        _series(
            "wip",
            "Work in progress",
            "accent",
            [{"x": _day_key(moment), "y": count, "t": _iso(moment)} for moment, count in samples],
            mark="line",
        )
    ]

    return ctx.envelope(
        "littles-law",
        series,
        percentiles=percentile_set(system_values),
        meta={
            "avg_wip": _r(avg_wip, 2),
            "wip_now": len(ctx.in_flight_at()),
            "throughput_per_day": _r(throughput_per_day, 4),
            "throughput_per_week": _r(throughput_per_day * 7, 2),
            "avg_system_lead_time_days": _r(mean_system, 2),
            "avg_customer_lead_time_days": _r(_mean(customer_values), 2),
            "predicted_wip": _r(predicted_wip, 2),
            "implied_lead_time_days": _r(implied_lead, 2),
            "coherence_ratio": _r(coherence, 3),
            "arrivals": arrivals,
            "departures": len(delivered),
            "conservation_gap": conservation,
            "stable": bool(coherence is not None and 0.75 <= coherence <= 1.35),
            "verdict": _littles_law_verdict(coherence, conservation, len(delivered)),
        },
        x_label="Date",
        y_label="WIP",
        unit="count",
        sample_size=len(delivered),
    )


def _littles_law_verdict(coherence: float | None, conservation: int, delivered: int) -> str:
    if delivered == 0:
        return "Nothing was delivered in this window, so Little's Law cannot be evaluated."
    if coherence is None:
        return "Not enough committed work in flight to compare observed and predicted WIP."
    if 0.75 <= coherence <= 1.35:
        base = "The system is behaving as a stable queue: observed WIP matches throughput × cycle time."
    elif coherence > 1.35:
        base = (
            "Observed WIP is well above throughput × cycle time — work is entering faster "
            "than it leaves, so lead times will keep growing."
        )
    else:
        base = (
            "Observed WIP is below throughput × cycle time — the window probably closed on a "
            "batch of deliveries, or items are leaving the board without a delivery movement."
        )
    if conservation > max(3, delivered * 0.25):
        return base + " Arrivals are outpacing departures; the queue is still growing."
    if conservation < -max(3, delivered * 0.25):
        return base + " Departures outpaced arrivals; the team is draining an existing queue."
    return base + " Arrivals and departures are balanced."
