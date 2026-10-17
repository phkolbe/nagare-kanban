"""Probabilistic forecasting and the discrete-event flow simulator (SPEC §8).

This module is pure computation: it never touches FastAPI and only reads the
database through a duck-typed handle in :func:`replay`. Everything is
**deterministic given ``seed``** — no wall-clock, no ``random`` module globals,
no set/dict iteration order that depends on hashing of non-deterministic values.

Three families of function live here.

**Monte Carlo** (:func:`monte_carlo_when`, :func:`monte_carlo_how_many`)
    Resample observed weekly throughput with replacement. No distribution is
    assumed and no average is used: the *variability* of the samples is the whole
    point, which is why 10 000 trials of resampling beats "backlog ÷ velocity".

**Discrete-event flow simulator** (:func:`simulate_flow`)
    A pull system. Columns are stations; ``activity`` columns hold a finite
    number of servers, ``queue`` columns are pure buffers. A card advances only
    when the next column has WIP headroom, so queueing is *emergent* rather than
    modelled — which is what makes the WIP experiment honest.

    Two numbers are deliberately kept apart, and this is the single most
    important modelling decision in the file:

    ``capacity``  how many items a team can genuinely work at once. A property
                  of the people, fixed for a scenario.
    ``wip_max``   the *policy* limit written on the board. The experiment dial.

    If they were the same number, lowering a WIP limit would lower capacity and
    every experiment would trivially conclude "less WIP, less throughput". By
    separating them, tightening the limits pushes queueing *upstream of the
    commitment point* — system lead time collapses while throughput holds, until
    the limits fall below capacity and the bottleneck actually starves. That is
    Little's Law made visible.

**Experiments** (:func:`wip_experiment`, :func:`class_of_service_experiment`,
:func:`replay`)
    Each runs the **same pre-drawn demand stream** through several policies. The
    arrival times, service-time draws, sizes and classes are generated once and
    reused, so the only difference between two rows of a comparison table is the
    policy. Anything else would confound the result.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..texts import N_, _, _n, date_label, delta_pct, num

__all__ = [
    "CONFIDENCE_LEVELS",
    "DEFAULT_CLASS_MIX",
    "DEFAULT_EXPEDITE_SHARES",
    "DEFAULT_SIZE_MIX",
    # constants
    "DEFAULT_TRIALS",
    "DEFAULT_WIP_CONFIGS",
    "SIZE_FACTORS",
    # simulator
    "ColumnSpec",
    "DemandCard",
    "DemandStream",
    "SimulationResult",
    "build_demand_stream",
    "class_of_service_experiment",
    "column_specs_from_rows",
    "compare_runs",
    "histogram",
    "monte_carlo_how_many",
    # monte carlo
    "monte_carlo_when",
    # statistics helpers
    "percentile",
    "percentiles",
    "replay",
    "simulate_flow",
    "weekly_throughput_from_deliveries",
    # experiments
    "wip_experiment",
]

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

DEFAULT_TRIALS: int = 10_000
#: The four confidence levels SPEC §7.1 demands wherever a distribution exists.
CONFIDENCE_LEVELS: tuple[int, ...] = (50, 70, 85, 95)

DEFAULT_CLASS_MIX: dict[str, float] = {
    "expedite": 0.08,
    "standard": 0.70,
    "fixed_date": 0.12,
    "intangible": 0.10,
}

DEFAULT_SIZE_MIX: dict[str, float] = {
    "xs": 0.10,
    "s": 0.30,
    "m": 0.35,
    "l": 0.20,
    "xl": 0.05,
}

#: Work content multiplier per card size — the only thing "size" does here.
SIZE_FACTORS: dict[str, float] = {
    "xs": 0.45,
    "s": 0.75,
    "m": 1.0,
    "l": 1.55,
    "xl": 2.4,
}

#: Class-of-service pull order (lower sorts first). Fixed-date items float up to
#: rank 1 once their slack runs out; until then they wait behind standard work,
#: which is exactly the point of a fixed-date policy.
CLASS_PRIORITY: dict[str, int] = {
    "expedite": 0,
    "standard": 2,
    "fixed_date": 3,
    "intangible": 4,
}
_URGENT_FIXED_DATE_PRIORITY = 1

#: Mean service days per activity column, matched by lower-cased column name.
#: Only a starting point: :func:`simulate_flow` rescales these so the bottleneck
#: runs at ``target_utilization`` for the requested arrival rate.
DEFAULT_SERVICE_DAYS: dict[str, float] = {
    "discovery": 1.6,
    "analysis": 2.0,
    "design": 2.4,
    "build": 4.2,
    "development": 4.2,
    "implementation": 4.2,
    "verify": 1.6,
    "test": 1.6,
    "testing": 1.6,
    "review": 1.2,
    "deploy": 0.8,
}
_FALLBACK_SERVICE_DAYS = 2.0
_DEFAULT_CAPACITY = 4

#: Six policies that trace the whole Little's Law curve: no limits, loose, the
#: board's own policy, two progressively tighter buffer settings, and one that
#: tightens the *activity* columns too so the bottleneck genuinely starves.
#: The names are English catalog keys, translated when a run uses the defaults.
DEFAULT_WIP_CONFIGS: tuple[dict[str, Any], ...] = (
    {"name": N_("No WIP limits"), "unlimited": True},
    {"name": N_("Loose buffers (x2)"), "queue_scale": 2.0, "flow_scale": 2.0},
    {"name": N_("Board policy"), "scale": 1.0},
    {"name": N_("Tight buffers (x0.6)"), "queue_scale": 0.6, "flow_scale": 0.6},
    {"name": N_("Very tight buffers (x0.35)"), "queue_scale": 0.35, "flow_scale": 0.35},
    {"name": N_("Starved (x0.35 everywhere)"), "scale": 0.35},
)

DEFAULT_EXPEDITE_SHARES: tuple[float, ...] = (0.0, 0.05, 0.10, 0.20, 0.35)

_EPS = 1e-9
_INF = float("inf")


# ---------------------------------------------------------------------------
# small numeric helpers
# ---------------------------------------------------------------------------


def _round(value: float | None, places: int = 2) -> float | None:
    """Round for JSON transport; ``None`` and non-finite values survive intact."""
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), places)


def _pct_change(new: float | None, old: float | None) -> float | None:
    """Percentage change from ``old`` to ``new``; ``None`` when undefined."""
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100.0, 1)


def percentile(values: Sequence[float], p: float, *, interpolate: bool = True) -> float | None:
    """The ``p``-th percentile (0-100) of ``values``.

    ``interpolate=True`` gives the linear-interpolation percentile used for
    continuous quantities such as lead time. ``interpolate=False`` gives the
    nearest-rank percentile, which is the right answer for whole counts (weeks,
    items) because it never invents a value the system cannot produce.
    """
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    p = min(100.0, max(0.0, float(p)))
    if not interpolate:
        rank = math.ceil(p / 100.0 * n)
        return float(ordered[min(n - 1, max(0, rank - 1))])
    position = (p / 100.0) * (n - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[int(position)])
    weight = position - low
    return float(ordered[low]) * (1 - weight) + float(ordered[high]) * weight


def percentiles(
    values: Sequence[float],
    levels: Sequence[float] = CONFIDENCE_LEVELS,
    *,
    interpolate: bool = True,
    places: int = 2,
) -> dict[str, float]:
    """``{"p50": …, "p70": …}`` for every level, skipping empty inputs."""
    if not values:
        return {}
    ordered = sorted(values)
    out: dict[str, float] = {}
    for level in levels:
        value = percentile(ordered, level, interpolate=interpolate)
        if value is not None:
            out[f"p{int(level)}"] = round(value, places)
    return out


def _mean(values: Sequence[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _stdev(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = sum(values) / len(values)
    return math.sqrt(sum((v - avg) ** 2 for v in values) / (len(values) - 1))


def histogram(
    values: Sequence[float], *, max_bins: int = 40, integer: bool = True
) -> list[dict[str, Any]]:
    """A chart-ready histogram: one bin per distinct value when that is sensible.

    Forecast distributions are usually narrow integers ("6, 7, 8 weeks"), where a
    bin per value is far more readable than fixed-width buckets. Only when the
    support is wider than ``max_bins`` do we fall back to equal-width bins.
    """
    if not values:
        return []
    total = len(values)
    distinct = sorted(set(values))
    if integer and len(distinct) <= max_bins:
        counts: dict[float, int] = {value: 0 for value in distinct}
        for value in values:
            counts[value] += 1
        bins: list[dict[str, Any]] = []
        running = 0
        for value in distinct:
            count = counts[value]
            running += count
            bins.append(
                {
                    "x": int(value) if float(value).is_integer() else round(float(value), 3),
                    "label": num(int(value)) if float(value).is_integer() else num(value, 2),
                    "count": count,
                    "pct": round(count / total * 100.0, 3),
                    "cumulative_pct": round(running / total * 100.0, 3),
                }
            )
        return bins

    low, high = float(distinct[0]), float(distinct[-1])
    if high <= low:
        return [
            {
                "x": round(low, 3),
                "label": num(low, 2),
                "count": total,
                "pct": 100.0,
                "cumulative_pct": 100.0,
            }
        ]
    bin_count = min(max_bins, max(8, int(math.sqrt(total))))
    width = (high - low) / bin_count
    counts_list = [0] * bin_count
    for value in values:
        index = int((float(value) - low) / width)
        counts_list[min(bin_count - 1, max(0, index))] += 1
    bins = []
    running = 0
    for index, count in enumerate(counts_list):
        start = low + index * width
        end = start + width
        running += count
        bins.append(
            {
                "x": round(start + width / 2, 3),
                "x0": round(start, 3),
                "x1": round(end, 3),
                "label": f"{num(start, 1)}–{num(end, 1)}",
                "count": count,
                "pct": round(count / total * 100.0, 3),
                "cumulative_pct": round(running / total * 100.0, 3),
            }
        )
    return bins


def _parse_iso(value: Any) -> datetime | None:
    """Parse the app's ISO-8601 ``Z`` timestamps without importing the db layer."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _format_day(value: date, reference: date | None = None) -> str:
    """``12 Oct`` — with the year appended only when it is not the obvious one."""
    return date_label(value, reference)


def _items(count: float) -> str:
    return _n("item", "items", count)


def _weeks(count: float) -> str:
    return _n("week", "weeks", count)


def weekly_throughput_from_deliveries(
    delivered_at: Iterable[Any],
    *,
    weeks: int | None = None,
    end: Any = None,
    include_empty_weeks: bool = True,
) -> list[int]:
    """Bucket delivery timestamps into consecutive 7-day weeks, oldest first.

    Empty weeks are kept by default: a week in which nothing shipped is a real
    observation and dropping it would flatter every forecast built on the data.
    """
    moments = [moment for moment in (_parse_iso(value) for value in delivered_at) if moment]
    if not moments:
        return []
    moments.sort()
    last = _parse_iso(end) or moments[-1]
    first = moments[0]
    span_weeks = max(1, math.ceil(((last - first).total_seconds() / 86400.0) / 7.0))
    if weeks is not None:
        span_weeks = (
            max(1, min(int(weeks), span_weeks)) if include_empty_weeks else max(1, int(weeks))
        )
        span_weeks = max(1, int(weeks))
    buckets = [0] * span_weeks
    window_start = last - timedelta(weeks=span_weeks)
    for moment in moments:
        if moment <= window_start:
            continue
        offset = (moment - window_start).total_seconds() / 86400.0 / 7.0
        index = min(span_weeks - 1, max(0, int(offset)))
        buckets[index] += 1
    if not include_empty_weeks:
        return [value for value in buckets if value > 0]
    # Leading zeros before the very first delivery are an artefact of the window,
    # not evidence of a bad week, so they are trimmed.
    first_nonzero = next((i for i, value in enumerate(buckets) if value > 0), None)
    if first_nonzero is None:
        return []
    return buckets[first_nonzero:]


