"""
Flow metrics package for analyzing and forecasting work flow through kanban boards.

Provides interval calculation, flow metrics computation, and Monte Carlo simulation
for forecasting and experimentation.
"""

from .intervals import (
    Interval,
    TouchWait,
    LeadTimes,
    CardFlow,
    card_intervals,
    bulk_card_intervals,
    touch_wait,
    flow_start,
    lead_times,
    board_flows,
    first_reach_times,
    reached_by,
)

from .metrics import (
    MetricsContext,
    CardRecord,
    percentile,
    percentile_set,
    summary,
    cfd,
    scatter,
    throughput,
    aging,
    flow_efficiency,
    distribution,
    blockers,
    demand,
    littles_law,
)

from .forecast import (
    SimulationResult,
    ColumnSpec,
    DemandCard,
    DemandStream,
    percentiles,
    histogram,
    monte_carlo_when,
    monte_carlo_how_many,
    wip_experiment,
    class_of_service_experiment,
    board_history,
    replay,
    compare_runs,
    simulate_flow,
    column_specs_from_rows,
    build_demand_stream,
    weekly_throughput_from_deliveries,
)

__all__ = [
    # intervals.py
    "Interval",
    "TouchWait",
    "LeadTimes",
    "CardFlow",
    "card_intervals",
    "bulk_card_intervals",
    "touch_wait",
    "flow_start",
    "lead_times",
    "board_flows",
    "first_reach_times",
    "reached_by",
    # metrics.py
    "MetricsContext",
    "CardRecord",
    "percentile",
    "percentile_set",
    "summary",
    "cfd",
    "scatter",
    "throughput",
    "aging",
    "flow_efficiency",
    "distribution",
    "blockers",
    "demand",
    "littles_law",
    # forecast.py
    "SimulationResult",
    "ColumnSpec",
    "DemandCard",
    "DemandStream",
    "percentiles",
    "histogram",
    "monte_carlo_when",
    "monte_carlo_how_many",
    "wip_experiment",
    "class_of_service_experiment",
    "board_history",
    "replay",
    "compare_runs",
    "simulate_flow",
    "column_specs_from_rows",
    "build_demand_stream",
    "weekly_throughput_from_deliveries",
]
