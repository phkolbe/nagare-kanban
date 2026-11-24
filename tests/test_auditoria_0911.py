"""Achados médios e baixos da auditoria Cartographer de 09/11/2026 (backend do Nagare).

Cada teste cria o seu próprio quadro (apagado no fim) em vez de mexer no quadro
semeado "Nagare Flow", que os outros módulos leem.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from sqlhelper import Database

API = "/api/v1"


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


def _make_board(client, headers, template: str = "simple_delivery", **extra: Any) -> dict:
    body = {
        "name": f"Auditoria {uuid.uuid4().hex[:8]}",
        "kind": extra.pop("kind", "operational"),
        "template": template,
        **extra,
    }
    response = client.post(f"{API}/boards", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def flow_board(client, master) -> Iterator[dict[str, Any]]:
    """Backlog → In Progress → Review → Done, com o grupo "Delivery"."""
    board = _make_board(client, master)
    yield board
    client.delete(f"{API}/boards/{board['id']}?hard=true", headers=master)


def col_id(board: dict[str, Any], name: str) -> str:
    for column in board["columns"]:
        if column["name"] == name:
            return column["id"]
    raise KeyError(name)


def make_card(client, headers, board_id: str, column_id: str | None = None, **kw: Any):
    body = {"board_id": board_id, "title": kw.pop("title", "Cartão"), **kw}
    if column_id:
        body["column_id"] = column_id
    return client.post(f"{API}/cards", json=body, headers=headers)


# ---------------------------------------------------------------------------
# 1. IntegrityError → 409; null explícito → 422; limites do simulador
# ---------------------------------------------------------------------------


def test_an_integrity_error_is_a_409_conflict_not_a_500() -> None:
    from backend.app.errors import install_error_handlers
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import IntegrityError

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key"))

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "conflict"
    assert body["codigo"] == "conflict"
    assert "duplicate key" not in response.text  # o detalhe do banco fica no log


@pytest.mark.parametrize(
    "field", ["title", "service_class", "demand_type", "position", "tags", "archived"]
)
def test_explicit_null_on_a_required_card_field_is_a_422(client, master, flow_board, field) -> None:
    card = make_card(client, master, flow_board["id"]).json()
    response = client.patch(f"{API}/cards/{card['id']}", json={field: None}, headers=master)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["fields"][0]["field"] == field


def test_explicit_null_on_an_optional_card_field_still_clears_it(
    client, master, flow_board
) -> None:
    card = make_card(client, master, flow_board["id"], requester="Sarah").json()
    response = client.patch(
        f"{API}/cards/{card['id']}",
        json={"requester": None, "value_area": None},
        headers=master,
    )
    assert response.status_code == 200, response.text
    assert response.json()["requester"] is None
    assert response.json()["value_area"] == card["value_area"]


def test_a_requester_longer_than_the_column_is_a_422(client, master, flow_board) -> None:
    response = make_card(client, master, flow_board["id"], requester="x" * 201)
    assert response.status_code == 422, response.text


@pytest.fixture()
def created_runs(db: Database) -> Iterator[list[str]]:
    ids: list[str] = []
    yield ids
    for run_id in ids:
        db.ex("DELETE FROM simulation_runs WHERE id = ?", run_id)


@pytest.mark.parametrize(
    ("kind", "params", "param"),
    [
        ("monte_carlo_when", {"backlog_size": 10, "trials": 10**8}, "trials"),
        ("monte_carlo_when", {"backlog_size": 10**7}, "backlog_size"),
        ("monte_carlo_how_many", {"days": 10**6}, "days"),
        ("wip_experiment", {"weeks": 10_000}, "weeks"),
        ("class_of_service_mix", {"arrival_rate": 1e9}, "arrival_rate"),
        ("monte_carlo_when", {"backlog_size": 10, "trials": "muitos"}, "trials"),
        ("monte_carlo_when", {"backlog_size": 5, "throughput_samples": [1] * 600}, None),
    ],
)
def test_a_simulation_run_out_of_range_is_refused_before_running(
    client, master, sim_board, kind, params, param
) -> None:
    response = client.post(
        f"{API}/simulator/runs",
        json={"board_id": sim_board["id"], "kind": kind, "params": params},
        headers=master,
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "simulation_params_out_of_range"
    if param:
        assert error["details"]["param"] == param


def test_a_simulation_run_inside_the_limits_still_runs(
    client, master, sim_board, created_runs
) -> None:
    params = {"backlog_size": 20, "throughput_samples": [3, 4, 5], "trials": 100_000, "seed": 1}
    response = client.post(
        f"{API}/simulator/runs",
        json={"board_id": sim_board["id"], "kind": "monte_carlo_when", "params": params},
        headers=master,
    )
    assert response.status_code == 201, response.text
    created_runs.append(response.json()["id"])


# ---------------------------------------------------------------------------
# 2. Demanda, envelope e horas respeitam as regras de escrita do quadro
# ---------------------------------------------------------------------------


@pytest.fixture()
def scratch(client, master) -> Iterator[dict[str, Any]]:
    """Quadros e projetos descartáveis; os quadros saem antes dos projetos."""
    made: dict[str, list[str]] = {"boards": [], "projects": []}

    def board(**kw: Any) -> dict[str, Any]:
        row = _make_board(client, master, **kw)
        made["boards"].append(row["id"])
        return row

    def project() -> dict[str, Any]:
        body = {"name": f"Auditoria {uuid.uuid4().hex[:6]}", "key": f"A{uuid.uuid4().hex[:5]}"}
        response = client.post(
            f"{API}/projects", json=body | {"key": body["key"].upper()}, headers=master
        )
        assert response.status_code == 201, response.text
        made["projects"].append(response.json()["id"])
        return response.json()

    yield {"board": board, "project": project}
    for board_id in made["boards"]:
        client.delete(f"{API}/boards/{board_id}?hard=true", headers=master)
    for project_id in made["projects"]:
        client.delete(f"{API}/projects/{project_id}", headers=master)


def test_an_editor_cannot_capture_a_demand_on_a_simulator_board(client, auth, scratch) -> None:
    board = scratch["board"](kind="simulator", template="upstream_downstream")
    project = scratch["project"]()
    response = client.post(
        f"{API}/projects/{project['id']}/demands",
        json={"title": "Demanda", "board_id": board["id"]},
        headers=auth("editor"),
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "board_write_forbidden"


def test_an_editor_cannot_open_an_envelope_on_a_simulator_board(
    client, auth, master, scratch
) -> None:
    board = scratch["board"](kind="simulator", template="upstream_downstream")
    project = scratch["project"]()
    demand = client.post(
        f"{API}/projects/{project['id']}/demands",
        json={"title": "Demanda", "board_id": board["id"]},
        headers=master,
    ).json()
    response = client.post(f"{API}/cards/{demand['id']}/open-envelope", headers=auth("editor"))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "board_write_forbidden"


def test_an_archived_demand_is_not_opened(client, master, scratch) -> None:
    board = scratch["board"](template="upstream_downstream")
    project = scratch["project"]()
    demand = client.post(
        f"{API}/projects/{project['id']}/demands",
        json={"title": "Demanda", "board_id": board["id"]},
        headers=master,
    ).json()
    archived = client.patch(f"{API}/cards/{demand['id']}", json={"archived": True}, headers=master)
    assert archived.status_code == 200, archived.text
    response = client.post(f"{API}/cards/{demand['id']}/open-envelope", headers=master)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "card_archived"


HOURS = {"worked_on": "2026-11-20", "minutes": 30}


def test_an_editor_cannot_log_hours_on_a_simulator_board(client, auth, master, scratch) -> None:
    board = scratch["board"](kind="simulator")
    card = make_card(client, master, board["id"]).json()
    response = client.post(f"{API}/cards/{card['id']}/time", json=HOURS, headers=auth("editor"))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "board_write_forbidden"


def test_hours_are_not_logged_on_an_archived_card(client, master, flow_board) -> None:
    card = make_card(client, master, flow_board["id"]).json()
    client.patch(f"{API}/cards/{card['id']}", json={"archived": True}, headers=master)
    response = client.post(f"{API}/cards/{card['id']}/time", json=HOURS, headers=master)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "card_archived"


def test_hours_on_a_live_card_are_still_logged(client, auth, flow_board, master) -> None:
    card = make_card(client, master, flow_board["id"]).json()
    response = client.post(f"{API}/cards/{card['id']}/time", json=HOURS, headers=auth("editor"))
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# 3. O WIP vale para todo jeito de um cartão entrar numa coluna
# ---------------------------------------------------------------------------


def _limit(client, master, path: str, wip_max: int) -> None:
    body = (
        {"wip_max": wip_max} if path.startswith("boards/") else {"wip_min": 0, "wip_max": wip_max}
    )
    response = client.patch(f"{API}/{path}", json=body, headers=master)
    assert response.status_code == 200, response.text


def test_a_new_card_respects_the_column_wip(client, master, flow_board) -> None:
    doing = col_id(flow_board, "In Progress")
    _limit(client, master, f"columns/{doing}", 1)
    assert make_card(client, master, flow_board["id"], doing).status_code == 201
    refused = make_card(client, master, flow_board["id"], doing)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "wip_limit_exceeded"
    assert refused.json()["error"]["details"]["policy"] == "column"
    expedite = make_card(client, master, flow_board["id"], doing, service_class="expedite")
    assert expedite.status_code == 201, "expedite bypasses the column limit only"


def test_a_new_card_respects_the_group_and_flow_wip(client, master, flow_board) -> None:
    doing = col_id(flow_board, "In Progress")
    group_id = next(c["group_id"] for c in flow_board["columns"] if c["id"] == doing)
    _limit(client, master, f"groups/{group_id}", 1)
    assert make_card(client, master, flow_board["id"], doing).status_code == 201
    refused = make_card(client, master, flow_board["id"], doing, service_class="expedite")
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["policy"] == "group"

    _limit(client, master, f"groups/{group_id}", 10)
    _limit(client, master, f"boards/{flow_board['id']}", 1)
    refused = make_card(client, master, flow_board["id"], doing)
    assert refused.json()["error"]["details"]["policy"] == "flow"
    # The backlog is before the commitment point: not in flight, no flow limit.
    backlog = col_id(flow_board, "Backlog")
    assert make_card(client, master, flow_board["id"], backlog).status_code == 201


def test_a_card_is_not_restored_into_a_full_column(client, master, flow_board, db) -> None:
    doing = col_id(flow_board, "In Progress")
    first = make_card(client, master, flow_board["id"], doing).json()
    client.patch(f"{API}/cards/{first['id']}", json={"archived": True}, headers=master)
    _limit(client, master, f"columns/{doing}", 1)
    assert make_card(client, master, flow_board["id"], doing).status_code == 201

    restored = client.patch(f"{API}/cards/{first['id']}", json={"archived": False}, headers=master)
    assert restored.status_code == 409, restored.text
    assert restored.json()["error"]["code"] == "wip_limit_exceeded"
    row = db.q1("SELECT archived FROM cards WHERE id = ?", first["id"])
    assert bool(row["archived"]) is True


def test_an_open_demand_respects_the_wip_of_its_column(client, master, scratch) -> None:
    board = scratch["board"](template="upstream_downstream")
    project = scratch["project"]()
    ideas = col_id(board, "Ideas")
    _limit(client, master, f"columns/{ideas}", 1)
    url = f"{API}/projects/{project['id']}/demands"
    body = {"title": "Demanda", "board_id": board["id"], "sealed": False}
    assert client.post(url, json=body, headers=master).status_code == 201
    refused = client.post(url, json=body, headers=master)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "wip_limit_exceeded"
    # A sealed demand waits in the envelope, outside the flow: still accepted.
    sealed = client.post(url, json=body | {"sealed": True}, headers=master)
    assert sealed.status_code == 201, sealed.text

    opened = client.post(f"{API}/cards/{sealed.json()['id']}/open-envelope", headers=master)
    assert opened.status_code == 409, opened.text
    assert opened.json()["error"]["code"] == "wip_limit_exceeded"


# ---------------------------------------------------------------------------
# 4. Chave de cartão sem corrida
# ---------------------------------------------------------------------------


def parallel(calls: int, fn) -> list[Any]:
    """``calls`` requests at once through the same TestClient (one event loop)."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=calls) as pool:
        return list(pool.map(lambda _: fn(), range(calls)))


