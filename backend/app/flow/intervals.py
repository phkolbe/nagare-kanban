"""Turn ``card_movements`` rows into per-column intervals.

Every flow metric in this product is ultimately a question about *how long a card
sat where*. The movement table only records transitions, so the first job is to
reconstruct the dwell intervals::

    movements:  NULL -> Ideas @T0, Ideas -> Build @T1, Build -> Done @T2
    intervals:  Ideas [T0, T1)  Build [T1, T2)  Done [T2, terminal]

The reconstruction has to survive real data, not just the happy path:

* **in-flight cards** — the last interval has no exit; it is measured against a
  reference instant (``now``) and flagged ``is_open``.
* **backward moves** — a card may re-enter a column it already left. Each visit
  is its own interval; per-column totals sum the visits.
* **same-day churn** — two movements at the same instant produce a zero-length
  interval rather than a negative one.
* **clock skew / out-of-order rows** — durations are clamped at zero.
* **deleted columns** — a movement into a column that no longer exists still
  advances the clock for its neighbours; only the interval itself is dropped.
* **no movements at all** — the card's current column is used with
  ``created_at`` as the entry, so a board imported without history still
  produces something honest.

Column ``kind`` decides what a dwell *means* (SPEC §2.4):

===========  ==========================================================
``activity`` touch time — someone is actually working the item
``queue``    wait time — the item is parked between activities
``envelope`` sealed demand, counted only when the board opts in (Req. 8)
``done``     terminal; the card has left the system, the clock stops
===========  ==========================================================

Bulk helpers load a whole board's movements in **one** query and bucket them by
card, because the metrics endpoints touch every card on the board and an N+1
would make the dashboard unusable at a few thousand cards.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..db import Database, parse_iso

__all__ = [
    "ENVELOPE",
    "QUEUE",
    "ACTIVITY",
    "DONE",
    "DAY",
    "Interval",
    "TouchWait",
    "LeadTimes",
    "CardFlow",
    "card_intervals",
    "bulk_card_intervals",
    "touch_wait",
    "lead_times",
    "flow_start",
    "load_columns",
    "load_movements",
    "load_cards",
    "board_flows",
    "column_at",
    "seconds_in_window",
    "overlap_seconds",
]

ENVELOPE = "envelope"
QUEUE = "queue"
ACTIVITY = "activity"
DONE = "done"

#: seconds in a day — every public duration is reported in seconds *and* days,
#: and dividing by a named constant beats a bare 86400 sprinkled through the file.
DAY = 86_400.0

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Interval:
    """One continuous visit of one card to one column."""

    card_id: str
    column_id: str
    column_name: str
    kind: str
    segment: str
    position: int
    entered_at: str
    exited_at: str | None
    seconds: float
    is_open: bool
    is_terminal: bool
    visit: int

    @property
    def days(self) -> float:
        return self.seconds / DAY

    @property
    def is_touch(self) -> bool:
        return self.kind == ACTIVITY

    @property
    def is_wait(self) -> bool:
        return self.kind == QUEUE

    @property
    def is_envelope(self) -> bool:
        return self.kind == ENVELOPE

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly projection (used by the card drawer and the tests)."""
        return {
            "card_id": self.card_id,
            "column_id": self.column_id,
            "column_name": self.column_name,
            "kind": self.kind,
            "segment": self.segment,
            "position": self.position,
            "entered_at": self.entered_at,
            "exited_at": self.exited_at,
            "seconds": round(self.seconds, 3),
            "days": round(self.seconds / DAY, 4),
            "is_open": self.is_open,
            "is_terminal": self.is_terminal,
            "visit": self.visit,
        }


