"""Kanban Maturity Model readiness ladder (SPEC §7.2).

David J. Anderson's Kanban Maturity Model (KMM) describes seven levels — ML0
"Oblivious" through ML6 "Congruent" — each defined by *what a team can measure
and act on*, not by how long they have used a board. Rodrigo Yoshima's Brazilian
Kanban coaching practice adds the same discipline in plain language: a metric
only counts once the team actually reads it in a real ritual (daily walk,
replenishment, delivery review), not once the software merely computes it.

This module never invents data. Every ``available`` flag is derived from what
:mod:`backend.app.flow.metrics` can *actually* compute for this board right now
— a board with no delivered cards is honestly ML1 at best, no matter how many
columns it has. That is also why this module takes a live
:class:`~backend.app.flow.metrics.MetricsContext` rather than recomputing
anything: the KMM ladder is a read-out of the same snapshot the other metric
endpoints already agree on.

``readiness_pct`` is ``available / total`` for a level's metric list. ML0 is
special: "Oblivious" means *no* measurement is expected, so it has no metric
list and is defined as always satisfied (``100%``) — every board that runs this
software has already left it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..db import iso
from ..texts import N_, _, on_platform
from .metrics import (
    LOW_CONFIDENCE_SAMPLE,
    MetricsContext,
)
from .metrics import (
    blockers as _blockers,
)
from .metrics import (
    cfd as _cfd,
)
from .metrics import (
    demand as _demand,
)
from .metrics import (
    distribution as _distribution,
)
from .metrics import (
    flow_efficiency as _flow_efficiency,
)
from .metrics import (
    littles_law as _littles_law,
)
from .metrics import (
    summary as _summary,
)
from .metrics import (
    throughput as _throughput,
)

__all__ = ["LEVEL_ORDER", "compute_kmm"]

#: The threshold SPEC §7.2 sets for a level to count as the team's current one.
READINESS_THRESHOLD = 80.0

LEVEL_ORDER: tuple[str, ...] = ("ML0", "ML1", "ML2", "ML3", "ML4", "ML5", "ML6")


@dataclass(slots=True)
class _Snapshot:
    """Everything the ladder reads, computed once and shared by every level."""

    ctx: MetricsContext
    summary: dict[str, Any]
    blockers: dict[str, Any]
    demand: dict[str, Any]
    distribution: dict[str, Any]
    flow_efficiency: dict[str, Any]
    littles_law: dict[str, Any]
    cfd: dict[str, Any]
    throughput: dict[str, Any]
    operational_board_count: int
    wip_experiment_count: int

    @property
    def metrics(self) -> dict[str, dict[str, Any]]:
        return self.summary["metrics"]


def _build_snapshot(ctx: MetricsContext) -> _Snapshot:
    board_count = ctx.operational_board_count
    wip_experiments = ctx.wip_experiment_count
    return _Snapshot(
        ctx=ctx,
        summary=_summary(ctx),
        blockers=_blockers(ctx),
        demand=_demand(ctx),
        distribution=_distribution(ctx),
        flow_efficiency=_flow_efficiency(ctx),
        littles_law=_littles_law(ctx),
        cfd=_cfd(ctx),
        throughput=_throughput(ctx),
        operational_board_count=board_count,
        wip_experiment_count=wip_experiments,
    )


@dataclass(slots=True)
class _MetricSpec:
    """One KMM ladder metric: how to tell if the data supports it, and why it matters."""

    key: str
    label: str
    why: str
    action: str
    check: Callable[[_Snapshot], tuple[bool, Any]]


def _mval(snapshot: _Snapshot, key: str) -> dict[str, Any]:
    return snapshot.metrics.get(key, {})


# ---------------------------------------------------------------------------
# ML1 — Emerging: the board exists, work is visible, WIP can be counted.
# ---------------------------------------------------------------------------


def _ml1_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="board_in_use",
            label=_("Board in use"),
            why=_(
                "Anderson's first rung asks only that work be pulled onto a visual board "
                "at all — Yoshima calls this simply colocar o trabalho na parede (put the "
                "work on the wall), the precondition every later metric depends on."
            ),
            action=_("Put real, current work onto this board — an empty board cannot be measured."),
            check=lambda s: (
                len(s.ctx.all_columns) >= 2 and len(s.ctx.records) >= 1,
                {"columns": len(s.ctx.all_columns), "cards": len(s.ctx.records)},
            ),
        ),
        _MetricSpec(
            key="wip_count",
            label=_("WIP count"),
            why=_(
                "Reading the number of items currently in progress is Anderson's ML1 "
                "threshold metric — the first number a team can state without any "
                "movement history at all."
            ),
            action=_(
                "Mark one column as the commitment point and one as the delivery point so "
                "work-in-progress has a clear boundary to be counted against."
            ),
            check=lambda s: (
                s.ctx.commitment_column is not None and s.ctx.delivery_column is not None,
                _mval(s, "wip").get("value"),
            ),
        ),
        _MetricSpec(
            key="cards_visualised",
            label=_("Cards visualised"),
            why=_(
                "Representing each unit of work as its own card — not a batched checklist "
                "— is what turns a board into a kanban system rather than a generic to-do "
                "list, per Anderson's ML1 definition."
            ),
            action=_("Add the team's actual work items as individual cards on the board."),
            check=lambda s: (len(s.ctx.records) >= 1, len(s.ctx.records)),
        ),
    ]


# ---------------------------------------------------------------------------
# ML2 — Defined: averages, aging and classification exist.
# ---------------------------------------------------------------------------


def _ml2_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="lead_time_avg",
            label=_("Average lead time"),
            why=_(
                "Computing a mean lead time from real movement timestamps — not a guess — "
                "is Anderson's ML2 threshold: the first quantitative promise a team can "
                "make to a customer."
            ),
            action=_("Deliver at least one card through the board so a lead time can be observed."),
            check=lambda s: (
                _mval(s, "customer_lead_time").get("sample_size", 0) >= 1,
                _mval(s, "customer_lead_time").get("value"),
            ),
        ),
        _MetricSpec(
            key="throughput",
            label=_("Throughput"),
            why=_(
                "Counting delivered items per period gives Defined-stage teams their first "
                "capacity signal — what Yoshima's coaching practice calls o ritmo do time "
                "(the team's rhythm)."
            ),
            action=_("Deliver work regularly enough that a per-week count becomes meaningful."),
            check=lambda s: (
                _mval(s, "throughput").get("sample_size", 0) >= 1,
                _mval(s, "throughput").get("value"),
            ),
        ),
        _MetricSpec(
            key="wip_aging",
            label=_("WIP aging"),
            why=_(
                "Watching how long committed items have already been sitting — aging WIP, "
                "not just a headcount — is Defined-stage evidence that the team tracks "
                "flow, not only completion."
            ),
            action=_("Commit at least one card and leave it in flight so its age can be tracked."),
            check=lambda s: (
                len(s.ctx.in_flight_at()) >= 1,
                len(s.ctx.in_flight_at()),
            ),
        ),
        _MetricSpec(
            key="blocker_count",
            label=_("Blocker count"),
            why=_(
                "Recording blockers explicitly, with a start and end time, is what turns "
                "'we were stuck' into a number Anderson's ML2 teams can act on."
            ),
            action=_(
                "Log blockers on cards as they happen, with a category, instead of only in conversation."
            ),
            check=lambda s: (
                len(s.ctx.blocker_rows) >= 1,
                s.blockers["meta"].get("blocker_count"),
            ),
        ),
        _MetricSpec(
            key="work_item_types",
            label=_("Work-item types"),
            why=_(
                "Distinguishing types of demand — feature, defect, risk, debt, compliance "
                "— is Anderson's ML2 requirement for classifying work before you can "
                "improve how it flows."
            ),
            action=_(
                "Tag delivered cards with more than one value area or demand type as they actually differ."
            ),
            check=lambda s: (
                len(s.demand["meta"].get("value_area_mix", [])) >= 2
                or len(s.demand["meta"].get("demand_type_mix", [])) >= 2,
                s.demand["meta"].get("value_area_mix"),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# ML3 — Managed: distributions, SLE, CFD, service classes, flow efficiency.
# ---------------------------------------------------------------------------


def _ml3_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="lead_time_distribution",
            label=_("Lead-time distribution & percentiles"),
            why=_(
                "Reporting the full percentile spread (50/70/85/95), not just the mean, is "
                "Anderson's ML3 requirement — averages hide the tail that actually breaks "
                "customer promises."
            ),
            action=_(
                "Deliver at least {n} cards in the window so the "
                "percentile spread stops being a guess.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                s.distribution.get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                s.distribution.get("percentiles"),
            ),
        ),
        _MetricSpec(
            key="sle_attainment",
            label=_("SLE & attainment"),
            why=_(
                "A committed Service Level Expectation with a measured attainment rate is "
                "the customer-facing promise Anderson's Managed stage exists to keep."
            ),
            action=_(
                "Set a board SLE (days + target percentile) and deliver enough cards to "
                "measure attainment against it."
            ),
            check=lambda s: (
                bool(s.ctx.board.get("sle_days"))
                and _mval(s, "sle_attainment").get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                _mval(s, "sle_attainment").get("value"),
            ),
        ),
        _MetricSpec(
            key="cfd",
            label=_("Cumulative flow diagram"),
            why=_(
                "The CFD is Anderson's canonical Managed-stage instrument: it exposes WIP, "
                "lead time and where work is piling up in one picture instead of three "
                "separate numbers."
            ),
            action=_(
                "Accumulate at least {n} cards of movement history so "
                "the cumulative bands are readable rather than noise.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                s.cfd.get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE and bool(s.cfd.get("series")),
                s.cfd["meta"].get("approx_lead_time_avg_days"),
            ),
        ),
        _MetricSpec(
            key="due_date_performance",
            label=_("Due-date performance"),
            why=_(
                "Measuring on-time delivery for fixed-date commitments is how Managed-"
                "stage teams prove a class-of-service policy actually works, not just "
                "exists on paper."
            ),
            action=_("Deliver at least one fixed_date card against a due_date."),
            check=lambda s: (
                s.demand["meta"]["due_date_performance"].get("total", 0) >= 1,
                s.demand["meta"]["due_date_performance"].get("pct"),
            ),
        ),
        _MetricSpec(
            key="class_of_service_mix",
            label=_("Class-of-service mix"),
            why=_(
                "Quantifying the delivered mix across expedite, standard, fixed-date and "
                "intangible is what lets a Managed-stage team see whether its service "
                "classes are a real policy or just labels."
            ),
            action=_("Deliver cards under at least two different service classes."),
            check=lambda s: (
                len(s.demand["meta"].get("service_class_mix", [])) >= 2,
                s.demand["meta"].get("service_class_mix"),
            ),
        ),
        _MetricSpec(
            key="flow_efficiency",
            label=_("Flow efficiency"),
            why=_(
                "Splitting lead time into touch and wait time is Anderson's Managed-stage "
                "diagnostic for where to intervene — Yoshima teaches Brazilian teams to "
                "attack the queue first, not ask people to work faster."
            ),
            action=_(
                "Deliver at least {n} cards through activity and "
                "queue columns so the touch/wait split is trustworthy.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                s.flow_efficiency.get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                s.flow_efficiency["meta"].get("flow_efficiency"),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# ML4 — Quantitatively Managed: forecasting, variability, failure demand, debt.
# ---------------------------------------------------------------------------


def _ml4_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="monte_carlo_forecast",
            label=_("Monte Carlo forecasts"),
            why=_(
                "Probabilistic forecasting from resampled weekly throughput is Anderson's "
                "Quantitatively Managed hallmark: a date that comes with a stated "
                "confidence instead of a single-point guess."
            ),
            action=_(
                "Accumulate at least {n} weeks of delivered throughput history to resample from.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                s.throughput["meta"].get("periods", 0) >= LOW_CONFIDENCE_SAMPLE
                and s.throughput["meta"].get("total", 0) > 0,
                s.throughput["meta"].get("mean"),
            ),
        ),
        _MetricSpec(
            key="throughput_variability",
            label=_("Throughput variability"),
            why=_(
                "Tracking the coefficient of variation of weekly throughput tells a "
                "Quantitatively Managed team whether its own forecasts can be trusted "
                "before it ships them to a stakeholder."
            ),
            action=_(
                "Keep delivering for {n}+ weeks so variability is "
                "measured over a real sample, not a handful of lucky weeks.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                _mval(s, "throughput_variability").get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                _mval(s, "throughput_variability").get("value"),
            ),
        ),
        _MetricSpec(
            key="failure_demand",
            label=_("Failure demand %"),
            why=_(
                "Separating failure demand from value demand is Anderson's Quantitatively "
                "Managed requirement for pricing the true cost of poor quality upstream."
            ),
            action=_(
                "Tag at least {n} delivered cards with a demand_type "
                "so failure demand has a denominator worth trusting.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                _mval(s, "failure_demand").get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                _mval(s, "failure_demand").get("value"),
            ),
        ),
        _MetricSpec(
            key="flow_debt",
            label=_("Flow debt (expedite impact)"),
            why=_(
                "Quantifying flow debt — how much slower standard work moves because of "
                "expedite interruptions — is what lets Quantitatively Managed teams price "
                "their own class-of-service policy instead of assuming it is free."
            ),
            action=_(
                "Deliver enough expedite and standard cards that both have a measurable p85 lead time."
            ),
            check=lambda s: (
                bool(s.demand["meta"]["flow_debt"].get("available")),
                s.demand["meta"]["flow_debt"].get("ratio"),
            ),
        ),
        _MetricSpec(
            key="discovery_lead_time",
            label=_("Discovery (upstream) lead time"),
            why=_(
                "Measuring upstream discovery lead time alongside delivery lead time is "
                "Anderson's Quantitatively Managed step toward managing the whole value "
                "stream, not just the build phase."
            ),
            action=_(
                "Commit at least {n} cards that first spent time "
                "upstream so discovery lead time has a real sample.",
                n=LOW_CONFIDENCE_SAMPLE,
            ),
            check=lambda s: (
                _mval(s, "discovery_lead_time").get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE,
                _mval(s, "discovery_lead_time").get("value"),
            ),
        ),
        _MetricSpec(
            key="littles_law_coherence",
            label=_("Little's Law coherence"),
            why=_(
                "Checking that observed WIP, throughput and lead time agree via Little's "
                "Law is the statistical proof Anderson demands before anyone trusts a "
                "forecast built on top of them."
            ),
            action=_(
                "Deliver a stable, non-empty stream of work so WIP, throughput and lead time can be reconciled."
            ),
            check=lambda s: (
                s.littles_law["meta"].get("coherence_ratio") is not None,
                s.littles_law["meta"].get("coherence_ratio"),
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# ML5 — Optimizing: end-to-end view, bottlenecks, cross-board comparison, WIP experiments.
# ---------------------------------------------------------------------------


def _ml5_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="envelope_to_delivery_lead_time",
            label=_("Envelope-to-delivery lead time"),
            why=_(
                "Measuring the full envelope-to-delivery lead time — including sealed "
                "pre-discovery time — is Anderson's Optimizing-stage view of the entire "
                "customer promise, not only the visible part of the flow."
            ),
            action=_(
                "Turn on 'count sealed-envelope time in metrics' for this board and "
                "deliver cards that passed through the envelope."
            ),
            check=lambda s: (
                bool(s.ctx.include_envelope)
                and _mval(s, "customer_lead_time").get("sample_size", 0) >= LOW_CONFIDENCE_SAMPLE
                and s.flow_efficiency["meta"].get("envelope_days", 0) > 0,
                s.flow_efficiency["meta"].get("envelope_days"),
            ),
        ),
        _MetricSpec(
            key="bottleneck_queue_analysis",
            label=_("Bottleneck / queue analysis"),
            why=_(
                "Ranking queues by their wait-time Pareto to find the single worst offender "
                "is Anderson's Optimizing-stage discipline of fixing the constraint instead "
                "of everything at once."
            ),
            action=_(
                "Deliver enough cards that a wait-time Pareto across queue columns can be ranked."
            ),
            check=lambda s: (
                bool(s.flow_efficiency["meta"].get("worst_queue")),
                s.flow_efficiency["meta"].get("worst_queue"),
            ),
        ),
        _MetricSpec(
            key="capability_comparison",
            label=_("Cross-board capability comparison"),
            why=_(
                "Comparing lead-time capability across boards — or before/after a policy "
                "change via a cloned simulator board — is Anderson's Optimizing-stage "
                "practice of treating the process itself as an experiment."
            ),
            action=_("Stand up at least one more operational board so capability can be compared."),
            check=lambda s: (
                s.operational_board_count >= 2,
                s.operational_board_count,
            ),
        ),
        _MetricSpec(
            key="wip_experiment_outcomes",
            label=_("WIP experiment outcomes"),
            why=_(
                "Running a WIP experiment and reading the resulting Little's-Law delta is "
                "the Optimizing-stage proof that lowering WIP shortens lead time — "
                "Anderson's central kanban claim made empirical rather than theoretical."
            ),
            # The simulator is a Nagare-only screen: inside the Pampufa it is not there.
            action=(
                _("Run a WIP experiment in the Nagare simulator.")
                if on_platform()
                else _("Clone this board into the simulator and run a wip_experiment scenario.")
            ),
            check=lambda s: (
                s.wip_experiment_count >= 1,
                s.wip_experiment_count,
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# ML6 — Congruent: portfolio roll-up, business-outcome alignment.
# ---------------------------------------------------------------------------


def _ml6_specs() -> list[_MetricSpec]:
    return [
        _MetricSpec(
            key="portfolio_rollup",
            label=_("Portfolio roll-up across projects"),
            why=_(
                "Rolling flow metrics up across every project onto one portfolio view is "
                "Anderson's Congruent-stage requirement — flow data that speaks to the "
                "business rather than only to the delivery team."
            ),
            action=_(
                "Deliver work under at least two distinct projects so a portfolio roll-up is possible."
            ),
            check=lambda s: (
                sum(1 for row in s.demand["meta"].get("per_project", []) if row.get("delivered"))
                >= 2,
                len(s.demand["meta"].get("per_project", [])),
            ),
        ),
        _MetricSpec(
            key="business_outcome_alignment",
            label=_("Business-outcome alignment"),
            why=_(
                "Tying flow metrics to explicit business-value categories — feature, "
                "defect, risk, debt, compliance — is Anderson's Congruent-stage bridge "
                "between kanban mechanics and business outcomes, the same dashboard "
                "Yoshima insists Brazilian leadership and delivery teams should both read."
            ),
            action=_("Classify delivered work across at least three distinct value areas."),
            check=lambda s: (
                len(s.demand["meta"].get("value_area_mix", [])) >= 3,
                s.demand["meta"].get("value_area_mix"),
            ),
        ),
    ]


#: (level, name, focus, metric specs). Names and focus are English catalog keys,
#: translated when the ladder is computed.
_LEVEL_DEFS: tuple[tuple[str, str, str, Callable[[], list[_MetricSpec]] | None], ...] = (
    ("ML0", N_("Oblivious"), N_("No measurement at all — the pre-kanban baseline."), None),
    ("ML1", N_("Emerging"), N_("Visualise the work and count what is in progress."), _ml1_specs),
    (
        "ML2",
        N_("Defined"),
        N_("Average lead time, throughput, aging and classification."),
        _ml2_specs,
    ),
    (
        "ML3",
        N_("Managed"),
        N_("Distributions, service level expectations, CFD and flow efficiency."),
        _ml3_specs,
    ),
    (
        "ML4",
        N_("Quantitatively Managed"),
        N_("Probabilistic forecasts, variability, failure demand and flow debt."),
        _ml4_specs,
    ),
    (
        "ML5",
        N_("Optimizing"),
        N_("End-to-end lead time, bottleneck analysis and controlled experiments."),
        _ml5_specs,
    ),
    (
        "ML6",
        N_("Congruent"),
        N_("Portfolio roll-up and alignment between flow and business outcomes."),
        _ml6_specs,
    ),
)


def _level_payload(
    level: str, name: str, focus: str, specs: Sequence[_MetricSpec], snapshot: _Snapshot | None
) -> dict[str, Any]:
    if snapshot is None or not specs:
        # ML0: nothing is expected to be measured, so it is trivially satisfied.
        return {
            "level": level,
            "name": name,
            "focus": focus,
            "metrics": [],
            "readiness_pct": 100.0,
        }

    metrics: list[dict[str, Any]] = []
    available_count = 0
    for spec in specs:
        available, value = spec.check(snapshot)
        available = bool(available)
        if available:
            available_count += 1
        metrics.append(
            {
                "key": spec.key,
                "label": spec.label,
                "available": available,
                "value": value,
                "why": spec.why,
            }
        )
    readiness = round(available_count / len(specs) * 100.0, 1)
    return {
        "level": level,
        "name": name,
        "focus": focus,
        "metrics": metrics,
        "readiness_pct": readiness,
    }


def _next_actions(
    levels: list[dict[str, Any]],
    specs_by_level: dict[str, list[_MetricSpec]],
    current_index: int,
) -> list[str]:
    """Concrete steps to reach the next level, or to finish the current one."""
    actions: list[str] = []

    def _unavailable_actions(level_index: int) -> list[str]:
        if level_index >= len(levels):
            return []
        level = levels[level_index]
        specs = specs_by_level.get(level["level"], [])
        by_key = {spec.key: spec for spec in specs}
        out: list[str] = []
        for metric in level["metrics"]:
            if metric["available"]:
                continue
            spec = by_key.get(metric["key"])
            if spec is None:
                continue
            out.append(f"[{level['level']}] {spec.label}: {spec.action}")
        return out

    # Finish anything still missing at the current level (readiness can be
    # "current" at 80-99%, not necessarily 100%).
    actions.extend(_unavailable_actions(current_index))
    # Then reach for the next rung.
    if current_index + 1 < len(levels):
        actions.extend(_unavailable_actions(current_index + 1))

    if not actions:
        if current_index >= len(levels) - 1:
            actions.append(
                _(
                    "ML6 Congruent is fully satisfied — keep monitoring so a regression in any "
                    "metric is caught before it erodes the ladder."
                )
            )
        else:
            actions.append(
                _(
                    "{level} is fully satisfied; no further data gaps block the next level.",
                    level=levels[current_index]["name"],
                )
            )
    # A dashboard, not a novel: cap the list at something a person will read.
    return actions[:6]


def compute_kmm(ctx: MetricsContext) -> dict[str, Any]:
    """The full ML0..ML6 ladder for one board (SPEC §7.2).

    Returns ``{levels, current_level, overall_readiness_pct, next_actions,
    board_id, generated_at}``. ``current_level`` is the highest level whose
    ``readiness_pct`` is at least :data:`READINESS_THRESHOLD`; ``next_actions``
    names the concrete data gaps standing between the board and the level above.
    """
    snapshot = _build_snapshot(ctx)
    specs_by_level: dict[str, list[_MetricSpec]] = {}
    levels: list[dict[str, Any]] = []

    for level, name, focus, factory in _LEVEL_DEFS:
        specs = factory() if factory else []
        specs_by_level[level] = specs
        levels.append(_level_payload(level, _(name), _(focus), specs, snapshot if specs else None))

    # A maturity model is cumulative by definition: the team has not reached
    # level N while any level below N is unmet. Walk up from ML0 and stop at
    # the first gap rather than taking the last level that happens to clear
    # the bar on its own.
    current_index = 0
    for index, payload in enumerate(levels):
        if payload["readiness_pct"] >= READINESS_THRESHOLD:
            current_index = index
        else:
            break

    next_level = None
    if current_index + 1 < len(levels):
        next_level = levels[current_index + 1]["level"]

    overall = round(sum(level["readiness_pct"] for level in levels) / len(levels), 1)

    return {
        "board_id": ctx.board_id,
        "board_name": ctx.board.get("name"),
        "levels": levels,
        "current_level": levels[current_index]["level"],
        "current_level_name": levels[current_index]["name"],
        "next_level": next_level,
        "overall_readiness_pct": overall,
        "next_actions": _next_actions(levels, specs_by_level, current_index),
        "filters": ctx.filters(),
        "generated_at": iso(ctx.now),
    }
