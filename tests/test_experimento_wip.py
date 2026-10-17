"""The WIP experiment and its verdict (fix of 17/10/2026).

- A card that arrives upstream of the commitment point is still an option: it must not
  hold a slot of the whole-flow limit (it used to, and was counted again on commitment,
  so any flow limit starved the board and "No WIP limits" always won).
- The verdict measures against the row without limits and never compares a policy
  with itself: when no limit shortens lead time without costing throughput, it says so.
"""

from __future__ import annotations

from typing import Any

from backend.app.flow import forecast
from backend.app.texts import PT_BR, current_locale, set_locale


def _column(position: int, name: str, kind: str, wip: int | None = None, **flags: Any) -> dict:
    return {"position": position, "name": name, "kind": kind, "wip_max": wip, **flags}


COLUMNS = [
    _column(0, "Options", "queue", segment="upstream"),
    _column(1, "Ready", "queue", 5, is_commitment_point=True),
    _column(2, "Analysis", "activity", 3),
    _column(3, "Dev", "activity", 4),
    _column(4, "Review", "queue", 3),
    _column(5, "Test", "activity", 2),
    _column(6, "Done", "done", is_delivery_point=True),
]


def test_upstream_options_do_not_hold_flow_slots() -> None:
    specs = forecast.column_specs_from_rows(COLUMNS)

    def run(**policy: Any) -> forecast.SimulationResult:
        # Standard work only: an expedite card may cross the flow limit by design.
        stream = forecast.build_demand_stream(
            specs, weeks=16, arrival_rate=5, seed=20260816, class_mix={"standard": 1.0}
        )
        return forecast.simulate_flow(specs, stream=stream, seed=20260816, **policy)

    limited = run(flow_wip_max=12)
    unlimited = run(unlimited_wip=True)
    assert limited.metrics["peak_flow_wip"] <= 12
    # The board policy (12 in flow) keeps the delivery of the unlimited board.
    assert limited.metrics["throughput_per_week"] >= (
        unlimited.metrics["throughput_per_week"] * 0.95
    )


def test_the_verdict_measures_against_no_limits() -> None:
    result = forecast.wip_experiment(
        COLUMNS, arrival_rate=8, seed=20260816, baseline_flow_wip_max=12
    )
    verdict = result["verdict"]
    assert "is the policy to run" in verdict
    assert "under “No WIP limits”" in verdict
    winner = verdict.split("”")[0].lstrip("“")
    assert winner != "No WIP limits"


def test_without_a_gain_the_verdict_says_the_limits_make_no_difference() -> None:
    result = forecast.wip_experiment(
        COLUMNS, arrival_rate=3, seed=20260816, baseline_flow_wip_max=12
    )
    verdict = result["verdict"]
    assert verdict.startswith("With this demand no WIP limit shortened"), verdict
    assert "(+0.0%)" not in verdict and "is the policy to run" not in verdict
    assert "Tighter limits only starve it" in verdict
    assert verdict.endswith("test again with more arrivals per week.")


def test_the_no_gain_verdict_in_portuguese() -> None:
    token = set_locale(PT_BR)
    try:
        result = forecast.wip_experiment(
            COLUMNS, arrival_rate=3, seed=20260816, baseline_flow_wip_max=12
        )
    finally:
        current_locale.reset(token)
    verdict = result["verdict"]
    assert verdict.startswith("Com essa demanda, nenhum limite de WIP encurtou"), verdict
    assert "“Sem limites de WIP”" in verdict
    assert verdict.endswith("mais chegadas por semana.")