@dataclass(slots=True)
class TouchWait:
    """The touch/wait split of a single card (SPEC §7.1)."""

    touch_seconds: float = 0.0
    wait_seconds: float = 0.0
    envelope_seconds: float = 0.0
    done_seconds: float = 0.0
    include_envelope: bool = False
    by_column: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def total_seconds(self) -> float:
        """Touch + wait, plus envelope time when the board counts it."""
        total = self.touch_seconds + self.wait_seconds
        if self.include_envelope:
            total += self.envelope_seconds
        return total

    @property
    def flow_efficiency(self) -> float | None:
        """``touch / (touch + wait) × 100``; ``None`` when nothing elapsed."""
        total = self.total_seconds
        if total <= 0:
            return None
        return self.touch_seconds / total * 100.0

    def as_dict(self) -> dict[str, Any]:
        efficiency = self.flow_efficiency
        return {
            "touch_seconds": round(self.touch_seconds, 3),
            "wait_seconds": round(self.wait_seconds, 3),
            "envelope_seconds": round(self.envelope_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "touch_days": round(self.touch_seconds / DAY, 4),
            "wait_days": round(self.wait_seconds / DAY, 4),
            "envelope_days": round(self.envelope_seconds / DAY, 4),
            "total_days": round(self.total_seconds / DAY, 4),
            "flow_efficiency": None if efficiency is None else round(efficiency, 2),
            "include_envelope": self.include_envelope,
        }


@dataclass(slots=True)
class LeadTimes:
    """The three lead times of SPEC §7.1, in seconds (``None`` when unknowable)."""

    customer: float | None = None
    discovery: float | None = None
    system: float | None = None
    #: ``committed_at − created_at`` regardless of the envelope toggle, so the
    #: raw SPEC formula stays available next to the toggle-aware number.
    discovery_full: float | None = None
    start_at: str | None = None
    committed_at: str | None = None
    delivered_at: str | None = None

    @staticmethod
    def _days(value: float | None) -> float | None:
        return None if value is None else round(value / DAY, 4)

    @property
    def customer_days(self) -> float | None:
        return self._days(self.customer)

    @property
    def discovery_days(self) -> float | None:
        return self._days(self.discovery)

    @property
    def system_days(self) -> float | None:
        return self._days(self.system)

    def as_dict(self) -> dict[str, Any]:
        return {
            "customer": None if self.customer is None else round(self.customer, 3),
            "discovery": None if self.discovery is None else round(self.discovery, 3),
            "system": None if self.system is None else round(self.system, 3),
            "discovery_full": (
                None if self.discovery_full is None else round(self.discovery_full, 3)
            ),
            "customer_days": self.customer_days,
            "discovery_days": self.discovery_days,
            "system_days": self.system_days,
            "discovery_full_days": self._days(self.discovery_full),
            "start_at": self.start_at,
            "committed_at": self.committed_at,
            "delivered_at": self.delivered_at,
        }


@dataclass(slots=True)
class CardFlow:
    """A card bundled with its reconstructed intervals."""

    card: dict[str, Any]
    intervals: list[Interval] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.card.get("id"))

    def touch_wait(self, include_envelope: bool = False) -> TouchWait:
        return touch_wait(self.card, self.intervals, include_envelope)

    def lead_times(self, include_envelope: bool = False) -> LeadTimes:
        return lead_times(self.card, include_envelope)

    def column_at(self, moment: datetime) -> Interval | None:
        return column_at(self.intervals, moment)


# ---------------------------------------------------------------------------
# reconstruction
# ---------------------------------------------------------------------------


def _reference(now: str | datetime | None) -> datetime:
    """Normalise the 'as of' instant used to close in-flight intervals."""
    if now is None:
        return datetime.now(timezone.utc)
    parsed = parse_iso(now) if not isinstance(now, datetime) else now
    if parsed is None:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _moment(value: Any) -> datetime | None:
    return parse_iso(value)


