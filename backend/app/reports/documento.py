"""The report Document (24/11/2026): one JSON, four outputs.

A report template builds one :class:`dict` — the *Document* — and every output reads it:
the screen draws it, the spreadsheet turns its tables into sheets, an issued report (wave 2)
keeps it whole and the client link (wave 2) shows the issued copy. So the numbers on the
screen, in the spreadsheet and in the issued copy are the same numbers.

Shape (``FORMAT_VERSION`` 1)::

    {"template", "code", "title", "subtitle", "format_version", "product_id",
     "params", "period": {kind, from, to, label}, "previous": {from, to, label},
     "generated_at", "scope": {...}, "sections": [{id, title, description, blocks}]}

Blocks: ``kpis`` (figures against the previous period), ``table`` (columns + rows),
``bars`` (labelled values) and ``note`` (a sentence). Units are machine keys the screen and
the spreadsheet format: ``days``, ``percent``, ``count``, ``cards/week``, ``hours``,
``ratio``, ``date``, ``datetime``, ``text``, ``card``.

Periods follow decision R22: Monday-to-Sunday weeks and calendar months in the product's
time zone, or a free interval. The previous period is the one right before: the previous
week, the previous calendar month, or an interval of the same length.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..texts import _, date_label

__all__ = [
    "FORMAT_VERSION",
    "PERIOD_KINDS",
    "Period",
    "bars",
    "kpi",
    "kpi_from_summary",
    "kpis",
    "note",
    "resolve_period",
    "section",
    "table",
    "trend",
]

FORMAT_VERSION = 1
PERIOD_KINDS: tuple[str, ...] = ("week", "month", "custom")
#: The longest free interval a report reads (a year and a bit).
MAX_CUSTOM_DAYS = 400


@dataclass(frozen=True)
class Period:
    """A reporting period and the one before it, as local days and as UTC instants."""

    kind: str
    start: date
    end: date
    previous_start: date
    previous_end: date
    zone: ZoneInfo

    def _bounds(self, first: date, last: date) -> tuple[datetime, datetime]:
        begin = datetime.combine(first, time(0, 0), tzinfo=self.zone).astimezone(UTC)
        after = datetime.combine(last + timedelta(days=1), time(0, 0), tzinfo=self.zone)
        return begin, after.astimezone(UTC) - timedelta(microseconds=1)

    @property
    def window(self) -> tuple[datetime, datetime]:
        """The period as ``(first instant, last instant)`` in UTC."""
        return self._bounds(self.start, self.end)

    @property
    def previous_window(self) -> tuple[datetime, datetime]:
        return self._bounds(self.previous_start, self.previous_end)

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "from": self.start.isoformat(),
            "to": self.end.isoformat(),
            "label": period_label(self.kind, self.start, self.end),
            "timezone": str(self.zone),
        }

    def describe_previous(self) -> dict[str, Any]:
        return {
            "from": self.previous_start.isoformat(),
            "to": self.previous_end.isoformat(),
            "label": period_label(self.kind, self.previous_start, self.previous_end),
        }


def _month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    last = day.replace(day=calendar.monthrange(day.year, day.month)[1])
    return first, last


def resolve_period(
    kind: str,
    *,
    today: date,
    zone: ZoneInfo,
    ref: date | None = None,
    frm: date | None = None,
    to: date | None = None,
) -> Period:
    """The period a report reads.

    * ``week``: the Monday-to-Sunday week holding ``ref``; by default the last complete week.
    * ``month``: the calendar month holding ``ref``; by default the last complete month.
    * ``custom``: ``frm`` to ``to`` inclusive (``to`` defaults to today, ``frm`` to 29 days
      before it); the previous period has the same length.
    """
    if kind == "week":
        day = ref or (today - timedelta(days=7))
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=6)
        return Period(kind, start, end, start - timedelta(days=7), start - timedelta(days=1), zone)
    if kind == "month":
        day = ref or (today.replace(day=1) - timedelta(days=1))
        start, end = _month_bounds(day)
        previous_start, previous_end = _month_bounds(start - timedelta(days=1))
        return Period(kind, start, end, previous_start, previous_end, zone)
    if kind != "custom":
        raise ValueError(f"unknown period kind: {kind}")
    end = to or today
    start = frm or (end - timedelta(days=29))
    if start > end:
        start, end = end, start
    length = (end - start).days + 1
    if length > MAX_CUSTOM_DAYS:
        raise ValueError(f"interval longer than {MAX_CUSTOM_DAYS} days")
    previous_end = start - timedelta(days=1)
    return Period(kind, start, end, previous_end - timedelta(days=length - 1), previous_end, zone)


def period_label(kind: str, start: date, end: date) -> str:
    """``Week of 17 to 23 Nov 2026``, ``November 2026`` or ``1 Oct to 15 Nov 2026``."""
    if kind == "month":
        return _("Month of {month}", month=date_label(start).split(" ", 1)[-1].removeprefix("de "))
    first = date_label(start, reference=end)
    last = date_label(end)
    if kind == "week":
        return _("Week of {first} to {last}", first=first, last=last)
    return _("{first} to {last}", first=first, last=last)


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------


def trend(value: float | None, previous: float | None) -> float | None:
    """Relative change in percent against the previous period (``None`` when undefined)."""
    if value is None or previous in (None, 0):
        return None
    return round((float(value) - float(previous)) / abs(float(previous)) * 100.0, 1)


def kpi(
    key: str,
    label: str,
    value: float | None,
    unit: str,
    *,
    previous: float | None = None,
    higher_is_better: bool | None = None,
    low_confidence: bool = False,
    hint: str | None = None,
    compare: bool = True,
) -> dict[str, Any]:
    """One figure, with the previous period's value and the change when ``compare``."""
    return {
        "key": key,
        "label": label,
        "value": None if value is None else round(float(value), 2),
        "unit": unit,
        "previous": None if (previous is None or not compare) else round(float(previous), 2),
        "trend_pct": trend(value, previous) if compare else None,
        "higher_is_better": higher_is_better,
        "low_confidence": bool(low_confidence),
        "hint": hint,
    }