def _clean_samples(throughput_samples: Iterable[Any]) -> list[float]:
    values: list[float] = []
    for raw in throughput_samples:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value >= 0:
            values.append(value)
    return values


def _sample_stats(pool: Sequence[float]) -> dict[str, Any]:
    avg = _mean(pool)
    deviation = _stdev(pool)
    return {
        "weeks": len(pool),
        "values": [int(v) if float(v).is_integer() else round(float(v), 3) for v in pool],
        "mean": _round(avg),
        "stdev": _round(deviation),
        "cv": _round(deviation / avg, 3) if avg and deviation is not None and avg > 0 else None,
        "min": _round(min(pool)) if pool else None,
        "max": _round(max(pool)) if pool else None,
        "median": _round(percentile(pool, 50)),
        "distinct": len(set(pool)),
    }


# ---------------------------------------------------------------------------
# Monte Carlo forecasting (SPEC §8.3)
# ---------------------------------------------------------------------------


def monte_carlo_when(
    throughput_samples: Iterable[Any],
    backlog_size: int,
    trials: int = DEFAULT_TRIALS,
    seed: int = 20260816,
    *,
    start_date: Any = None,
    confidence_levels: Sequence[int] = CONFIDENCE_LEVELS,
    max_weeks: int = 520,
) -> dict[str, Any]:
    """ "How long for ``backlog_size`` items?" — resampled weekly throughput.

    Each trial draws weekly throughput values *with replacement* until the
    backlog is cleared and records how many weeks that took. The answer is the
    resulting distribution, not its mean: a team that ships 0-14 items a week
    averaging 7 is a very different promise from one that always ships 7.

    Higher confidence means **more** weeks, so the percentiles ascend.
    """
    pool = _clean_samples(throughput_samples)
    if not pool:
        raise ValueError("Monte Carlo needs at least one weekly throughput sample.")
    if not any(value > 0 for value in pool):
        raise ValueError("Every throughput sample is zero — no forecast is possible.")
    backlog = int(backlog_size)
    if backlog <= 0:
        raise ValueError("backlog_size must be a positive number of items.")
    trials = max(1, int(trials))

    rnd = random.Random(seed)
    draw = rnd.random
    size = len(pool)
    # A local list plus index arithmetic: 10 000 trials must not allocate 10 000
    # objects, so nothing inside the loop builds anything.
    results: list[int] = [0] * trials
    truncated = 0
    for trial in range(trials):
        total = 0.0
        weeks = 0
        while total < backlog and weeks < max_weeks:
            total += pool[int(draw() * size)]
            weeks += 1
        if total < backlog:
            truncated += 1
        results[trial] = weeks

    ordered = sorted(results)
    origin = _resolve_start_date(start_date)
    levels: list[dict[str, Any]] = []
    percentile_map: dict[str, int] = {}
    date_map: dict[str, str] = {}
    for level in confidence_levels:
        weeks = int(percentile(ordered, level, interpolate=False) or 0)
        target = origin + timedelta(weeks=weeks)
        key = f"p{int(level)}"
        percentile_map[key] = weeks
        date_map[key] = target.isoformat()
        levels.append(
            {
                "confidence": int(level),
                "weeks": weeks,
                "days": weeks * 7,
                "date": target.isoformat(),
                "date_label": _format_day(target, origin),
                "sentence": _(
                    "{confidence}% confidence: all {backlog} {items} delivered within "
                    "{weeks} {weeks_word} — by {date}.",
                    confidence=int(level),
                    backlog=backlog,
                    items=_items(backlog),
                    weeks=weeks,
                    weeks_word=_weeks(weeks),
                    date=_format_day(target, origin),
                ),
            }
        )

    headline = next(
        (row for row in levels if row["confidence"] == 85), levels[-1] if levels else None
    )
    return {
        "kind": "monte_carlo_when",
        "question": _(
            "How long to deliver {backlog} {items}?", backlog=backlog, items=_items(backlog)
        ),
        "trials": trials,
        "seed": seed,
        "backlog_size": backlog,
        "unit": "weeks",
        "start_date": origin.isoformat(),
        "samples": _sample_stats(pool),
        "percentiles": percentile_map,
        "dates": date_map,
        "levels": levels,
        "histogram": histogram(results),
        "distribution": {
            "mean_weeks": _round(_mean(results)),
            "min_weeks": min(ordered),
            "max_weeks": max(ordered),
            "stdev_weeks": _round(_stdev(results)),
        },
        "truncated_trials": truncated,
        "sentence": headline["sentence"] if headline else "",
        "verdict": _when_verdict(levels, backlog, pool),
        "low_confidence": len(pool) < 8,
    }


def _when_verdict(levels: Sequence[dict[str, Any]], backlog: int, pool: Sequence[float]) -> str:
    """A two-sentence read-out: the commitment, and how wide the risk band is."""
    if not levels:
        return ""
    by_level = {row["confidence"]: row for row in levels}
    optimistic = by_level.get(50) or levels[0]
    safe = by_level.get(85) or levels[-1]
    spread = safe["weeks"] - optimistic["weeks"]
    average = _mean(list(pool)) or 0.0
    naive = math.ceil(backlog / average) if average > 0 else 0
    parts = [
        _(
            "A coin-flip date is {optimistic_date} ({optimistic_weeks} {optimistic_word}); "
            "the date worth committing to is {safe_date} ({safe_weeks} {safe_word}).",
            optimistic_date=optimistic["date_label"],
            optimistic_weeks=optimistic["weeks"],
            optimistic_word=_weeks(optimistic["weeks"]),
            safe_date=safe["date_label"],
            safe_weeks=safe["weeks"],
            safe_word=_weeks(safe["weeks"]),
        )
    ]
    if spread <= 0:
        parts.append(_("Throughput has been steady enough that the risk band is a single week."))
    else:
        parts.append(
            _(
                "The {spread}-{weeks_word} gap between them is the cost of throughput "
                "variability, not of the work itself.",
                spread=spread,
                weeks_word=_weeks(spread),
            )
        )
    if naive:
        parts.append(
            _(
                "Dividing the backlog by average throughput would have promised {naive} "
                "{weeks_word} — a date this simulation only reaches {probability}.",
                naive=naive,
                weeks_word=_weeks(naive),
                probability=_reach_probability(levels, naive),
            )
        )
    return " ".join(parts)


def _reach_probability(levels: Sequence[dict[str, Any]], weeks: int) -> str:
    """Describe how safe a naive week count is, in words rather than a number."""
    for row in levels:
        if row["weeks"] >= weeks:
            return _("about {confidence}% of the time", confidence=row["confidence"])
    return _("less often than 95% of the time")


def monte_carlo_how_many(
    throughput_samples: Iterable[Any],
    days: int,
    trials: int = DEFAULT_TRIALS,
    seed: int = 20260816,
    *,
    start_date: Any = None,
    confidence_levels: Sequence[int] = CONFIDENCE_LEVELS,
) -> dict[str, Any]:
    """ "How many items by a date?" — resampled weekly throughput.

    Higher confidence means **fewer** items, so these percentiles descend: a
    forecast you are 95% sure of is by construction a smaller promise than one
    you are 50% sure of. The reported value at confidence *c* is the largest
    count ``N`` for which ``P(delivered ≥ N) ≥ c``, i.e. the ``100 − c`` th
    percentile of the simulated distribution.
    """
    pool = _clean_samples(throughput_samples)
    if not pool:
        raise ValueError("Monte Carlo needs at least one weekly throughput sample.")
    if not any(value > 0 for value in pool):
        raise ValueError("Every throughput sample is zero — no forecast is possible.")
    horizon_days = int(days)
    if horizon_days <= 0:
        raise ValueError("days must be a positive horizon.")
    trials = max(1, int(trials))

    rnd = random.Random(seed)
    weeks = horizon_days / 7.0
    whole_weeks = int(weeks)
    remainder = weeks - whole_weeks

    # rnd.choices() draws the entire trial matrix in one C-level call, which is
    # roughly an order of magnitude faster than a Python-level per-week loop.
    if whole_weeks:
        flat = rnd.choices(pool, k=trials * whole_weeks)
        totals = [
            math.fsum(flat[index * whole_weeks : (index + 1) * whole_weeks])
            for index in range(trials)
        ]
    else:
        totals = [0.0] * trials
    if remainder > _EPS:
        partial = rnd.choices(pool, k=trials)
        totals = [totals[index] + partial[index] * remainder for index in range(trials)]

    # Floor, not round: a half-finished item has not been delivered.
    counts = [int(value + _EPS) for value in totals]
    ordered = sorted(counts)
    n = len(ordered)
    origin = _resolve_start_date(start_date)
    target = origin + timedelta(days=horizon_days)
    target_label = _format_day(target, origin)

    levels: list[dict[str, Any]] = []
    percentile_map: dict[str, int] = {}
    for level in confidence_levels:
        index = min(n - 1, max(0, math.floor(n * (1.0 - level / 100.0))))
        value = ordered[index]
        key = f"p{int(level)}"
        percentile_map[key] = value
        levels.append(
            {
                "confidence": int(level),
                "items": value,
                "date": target.isoformat(),
                "date_label": target_label,
                "sentence": _(
                    "{confidence}% confidence: {value} {items} or more by {date}.",
                    confidence=int(level),
                    value=value,
                    items=_items(value),
                    date=target_label,
                ),
            }
        )

    headline = next(
        (row for row in levels if row["confidence"] == 85), levels[-1] if levels else None
    )
    return {
        "kind": "monte_carlo_how_many",
        "question": _("How many items can we deliver in {days} days?", days=horizon_days),
        "trials": trials,
        "seed": seed,
        "days": horizon_days,
        "weeks": _round(weeks),
        "unit": "items",
        "start_date": origin.isoformat(),
        "target_date": target.isoformat(),
        "target_date_label": target_label,
        "samples": _sample_stats(pool),
        "percentiles": percentile_map,
        "levels": levels,
        "histogram": histogram(counts),
        # The plain ascending percentiles of the simulated distribution, for
        # charts that want to draw the curve rather than read a commitment off it.
        "distribution_percentiles": {
            f"p{int(level)}": int(percentile(ordered, level, interpolate=False) or 0)
            for level in confidence_levels
        },
        "distribution": {
            "mean_items": _round(_mean(counts)),
            "min_items": ordered[0],
            "max_items": ordered[-1],
            "stdev_items": _round(_stdev(counts)),
        },
        "sentence": headline["sentence"] if headline else "",
        "verdict": _how_many_verdict(levels, target_label, pool, weeks),
        "low_confidence": len(pool) < 8,
    }


