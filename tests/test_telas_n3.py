"""N3: the React screens replace the old one, and the three defects found on the way.

- The standalone Nagare serves the React shell at ``/`` and on every non-API path
  (decision of 17/10/2026: one build, two modes; the old ``frontend/`` is gone).
- A simulator board can start blank (the old screen sent the default template).
- Comparing runs honours the baseline the person picks (the router used to drop it).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from sqlhelper import Database

API = "/api/v1"
MC_SAMPLES = [3, 5, 4, 6, 2, 5, 4, 3]


@pytest.fixture()
def created_runs(db: Database) -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    for run_id in ids:
        db.ex("DELETE FROM simulation_runs WHERE id = ?", run_id)


def _forecast(client, auth, board_id: str, backlog: int) -> dict[str, Any]:
    params = {"backlog_size": backlog, "throughput_samples": MC_SAMPLES, "trials": 300, "seed": 7}
    response = client.post(
        f"{API}/simulator/runs",
        json={"board_id": board_id, "kind": "monte_carlo_when", "params": params},
        headers=auth("master"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_the_react_shell_answers_the_root_and_every_screen(client, tmp_path, monkeypatch) -> None:
    from backend.app.config import settings

    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    monkeypatch.setattr(settings, "screens_dir", tmp_path)
    for path in (
        "/",
        "/quadro",
        "/quadro/abc?cartao=1",
        "/metricas/x",
        "/admin/usuarios",
        "/entrar",
    ):
        page = client.get(path)
        assert page.status_code == 200 and "root" in page.text, path
    missing = client.get(f"{API}/nothing-here")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_without_a_build_the_root_says_how_to_build(client, tmp_path, monkeypatch) -> None:
    from backend.app.config import settings

    monkeypatch.setattr(settings, "screens_dir", tmp_path / "missing")
    page = client.get("/")
    assert page.status_code == 200 and "npm run build" in page.text


def test_a_simulator_board_can_start_blank(client, auth, db: Database) -> None:
    response = client.post(
        f"{API}/simulator/boards",
        json={"name": "Blank lab", "template": "blank"},
        headers=auth("master"),
    )
    assert response.status_code == 201, response.text
    board = response.json()
    try:
        assert board["kind"] == "simulator"
        assert db.scalar("SELECT COUNT(*) FROM columns WHERE board_id = ?", board["id"]) == 0
    finally:
        client.delete(f"{API}/boards/{board['id']}?hard=true", headers=auth("master"))


def test_compare_measures_against_the_chosen_baseline(
    client, auth, sim_board: dict[str, Any], created_runs: list[str]
) -> None:
    small = _forecast(client, auth, sim_board["id"], 10)
    large = _forecast(client, auth, sim_board["id"], 60)
    created_runs.extend([small["id"], large["id"]])

    default = client.post(
        f"{API}/simulator/compare",
        json={"run_ids": [small["id"], large["id"]]},
        headers=auth("master"),
    ).json()
    assert default["baseline_run_id"] == small["id"]

    chosen = client.post(
        f"{API}/simulator/compare",
        json={"run_ids": [small["id"], large["id"]], "baseline_run_id": large["id"]},
        headers=auth("master"),
    )
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert body["baseline_run_id"] == large["id"]
    assert body["runs"][0]["id"] == large["id"]
    for row in body["rows"]:
        base_cell = next(c for c in row["cells"] if c["run_id"] == large["id"])
        assert row["baseline"] == base_cell["value"], row["key"]

    outside = client.post(
        f"{API}/simulator/compare",
        json={"run_ids": [small["id"], large["id"]], "baseline_run_id": "nope"},
        headers=auth("master"),
    )
    assert outside.status_code == 400 and outside.json()["error"]["code"] == "compare_failed"
