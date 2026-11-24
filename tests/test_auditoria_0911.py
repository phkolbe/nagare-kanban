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
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.exc import IntegrityError

    from backend.app.errors import install_error_handlers

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


def test_explicit_null_on_an_optional_card_field_still_clears_it(client, master, flow_board) -> None:
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