def kpi_from_summary(
    current: dict[str, Any], previous: dict[str, Any] | None, key: str
) -> dict[str, Any]:
    """A figure of the metrics summary, against the same figure of the previous period.

    The label, unit and description come from the summary itself, so the report says what the
    Metrics screen says.
    """
    metric = current["metrics"][key]
    before = (previous or {}).get("metrics", {}).get(key, {}).get("value") if previous else None
    return kpi(
        key,
        metric["label"],
        metric.get("value"),
        metric.get("unit") or "count",
        previous=before,
        higher_is_better=metric.get("higher_is_better"),
        low_confidence=bool(metric.get("low_confidence")),
        hint=metric.get("description"),
    )


def kpis(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "kpis", "items": items}


def table(
    columns: list[tuple[str, str, str]],
    rows: list[dict[str, Any]],
    *,
    title: str | None = None,
    empty: str | None = None,
) -> dict[str, Any]:
    """``columns`` as ``(key, label, unit)``; a ``card`` cell is ``{"key", "title"}``."""
    return {
        "type": "table",
        "title": title,
        "columns": [{"key": k, "label": label, "unit": unit} for k, label, unit in columns],
        "rows": rows,
        "empty": empty or _("Nothing in this period."),
    }


def bars(
    items: list[dict[str, Any]], *, title: str, unit: str, empty: str | None = None
) -> dict[str, Any]:
    """Labelled values (``{"label", "value", "note"?}``), drawn as horizontal bars."""
    return {
        "type": "bars",
        "title": title,
        "unit": unit,
        "items": items,
        "empty": empty or _("Nothing in this period."),
    }


def note(text: str, tone: str = "info") -> dict[str, Any]:
    return {"type": "note", "tone": tone, "text": text}


def section(
    key: str, title: str, blocks: list[dict[str, Any]], description: str | None = None
) -> dict[str, Any]:
    return {"id": key, "title": title, "description": description, "blocks": blocks}