def _sorted_movements(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Chronological order, tolerating unsorted input and unparseable stamps.

    The sort is stable, so rows sharing an instant keep the order the caller
    supplied — which is the insertion order when the query ends in ``rowid``.
    """
    return sorted(rows, key=lambda row: _moment(row.get("moved_at")) or _EPOCH)


def card_intervals(
    card: Mapping[str, Any],
    movements: Sequence[Mapping[str, Any]],
    columns: Mapping[str, Mapping[str, Any]],
    now: str | datetime | None = None,
) -> list[Interval]:
    """Reconstruct one card's per-column intervals.

    ``columns`` maps ``column_id`` to a column row (``name``, ``kind``,
    ``segment``, ``position``). ``now`` is the instant an unfinished interval is
    measured against; pass it explicitly for reproducible analysis.
    """
    card_id = str(card.get("id"))
    reference = _reference(now)
    rows = _sorted_movements(movements or ())

    if not rows:
        # No history: assume the card has been in its current column since it was
        # created. Better an honest single interval than a silent empty result.
        column_id = card.get("column_id")
        if not column_id or column_id not in columns:
            return []
        rows = [{"to_column_id": column_id, "moved_at": card.get("created_at")}]

    entries: list[tuple[str, datetime | None]] = []
    for row in rows:
        column_id = row.get("to_column_id")
        if not column_id:
            continue
        entries.append((str(column_id), _moment(row.get("moved_at"))))
    if not entries:
        return []

    delivered_at = _moment(card.get("delivered_at"))
    out: list[Interval] = []
    visits: dict[str, int] = {}

    for index, (column_id, entered) in enumerate(entries):
        column = columns.get(column_id)
        # The exit is the *next* movement's instant even when the next column is
        # unknown, so deleting a column never inflates its predecessor's dwell.
        exited = entries[index + 1][1] if index + 1 < len(entries) else None
        if column is None or entered is None:
            continue

        kind = str(column.get("kind") or QUEUE)
        is_last = index + 1 == len(entries)
        is_terminal = is_last and kind == DONE

        if is_terminal:
            # The delivery column is a resting place, not a stage: the clock stops.
            close_at: datetime | None = entered
        elif exited is not None:
            close_at = exited
        elif is_last and delivered_at is not None and delivered_at >= entered:
            # Delivered but the final movement was never written (imported data):
            # trust the card's own timestamp rather than aging it forever.
            close_at = delivered_at
        else:
            close_at = None

        if close_at is None:
            seconds = max(0.0, (reference - entered).total_seconds())
            is_open = True
        else:
            seconds = max(0.0, (close_at - entered).total_seconds())
            is_open = False

        visit = visits.get(column_id, 0) + 1
        visits[column_id] = visit
        out.append(
            Interval(
                card_id=card_id,
                column_id=column_id,
                column_name=str(column.get("name") or ""),
                kind=kind,
                segment=str(column.get("segment") or "downstream"),
                position=int(column.get("position") or 0),
                entered_at=_iso(entered),
                exited_at=None if close_at is None else _iso(close_at),
                seconds=seconds,
                is_open=is_open,
                is_terminal=is_terminal,
                visit=visit,
            )
        )
    return out


def _iso(moment: datetime) -> str:
    """Render an aware datetime the way the rest of the app writes timestamps."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bulk_card_intervals(
    cards: Iterable[Mapping[str, Any]],
    movements_by_card: Mapping[str, Sequence[Mapping[str, Any]]],
    columns: Mapping[str, Mapping[str, Any]],
    now: str | datetime | None = None,
) -> dict[str, list[Interval]]:
    """Reconstruct intervals for many cards from one pre-bucketed movement map."""
    reference = _reference(now)
    return {
        str(card["id"]): card_intervals(
            card, movements_by_card.get(str(card["id"]), ()), columns, reference
        )
        for card in cards
    }


# ---------------------------------------------------------------------------
# derived measures
# ---------------------------------------------------------------------------


def touch_wait(
    card: Mapping[str, Any],
    intervals: Sequence[Interval],
    include_envelope: bool = False,
) -> TouchWait:
    """Split a card's intervals into touch, wait and envelope time.

    ``include_envelope`` mirrors ``boards.count_envelope_in_metrics``: when the
    board counts sealed-envelope time, that time behaves as queue time (nobody is
    working the item) and therefore enters the flow-efficiency denominator.

    The ``card`` argument is part of the signature because per-card results are
    keyed by card id and callers routinely pass intervals from a bulk load; it is
    never trusted for durations, which come only from the movement history.
    """
    result = TouchWait(include_envelope=include_envelope)
    card_id = str(card.get("id")) if card else None
    for interval in intervals:
        if card_id and interval.card_id and interval.card_id != card_id:
            continue
        bucket = result.by_column.setdefault(
            interval.column_id,
            {
                "column_id": interval.column_id,
                "column_name": interval.column_name,
                "kind": interval.kind,
                "segment": interval.segment,
                "position": interval.position,
                "seconds": 0.0,
                "visits": 0,
            },
        )
        bucket["seconds"] += interval.seconds
        bucket["visits"] += 1

        if interval.kind == ACTIVITY:
            result.touch_seconds += interval.seconds
        elif interval.kind == QUEUE:
            result.wait_seconds += interval.seconds
        elif interval.kind == ENVELOPE:
            result.envelope_seconds += interval.seconds
        else:  # done — terminal, deliberately outside the split
            result.done_seconds += interval.seconds
    return result


