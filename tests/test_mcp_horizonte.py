"""MCP `run_forecast`: the how-many horizon the API reads is `days` (fix of 17/10/2026).

The tool also takes `weeks` and `target_date`; it used to pass them through, and the
backend, which only reads `days`, answered 400.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "mcp" / "nagare_mcp.py"
_SPEC = importlib.util.spec_from_file_location("nagare_mcp", _PATH)
assert _SPEC and _SPEC.loader
mcp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mcp)


class _Client:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.sent = body
        return {"id": "run-1", "results": {"percentiles": {}}}


def _days(**args: Any) -> int:
    client = _Client()
    mcp.tool_run_forecast(client, {"board_id": "b1", "kind": "monte_carlo_how_many", **args})
    params = client.sent["params"]
    assert "weeks" not in params and "target_date" not in params
    return params["days"]


def test_weeks_and_target_date_become_days() -> None:
    assert _days(days=10) == 10
    assert _days(weeks=4) == 28
    assert _days(target_date="2026-11-01", start_date="2026-10-17") == 15


def test_a_horizon_before_the_start_is_refused() -> None:
    with pytest.raises(mcp.ToolError):
        _days(target_date="2026-10-01", start_date="2026-10-17")
    with pytest.raises(mcp.ToolError):
        _days(target_date="17/10/2026")
