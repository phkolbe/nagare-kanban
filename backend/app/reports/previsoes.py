"""Forecast against actual (REP-09, wave 2 of the Reports, 25/11/2026): the pure part.

A registered forecast (:class:`~backend.app.models.ForecastRecord`) is checked against the
deliveries that came after it, **at each confidence level it gave** (50, 70, 85 and 95):

* ``when`` ("how long for N items?"): a level is *met* when the N-th delivery after the
  record came on or before that level's date; *missed* once that date has passed (in the
  product's time zone) without it; *open* before that.
* ``how_many`` ("how many items by a date?"): a level is *met* as soon as the deliveries
  after the record reach that level's quantity, up to the end of the target day; *missed*
  when the target day ends short of it; *open* before that.

The report reads the 85% level as the promise (the headline of the forecast panel) and the
other levels for the calibration table: well-calibrated forecasts are met about as often as
their confidence says.

The deliveries are the board's (or, when the record names a project, the project's), the
same cards the forecast sampled: delivered and not archived.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

__all__ = ["PROMISE_LEVEL", "Outcome", "end_of_day", "evaluate", "levels_of"]

#: The confidence level read as the promise (the panel's headline sentence is the 85%).
PROMISE_LEVEL = 85


@dataclass(frozen=True)
class Outcome:
    """How a forecast stands at a given instant."""

    #: confidence level -> ``met``, ``missed`` or ``open``
    levels: dict[int, str]
    #: confidence level -> when that level was decided (``None`` while open)
    decided_at: dict[int, datetime | None]
    #: deliveries counted after the record (up to the instant, or the target day)
    delivered: int
    #: ``when``: the day the N-th item was delivered (``None`` before that)
    realized_on: date | None

    @property
    def status(self) -> str:
        """The promise's status: the 85% level (or the closest one the record has)."""
        return self.levels[_promise(self.levels)]

    @property
    def decided(self) -> datetime | None:
        return self.decided_at[_promise(self.levels)]

    @property
    def band(self) -> int | None:
        """The most demanding confidence level met (``None``: none so far)."""
        return next((c for c in sorted(self.levels) if self.levels[c] == "met"), None)


def _promise(levels: dict[int, Any]) -> int:
    if PROMISE_LEVEL in levels:
        return PROMISE_LEVEL
    return min(levels, key=lambda c: abs(c - PROMISE_LEVEL))


def levels_of(percentiles: dict[str, Any]) -> list[int]:
    """``{"p50": .., "p85": ..}`` -> ``[50, 85]``."""
    out = []
    for key in percentiles:
        if str(key).startswith("p") and str(key)[1:].isdigit():
            out.append(int(str(key)[1:]))
    return sorted(out)


def end_of_day(day: date, zone: ZoneInfo) -> datetime:
    """The last instant of ``day`` in ``zone``, in UTC."""
    after = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)
    return after.astimezone(UTC) - timedelta(microseconds=1)


def _day(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def evaluate(
    record: dict[str, Any],
    deliveries: Sequence[datetime],
    as_of: datetime,
    zone: ZoneInfo,
) -> Outcome:
    """The outcome of one ``record`` at ``as_of``.

    ``record`` carries ``question``, ``created_at`` (aware datetime), ``params``,
    ``percentiles``, ``dates`` and ``target_date``; ``deliveries`` are the instants of the
    relevant deliveries (any order).
    """
    created: datetime = record["created_at"]
    percentiles: dict[str, Any] = record.get("percentiles") or {}
    levels = levels_of(percentiles)
    if not levels:
        raise ValueError("a forecast record without percentiles")
    ordered = sorted(d for d in deliveries if d > created)
    state: dict[int, str] = {}
    decided: dict[int, datetime | None] = {}

    if record["question"] == "when":
        wanted = int((record.get("params") or {}).get("backlog_size") or 0)
        dates: dict[str, Any] = record.get("dates") or {}
        upto = [d for d in ordered if d <= as_of]
        realized_at = upto[wanted - 1] if wanted > 0 and len(upto) >= wanted else None
        realized_on = realized_at.astimezone(zone).date() if realized_at else None
        for level in levels:
            deadline = end_of_day(_day(dates[f"p{level}"]), zone)
            if realized_at is not None and realized_at <= deadline:
                state[level], decided[level] = "met", realized_at
            elif as_of > deadline:
                state[level], decided[level] = "missed", deadline
            else:
                state[level], decided[level] = "open", None
        delivered = min(len(upto), wanted) if wanted else len(upto)
        return Outcome(state, decided, delivered, realized_on)

    # how_many
    target_end = end_of_day(_day(record["target_date"]), zone)
    window_end = min(as_of, target_end)
    counted = [d for d in ordered if d <= window_end]
    for level in levels:
        quantity = int(percentiles[f"p{level}"] or 0)
        if len(counted) >= quantity:
            state[level] = "met"
            decided[level] = counted[quantity - 1] if quantity > 0 else created
        elif as_of >= target_end:
            state[level], decided[level] = "missed", target_end
        else:
            state[level], decided[level] = "open", None
    return Outcome(state, decided, len(counted), None)