def flow_start(card: Mapping[str, Any], include_envelope: bool = False) -> str | None:
    """When the customer's clock starts for this card.

    With the envelope counted, that is ``created_at`` — the moment demand was
    captured, sealed or not. Without it, the clock starts when the envelope was
    opened (``opened_at``), falling back to ``created_at`` for cards that never
    passed through an envelope column.
    """
    created = card.get("created_at")
    if include_envelope:
        return created or card.get("opened_at")
    return card.get("opened_at") or created


def lead_times(card: Mapping[str, Any], include_envelope: bool = False) -> LeadTimes:
    """Customer / discovery / system lead time in seconds (SPEC §7.1).

    * **customer** — ``delivered_at − start`` where *start* honours the envelope
      toggle (see :func:`flow_start`).
    * **discovery** — ``committed_at − start``: the time demand spends being
      shaped before the team commits. The unconditional
      ``committed_at − created_at`` form is kept as ``discovery_full`` so the raw
      SPEC formula is never lost when a board excludes envelope time.
    * **system** — ``delivered_at − committed_at``: cycle time, the part of the
      flow the delivery team actually controls.
    """
    start = flow_start(card, include_envelope)
    start_dt = _moment(start)
    created_dt = _moment(card.get("created_at"))
    committed_dt = _moment(card.get("committed_at"))
    delivered_dt = _moment(card.get("delivered_at"))

    def span(a: datetime | None, b: datetime | None) -> float | None:
        if a is None or b is None:
            return None
        return max(0.0, (b - a).total_seconds())

    return LeadTimes(
        customer=span(start_dt, delivered_dt),
        discovery=span(start_dt, committed_dt),
        system=span(committed_dt, delivered_dt),
        discovery_full=span(created_dt, committed_dt),
        start_at=start,
        committed_at=card.get("committed_at"),
        delivered_at=card.get("delivered_at"),
    )


def column_at(intervals: Sequence[Interval], moment: datetime) -> Interval | None:
    """Which interval covers ``moment`` (the card's column at that instant)."""
    found: Interval | None = None
    for interval in intervals:
        entered = _moment(interval.entered_at)
        if entered is None or entered > moment:
            continue
        exited = _moment(interval.exited_at) if interval.exited_at else None
        if exited is None or exited > moment or interval.is_terminal:
            found = interval
    return found


def seconds_in_window(
    interval: Interval, start: datetime | None, end: datetime | None
) -> float:
    """How much of an interval falls inside ``[start, end]``."""
    entered = _moment(interval.entered_at)
    if entered is None:
        return 0.0
    exited = _moment(interval.exited_at) if interval.exited_at else None
    if exited is None:
        exited = entered if interval.is_terminal else (end or entered)
    return overlap_seconds(entered, exited, start, end)


def overlap_seconds(
    a_start: datetime | None,
    a_end: datetime | None,
    b_start: datetime | None,
    b_end: datetime | None,
) -> float:
    """Seconds shared by two half-open intervals; 0 when they do not overlap."""
    if a_start is None or a_end is None:
        return 0.0
    lo = a_start if b_start is None else max(a_start, b_start)
    hi = a_end if b_end is None else min(a_end, b_end)
    return max(0.0, (hi - lo).total_seconds())


# ---------------------------------------------------------------------------
# bulk loading — one query per table, never per card
# ---------------------------------------------------------------------------


def load_columns(db: Database, board_id: str) -> dict[str, dict[str, Any]]:
    """Every column on a board, keyed by id, ordered fields intact."""
    rows = db.q(
        "SELECT id, board_id, group_id, name, position, segment, kind, wip_min, "
        "wip_max, is_commitment_point, is_delivery_point, color, description "
        "FROM columns WHERE board_id = ? ORDER BY position, name",
        board_id,
    )
    for row in rows:
        row["is_commitment_point"] = bool(row["is_commitment_point"])
        row["is_delivery_point"] = bool(row["is_delivery_point"])
        row["position"] = int(row["position"] or 0)
    return {row["id"]: row for row in rows}