def _how_many_verdict(
    levels: Sequence[dict[str, Any]], target_label: str, pool: Sequence[float], weeks: float
) -> str:
    if not levels:
        return ""
    by_level = {row["confidence"]: row for row in levels}
    optimistic = by_level.get(50) or levels[0]
    safe = by_level.get(85) or levels[-1]
    average = _mean(list(pool)) or 0.0
    naive = int(average * weeks)
    parts = [
        _(
            "Plan on {safe_items} {items} by {date} and you will be right {confidence}% of the "
            "time; plan on {optimistic_items} and it is a coin flip.",
            safe_items=safe["items"],
            items=_items(safe["items"]),
            date=target_label,
            confidence=safe["confidence"],
            optimistic_items=optimistic["items"],
        )
    ]
    gap = optimistic["items"] - safe["items"]
    if gap > 0:
        parts.append(
            _(
                "Those {gap} {items} are the negotiating room variability takes away from you.",
                gap=gap,
                items=_items(gap),
            )
        )
    if naive:
        parts.append(_("Average throughput alone would have promised {naive}.", naive=naive))
    return " ".join(parts)


def _resolve_start_date(value: Any) -> date:
    if value is None:
        return datetime.now(UTC).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = _parse_iso(value)
    if parsed:
        return parsed.date()
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Discrete-event flow simulator (SPEC §8.2, §8.4)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ColumnSpec:
    """One station in the simulated pull system.

    ``capacity`` (servers) and ``wip_max`` (policy limit) are independent on
    purpose — see the module docstring. For an ``activity`` column the effective
    concurrency is ``min(capacity, wip_max)``, so a policy tighter than the team
    genuinely starves it and a policy looser than the team simply does nothing.
    """

    key: str
    name: str
    position: int
    segment: str = "downstream"
    kind: str = "queue"
    wip_min: int | None = None
    wip_max: int | None = None
    capacity: int | None = None
    service_days: float = 0.0
    is_commitment_point: bool = False
    is_delivery_point: bool = False

    @property
    def is_activity(self) -> bool:
        return self.kind == "activity"

    @property
    def is_done(self) -> bool:
        return self.kind == "done"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "position": self.position,
            "segment": self.segment,
            "kind": self.kind,
            "wip_min": self.wip_min,
            "wip_max": self.wip_max,
            "capacity": self.capacity,
            "service_days": _round(self.service_days, 3),
            "is_commitment_point": self.is_commitment_point,
            "is_delivery_point": self.is_delivery_point,
        }


def column_specs_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    service_times: Mapping[str, float] | None = None,
    capacities: Mapping[str, int] | None = None,
    include_envelope: bool = False,
) -> list[ColumnSpec]:
    """Turn ``columns`` table rows into simulator stations, in flow order.

    ``envelope`` columns are excluded by default: a sealed envelope is demand
    that has not entered the system yet, so simulating flow through it would
    invent work that nobody is doing.
    """
    ordered = sorted(
        rows, key=lambda row: (int(row.get("position") or 0), str(row.get("name") or ""))
    )
    specs: list[ColumnSpec] = []
    for row in ordered:
        kind = str(row.get("kind") or "queue")
        if kind == "envelope" and not include_envelope:
            continue
        key = str(row.get("id") or row.get("key") or row.get("name") or f"col{len(specs)}")
        name = str(row.get("name") or key)
        wip_max = row.get("wip_max")
        wip_max = int(wip_max) if wip_max not in (None, "") else None
        wip_min = row.get("wip_min")
        wip_min = int(wip_min) if wip_min not in (None, "") else None
        capacity = _lookup(capacities, key, name)
        if capacity is None:
            capacity = wip_max if (kind == "activity" and wip_max) else None
        if kind == "activity" and not capacity:
            capacity = _DEFAULT_CAPACITY
        service = _lookup(service_times, key, name)
        if service is None and kind == "activity":
            service = DEFAULT_SERVICE_DAYS.get(name.strip().lower(), _FALLBACK_SERVICE_DAYS)
        specs.append(
            ColumnSpec(
                key=key,
                name=name,
                position=len(specs),
                segment=str(row.get("segment") or "downstream"),
                kind=kind,
                wip_min=wip_min,
                wip_max=wip_max,
                capacity=int(capacity) if capacity else None,
                service_days=float(service or 0.0),
                is_commitment_point=bool(row.get("is_commitment_point")),
                is_delivery_point=bool(row.get("is_delivery_point")),
            )
        )
    if not specs:
        raise ValueError("The board has no simulatable columns.")
    if not any(spec.is_done for spec in specs):
        raise ValueError("The board has no 'done' column, so nothing can ever be delivered.")
    if not any(spec.is_commitment_point for spec in specs):
        # Fall back to the first downstream column, which is the SPEC definition
        # of the commitment point even when the flag was never set.
        for spec in specs:
            if spec.segment == "downstream":
                spec.is_commitment_point = True
                break
        else:
            specs[0].is_commitment_point = True
    return specs