def on_postgres(client) -> bool:
    return client.app.state.database.url.startswith("postgresql")


def test_cards_created_at_once_never_share_a_key(client, master, scratch) -> None:
    board = scratch["board"]()
    project = scratch["project"]()
    responses = parallel(
        8, lambda: make_card(client, master, board["id"], project_id=project["id"])
    )
    statuses = [r.status_code for r in responses]
    created = [r.json()["key"] for r in responses if r.status_code == 201]
    assert len(created) == len(set(created)), created
    if on_postgres(client):
        assert statuses == [201] * 8, [r.text for r in responses]
    else:  # one writer: a collision that slips through is a 409, never a 500
        assert set(statuses) <= {201, 409}, [r.text for r in responses]


# ---------------------------------------------------------------------------
# 5. GET não grava
# ---------------------------------------------------------------------------


def test_reading_the_workspace_never_writes_it(client, auth, db: Database) -> None:
    saved = db.q1("SELECT * FROM workspace WHERE id = 'workspace'")
    db.ex("DELETE FROM workspace WHERE id = 'workspace'")
    try:
        response = client.get(f"{API}/workspace", headers=auth("master"))
        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Nagare"
        assert db.q1("SELECT id FROM workspace WHERE id = 'workspace'") is None

        renamed = client.patch(
            f"{API}/workspace", json={"name": "Nagare SR"}, headers=auth("master")
        )
        assert renamed.status_code == 200, renamed.text
        assert db.scalar("SELECT name FROM workspace WHERE id = 'workspace'") == "Nagare SR"
    finally:
        db.ex("DELETE FROM workspace WHERE id = 'workspace'")
        columns = ", ".join(saved)
        marks = ", ".join("?" for _ in saved)
        db.ex(f"INSERT INTO workspace ({columns}) VALUES ({marks})", *saved.values())