def load_movements(
    db: Database, board_id: str, card_ids: Sequence[str] | None = None
) -> dict[str, list[dict[str, Any]]]:
    """All movements for a board in **one** query, bucketed by card id.

    ``card_ids`` narrows the scan when only part of the board is in play; it is
    chunked because sqlite caps the number of bound parameters.
    """
    sql = (
        "SELECT id, card_id, from_column_id, to_column_id, from_segment, to_segment, "
        "moved_at, moved_by, seconds_in_from_column, is_backward, source, note "
        "FROM card_movements WHERE board_id = ?"
    )
    rows: list[dict[str, Any]] = []
    if card_ids is None:
        rows = db.q(sql + " ORDER BY card_id, moved_at, rowid", board_id)
    else:
        unique = list(dict.fromkeys(str(cid) for cid in card_ids))
        for offset in range(0, len(unique), 400):
            chunk = unique[offset : offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                db.q(
                    f"{sql} AND card_id IN ({placeholders}) ORDER BY card_id, moved_at, rowid",
                    [board_id, *chunk],
                )
            )

    bucketed: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        bucketed.setdefault(str(row["card_id"]), []).append(row)
    return bucketed


def load_cards(
    db: Database,
    board_id: str,
    *,
    include_archived: bool = False,
    project_id: str | None = None,
    service_class: str | None = None,
) -> list[dict[str, Any]]:
    """Board cards with the SPEC §5 metric filters applied in SQL."""
    sql = ["SELECT * FROM cards WHERE board_id = ?"]
    args: list[Any] = [board_id]
    if not include_archived:
        sql.append("AND archived = 0")
    if project_id:
        sql.append("AND project_id = ?")
        args.append(project_id)
    if service_class:
        sql.append("AND service_class = ?")
        args.append(service_class)
    sql.append("ORDER BY created_at")
    rows = db.q(" ".join(sql), args)
    for row in rows:
        row["is_blocked"] = bool(row["is_blocked"])
        row["archived"] = bool(row["archived"])
    return rows


def board_flows(
    db: Database,
    board_id: str,
    *,
    cards: Sequence[Mapping[str, Any]] | None = None,
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    now: str | datetime | None = None,
    **card_filters: Any,
) -> tuple[list[CardFlow], dict[str, dict[str, Any]]]:
    """Load a whole board and return ``(card flows, columns by id)``.

    Three queries total — columns, cards, movements — regardless of how many
    cards the board holds.
    """
    resolved_columns = dict(columns) if columns is not None else load_columns(db, board_id)
    resolved_cards = (
        list(cards) if cards is not None else load_cards(db, board_id, **card_filters)
    )
    movements = load_movements(db, board_id, [str(c["id"]) for c in resolved_cards])
    reference = _reference(now)
    flows = [
        CardFlow(
            card=dict(card),
            intervals=card_intervals(
                card, movements.get(str(card["id"]), ()), resolved_columns, reference
            ),
        )
        for card in resolved_cards
    ]
    return flows, resolved_columns


def first_reach_times(
    flows: Iterable[CardFlow], ordered_column_ids: Sequence[str]
) -> list[list[datetime]]:
    """For each column index, the sorted instants cards first reached it *or beyond*.

    This is the primitive behind the cumulative flow diagram: with these lists a
    CFD point is a binary search rather than a rescan of the movement history.
    Forward progress is monotone by construction — a backward move never rewinds
    the furthest column already reached, which is what keeps the CFD's cumulative
    curves non-decreasing.
    """
    index_of = {column_id: index for index, column_id in enumerate(ordered_column_ids)}
    reach: list[list[datetime]] = [[] for _ in ordered_column_ids]
    for flow in flows:
        furthest = -1
        for interval in flow.intervals:
            index = index_of.get(interval.column_id)
            if index is None or index <= furthest:
                continue
            entered = _moment(interval.entered_at)
            if entered is None:
                continue
            for step in range(furthest + 1, index + 1):
                reach[step].append(entered)
            furthest = index
    for series in reach:
        series.sort()
    return reach


def reached_by(reach: Sequence[datetime], moment: datetime) -> int:
    """How many cards had reached a column (or beyond) by ``moment``."""
    return bisect_right(reach, moment)