def _lookup(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    """Look a column up by id, exact name, or case-insensitive name."""
    if not mapping:
        return None
    for key in keys:
        if key in mapping:
            return mapping[key]
    lowered = {str(k).strip().lower(): v for k, v in mapping.items()}
    for key in keys:
        candidate = lowered.get(str(key).strip().lower())
        if candidate is not None:
            return candidate
    return None


@dataclass(slots=True)
class DemandCard:
    """One unit of demand, with **all** of its randomness drawn up front.

    Pre-drawing is what lets two policies be compared honestly: the same card
    arrives at the same moment carrying the same work content in every run, so
    any difference in the results is caused by the policy alone.
    """

    index: int
    arrival_day: float
    service_class: str
    size: str
    demand_type: str
    value_area: str
    #: normalised work content per activity column (mean 1.0 before size scaling)
    factors: dict[str, float] = field(default_factory=dict)
    due_day: float | None = None


@dataclass(slots=True)
class DemandStream:
    """A reusable arrival stream. Identical seed ⇒ identical stream."""

    cards: list[DemandCard]
    weeks: float
    arrival_rate: float
    seed: int
    column_keys: tuple[str, ...]
    label: str = "poisson"

    def __len__(self) -> int:
        return len(self.cards)

    def class_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for card in self.cards:
            counts[card.service_class] = counts.get(card.service_class, 0) + 1
        return counts


def _normalise_mix(
    mix: Mapping[str, float] | None, default: Mapping[str, float]
) -> list[tuple[str, float]]:
    """Weighted choices as a stable, ordered list — dict order must not matter."""
    source = {str(k): float(v) for k, v in (mix or {}).items() if float(v) > 0} or dict(default)
    total = sum(source.values())
    if total <= 0:
        source = dict(default)
        total = sum(source.values())
    return sorted(((key, value / total) for key, value in source.items()), key=lambda pair: pair[0])


def _pick(rnd: random.Random, weighted: Sequence[tuple[str, float]]) -> str:
    roll = rnd.random()
    cumulative = 0.0
    for key, weight in weighted:
        cumulative += weight
        if roll <= cumulative:
            return key
    return weighted[-1][0]


def build_demand_stream(
    specs: Sequence[ColumnSpec],
    *,
    weeks: float = 12.0,
    arrival_rate: float = 8.0,
    class_mix: Mapping[str, float] | None = None,
    size_mix: Mapping[str, float] | None = None,
    seed: int = 20260816,
    due_horizon_days: float = 30.0,
    variability: float = 0.6,
    failure_demand_share: float = 0.12,
    arrival_days: Sequence[float] | None = None,
) -> DemandStream:
    """Draw a Poisson arrival stream (or replay ``arrival_days``) with work content.

    ``variability`` is the sigma of a unit-mean lognormal service factor: 0.6
    gives a coefficient of variation around 0.65, which is what real knowledge
    work looks like. Setting it to 0 makes every item identical, which is useful
    for isolating queueing effects from size effects.
    """
    rnd = random.Random(seed)
    activity_keys = tuple(spec.key for spec in specs if spec.is_activity)
    classes = _normalise_mix(class_mix, DEFAULT_CLASS_MIX)
    sizes = _normalise_mix(size_mix, DEFAULT_SIZE_MIX)
    horizon = float(weeks) * 7.0
    rate_per_day = max(1e-6, float(arrival_rate) / 7.0)
    sigma = max(0.0, float(variability))
    # A unit-mean lognormal: exp(mu + sigma^2/2) == 1 exactly when mu == -sigma^2/2.
    mu = -(sigma**2) / 2.0

    if arrival_days is not None:
        moments = [float(value) for value in arrival_days if float(value) >= 0.0]
        moments.sort()
    else:
        moments = []
        clock = 0.0
        while True:
            clock += rnd.expovariate(rate_per_day)
            if clock > horizon:
                break
            moments.append(clock)

    cards: list[DemandCard] = []
    for index, moment in enumerate(moments):
        service_class = _pick(rnd, classes)
        size = _pick(rnd, sizes)
        size_factor = SIZE_FACTORS.get(size, 1.0)
        factors = {
            key: size_factor * (rnd.lognormvariate(mu, sigma) if sigma > 0 else 1.0)
            for key in activity_keys
        }
        is_failure = rnd.random() < failure_demand_share
        due_day: float | None = None
        if service_class == "fixed_date":
            due_day = moment + float(due_horizon_days) * rnd.uniform(0.7, 1.6)
        cards.append(
            DemandCard(
                index=index,
                arrival_day=moment,
                service_class=service_class,
                size=size,
                demand_type="failure" if is_failure else "value",
                value_area="defect" if is_failure else "feature",
                factors=factors,
                due_day=due_day,
            )
        )
    return DemandStream(
        cards=cards,
        weeks=float(weeks),
        arrival_rate=float(arrival_rate),
        seed=seed,
        column_keys=activity_keys,
        label="replay" if arrival_days is not None else "poisson",
    )


@dataclass(slots=True)
class SimulationResult:
    """Everything one simulated scenario produced."""

    specs: list[ColumnSpec]
    cards: list[dict[str, Any]]
    movements: list[dict[str, Any]]
    metrics: dict[str, Any]
    series: dict[str, Any]
    config: dict[str, Any]

    @property
    def horizon_days(self) -> float:
        return float(self.config.get("horizon_days") or 0.0)

    @property
    def elapsed_days(self) -> float:
        """Simulated span actually covered, including any drain tail."""
        return float(self.config.get("elapsed_days") or self.horizon_days)


@dataclass(slots=True)
class _Live:
    """Mutable per-card state while the card is inside the system."""

    card: DemandCard
    col: int
    entered_at: float
    remaining: float = 0.0
    service_end: float = _INF
    serving: bool = False
    served: bool = False
    state_since: float = 0.0
    touch: float = 0.0
    wait: float = 0.0
    committed_at: float | None = None
    delivered_at: float | None = None
    path: list[tuple[int, float]] = field(default_factory=list)
    preempted: int = 0


def _resolve_limits(
    specs: Sequence[ColumnSpec],
    *,
    wip_limits: Mapping[str, Any] | None,
    unlimited: bool,
    queue_scale: float | None,
    activity_scale: float | None,
    scale: float | None,
) -> list[float]:
    """The effective pull limit for every column under one policy."""
    limits: list[float] = []
    for index, spec in enumerate(specs):
        if spec.is_done:
            limits.append(_INF)
            continue
        if unlimited:
            limits.append(_INF)
            continue
        override = _lookup(wip_limits, spec.key, spec.name)
        if override is not None:
            limit = _INF if override in (0, "", None) and override != 0 else float(override)
            if isinstance(override, (int, float)) and float(override) <= 0:
                limit = _INF
        else:
            base = spec.wip_max
            if base is None:
                limit = _INF
            else:
                factor = scale if scale is not None else 1.0
                if spec.is_activity and activity_scale is not None:
                    factor = activity_scale
                elif not spec.is_activity and queue_scale is not None:
                    factor = queue_scale
                limit = max(1.0, math.floor(float(base) * factor))
        limits.append(limit)

    # The first flow column is the demand backlog. Real demand cannot be
    # rejected at the door, so it is never limited — otherwise a tight policy
    # would silently drop arrivals and flatter its own throughput number.
    if limits:
        limits[0] = _INF
    for index, spec in enumerate(specs):
        if spec.is_activity and spec.capacity:
            limits[index] = min(limits[index], float(spec.capacity))
    return limits


def simulate_flow(
    columns: Sequence[Mapping[str, Any] | ColumnSpec],
    *,
    weeks: float = 12.0,
    arrival_rate: float = 8.0,
    class_mix: Mapping[str, float] | None = None,
    size_mix: Mapping[str, float] | None = None,
    seed: int = 20260816,
    stream: DemandStream | None = None,
    wip_limits: Mapping[str, Any] | None = None,
    flow_wip_max: int | None = None,
    unlimited_wip: bool = False,
    queue_scale: float | None = None,
    activity_scale: float | None = None,
    scale: float | None = None,
    service_times: Mapping[str, float] | None = None,
    capacities: Mapping[str, int] | None = None,
    target_utilization: float = 0.85,
    auto_calibrate: bool = True,
    warmup_weeks: float | None = None,
    drain: bool = False,
    drain_limit_weeks: float = 26.0,
    variability: float = 0.6,
    expedite_preemption: bool = True,
    collect_series: bool = True,
    label: str = "scenario",
) -> SimulationResult:
    """Run one scenario end to end and return cards, movements and metrics.

    The clock is event-driven: it jumps to the next arrival or service
    completion, never ticks. Between events nothing can change, so the
    simulation is exact rather than approximate, and 12 simulated weeks cost a
    few milliseconds.

    Pull rule, in order of precedence:

    1. Cards are considered from the **last** column backwards, so a slot freed
       downstream cascades upstream within the same instant — which is what a
       real standup does when it walks the board right to left.
    2. Within a column, candidates are ordered by class of service, then by
       arrival (FIFO inside a class).
    3. A card may enter the next column only if that column has headroom, and —
       when crossing the commitment point — only if the whole-flow limit allows
       it. ``expedite`` bypasses both (SPEC §6.6) and, if enabled, preempts the
       lowest-priority item currently in service.
    """
    specs = (
        list(columns)  # type: ignore[arg-type]
        if columns and isinstance(columns[0], ColumnSpec)
        else column_specs_from_rows(columns, service_times=service_times, capacities=capacities)  # type: ignore[arg-type]
    )
    if len(specs) < 2:
        raise ValueError("A simulated board needs at least two columns.")

    if stream is None:
        stream = build_demand_stream(
            specs,
            weeks=weeks,
            arrival_rate=arrival_rate,
            class_mix=class_mix,
            size_mix=size_mix,
            seed=seed,
            variability=variability,
        )
    weeks = float(stream.weeks if stream.weeks else weeks)
    horizon = weeks * 7.0
    warmup_days = (
        float(warmup_weeks) * 7.0
        if warmup_weeks is not None
        else min(horizon * 0.25, 14.0)
        if weeks >= 8
        else 0.0
    )
    window = max(_EPS, horizon - warmup_days)

    capacity = [int(spec.capacity or 0) for spec in specs]
    service_scale = 1.0
    if auto_calibrate:
        service_scale = _calibration_scale(specs, capacity, stream, target_utilization)
    service_days = [spec.service_days * service_scale for spec in specs]

    limits = _resolve_limits(
        specs,
        wip_limits=wip_limits,
        unlimited=unlimited_wip,
        queue_scale=queue_scale,
        activity_scale=activity_scale,
        scale=scale,
    )
    commit_index = next((i for i, spec in enumerate(specs) if spec.is_commitment_point), 0)
    done_index = next((i for i, spec in enumerate(specs) if spec.is_done), len(specs) - 1)
    flow_limit = _INF if (unlimited_wip or not flow_wip_max) else float(max(1, int(flow_wip_max)))

    cols: list[list[_Live]] = [[] for _ in specs]
    serving_count = [0] * len(specs)
    col_integral = [0.0] * len(specs)
    serving_integral = [0.0] * len(specs)
    col_entries = [0] * len(specs)
    col_time_total = [0.0] * len(specs)
    live: list[_Live] = []
    finished: list[_Live] = []
    movements: list[dict[str, Any]] = []
    preemptions = 0
    flow_wip = 0
    wip_integral = 0.0
    total_integral = 0.0
    flow_wip_peak = 0
    clock = 0.0
    urgency_window = 5.0  # days of slack below which fixed-date work is expedited

    def priority_key(item: _Live) -> tuple[int, float, int]:
        service_class = item.card.service_class
        rank = CLASS_PRIORITY.get(service_class, 3)
        if service_class == "fixed_date" and item.card.due_day is not None:
            if item.card.due_day - clock <= urgency_window:
                rank = _URGENT_FIXED_DATE_PRIORITY
        return (rank, item.card.arrival_day, item.card.index)

    def charge(item: _Live, now: float) -> None:
        delta = now - item.state_since
        if delta > 0:
            if item.serving:
                item.touch += delta
            else:
                item.wait += delta
        item.state_since = now

    def advance(now: float) -> None:
        """Accumulate the time-weighted integrals for the statistics window."""
        nonlocal clock, wip_integral, total_integral
        low = max(clock, warmup_days)
        high = min(now, horizon)
        if high > low:
            span = high - low
            wip_integral += span * flow_wip
            total_integral += span * len(live)
            for index in range(len(specs)):
                col_integral[index] += span * len(cols[index])
                serving_integral[index] += span * serving_count[index]
        clock = now

    def start_service(item: _Live, index: int, now: float) -> None:
        charge(item, now)
        item.serving = True
        item.service_end = now + max(0.0, item.remaining)
        serving_count[index] += 1

    def stop_service(item: _Live, index: int, now: float, *, completed: bool) -> None:
        charge(item, now)
        item.remaining = 0.0 if completed else max(0.0, item.service_end - now)
        item.serving = False
        item.service_end = _INF
        item.served = item.served or completed
        serving_count[index] -= 1

    def fill_servers(index: int, now: float) -> None:
        spec = specs[index]
        if not spec.is_activity:
            return
        free = (capacity[index] or 0) - serving_count[index]
        if free <= 0:
            return
        waiting = [item for item in cols[index] if not item.serving and not item.served]
        if not waiting:
            return
        waiting.sort(key=priority_key)
        for item in waiting[:free]:
            start_service(item, index, now)

    def preempt(index: int, incoming: _Live, now: float) -> bool:
        """Evict the lowest-priority item in service so an expedite can start."""
        nonlocal preemptions
        serving = [item for item in cols[index] if item.serving]
        if not serving:
            return False
        victim = max(serving, key=priority_key)
        if priority_key(victim) <= priority_key(incoming):
            return False
        stop_service(victim, index, now, completed=False)
        victim.preempted += 1
        preemptions += 1
        return True

    def enter(item: _Live, index: int, now: float, previous: int | None) -> None:
        nonlocal flow_wip, flow_wip_peak
        spec = specs[index]
        seconds_in_from = None
        if previous is not None:
            seconds_in_from = round((now - item.entered_at) * 86400.0)
            col_time_total[previous] += now - item.entered_at
        charge(item, now)
        item.col = index
        item.entered_at = now
        item.served = False
        item.path.append((index, now))
        col_entries[index] += 1
        movements.append(
            {
                "card_index": item.card.index,
                "from_index": previous,
                "to_index": index,
                "from_key": specs[previous].key if previous is not None else None,
                "to_key": spec.key,
                "at_day": now,
                "seconds_in_from_column": seconds_in_from,
            }
        )
        if index >= commit_index and item.committed_at is None and not spec.is_done:
            item.committed_at = now
        if spec.is_done:
            item.delivered_at = now
            if item.committed_at is None:
                item.committed_at = now
            live.remove(item)
            finished.append(item)
            if previous is not None and previous >= commit_index:
                flow_wip -= 1
            return
        cols[index].append(item)
        if previous is None or previous < commit_index <= index:
            flow_wip += 1
            flow_wip_peak = max(flow_wip_peak, flow_wip)
        if spec.is_activity:
            item.remaining = service_days[index] * item.card.factors.get(spec.key, 1.0)
            if serving_count[index] < (capacity[index] or 0):
                start_service(item, index, now)
            elif item.card.service_class == "expedite" and expedite_preemption:
                if preempt(index, item, now):
                    start_service(item, index, now)

    def leave(item: _Live, index: int, now: float) -> None:
        if item.serving:
            stop_service(item, index, now, completed=False)
        cols[index].remove(item)
        fill_servers(index, now)

    def can_enter(item: _Live, target: int) -> bool:
        if specs[target].is_done:
            return True
        expedite = item.card.service_class == "expedite"
        if not expedite and len(cols[target]) >= limits[target]:
            return False
        return not (target >= commit_index > item.col and not expedite and flow_wip >= flow_limit)

    def ready(item: _Live) -> bool:
        return (not specs[item.col].is_activity) or item.served

    def pull_pass(now: float) -> None:
        """Right-to-left sweep, repeated until the board stops moving."""
        moved = True
        while moved:
            moved = False
            for index in range(len(specs) - 2, -1, -1):
                if not cols[index]:
                    continue
                candidates = [item for item in cols[index] if ready(item)]
                if not candidates:
                    continue
                candidates.sort(key=priority_key)
                for item in candidates:
                    target = index + 1
                    if not can_enter(item, target):
                        continue
                    leave(item, index, now)
                    enter(item, target, now, index)
                    moved = True

    cards_in = stream.cards
    arrival_cursor = 0
    total_cards = len(cards_in)
    drain_deadline = horizon + (drain_limit_weeks * 7.0 if drain else 0.0)

    while True:
        next_arrival = (
            cards_in[arrival_cursor].arrival_day if arrival_cursor < total_cards else _INF
        )
        next_service = _INF
        for item in live:
            if item.serving and item.service_end < next_service:
                next_service = item.service_end
        moment = min(next_arrival, next_service)
        if moment == _INF:
            break
        if moment > horizon and not drain:
            break
        if moment > drain_deadline:
            break
        advance(moment)

        # 1. completions — free the servers before anything tries to use them
        for item in [
            entry for entry in live if entry.serving and entry.service_end <= moment + _EPS
        ]:
            stop_service(item, item.col, moment, completed=True)
            fill_servers(item.col, moment)

        # 2. arrivals — demand is never rejected at the entry column
        while (
            arrival_cursor < total_cards and cards_in[arrival_cursor].arrival_day <= moment + _EPS
        ):
            card = cards_in[arrival_cursor]
            arrival_cursor += 1
            if card.arrival_day > horizon:
                continue
            item = _Live(
                card=card, col=0, entered_at=card.arrival_day, state_since=card.arrival_day
            )
            live.append(item)
            enter(item, 0, moment, None)

        # 3. pull — instantaneous, cascading right to left
        pull_pass(moment)

    end_time = min(max(clock, horizon), drain_deadline) if drain else horizon
    advance(end_time)
    for item in live:
        charge(item, end_time)
        col_time_total[item.col] += max(0.0, end_time - item.entered_at)

    metrics, series, card_records = _summarise(
        specs=specs,
        finished=finished,
        live=live,
        stream=stream,
        limits=limits,
        capacity=capacity,
        service_days=service_days,
        col_integral=col_integral,
        serving_integral=serving_integral,
        col_entries=col_entries,
        col_time_total=col_time_total,
        wip_integral=wip_integral,
        total_integral=total_integral,
        warmup_days=warmup_days,
        horizon=horizon,
        window=window,
        end_time=end_time,
        flow_limit=flow_limit,
        flow_wip_peak=flow_wip_peak,
        preemptions=preemptions,
        collect_series=collect_series,
    )

    config = {
        "label": label,
        "weeks": weeks,
        "horizon_days": horizon,
        "elapsed_days": end_time,
        "warmup_days": warmup_days,
        "window_days": window,
        "arrival_rate": stream.arrival_rate,
        "seed": stream.seed,
        "service_scale": _round(service_scale, 4),
        "target_utilization": target_utilization,
        "flow_wip_max": None if flow_limit == _INF else int(flow_limit),
        "unlimited_wip": unlimited_wip,
        "expedite_preemption": expedite_preemption,
        "drain": drain,
        "columns": [
            {
                **spec.as_dict(),
                "effective_wip_max": None if limits[index] == _INF else int(limits[index]),
                "calibrated_service_days": _round(service_days[index], 3),
            }
            for index, spec in enumerate(specs)
        ],
    }
    return SimulationResult(
        specs=specs,
        cards=card_records,
        movements=movements,
        metrics=metrics,
        series=series,
        config=config,
    )


def _calibration_scale(
    specs: Sequence[ColumnSpec],
    capacity: Sequence[int],
    stream: DemandStream,
    target_utilization: float,
) -> float:
    """Scale every service time so the bottleneck runs at ``target_utilization``.

    Without this, a board's WIP limits and a scenario's arrival rate are two
    unrelated numbers and the simulation lands either in free flow (nothing to
    learn) or in collapse (nothing to compare). Calibration depends only on the
    board and the arrival rate — never on the WIP policy — so every row of a WIP
    experiment shares one identical service-time model.
    """
    required = max(1e-6, float(stream.arrival_rate) / 7.0)
    target = min(0.99, max(0.05, float(target_utilization)))
    bottleneck = _INF
    mean_factor = 1.0
    if stream.cards:
        factors = [value for card in stream.cards for value in card.factors.values()]
        if factors:
            mean_factor = max(0.05, sum(factors) / len(factors))
    for index, spec in enumerate(specs):
        if not spec.is_activity or spec.service_days <= 0 or not capacity[index]:
            continue
        rate = capacity[index] / (spec.service_days * mean_factor)
        bottleneck = min(bottleneck, rate)
    if bottleneck == _INF or bottleneck <= 0:
        return 1.0
    return min(20.0, max(0.05, bottleneck * target / required))


def _summarise(
    *,
    specs: Sequence[ColumnSpec],
    finished: Sequence[_Live],
    live: Sequence[_Live],
    stream: DemandStream,
    limits: Sequence[float],
    capacity: Sequence[int],
    service_days: Sequence[float],
    col_integral: Sequence[float],
    serving_integral: Sequence[float],
    col_entries: Sequence[int],
    col_time_total: Sequence[float],
    wip_integral: float,
    total_integral: float,
    warmup_days: float,
    horizon: float,
    window: float,
    end_time: float,
    flow_limit: float,
    flow_wip_peak: int,
    preemptions: int,
    collect_series: bool,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Turn raw simulation state into the metric set SPEC §7.1 defines."""
    in_window = [
        item
        for item in finished
        if item.delivered_at is not None and warmup_days <= item.delivered_at <= horizon + _EPS
    ]
    system_lead = [
        (item.delivered_at - item.committed_at)
        for item in in_window
        if item.committed_at is not None and item.delivered_at is not None
    ]
    customer_lead = [
        (item.delivered_at - item.card.arrival_day)
        for item in in_window
        if item.delivered_at is not None
    ]
    touch = sum(item.touch for item in in_window)
    wait = sum(item.wait for item in in_window)
    efficiency = (touch / (touch + wait) * 100.0) if (touch + wait) > 0 else None

    weeks_window = max(_EPS, window / 7.0)
    throughput = len(in_window) / weeks_window
    avg_wip = wip_integral / window if window > 0 else 0.0
    avg_total_wip = total_integral / window if window > 0 else 0.0
    avg_lead = _mean(system_lead)

    by_class: dict[str, dict[str, Any]] = {}
    for service_class in sorted(
        {item.card.service_class for item in finished} | set(DEFAULT_CLASS_MIX)
    ):
        subset = [item for item in in_window if item.card.service_class == service_class]
        leads = [
            item.delivered_at - item.committed_at
            for item in subset
            if item.committed_at is not None and item.delivered_at is not None
        ]
        customer = [
            item.delivered_at - item.card.arrival_day for item in subset if item.delivered_at
        ]
        by_class[service_class] = {
            "delivered": len(subset),
            "share_pct": _round(len(subset) / len(in_window) * 100.0, 1) if in_window else 0.0,
            "lead_time_avg": _round(_mean(leads)),
            "lead_time_p50": _round(percentile(leads, 50)) if leads else None,
            "lead_time_p85": _round(percentile(leads, 85)) if leads else None,
            "customer_lead_time_avg": _round(_mean(customer)),
            "throughput_per_week": _round(len(subset) / weeks_window),
        }

    fixed_date = [
        item
        for item in in_window
        if item.card.service_class == "fixed_date" and item.card.due_day is not None
    ]
    on_time = [
        item for item in fixed_date if (item.delivered_at or _INF) <= (item.card.due_day or 0.0)
    ]
    due_date_performance = (len(on_time) / len(fixed_date) * 100.0) if fixed_date else None

    column_stats: list[dict[str, Any]] = []
    bottleneck: dict[str, Any] | None = None
    for index, spec in enumerate(specs):
        if spec.is_done:
            continue
        utilisation = None
        if spec.is_activity and capacity[index]:
            utilisation = (
                serving_integral[index] / (capacity[index] * window) * 100.0 if window > 0 else None
            )
        avg_time = col_time_total[index] / col_entries[index] if col_entries[index] else None
        row = {
            "key": spec.key,
            "name": spec.name,
            "kind": spec.kind,
            "segment": spec.segment,
            "wip_max": None if limits[index] == _INF else int(limits[index]),
            "capacity": capacity[index] or None,
            "avg_wip": _round(col_integral[index] / window if window > 0 else 0.0),
            "entries": col_entries[index],
            "avg_days": _round(avg_time),
            "utilization_pct": _round(utilisation, 1),
            "service_days": _round(service_days[index], 3),
        }
        column_stats.append(row)
        if spec.is_activity and utilisation is not None:
            if bottleneck is None or utilisation > (bottleneck.get("utilization_pct") or 0):
                bottleneck = dict(row)

    unfinished_ages = [max(0.0, end_time - item.card.arrival_day) for item in live]
    arrivals_in_horizon = sum(1 for card in stream.cards if card.arrival_day <= horizon)

    coherence = None
    if avg_lead and throughput > 0:
        predicted = (throughput / 7.0) * avg_lead
        coherence = predicted / avg_wip if avg_wip > 0 else None

    metrics: dict[str, Any] = {
        "arrived": arrivals_in_horizon,
        "delivered": len(in_window),
        "delivered_total": len(finished),
        "in_flight_end": len(live),
        "throughput_per_week": _round(throughput),
        "lead_time": _distribution_block(system_lead),
        "customer_lead_time": _distribution_block(customer_lead),
        "flow_efficiency_pct": _round(efficiency, 1),
        "touch_days_total": _round(touch),
        "wait_days_total": _round(wait),
        "avg_wip": _round(avg_wip),
        "avg_total_wip": _round(avg_total_wip),
        "peak_flow_wip": flow_wip_peak,
        "flow_wip_max": None if flow_limit == _INF else int(flow_limit),
        "littles_law": {
            "avg_wip": _round(avg_wip),
            "throughput_per_day": _round(throughput / 7.0, 3),
            "throughput_per_week": _round(throughput),
            "avg_lead_time_days": _round(avg_lead),
            "predicted_wip": _round((throughput / 7.0) * avg_lead) if avg_lead else None,
            "coherence_ratio": _round(coherence, 3),
        },
        "by_class": by_class,
        "due_date_performance_pct": _round(due_date_performance, 1),
        "columns": column_stats,
        "bottleneck": bottleneck,
        "bottleneck_utilization_pct": (bottleneck or {}).get("utilization_pct"),
        "preemptions": preemptions,
        "unfinished": len(live),
        "unfinished_avg_age_days": _round(_mean(unfinished_ages)),
        "sample_size": len(in_window),
        "low_confidence": len(in_window) < 8,
    }

    series: dict[str, Any] = {}
    if collect_series:
        series["weekly_throughput"] = _weekly_series(finished, horizon)
        series["lead_time_scatter"] = [
            {
                "x": _round(item.delivered_at),
                "y": _round(item.delivered_at - (item.committed_at or item.card.arrival_day)),
                "service_class": item.card.service_class,
            }
            for item in in_window
        ]
        series["lead_time_histogram"] = histogram(
            [round(value, 1) for value in system_lead], integer=False
        )
        series["column_wip"] = [
            {"key": row["key"], "name": row["name"], "kind": row["kind"], "avg_wip": row["avg_wip"]}
            for row in column_stats
        ]

    card_records = [
        {
            "index": item.card.index,
            "service_class": item.card.service_class,
            "size": item.card.size,
            "demand_type": item.card.demand_type,
            "value_area": item.card.value_area,
            "arrival_day": item.card.arrival_day,
            "committed_day": item.committed_at,
            "delivered_day": item.delivered_at,
            "due_day": item.card.due_day,
            "touch_days": _round(item.touch, 4),
            "wait_days": _round(item.wait, 4),
            "preempted": item.preempted,
            "final_index": item.col,
            "path": [(index, moment) for index, moment in item.path],
        }
        for item in sorted(list(finished) + list(live), key=lambda entry: entry.card.index)
    ]
    return metrics, series, card_records


def _distribution_block(values: Sequence[float]) -> dict[str, Any]:
    """The uniform ``{avg, p50, p70, p85, p95, …}`` block used everywhere."""
    if not values:
        return {"avg": None, "samples": 0, "p50": None, "p70": None, "p85": None, "p95": None}
    block: dict[str, Any] = {
        "avg": _round(_mean(values)),
        "min": _round(min(values)),
        "max": _round(max(values)),
        "stdev": _round(_stdev(values)),
        "samples": len(values),
    }
    block.update(percentiles(values))
    return block


def _weekly_series(finished: Sequence[_Live], horizon: float) -> list[dict[str, Any]]:
    total_weeks = max(1, math.ceil(horizon / 7.0))
    buckets = [0] * total_weeks
    for item in finished:
        if item.delivered_at is None or item.delivered_at > horizon:
            continue
        index = min(total_weeks - 1, max(0, int(item.delivered_at // 7)))
        buckets[index] += 1
    return [{"week": index + 1, "delivered": value} for index, value in enumerate(buckets)]


# ---------------------------------------------------------------------------
# Experiments (SPEC §8.4, §8.5)
# ---------------------------------------------------------------------------


def _config_kwargs(config: Mapping[str, Any], baseline_flow: int | None) -> dict[str, Any]:
    """Translate one experiment row into :func:`simulate_flow` keyword arguments."""
    unlimited = bool(config.get("unlimited"))
    scale = config.get("scale")
    queue_scale = config.get("queue_scale")
    activity_scale = config.get("activity_scale")
    flow_scale = config.get("flow_scale", queue_scale if queue_scale is not None else scale)
    flow_max = config.get("flow_wip_max")
    if flow_max is None and not unlimited and baseline_flow:
        factor = float(flow_scale) if flow_scale is not None else 1.0
        flow_max = max(1, math.floor(baseline_flow * factor))
    return {
        "unlimited_wip": unlimited,
        "scale": float(scale) if scale is not None else None,
        "queue_scale": float(queue_scale) if queue_scale is not None else None,
        "activity_scale": float(activity_scale) if activity_scale is not None else None,
        "wip_limits": config.get("wip_limits"),
        "flow_wip_max": None if unlimited else (int(flow_max) if flow_max else None),
    }


def _default_wip_configs() -> list[dict[str, Any]]:
    """:data:`DEFAULT_WIP_CONFIGS` with their names in the caller's language."""
    return [{**entry, "name": _(entry["name"])} for entry in DEFAULT_WIP_CONFIGS]


def _experiment_row(
    name: str, result: SimulationResult, kwargs: Mapping[str, Any]
) -> dict[str, Any]:
    metrics = result.metrics
    lead = metrics["lead_time"]
    customer = metrics["customer_lead_time"]
    return {
        "name": name,
        "flow_wip_max": metrics.get("flow_wip_max"),
        "policy": {
            "unlimited": bool(kwargs.get("unlimited_wip")),
            "scale": kwargs.get("scale"),
            "queue_scale": kwargs.get("queue_scale"),
            "activity_scale": kwargs.get("activity_scale"),
            "flow_wip_max": kwargs.get("flow_wip_max"),
        },
        "lead_time_avg": lead.get("avg"),
        "lead_time_p50": lead.get("p50"),
        "lead_time_p85": lead.get("p85"),
        "lead_time_p95": lead.get("p95"),
        "customer_lead_time_avg": customer.get("avg"),
        "customer_lead_time_p85": customer.get("p85"),
        "throughput_per_week": metrics.get("throughput_per_week"),
        "flow_efficiency_pct": metrics.get("flow_efficiency_pct"),
        "avg_wip": metrics.get("avg_wip"),
        "avg_total_wip": metrics.get("avg_total_wip"),
        "delivered": metrics.get("delivered"),
        "unfinished": metrics.get("unfinished"),
        "bottleneck": (metrics.get("bottleneck") or {}).get("name"),
        "bottleneck_utilization_pct": metrics.get("bottleneck_utilization_pct"),
        "due_date_performance_pct": metrics.get("due_date_performance_pct"),
        "littles_law": metrics.get("littles_law"),
        "columns": metrics.get("columns"),
        "weekly_throughput": result.series.get("weekly_throughput", []),
        "lead_time_histogram": result.series.get("lead_time_histogram", []),
    }


def wip_experiment(
    columns: Sequence[Mapping[str, Any] | ColumnSpec],
    *,
    weeks: float = 16.0,
    arrival_rate: float = 8.0,
    class_mix: Mapping[str, float] | None = None,
    size_mix: Mapping[str, float] | None = None,
    seed: int = 20260816,
    configs: Sequence[Mapping[str, Any]] | None = None,
    baseline_flow_wip_max: int | None = None,
    service_times: Mapping[str, float] | None = None,
    capacities: Mapping[str, int] | None = None,
    target_utilization: float = 0.85,
    variability: float = 0.6,
    warmup_weeks: float | None = None,
) -> dict[str, Any]:
    """Run one demand stream through several WIP policies (SPEC §8.4).

    Every row sees byte-identical demand: the same cards arrive at the same
    moments carrying the same work content. Only the policy differs, so the
    comparison is causal rather than correlational.

    The headline result is Little's Law: as the whole-flow limit falls, system
    lead time falls with it in proportion while throughput holds — right up to
    the point where the limits drop below the team's capacity and the
    bottleneck starves.
    """
    specs = (
        list(columns)  # type: ignore[arg-type]
        if columns and isinstance(columns[0], ColumnSpec)
        else column_specs_from_rows(columns, service_times=service_times, capacities=capacities)  # type: ignore[arg-type]
    )
    stream = build_demand_stream(
        specs,
        weeks=weeks,
        arrival_rate=arrival_rate,
        class_mix=class_mix,
        size_mix=size_mix,
        seed=seed,
        variability=variability,
    )
    rows_config = list(configs) if configs else _default_wip_configs()
    rows: list[dict[str, Any]] = []
    for index, config in enumerate(rows_config):
        name = str(config.get("name") or _("Configuration {n}", n=index + 1))
        kwargs = _config_kwargs(config, baseline_flow_wip_max)
        result = simulate_flow(
            specs,
            stream=stream,
            seed=seed,
            service_times=service_times,
            capacities=capacities,
            target_utilization=target_utilization,
            warmup_weeks=warmup_weeks,
            label=name,
            collect_series=True,
            **kwargs,
        )
        rows.append(_experiment_row(name, result, kwargs))

    return {
        "kind": "wip_experiment",
        "seed": seed,
        "weeks": weeks,
        "arrival_rate": arrival_rate,
        "demand": {
            "cards": len(stream),
            "class_counts": stream.class_counts(),
            "arrivals_per_week": _round(len(stream) / max(1.0, float(weeks))),
        },
        "columns": [spec.as_dict() for spec in specs],
        "rows": rows,
        "littles_law": [
            {
                "name": row["name"],
                "avg_wip": row["avg_wip"],
                "throughput_per_week": row["throughput_per_week"],
                "avg_lead_time_days": row["lead_time_avg"],
                "predicted_wip": (row.get("littles_law") or {}).get("predicted_wip"),
                "coherence_ratio": (row.get("littles_law") or {}).get("coherence_ratio"),
            }
            for row in rows
        ],
        "verdict": _wip_verdict(rows),
        "chart": {
            "x": "avg_wip",
            "y": "lead_time_p85",
            "points": [
                {
                    "name": row["name"],
                    "avg_wip": row["avg_wip"],
                    "lead_time_p85": row["lead_time_p85"],
                    "throughput_per_week": row["throughput_per_week"],
                }
                for row in rows
            ],
        },
    }


def _wip_verdict(rows: Sequence[Mapping[str, Any]]) -> str:
    """A written read-out of the experiment, in the language of a coach."""
    usable = [row for row in rows if row.get("lead_time_avg") is not None]
    if len(usable) < 2:
        return _("Not enough delivered work to compare these policies.")
    loosest = max(usable, key=lambda row: row.get("avg_wip") or 0.0)
    best_throughput = max((row.get("throughput_per_week") or 0.0) for row in usable)
    # The best policy is the one with the shortest lead time that still keeps
    # throughput within 5% of the best any policy achieved. Anything below that
    # threshold is buying speed with delivery, which is not a trade worth making.
    healthy = [
        row for row in usable if (row.get("throughput_per_week") or 0.0) >= best_throughput * 0.95
    ]
    winner = min(healthy or usable, key=lambda row: row.get("lead_time_p85") or _INF)
    starved = [
        row for row in usable if (row.get("throughput_per_week") or 0.0) < best_throughput * 0.95
    ]

    parts: list[str] = []
    lead_drop = _pct_change(winner.get("lead_time_p85"), loosest.get("lead_time_p85"))
    wip_drop = _pct_change(winner.get("avg_wip"), loosest.get("avg_wip"))
    throughput_delta = _pct_change(
        winner.get("throughput_per_week"), loosest.get("throughput_per_week")
    )
    parts.append(
        _(
            "“{winner}” is the policy to run: 85th-percentile system lead time {lead} days "
            "against {loosest_lead} days under “{loosest}”{lead_change}, while throughput held "
            "at {throughput} items/week{throughput_change}.",
            winner=winner["name"],
            lead=num(winner.get("lead_time_p85")),
            loosest_lead=num(loosest.get("lead_time_p85")),
            loosest=loosest["name"],
            lead_change=delta_pct(lead_drop),
            throughput=num(winner.get("throughput_per_week")),
            throughput_change=delta_pct(throughput_delta),
        )
    )
    if wip_drop is not None:
        parts.append(
            _(
                "Average work in progress fell from {loosest_wip} to {wip} items ({change}%) — "
                "lead time tracked it almost exactly, which is Little's Law doing its job: the "
                "only way to finish sooner is to start less.",
                loosest_wip=num(loosest.get("avg_wip")),
                wip=num(winner.get("avg_wip")),
                change=num(wip_drop, "+.1f"),
            )
        )
    if starved:
        worst = min(starved, key=lambda row: row.get("throughput_per_week") or 0.0)
        if worst.get("bottleneck_utilization_pct") is not None:
            parts.append(
                _(
                    "Past that point the trade turns: “{policy}” cut throughput to {throughput} "
                    "items/week with the bottleneck ({bottleneck}) only {utilization}% busy — "
                    "the limits are now below the team's capacity and the board is starving.",
                    policy=worst["name"],
                    throughput=num(worst.get("throughput_per_week")),
                    bottleneck=worst.get("bottleneck"),
                    utilization=num(worst.get("bottleneck_utilization_pct")),
                )
            )
        else:
            parts.append(
                _(
                    "Past that point the trade turns: “{policy}” cut throughput to {throughput} "
                    "items/week — the limits are now below the team's capacity and the board is "
                    "starving.",
                    policy=worst["name"],
                    throughput=num(worst.get("throughput_per_week")),
                )
            )
    customer_change = _pct_change(
        winner.get("customer_lead_time_avg"), loosest.get("customer_lead_time_avg")
    )
    if customer_change is not None and abs(customer_change) < 12:
        parts.append(
            _(
                "Note the honest part: end-to-end customer lead time barely moved ({change}%). "
                "Tight WIP does not make the queue disappear, it moves it in front of the "
                "commitment point where it is still visible, still negotiable, and not yet a "
                "promise.",
                change=num(customer_change, "+.1f"),
            )
        )
    return " ".join(parts)


def class_of_service_experiment(
    columns: Sequence[Mapping[str, Any] | ColumnSpec],
    *,
    weeks: float = 16.0,
    arrival_rate: float = 8.0,
    seed: int = 20260816,
    expedite_shares: Sequence[float] = DEFAULT_EXPEDITE_SHARES,
    class_mix: Mapping[str, float] | None = None,
    size_mix: Mapping[str, float] | None = None,
    wip_limits: Mapping[str, Any] | None = None,
    flow_wip_max: int | None = None,
    service_times: Mapping[str, float] | None = None,
    capacities: Mapping[str, int] | None = None,
    target_utilization: float = 0.85,
    variability: float = 0.6,
    warmup_weeks: float | None = None,
    expedite_preemption: bool = True,
) -> dict[str, Any]:
    """Vary the expedite share and measure the damage to standard work (SPEC §8.5).

    One base stream is drawn with **no** expedite items; each share then promotes
    a deterministic subset of the *same* cards to expedite. Arrival times and
    work content never change, so the reported damage is caused purely by the
    priority policy — not by a different set of items showing up.
    """
    specs = (
        list(columns)  # type: ignore[arg-type]
        if columns and isinstance(columns[0], ColumnSpec)
        else column_specs_from_rows(columns, service_times=service_times, capacities=capacities)  # type: ignore[arg-type]
    )
    base_mix = dict(class_mix or DEFAULT_CLASS_MIX)
    base_mix.pop("expedite", None)
    base = build_demand_stream(
        specs,
        weeks=weeks,
        arrival_rate=arrival_rate,
        class_mix=base_mix,
        size_mix=size_mix,
        seed=seed,
        variability=variability,
    )
    # A fixed promotion order, drawn once: share 5% and share 20% promote nested
    # subsets, so the curve is a genuine dose-response rather than noise.
    order = list(range(len(base.cards)))
    random.Random(seed ^ 0x5EED).shuffle(order)

    rows: list[dict[str, Any]] = []
    for share in expedite_shares:
        share = min(1.0, max(0.0, float(share)))
        promote = set(order[: round(share * len(order))])
        cards = [
            DemandCard(
                index=card.index,
                arrival_day=card.arrival_day,
                service_class="expedite" if card.index in promote else card.service_class,
                size=card.size,
                demand_type=card.demand_type,
                value_area=card.value_area,
                factors=dict(card.factors),
                due_day=card.due_day,
            )
            for card in base.cards
        ]
        stream = DemandStream(
            cards=cards,
            weeks=base.weeks,
            arrival_rate=base.arrival_rate,
            seed=base.seed,
            column_keys=base.column_keys,
            label=f"expedite-{share:.2f}",
        )
        name = _("{share}% expedite", share=num(share * 100, 0))
        result = simulate_flow(
            specs,
            stream=stream,
            seed=seed,
            wip_limits=wip_limits,
            flow_wip_max=flow_wip_max,
            service_times=service_times,
            capacities=capacities,
            target_utilization=target_utilization,
            warmup_weeks=warmup_weeks,
            expedite_preemption=expedite_preemption,
            label=name,
            collect_series=True,
        )
        by_class = result.metrics["by_class"]
        standard = by_class.get("standard", {})
        expedite = by_class.get("expedite", {})
        rows.append(
            {
                "expedite_share": round(share, 4),
                "expedite_share_pct": round(share * 100, 1),
                "expedite_cards": len(promote),
                "name": name,
                "standard_lead_time_avg": standard.get("lead_time_avg"),
                "standard_lead_time_p85": standard.get("lead_time_p85"),
                "standard_delivered": standard.get("delivered"),
                "expedite_lead_time_avg": expedite.get("lead_time_avg"),
                "expedite_lead_time_p85": expedite.get("lead_time_p85"),
                "expedite_delivered": expedite.get("delivered"),
                "overall_lead_time_avg": result.metrics["lead_time"].get("avg"),
                "overall_lead_time_p85": result.metrics["lead_time"].get("p85"),
                "throughput_per_week": result.metrics.get("throughput_per_week"),
                "flow_efficiency_pct": result.metrics.get("flow_efficiency_pct"),
                "avg_wip": result.metrics.get("avg_wip"),
                "preemptions": result.metrics.get("preemptions"),
                "due_date_performance_pct": result.metrics.get("due_date_performance_pct"),
                "by_class": by_class,
            }
        )

    baseline = rows[0] if rows else None
    for row in rows:
        if baseline:
            row["standard_lead_time_delta_pct"] = _pct_change(
                row.get("standard_lead_time_p85"), baseline.get("standard_lead_time_p85")
            )
            row["throughput_delta_pct"] = _pct_change(
                row.get("throughput_per_week"), baseline.get("throughput_per_week")
            )
    return {
        "kind": "class_of_service_mix",
        "seed": seed,
        "weeks": weeks,
        "arrival_rate": arrival_rate,
        "expedite_preemption": expedite_preemption,
        "demand": {"cards": len(base), "class_counts": base.class_counts()},
        "columns": [spec.as_dict() for spec in specs],
        "rows": rows,
        "verdict": _class_verdict(rows),
        "chart": {
            "x": "expedite_share_pct",
            "y": "standard_lead_time_p85",
            "points": [
                {
                    "expedite_share_pct": row["expedite_share_pct"],
                    "standard_lead_time_p85": row["standard_lead_time_p85"],
                    "expedite_lead_time_p85": row["expedite_lead_time_p85"],
                    "throughput_per_week": row["throughput_per_week"],
                }
                for row in rows
            ],
        },
    }


def _class_verdict(rows: Sequence[Mapping[str, Any]]) -> str:
    usable = [row for row in rows if row.get("standard_lead_time_p85") is not None]
    if len(usable) < 2:
        return _("Not enough standard-class deliveries to measure the cost of expediting.")
    baseline = usable[0]
    worst = max(usable, key=lambda row: row.get("standard_lead_time_p85") or 0.0)
    change = _pct_change(
        worst.get("standard_lead_time_p85"), baseline.get("standard_lead_time_p85")
    )
    parts = [
        _(
            "Going from {from_share}% to {to_share}% expedite pushed the standard class's "
            "85th-percentile lead time from {from_lead} to {to_lead} days{change}.",
            from_share=num(baseline["expedite_share_pct"], 0),
            to_share=num(worst["expedite_share_pct"], 0),
            from_lead=num(baseline.get("standard_lead_time_p85")),
            to_lead=num(worst.get("standard_lead_time_p85")),
            change=delta_pct(change),
        )
    ]
    if worst.get("expedite_lead_time_p85") is not None:
        parts.append(
            _(
                "The expedited items themselves came through in {lead} days at the 85th "
                "percentile — the queue did not get shorter, it got reordered.",
                lead=num(worst.get("expedite_lead_time_p85")),
            )
        )
    extra_items = (worst.get("expedite_cards") or 0) - (baseline.get("expedite_cards") or 0)
    if extra_items > 0 and change:
        delay = (worst.get("standard_lead_time_p85") or 0) - (
            baseline.get("standard_lead_time_p85") or 0
        )
        per_item = delay / extra_items
        parts.append(
            _(
                "That is roughly {delay} days of delay imposed on the standard class for every "
                "extra item somebody called urgent.",
                delay=num(per_item, 2),
            )
        )
    throughput_change = _pct_change(
        worst.get("throughput_per_week"), baseline.get("throughput_per_week")
    )
    if throughput_change is not None:
        parts.append(
            _(
                "Total throughput moved {change}% — expediting does not create capacity, it only "
                "decides who waits.",
                change=num(throughput_change, "+.1f"),
            )
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Replay of a real board (SPEC §8 — "replay a real board under new policies")
# ---------------------------------------------------------------------------


def board_history(db: Any, board_id: str) -> dict[str, Any]:
    """Read the arrival pattern and observed dwell times of a real board.

    ``db`` is a synchronous SQLAlchemy ``Session`` (routers hand one over through
    ``AsyncSession.run_sync``); the persistence imports stay local to this reader.
    """
    from sqlalchemy import select

    from ..models import BoardColumn, Card, CardMovement

    columns = [
        dict(row._mapping)
        for row in db.execute(
            select(
                BoardColumn.id,
                BoardColumn.name,
                BoardColumn.position,
                BoardColumn.segment,
                BoardColumn.kind,
                BoardColumn.wip_min,
                BoardColumn.wip_max,
                BoardColumn.is_commitment_point,
                BoardColumn.is_delivery_point,
            )
            .where(BoardColumn.board_id == board_id)
            .order_by(BoardColumn.position)
        )
    ]
    if not columns:
        raise ValueError("That board has no columns to replay.")
    creations = [
        {"moved_at": moved_at}
        for moved_at in db.scalars(
            select(CardMovement.moved_at)
            .where(CardMovement.board_id == board_id, CardMovement.from_column_id.is_(None))
            .order_by(CardMovement.moved_at)
        )
    ]
    dwells = [
        {"column_id": column_id, "seconds": seconds}
        for column_id, seconds in db.execute(
            select(CardMovement.from_column_id, CardMovement.seconds_in_from_column).where(
                CardMovement.board_id == board_id,
                CardMovement.from_column_id.is_not(None),
                CardMovement.seconds_in_from_column.is_not(None),
                CardMovement.seconds_in_from_column > 0,
            )
        )
    ]
    deliveries = [
        {"delivered_at": delivered_at}
        for delivered_at in db.scalars(
            select(Card.delivered_at)
            .where(Card.board_id == board_id, Card.delivered_at.is_not(None))
            .order_by(Card.delivered_at)
        )
    ]

    moments = [moment for moment in (_parse_iso(row["moved_at"]) for row in creations) if moment]
    moments.sort()
    arrival_days: list[float] = []
    if moments:
        origin = moments[0]
        arrival_days = [(moment - origin).total_seconds() / 86400.0 for moment in moments]

    by_column: dict[str, list[float]] = {}
    for row in dwells:
        by_column.setdefault(str(row["column_id"]), []).append(float(row["seconds"]) / 86400.0)
    observed_service = {
        column_id: percentile(values, 50) or 0.0 for column_id, values in by_column.items()
    }

    span_days = arrival_days[-1] if arrival_days else 0.0
    weeks = max(1.0, span_days / 7.0)
    return {
        "columns": columns,
        "arrival_days": arrival_days,
        "weeks": weeks,
        "arrival_rate": (len(arrival_days) / weeks) if weeks else 0.0,
        "observed_service_days": observed_service,
        "observed_weekly_throughput": weekly_throughput_from_deliveries(
            [row["delivered_at"] for row in deliveries]
        ),
        "first_arrival_at": moments[0].isoformat().replace("+00:00", "Z") if moments else None,
        "last_arrival_at": moments[-1].isoformat().replace("+00:00", "Z") if moments else None,
    }


def replay(
    db: Any,
    board_id: str,
    *,
    policies: Sequence[Mapping[str, Any]] | None = None,
    seed: int = 20260816,
    weeks: float | None = None,
    use_observed_service_times: bool = True,
    target_utilization: float = 0.85,
    baseline_flow_wip_max: int | None = None,
    class_mix: Mapping[str, float] | None = None,
    warmup_weeks: float | None = None,
) -> dict[str, Any]:
    """Re-run a real board's own arrival pattern under different policies.

    The arrivals are the board's actual card-creation timestamps and the service
    times are its own observed median dwell per activity column, so the baseline
    row should land close to what the board really did. Every other row answers
    "what if we had run this policy instead, against exactly the demand we
    actually received".
    """
    history = board_history(db, board_id)
    service_times = history["observed_service_days"] if use_observed_service_times else None
    specs = column_specs_from_rows(history["columns"], service_times=service_times)
    arrival_days = history["arrival_days"]
    if not arrival_days:
        raise ValueError("That board has no card history to replay.")
    span_weeks = float(weeks) if weeks else float(history["weeks"])
    horizon = span_weeks * 7.0
    kept = [day for day in arrival_days if day <= horizon]
    if len(kept) < 2:
        # A shorter requested window than the board's history simply keeps the
        # most recent slice, which is the useful part of a long-lived board.
        offset = max(0.0, (arrival_days[-1] if arrival_days else 0.0) - horizon)
        kept = [day - offset for day in arrival_days if day >= offset]
    stream = build_demand_stream(
        specs,
        weeks=span_weeks,
        arrival_rate=history["arrival_rate"] or 1.0,
        class_mix=class_mix,
        seed=seed,
        arrival_days=kept,
    )

    rows_config = list(policies) if policies else _default_wip_configs()
    baseline_flow = baseline_flow_wip_max
    if baseline_flow is None:
        from sqlalchemy import select

        from ..models import Board

        wip_max = db.scalar(select(Board.wip_max).where(Board.id == board_id))
        baseline_flow = int(wip_max) if wip_max else None

    rows: list[dict[str, Any]] = []
    for index, config in enumerate(rows_config):
        name = str(config.get("name") or _("Policy {n}", n=index + 1))
        kwargs = _config_kwargs(config, baseline_flow)
        result = simulate_flow(
            specs,
            stream=stream,
            seed=seed,
            service_times=service_times,
            target_utilization=target_utilization,
            auto_calibrate=not use_observed_service_times,
            warmup_weeks=warmup_weeks,
            label=name,
            **kwargs,
        )
        rows.append(_experiment_row(name, result, kwargs))

    return {
        "kind": "replay",
        "seed": seed,
        "source_board_id": board_id,
        "weeks": _round(span_weeks),
        "demand": {
            "cards": len(stream),
            "class_counts": stream.class_counts(),
            "arrivals_per_week": _round(len(stream) / max(1.0, span_weeks)),
            "first_arrival_at": history["first_arrival_at"],
            "last_arrival_at": history["last_arrival_at"],
        },
        "observed": {
            "weekly_throughput": history["observed_weekly_throughput"],
            "throughput_per_week": _round(_mean(history["observed_weekly_throughput"] or [0])),
            "service_days": {
                key: _round(value, 3) for key, value in history["observed_service_days"].items()
            },
        },
        "columns": [spec.as_dict() for spec in specs],
        "rows": rows,
        "verdict": _replay_verdict(rows, history),
        "chart": {
            "x": "avg_wip",
            "y": "lead_time_p85",
            "points": [
                {
                    "name": row["name"],
                    "avg_wip": row["avg_wip"],
                    "lead_time_p85": row["lead_time_p85"],
                    "throughput_per_week": row["throughput_per_week"],
                }
                for row in rows
            ],
        },
    }


def _replay_verdict(rows: Sequence[Mapping[str, Any]], history: Mapping[str, Any]) -> str:
    if not rows:
        return _("No policy produced enough delivered work to judge.")
    observed = _mean(history.get("observed_weekly_throughput") or [])
    parts = [
        _(
            "Replayed {arrivals} real arrivals from this board through {policies} policies.",
            arrivals=len(history.get("arrival_days") or []),
            policies=len(rows),
        )
    ]
    if observed:
        parts.append(
            _(
                "The board itself delivered {throughput} items/week over the same demand.",
                throughput=num(observed, 1),
            )
        )
    parts.append(_wip_verdict(rows))
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Cross-run comparison (SPEC §8.6)
# ---------------------------------------------------------------------------

#: metric key -> (label, unit, lower_is_better). Labels are English catalog keys.
COMPARABLE_METRICS: tuple[tuple[str, str, str, bool], ...] = (
    ("lead_time_avg", N_("Lead time (avg)"), "days", True),
    ("lead_time_p85", N_("Lead time (85th)"), "days", True),
    ("customer_lead_time_avg", N_("Customer lead time (avg)"), "days", True),
    ("throughput_per_week", N_("Throughput"), "items/week", False),
    ("flow_efficiency_pct", N_("Flow efficiency"), "%", False),
    ("avg_wip", N_("Average WIP"), "items", True),
    ("delivered", N_("Delivered"), "items", False),
    ("due_date_performance_pct", N_("Due-date performance"), "%", False),
)


def _flatten_run(results: Mapping[str, Any]) -> dict[str, Any]:
    """Pull one comparable metric row out of any run kind's ``results`` blob."""
    kind = str(results.get("kind") or "")
    if kind in {"wip_experiment", "class_of_service_mix", "replay"}:
        rows = results.get("rows") or []
        if not rows:
            return {}
        # The representative row of an experiment is its recommended policy: the
        # fastest one that did not sacrifice throughput.
        usable = [row for row in rows if row.get("lead_time_p85") is not None]
        if not usable:
            return {}
        best_throughput = max((row.get("throughput_per_week") or 0.0) for row in usable)
        healthy = [
            row
            for row in usable
            if (row.get("throughput_per_week") or 0.0) >= best_throughput * 0.95
        ]
        chosen = min(healthy or usable, key=lambda row: row.get("lead_time_p85") or _INF)
        flat = {key: chosen.get(key) for key, _, _, _ in COMPARABLE_METRICS}
        if kind == "class_of_service_mix":
            flat["lead_time_avg"] = chosen.get("overall_lead_time_avg")
            flat["lead_time_p85"] = chosen.get("overall_lead_time_p85")
        flat["highlight"] = chosen.get("name")
        return flat
    if kind in {"monte_carlo_when", "monte_carlo_how_many"}:
        return {
            "forecast_p50": (results.get("percentiles") or {}).get("p50"),
            "forecast_p85": (results.get("percentiles") or {}).get("p85"),
            "forecast_p95": (results.get("percentiles") or {}).get("p95"),
            "unit": results.get("unit"),
            "highlight": results.get("sentence"),
        }
    if "metrics" in results:
        metrics = results["metrics"]
        return {
            "lead_time_avg": (metrics.get("lead_time") or {}).get("avg"),
            "lead_time_p85": (metrics.get("lead_time") or {}).get("p85"),
            "customer_lead_time_avg": (metrics.get("customer_lead_time") or {}).get("avg"),
            "throughput_per_week": metrics.get("throughput_per_week"),
            "flow_efficiency_pct": metrics.get("flow_efficiency_pct"),
            "avg_wip": metrics.get("avg_wip"),
            "delivered": metrics.get("delivered"),
            "due_date_performance_pct": metrics.get("due_date_performance_pct"),
        }
    return {}


def compare_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Align several stored runs into one table with deltas and a verdict.

    ``runs`` are ``simulation_runs`` rows whose ``results`` have already been
    decoded from JSON. The first run is the baseline every delta is measured
    against, because a comparison without a reference point is just a list.
    """
    if len(runs) < 2:
        raise ValueError("Comparing runs needs at least two of them.")
    flattened = [(run, _flatten_run(run.get("results") or {})) for run in runs]
    forecast_only = all(
        str((run.get("results") or {}).get("kind", "")).startswith("monte_carlo")
        for run, _ in flattened
    )

    metric_defs = (
        (
            ("forecast_p50", N_("Forecast (50%)"), "", False),
            ("forecast_p85", N_("Forecast (85%)"), "", False),
            ("forecast_p95", N_("Forecast (95%)"), "", False),
        )
        if forecast_only
        else COMPARABLE_METRICS
    )
    present = [
        definition
        for definition in metric_defs
        if any(flat.get(definition[0]) is not None for _, flat in flattened)
    ]

    baseline_run, baseline_flat = flattened[0]
    rows: list[dict[str, Any]] = []
    for key, label, unit, lower_is_better in present:
        cells = []
        base_value = baseline_flat.get(key)
        for run, flat in flattened:
            value = flat.get(key)
            delta = _pct_change(value, base_value)
            improved = None
            if delta is not None and delta != 0:
                improved = (delta < 0) if lower_is_better else (delta > 0)
            cells.append(
                {
                    "run_id": run.get("id"),
                    "run_name": run.get("name"),
                    "value": value,
                    "delta_pct": delta,
                    "improved": improved,
                }
            )
        rows.append(
            {
                "key": key,
                "label": _(label),
                "unit": unit,
                "lower_is_better": lower_is_better,
                "cells": cells,
                "baseline": base_value,
            }
        )

    deltas: dict[str, Any] = {}
    for run, flat in flattened[1:]:
        deltas[str(run.get("id"))] = {
            key: _pct_change(flat.get(key), baseline_flat.get(key)) for key, _, _, _ in present
        }

    return {
        "metrics": [definition[0] for definition in present],
        "rows": rows,
        "deltas": deltas,
        "baseline_run_id": baseline_run.get("id"),
        "verdict": _compare_verdict(flattened, present),
    }


def _compare_verdict(
    flattened: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    present: Sequence[tuple[str, str, str, bool]],
) -> str:
    baseline_run, baseline_flat = flattened[0]
    baseline_name = baseline_run.get("name") or _("the baseline run")
    keys = {definition[0] for definition in present}

    if "forecast_p85" in keys:
        parts = [_("Measured against “{baseline}”:", baseline=baseline_name)]
        for run, flat in flattened[1:]:
            delta = _pct_change(flat.get("forecast_p85"), baseline_flat.get("forecast_p85"))
            parts.append(
                _(
                    "“{run}” forecasts {forecast} at 85% confidence{change}.",
                    run=run.get("name"),
                    forecast=num(flat.get("forecast_p85")),
                    change=delta_pct(delta),
                )
            )
        return " ".join(parts)

    ranked = [
        (run, flat)
        for run, flat in flattened
        if flat.get("lead_time_p85") is not None and flat.get("throughput_per_week") is not None
    ]
    if not ranked:
        return _(
            "“{baseline}” is the only run with enough delivered work to compare.",
            baseline=baseline_name,
        )
    best_throughput = max((flat.get("throughput_per_week") or 0.0) for _, flat in ranked)
    healthy = [
        (run, flat)
        for run, flat in ranked
        if (flat.get("throughput_per_week") or 0.0) >= best_throughput * 0.95
    ]
    winner_run, winner_flat = min(
        healthy or ranked, key=lambda pair: pair[1].get("lead_time_p85") or _INF
    )
    lead_delta = _pct_change(winner_flat.get("lead_time_p85"), baseline_flat.get("lead_time_p85"))
    wip_delta = _pct_change(winner_flat.get("avg_wip"), baseline_flat.get("avg_wip"))
    throughput_delta = _pct_change(
        winner_flat.get("throughput_per_week"), baseline_flat.get("throughput_per_week")
    )
    if winner_run is baseline_run:
        return _(
            "“{baseline}” is still the best of these {runs} runs: {lead} days at the 85th "
            "percentile on {throughput} items/week. Nothing tried here beat it without costing "
            "delivery.",
            baseline=baseline_name,
            runs=len(flattened),
            lead=num(winner_flat.get("lead_time_p85")),
            throughput=num(winner_flat.get("throughput_per_week")),
        )
    values = {
        "winner": winner_run.get("name"),
        "lead": num(winner_flat.get("lead_time_p85")),
        "throughput": num(winner_flat.get("throughput_per_week")),
        "throughput_change": delta_pct(throughput_delta),
    }
    if lead_delta is not None:
        first = _(
            "“{winner}” wins: 85th-percentile lead time {lead} days ({change}% vs “{baseline}”) "
            "at {throughput} items/week{throughput_change}.",
            change=num(lead_delta, "+.1f"),
            baseline=baseline_name,
            **values,
        )
    else:
        first = _(
            "“{winner}” wins: 85th-percentile lead time {lead} days at {throughput} "
            "items/week{throughput_change}.",
            **values,
        )
    parts = [first]
    if wip_delta is not None:
        parts.append(
            _(
                "It carries {change}% work in progress to do it.",
                change=num(wip_delta, "+.1f"),
            )
        )
    if winner_flat.get("highlight"):
        parts.append(
            _(
                "Recommended policy inside that run: “{policy}”.",
                policy=winner_flat["highlight"],
            )
        )
    return " ".join(parts)
