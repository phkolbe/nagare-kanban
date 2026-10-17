"""Boards, columns, groups, WIP policies, projects and demands — SPEC §5/§6/§8.

This module is the BOARDS agent's contract. It runs against the real HTTP
surface with the real seeded database, and it owns exactly two responsibilities
that everything downstream depends on:

* the **structural invariants** ``backend.app.routers.boards`` promises —
  exactly one commitment point, exactly one delivery point, dense positions,
  contiguous groups — hold after every mutation a board can go through
  (create, add/insert a column, reorder, change a segment);
* the **sealed-envelope demand lifecycle** (SPEC §8 / Req. 8): a demand is
  born sealed in the envelope column and only becomes real work once
  ``open-envelope`` is called.

Tests that mutate structure build their own throwaway board through
``new_board`` (torn down with a hard delete afterwards) rather than touching
the shared, session-scoped "Nagare Flow" fixture other test modules read —
that board is only ever read here, never written.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from sqlhelper import Database

API = "/api/v1"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


@pytest.fixture()
def new_board(client, master) -> Iterator[Callable[..., dict[str, Any]]]:
    """Create a board through the real API; hard-delete it when the test ends.

    Defaults to the ``blank`` template so structural tests start from nothing
    and build exactly the columns they need — a fresh board per test avoids
    any cross-test interference through shared WIP counts or positions.
    """
    created: list[str] = []

    def _make(**overrides: Any) -> dict[str, Any]:
        body = {
            "name": overrides.pop("name", f"Test Board {uuid.uuid4().hex[:8]}"),
            "kind": overrides.pop("kind", "operational"),
            "template": overrides.pop("template", "blank"),
        }
        body.update(overrides)
        response = client.post(f"{API}/boards", json=body, headers=master)
        assert response.status_code == 201, response.text
        board = response.json()
        created.append(board["id"])
        return board

    yield _make

    for board_id in created:
        client.delete(f"{API}/boards/{board_id}?hard=true", headers=master)


@pytest.fixture()
def new_project(client, master) -> Iterator[Callable[..., dict[str, Any]]]:
    created: list[str] = []

    def _make(**overrides: Any) -> dict[str, Any]:
        body = {
            "name": overrides.pop("name", f"Test Project {uuid.uuid4().hex[:6]}"),
            "key": overrides.pop("key", f"T{uuid.uuid4().hex[:5]}".upper()),
        }
        body.update(overrides)
        response = client.post(f"{API}/projects", json=body, headers=master)
        assert response.status_code == 201, response.text
        project = response.json()
        created.append(project["id"])
        return project

    yield _make

    for project_id in created:
        client.delete(f"{API}/projects/{project_id}", headers=master)


def col_id(board: dict[str, Any], name: str) -> str:
    """The id of the column named ``name`` on a board payload."""
    for column in board["columns"]:
        if column["name"] == name:
            return column["id"]
    raise KeyError(f"no column named {name!r} on board {board.get('id')}")


def add_column(client, master, board_id: str, **kwargs: Any) -> dict[str, Any]:
    body = {"segment": "downstream", "kind": "queue"}
    body.update(kwargs)
    response = client.post(f"{API}/boards/{board_id}/columns", json=body, headers=master)
    assert response.status_code == 201, response.text
    return response.json()


def get_board(client, master, board_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/boards/{board_id}", headers=master)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# board CRUD
# ---------------------------------------------------------------------------


def test_create_board_from_template_lays_out_columns_groups_and_points(
    client, master, new_board
) -> None:
    board = new_board(template="upstream_downstream")
    assert board["kind"] == "operational"
    assert len(board["columns"]) == 12
    assert {g["name"] for g in board["groups"]} == {"Discovery", "Delivery"}

    by_name = {c["name"]: c for c in board["columns"]}
    assert by_name["Analysis"]["is_commitment_point"] is True
    assert by_name["Delivered"]["is_delivery_point"] is True
    assert sum(1 for c in board["columns"] if c["is_commitment_point"]) == 1
    assert sum(1 for c in board["columns"] if c["is_delivery_point"]) == 1
    # upstream before downstream, envelope always first (SPEC §2.4/§9.1 order)
    segments = [c["segment"] for c in board["columns"]]
    assert segments == sorted(segments, key=lambda s: {"upstream": 0, "downstream": 1}[s])
    assert board["columns"][0]["kind"] == "envelope"


def test_get_patch_and_delete_board(client, master, new_board) -> None:
    board = new_board(template="blank", wip_min=1, wip_max=10, sle_days=14)
    fetched = get_board(client, master, board["id"])
    assert fetched["id"] == board["id"]
    assert fetched["wip_max"] == 10

    patched = client.patch(
        f"{API}/boards/{board['id']}",
        json={"name": "Renamed board", "confirm_moves": False},
        headers=master,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Renamed board"
    assert patched.json()["confirm_moves"] is False

    invalid = client.patch(
        f"{API}/boards/{board['id']}", json={"wip_min": 20, "wip_max": 5}, headers=master
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_wip_range"

    archived = client.delete(f"{API}/boards/{board['id']}", headers=master)
    assert archived.status_code == 200 and archived.json()["ok"] is True
    assert get_board(client, master, board["id"])["archived"] is True


def test_hard_delete_requires_master(client, auth, new_board) -> None:
    board = new_board(template="blank")
    denied = client.delete(f"{API}/boards/{board['id']}?hard=true", headers=auth("administrator"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "insufficient_role"


def test_viewer_cannot_create_a_board(client, auth) -> None:
    response = client.post(
        f"{API}/boards",
        json={"name": "Should not exist", "template": "blank"},
        headers=auth("viewer"),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"

    also_denied = client.post(
        f"{API}/boards",
        json={"name": "Should not exist either", "template": "blank"},
        headers=auth("editor"),
    )
    assert also_denied.status_code == 403
    assert also_denied.json()["error"]["code"] == "insufficient_role"


def test_column_shape_invariants(client, master, new_board) -> None:
    board = new_board(template="blank")
    bad_envelope = client.post(
        f"{API}/boards/{board['id']}/columns",
        json={"name": "Bad envelope", "segment": "downstream", "kind": "envelope"},
        headers=master,
    )
    assert bad_envelope.status_code == 409
    assert bad_envelope.json()["error"]["code"] == "envelope_must_be_upstream"

    bad_done = client.post(
        f"{API}/boards/{board['id']}/columns",
        json={"name": "Bad done", "segment": "upstream", "kind": "done"},
        headers=master,
    )
    assert bad_done.status_code == 409
    assert bad_done.json()["error"]["code"] == "done_must_be_downstream"

    add_column(client, master, board["id"], name="Intake", segment="upstream", kind="envelope")
    second_envelope = client.post(
        f"{API}/boards/{board['id']}/columns",
        json={"name": "Another envelope", "segment": "upstream", "kind": "envelope"},
        headers=master,
    )
    assert second_envelope.status_code == 409
    assert second_envelope.json()["error"]["code"] == "envelope_column_exists"


# ---------------------------------------------------------------------------
# GET /boards/{id}/view — segments and ordering (uses the seeded board, read-only)
# ---------------------------------------------------------------------------


def test_board_view_segments_return_correct_columns_and_order(
    client, master, board_id: str, columns_by_name: dict[str, Any]
) -> None:
    upstream = client.get(
        f"{API}/boards/{board_id}/view", params={"segment": "upstream"}, headers=master
    )
    assert upstream.status_code == 200, upstream.text
    up_body = upstream.json()
    up_names = [c["name"] for c in up_body["columns"]]
    assert up_names == ["Envelope", "Ideas", "Discovery", "Options", "Ready to Commit"]
    assert all(c["segment"] == "upstream" for c in up_body["columns"])
    assert up_body["segment"] == "upstream"

    downstream = client.get(
        f"{API}/boards/{board_id}/view", params={"segment": "downstream"}, headers=master
    ).json()
    down_names = [c["name"] for c in downstream["columns"]]
    assert down_names == [
        "Analysis",
        "Analysis Done",
        "Build",
        "Build Done",
        "Verify",
        "Ready to Deploy",
        "Delivered",
    ]
    assert all(c["segment"] == "downstream" for c in downstream["columns"])

    unified = client.get(
        f"{API}/boards/{board_id}/view", params={"segment": "unified"}, headers=master
    ).json()
    assert [c["name"] for c in unified["columns"]] == up_names + down_names
    assert unified["commitment_column_id"] == columns_by_name["Analysis"]["id"]
    assert unified["delivery_column_id"] == columns_by_name["Delivered"]["id"]
    assert unified["wip"]["flow"] is not None
    assert set(unified["wip"]["columns"]) == {c["id"] for c in unified["columns"]}


# ---------------------------------------------------------------------------
# WIP status: under / ok / at_limit / over, per column, per group, whole flow
# ---------------------------------------------------------------------------


def test_wip_status_covers_every_state_at_every_scope(client, master, new_board) -> None:
    board = new_board(template="blank", wip_min=2, wip_max=3)
    intake = add_column(
        client,
        master,
        board["id"],
        name="Intake",
        segment="downstream",
        kind="queue",
        wip_min=2,
        wip_max=5,
    )
    doing = add_column(
        client,
        master,
        board["id"],
        name="Doing",
        segment="downstream",
        kind="queue",
        wip_min=None,
        wip_max=2,
    )
    waiting = add_column(
        client,
        master,
        board["id"],
        name="Waiting",
        segment="downstream",
        kind="queue",
        wip_min=None,
        wip_max=3,
    )
    group = client.post(
        f"{API}/boards/{board['id']}/groups",
        json={
            "name": "Work",
            "wip_min": 1,
            "wip_max": 4,
            "column_ids": [doing["id"], waiting["id"]],
        },
        headers=master,
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]

    def make_card(column_id: str) -> None:
        response = client.post(
            f"{API}/cards",
            json={"board_id": board["id"], "title": "load", "column_id": column_id},
            headers=master,
        )
        assert response.status_code == 201, response.text

    def wip() -> dict[str, Any]:
        response = client.get(f"{API}/boards/{board['id']}/wip", headers=master)
        assert response.status_code == 200, response.text
        return response.json()

    make_card(intake["id"])
    make_card(doing["id"])
    make_card(doing["id"])

    status = wip()
    assert status["columns"][intake["id"]] == {
        "count": 1,
        "min": 2,
        "max": 5,
        "state": "under",
        "label": "1/5",
    }
    assert status["columns"][doing["id"]]["state"] == "at_limit"
    assert status["columns"][doing["id"]]["count"] == 2
    assert status["columns"][waiting["id"]]["state"] == "ok"
    assert status["columns"][waiting["id"]]["count"] == 0
    assert status["groups"][group_id]["state"] == "ok"
    assert status["groups"][group_id]["count"] == 2
    # all three cards sit in downstream columns, and the first downstream
    # column ("Intake") is the commitment point, so every one of them is
    # already committed-and-not-delivered: the flow cell counts all 3.
    assert status["flow"] == {"count": 3, "min": 2, "max": 3, "state": "at_limit", "label": "3/3"}

    make_card(doing["id"])
    status = wip()
    assert status["columns"][doing["id"]]["state"] == "over"
    assert status["columns"][doing["id"]]["count"] == 3
    assert status["flow"]["state"] == "over"
    assert status["flow"]["count"] == 4

    make_card(waiting["id"])
    make_card(waiting["id"])
    status = wip()
    assert status["columns"][waiting["id"]]["count"] == 2
    assert status["columns"][waiting["id"]]["state"] == "ok"
    assert status["groups"][group_id]["count"] == 5  # 3 (Doing) + 2 (Waiting)
    assert status["groups"][group_id]["state"] == "over"
    assert status["flow"]["count"] == 6
    assert status["flow"]["state"] == "over"


# ---------------------------------------------------------------------------
# commitment-point / delivery-point recomputation
# ---------------------------------------------------------------------------


def test_commitment_and_delivery_points_recompute_on_add_reorder_and_segment_change(
    client, master, new_board
) -> None:
    board = new_board(template="blank")
    board_id = board["id"]
    u1 = add_column(client, master, board_id, name="U1", segment="upstream", kind="queue")
    d1 = add_column(client, master, board_id, name="D1", segment="downstream", kind="queue")
    d2 = add_column(client, master, board_id, name="D2", segment="downstream", kind="done")

    def points(b: dict[str, Any]) -> tuple[str | None, str | None]:
        commitment = next((c["id"] for c in b["columns"] if c["is_commitment_point"]), None)
        delivery = next((c["id"] for c in b["columns"] if c["is_delivery_point"]), None)
        assert sum(1 for c in b["columns"] if c["is_commitment_point"]) == 1
        assert sum(1 for c in b["columns"] if c["is_delivery_point"]) == 1
        return commitment, delivery

    board = get_board(client, master, board_id)
    commitment, delivery = points(board)
    assert commitment == d1["id"]
    assert delivery == d2["id"]

    # Insert a new downstream column ahead of D1: it becomes the new
    # commitment point (SPEC §2.4 — the first downstream column, always).
    order_before = [c["id"] for c in board["columns"]]
    insert_index = order_before.index(d1["id"])
    d0 = add_column(
        client,
        master,
        board_id,
        name="D0",
        segment="downstream",
        kind="queue",
        position=insert_index,
    )
    board = get_board(client, master, board_id)
    commitment, delivery = points(board)
    assert commitment == d0["id"], "the newly inserted column is now first downstream"
    assert delivery == d2["id"]

    # Reorder so D1 leads the downstream segment again.
    ordered_ids = [u1["id"], d1["id"], d0["id"], d2["id"]]
    reordered = client.post(
        f"{API}/boards/{board_id}/columns/reorder",
        json={"ordered_ids": ordered_ids},
        headers=master,
    )
    assert reordered.status_code == 200, reordered.text
    names_in_order = [c["name"] for c in reordered.json()["items"]]
    assert names_in_order == ["U1", "D1", "D0", "D2"]
    board = get_board(client, master, board_id)
    commitment, delivery = points(board)
    assert commitment == d1["id"]

    # Changing D1's segment to upstream hands the commitment point to D0.
    changed = client.patch(
        f"{API}/columns/{d1['id']}", json={"segment": "upstream"}, headers=master
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["segment"] == "upstream"
    board = get_board(client, master, board_id)
    commitment, delivery = points(board)
    assert commitment == d0["id"]
    assert delivery == d2["id"]

    # The two point flags are derived; the API refuses to let a client fake them.
    fake_commitment = client.patch(
        f"{API}/columns/{d2['id']}", json={"is_commitment_point": True}, headers=master
    )
    assert fake_commitment.status_code == 409
    assert fake_commitment.json()["error"]["code"] == "commitment_point_is_derived"


def test_group_must_span_contiguous_columns(client, master, new_board) -> None:
    board = new_board(template="blank")
    x = add_column(client, master, board["id"], name="X", segment="downstream", kind="queue")
    y = add_column(client, master, board["id"], name="Y", segment="downstream", kind="queue")
    z = add_column(client, master, board["id"], name="Z", segment="downstream", kind="queue")

    non_contiguous = client.post(
        f"{API}/boards/{board['id']}/groups",
        json={"name": "Broken", "column_ids": [x["id"], z["id"]]},
        headers=master,
    )
    assert non_contiguous.status_code == 409
    assert non_contiguous.json()["error"]["code"] == "group_not_contiguous"
    # Rejected atomically: the group must not have been left behind.
    listing = get_board(client, master, board["id"])
    assert not any(g["name"] == "Broken" for g in listing["groups"])

    contiguous = client.post(
        f"{API}/boards/{board['id']}/groups",
        json={"name": "Fine", "column_ids": [x["id"], y["id"]]},
        headers=master,
    )
    assert contiguous.status_code == 201, contiguous.text
    board = get_board(client, master, board["id"])
    by_id = {c["id"]: c for c in board["columns"]}
    assert by_id[x["id"]]["group_id"] == by_id[y["id"]]["group_id"] == contiguous.json()["id"]
    assert by_id[z["id"]]["group_id"] is None


# ---------------------------------------------------------------------------
# clone fidelity (SPEC §5 / §8.1)
# ---------------------------------------------------------------------------


def test_clone_board_copies_structure_policies_and_card_history(
    client, master, new_board, db: Database
) -> None:
    source = new_board(template="upstream_downstream")
    created = client.post(
        f"{API}/cards",
        json={
            "board_id": source["id"],
            "title": "Clone me",
            "column_id": col_id(source, "Ideas"),
        },
        headers=master,
    )
    assert created.status_code == 201, created.text
    card = created.json()
    moved = client.post(
        f"{API}/cards/{card['id']}/move",
        json={"to_column_id": col_id(source, "Discovery")},
        headers=master,
    )
    assert moved.status_code == 200, moved.text

    clone_resp = client.post(
        f"{API}/boards/{source['id']}/clone",
        json={"name": f"Clone {uuid.uuid4().hex[:6]}", "kind": "operational", "copy_cards": True},
        headers=master,
    )
    assert clone_resp.status_code == 201, clone_resp.text
    clone = clone_resp.json()
    try:
        assert clone["source_board_id"] == source["id"]
        assert len(clone["columns"]) == len(source["columns"])
        assert {c["name"] for c in clone["columns"]} == {c["name"] for c in source["columns"]}
        assert len(clone["groups"]) == len(source["groups"])
        assert clone["wip_min"] == source["wip_min"]
        assert clone["wip_max"] == source["wip_max"]
        assert clone["sle_days"] == source["sle_days"]
        assert clone["card_count"] == 1
        assert sum(1 for c in clone["columns"] if c["is_commitment_point"]) == 1
        assert sum(1 for c in clone["columns"] if c["is_delivery_point"]) == 1

        cloned_cards = client.get(
            f"{API}/cards", params={"board_id": clone["id"]}, headers=master
        ).json()["items"]
        assert len(cloned_cards) == 1
        cloned_card = cloned_cards[0]
        assert cloned_card["title"] == "Clone me"
        assert cloned_card["column_id"] == col_id(clone, "Discovery")
        assert cloned_card["id"] != card["id"]

        moves = db.q(
            "SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at", cloned_card["id"]
        )
        assert len(moves) == 2, "creation row plus the one move, both carried over"
        assert moves[0]["from_column_id"] is None

        without_cards = client.post(
            f"{API}/boards/{source['id']}/clone",
            json={"name": f"Clone bare {uuid.uuid4().hex[:6]}", "kind": "operational"},
            headers=master,
        )
        assert without_cards.status_code == 201, without_cards.text
        assert without_cards.json()["card_count"] == 0
        client.delete(f"{API}/boards/{without_cards.json()['id']}?hard=true", headers=master)
    finally:
        client.delete(f"{API}/boards/{clone['id']}?hard=true", headers=master)


# ---------------------------------------------------------------------------
# projects and demand counts
# ---------------------------------------------------------------------------


def test_project_crud_and_demand_counts(client, master, new_project) -> None:
    project = new_project(name="Test Flow Project")
    assert project["card_count"] == 0
    assert project["sealed_count"] == 0

    fetched = client.get(f"{API}/projects/{project['id']}", headers=master)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["demands"] == {
        "total": 0,
        "sealed": 0,
        "opened": 0,
        "upstream": 0,
        "downstream": 0,
        "delivered": 0,
        "in_flight": 0,
        "blocked": 0,
    }

    patched = client.patch(
        f"{API}/projects/{project['id']}", json={"status": "on_hold"}, headers=master
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["status"] == "on_hold"

    listed = client.get(f"{API}/projects", params={"q": project["key"]}, headers=master)
    assert listed.status_code == 200
    assert project["id"] in {p["id"] for p in listed.json()["items"]}

    archived = client.delete(f"{API}/projects/{project['id']}", headers=master)
    assert archived.status_code == 200 and archived.json()["ok"] is True
    still_visible = client.get(f"{API}/projects/{project['id']}", headers=master)
    assert still_visible.json()["archived"] is True


def test_viewer_cannot_manage_projects(client, auth, new_project) -> None:
    project = new_project()
    denied = client.patch(
        f"{API}/projects/{project['id']}", json={"status": "done"}, headers=auth("viewer")
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "insufficient_role"


def test_create_demand_seals_a_card_in_the_envelope_column(
    client, master, new_board, new_project, db: Database
) -> None:
    board = new_board(template="upstream_downstream")
    project = new_project()
    envelope_col = col_id(board, "Envelope")

    response = client.post(
        f"{API}/projects/{project['id']}/demands",
        json={"title": "Raw demand, unread", "board_id": board["id"]},
        headers=master,
    )
    assert response.status_code == 201, response.text
    card = response.json()
    assert card["column_id"] == envelope_col
    assert card["envelope_state"] == "sealed"
    assert card["opened_at"] is None
    assert card["key"].startswith(project["key"])

    movement = db.q1("SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at", card["id"])
    assert movement is not None
    assert movement["from_column_id"] is None
    assert movement["to_column_id"] == envelope_col

    event = db.q1(
        "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'created'", card["id"]
    )
    assert event is not None

    project_after = client.get(f"{API}/projects/{project['id']}", headers=master).json()
    assert project_after["sealed_count"] == 1
    assert project_after["card_count"] == 1


def test_open_envelope_moves_the_demand_into_upstream(
    client, master, new_board, new_project, db: Database
) -> None:
    board = new_board(template="upstream_downstream")
    project = new_project()
    envelope_col = col_id(board, "Envelope")
    ideas_col = col_id(board, "Ideas")

    demand = client.post(
        f"{API}/projects/{project['id']}/demands",
        json={"title": "Sealed thing", "board_id": board["id"]},
        headers=master,
    ).json()

    opened = client.post(f"{API}/cards/{demand['id']}/open-envelope", headers=master)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["card"]["envelope_state"] == "opened"
    assert body["card"]["opened_at"] is not None
    assert body["card"]["column_id"] == ideas_col
    assert body["movement"]["from_column_id"] == envelope_col
    assert body["movement"]["to_column_id"] == ideas_col
    assert body["committed"] is False  # Ideas is upstream, not the commitment point

    event = db.q1(
        "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'opened_envelope'",
        demand["id"],
    )
    assert event is not None
    assert event["old_value"] == "sealed" and event["new_value"] == "opened"

    # Opening twice is a conflict, not a silent no-op.
    again = client.post(f"{API}/cards/{demand['id']}/open-envelope", headers=master)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "envelope_already_opened"

    project_after = client.get(f"{API}/projects/{project['id']}", headers=master).json()
    assert project_after["opened_count"] == 1
    assert project_after["sealed_count"] == 0
