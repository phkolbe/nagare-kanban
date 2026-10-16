"""Board structure editing through the API — SPEC §5 + SPEC-v2 R2.1/R2.2.

The structure editor (SPEC-v2 R2.1) is the first UI that lets a person reshape a
board while it holds real work, so this module pins the whole column lifecycle
against the HTTP surface: create in every segment and kind, rename, re-kind,
re-segment, reorder, drain and delete — asserting after each step that the
invariants ``backend.app.routers.boards`` promises still hold.

Three of those invariants are worth stating plainly, because the UI surfaces
them rather than re-implementing them:

* the **commitment point** is the first downstream column and follows every
  re-segment and every reorder, and the **delivery point** is the last ``done``
  column;
* ``position`` stays **dense** (``0..n-1``) whatever the caller sends;
* a column holding cards is never deleted — ``cards.column_id`` cascades, so a
  permissive delete would silently destroy work and every metric derived from
  it. ``POST /columns/{id}/drain`` is the sanctioned way out, and it writes the
  movement rows that keep those metrics truthful.

Every test builds its own throwaway board and hard-deletes it afterwards; the
seeded "Nagare Flow" board is never written to from here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from sqlhelper import Database

API = "/api/v1"


# ---------------------------------------------------------------------------
# fixtures & helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


@pytest.fixture()
def new_board(client, master) -> Iterator[Callable[..., dict[str, Any]]]:
    """A blank board per test, hard-deleted when the test ends."""
    created: list[str] = []

    def _make(**overrides: Any) -> dict[str, Any]:
        body = {
            "name": overrides.pop("name", f"Structure Board {uuid.uuid4().hex[:8]}"),
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


def add_column(client, headers, board_id: str, **kwargs: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"segment": "downstream", "kind": "queue"}
    body.update(kwargs)
    response = client.post(f"{API}/boards/{board_id}/columns", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def get_board(client, headers, board_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/boards/{board_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def get_wip(client, headers, board_id: str) -> dict[str, Any]:
    response = client.get(f"{API}/boards/{board_id}/wip", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def make_card(client, headers, board_id: str, column_id: str, title: str, **extra: Any):
    body = {"board_id": board_id, "column_id": column_id, "title": title}
    body.update(extra)
    response = client.post(f"{API}/cards", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def names_in_order(board: dict[str, Any]) -> list[str]:
    return [column["name"] for column in board["columns"]]


def assert_dense(board: dict[str, Any]) -> None:
    """Positions are ``0..n-1`` in flow order, with no gaps and no repeats."""
    positions = [column["position"] for column in board["columns"]]
    assert positions == list(range(len(positions))), positions


def points(board: dict[str, Any]) -> tuple[str | None, str | None]:
    """``(commitment_column_id, delivery_column_id)`` — and there is at most one of each."""
    commitment = [c["id"] for c in board["columns"] if c["is_commitment_point"]]
    delivery = [c["id"] for c in board["columns"] if c["is_delivery_point"]]
    assert len(commitment) <= 1 and len(delivery) <= 1
    return (commitment[0] if commitment else None, delivery[0] if delivery else None)


def build_flow(client, headers, board_id: str) -> dict[str, dict[str, Any]]:
    """A small but complete flow: envelope → queue → activity → queue → done."""
    return {
        "Envelope": add_column(
            client, headers, board_id, name="Envelope", segment="upstream", kind="envelope"
        ),
        "Intake": add_column(
            client, headers, board_id, name="Intake", segment="upstream", kind="queue"
        ),
        "Shaping": add_column(
            client, headers, board_id, name="Shaping", segment="upstream", kind="activity"
        ),
        "Build": add_column(
            client, headers, board_id, name="Build", segment="downstream", kind="activity"
        ),
        "Review": add_column(
            client, headers, board_id, name="Review", segment="downstream", kind="queue"
        ),
        "Delivered": add_column(
            client, headers, board_id, name="Delivered", segment="downstream", kind="done"
        ),
    }


# ---------------------------------------------------------------------------
# create / rename / re-kind
# ---------------------------------------------------------------------------


def test_a_column_can_be_created_in_every_segment_and_kind(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)

    board = get_board(client, master, board_id)
    assert names_in_order(board) == [
        "Envelope",
        "Intake",
        "Shaping",
        "Build",
        "Review",
        "Delivered",
    ]
    assert_dense(board)
    by_id = {c["id"]: c for c in board["columns"]}
    assert by_id[columns["Envelope"]["id"]]["kind"] == "envelope"
    assert by_id[columns["Shaping"]["id"]]["kind"] == "activity"
    assert by_id[columns["Review"]["id"]]["kind"] == "queue"
    commitment, delivery = points(board)
    assert commitment == columns["Build"]["id"]
    assert delivery == columns["Delivered"]["id"]

    # Every optional field on the create payload round-trips.
    detailed = add_column(
        client,
        master,
        board_id,
        name="Buffer",
        segment="downstream",
        kind="queue",
        wip_min=1,
        wip_max=4,
        color="#3b7ea1",
        description="Waiting on the release train.",
    )
    assert detailed["wip_min"] == 1 and detailed["wip_max"] == 4
    assert detailed["color"] == "#3b7ea1"
    assert detailed["description"] == "Waiting on the release train."
    # No position given: it lands at the end of its own segment.
    assert names_in_order(get_board(client, master, board_id))[-1] == "Buffer"


def test_a_column_is_renamed_and_re_kinded_between_queue_and_activity(
    client, master, new_board
) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    review = columns["Review"]["id"]

    renamed = client.patch(f"{API}/columns/{review}", json={"name": "Verify"}, headers=master)
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Verify"
    assert renamed.json()["kind"] == "queue"

    # queue -> activity: the column now accrues touch time instead of wait time,
    # which is the whole reason the structure editor explains the choice.
    to_activity = client.patch(f"{API}/columns/{review}", json={"kind": "activity"}, headers=master)
    assert to_activity.status_code == 200, to_activity.text
    assert to_activity.json()["kind"] == "activity"

    back_to_queue = client.patch(f"{API}/columns/{review}", json={"kind": "queue"}, headers=master)
    assert back_to_queue.status_code == 200, back_to_queue.text
    assert back_to_queue.json()["kind"] == "queue"

    board = get_board(client, master, board_id)
    assert_dense(board)
    assert points(board) == (columns["Build"]["id"], columns["Delivered"]["id"])


def test_column_shape_rules_are_refused_with_a_readable_conflict(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)

    second_envelope = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Another envelope", "segment": "upstream", "kind": "envelope"},
        headers=master,
    )
    assert second_envelope.status_code == 409
    assert second_envelope.json()["error"]["code"] == "envelope_column_exists"

    envelope_downstream = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Wrong side", "segment": "downstream", "kind": "envelope"},
        headers=master,
    )
    assert envelope_downstream.status_code == 409
    assert envelope_downstream.json()["error"]["code"] == "envelope_must_be_upstream"

    done_upstream = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Wrong side", "segment": "upstream", "kind": "done"},
        headers=master,
    )
    assert done_upstream.status_code == 409
    assert done_upstream.json()["error"]["code"] == "done_must_be_downstream"

    # An envelope column is the first column or it is nothing — asking for any
    # other slot is refused rather than silently snapped back to zero.
    moved_envelope = client.patch(
        f"{API}/columns/{columns['Envelope']['id']}",
        json={"position": 3},
        headers=master,
    )
    assert moved_envelope.status_code == 409
    assert moved_envelope.json()["error"]["code"] == "envelope_must_be_first"
    assert get_board(client, master, board_id)["columns"][0]["name"] == "Envelope"

    bad_range = client.patch(
        f"{API}/columns/{columns['Review']['id']}",
        json={"wip_min": 9, "wip_max": 2},
        headers=master,
    )
    assert bad_range.status_code == 400
    assert bad_range.json()["error"]["code"] == "invalid_wip_range"


def test_an_out_of_range_position_is_refused_instead_of_silently_clamped(
    client, master, new_board
) -> None:
    board_id = new_board()["id"]
    build_flow(client, master, board_id)

    too_far = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Nowhere", "segment": "downstream", "kind": "queue", "position": 99},
        headers=master,
    )
    assert too_far.status_code == 400
    assert too_far.json()["error"]["code"] == "invalid_position"
    assert too_far.json()["error"]["details"]["max"] == 6

    negative = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Nowhere", "segment": "downstream", "kind": "queue", "position": -1},
        headers=master,
    )
    assert negative.status_code == 400
    assert negative.json()["error"]["code"] == "invalid_position"

    # Nothing was written by either attempt.
    assert len(get_board(client, master, board_id)["columns"]) == 6

    # An in-range index inserts exactly where it was asked to.
    inserted = add_column(
        client,
        master,
        board_id,
        name="Ready",
        segment="downstream",
        kind="queue",
        position=3,
    )
    board = get_board(client, master, board_id)
    assert names_in_order(board) == [
        "Envelope",
        "Intake",
        "Shaping",
        "Ready",
        "Build",
        "Review",
        "Delivered",
    ]
    assert_dense(board)
    assert points(board)[0] == inserted["id"], "the new first downstream column commits"


def test_the_last_done_column_cannot_be_re_kinded_away(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    delivered = columns["Delivered"]["id"]

    refused = client.patch(f"{API}/columns/{delivered}", json={"kind": "queue"}, headers=master)
    assert refused.status_code == 409
    body = refused.json()["error"]
    assert body["code"] == "invalid_structure"
    assert body["details"]["reason"] == "delivery_point_required"
    assert body["details"]["column"] == "Delivered"

    board = get_board(client, master, board_id)
    assert points(board)[1] == delivered

    # With a second 'done' column in place the first one is free to change.
    second_done = add_column(
        client, master, board_id, name="Shipped", segment="downstream", kind="done"
    )
    allowed = client.patch(f"{API}/columns/{delivered}", json={"kind": "queue"}, headers=master)
    assert allowed.status_code == 200, allowed.text
    board = get_board(client, master, board_id)
    assert points(board)[1] == second_done["id"], "the delivery point moved to the last done"


# ---------------------------------------------------------------------------
# segment changes & reordering
# ---------------------------------------------------------------------------


def test_changing_a_segment_moves_the_commitment_point(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    build, review, delivered = (
        columns["Build"]["id"],
        columns["Review"]["id"],
        columns["Delivered"]["id"],
    )

    assert points(get_board(client, master, board_id))[0] == build

    # downstream -> upstream: Build leaves the delivery half and hands the
    # commitment point to whatever is now first downstream.
    to_upstream = client.patch(
        f"{API}/columns/{build}", json={"segment": "upstream"}, headers=master
    )
    assert to_upstream.status_code == 200, to_upstream.text
    assert to_upstream.json()["segment"] == "upstream"
    assert to_upstream.json()["is_commitment_point"] is False
    board = get_board(client, master, board_id)
    assert_dense(board)
    assert names_in_order(board) == [
        "Envelope",
        "Intake",
        "Shaping",
        "Build",
        "Review",
        "Delivered",
    ]
    assert points(board) == (review, delivered)

    # upstream -> downstream again, this time asking for the first downstream
    # slot: the commitment point follows the column back.
    to_downstream = client.patch(
        f"{API}/columns/{build}",
        json={"segment": "downstream", "position": 3},
        headers=master,
    )
    assert to_downstream.status_code == 200, to_downstream.text
    board = get_board(client, master, board_id)
    assert_dense(board)
    assert points(board) == (build, delivered)
    assert board["columns"][3]["id"] == build


def test_reorder_keeps_positions_dense_and_the_commitment_point_true(
    client, master, new_board
) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    ids = {name: column["id"] for name, column in columns.items()}

    reordered = client.post(
        f"{API}/boards/{board_id}/columns/reorder",
        json={
            "ordered_ids": [
                ids["Envelope"],
                ids["Shaping"],
                ids["Intake"],
                ids["Review"],
                ids["Build"],
                ids["Delivered"],
            ]
        },
        headers=master,
    )
    assert reordered.status_code == 200, reordered.text
    assert [c["name"] for c in reordered.json()["items"]] == [
        "Envelope",
        "Shaping",
        "Intake",
        "Review",
        "Build",
        "Delivered",
    ]
    board = get_board(client, master, board_id)
    assert_dense(board)
    assert points(board) == (ids["Review"], ids["Delivered"]), (
        "Review now leads the downstream run, so it is the commitment point"
    )


def test_reorder_refuses_foreign_ids_partial_lists_and_repeats(client, master, new_board) -> None:
    board_id = new_board()["id"]
    other_board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    ids = [column["id"] for column in columns.values()]
    foreign = add_column(
        client, master, other_board_id, name="Elsewhere", segment="downstream", kind="queue"
    )

    not_ours = client.post(
        f"{API}/boards/{board_id}/columns/reorder",
        json={"ordered_ids": [*ids, foreign["id"]]},
        headers=master,
    )
    assert not_ours.status_code == 400
    assert not_ours.json()["error"]["code"] == "column_not_on_board"

    partial = client.post(
        f"{API}/boards/{board_id}/columns/reorder",
        json={"ordered_ids": ids[:3]},
        headers=master,
    )
    assert partial.status_code == 400
    body = partial.json()["error"]
    assert body["code"] == "incomplete_column_order"
    assert body["details"]["expected"] == 6 and body["details"]["received"] == 3
    assert {entry["name"] for entry in body["details"]["missing"]} == {
        "Build",
        "Review",
        "Delivered",
    }

    repeated = client.post(
        f"{API}/boards/{board_id}/columns/reorder",
        json={"ordered_ids": [*ids, ids[0]]},
        headers=master,
    )
    assert repeated.status_code == 400
    assert repeated.json()["error"]["code"] == "duplicate_column_in_order"

    # None of the three rejected orders touched the board.
    board = get_board(client, master, board_id)
    assert names_in_order(board) == [
        "Envelope",
        "Intake",
        "Shaping",
        "Build",
        "Review",
        "Delivered",
    ]
    assert_dense(board)


# ---------------------------------------------------------------------------
# delete, drain, and the history that survives both
# ---------------------------------------------------------------------------


def test_deleting_an_occupied_column_is_refused_with_the_card_count(
    client, master, new_board
) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    intake = columns["Intake"]["id"]
    for index in range(3):
        make_card(client, master, board_id, intake, f"Occupant {index}")

    refused = client.delete(f"{API}/columns/{intake}", headers=master)
    assert refused.status_code == 409
    body = refused.json()["error"]
    assert body["code"] == "column_not_empty"
    assert body["details"]["card_count"] == 3
    assert body["details"]["column"] == "Intake"
    assert body["details"]["drain_endpoint"].endswith(f"/columns/{intake}/drain")

    # The column — and therefore the cards that cascade from it — is untouched.
    assert len(get_board(client, master, board_id)["columns"]) == 6


def test_drain_then_delete_moves_every_card_and_keeps_its_history(
    client, master, new_board, db: Database
) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    intake, build = columns["Intake"]["id"], columns["Build"]["id"]
    cards = [make_card(client, master, board_id, intake, f"Card {i}") for i in range(3)]
    assert all(card["committed_at"] is None for card in cards), "upstream is not committed"

    drained = client.post(
        f"{API}/columns/{intake}/drain", json={"to_column_id": build}, headers=master
    )
    assert drained.status_code == 200, drained.text
    result = drained.json()
    assert result["moved"] == 3
    assert result["is_backward"] is False
    assert result["committed"] == 3, "Build is the commitment point, so the cards commit"
    assert result["from_column_id"] == intake and result["to_column_id"] == build

    for card in cards:
        moved = client.get(f"{API}/cards/{card['id']}", headers=master).json()
        assert moved["column_id"] == build
        assert moved["committed_at"] is not None
        movements = moved["movements"]
        assert len(movements) == 2, "creation plus the drain, nothing lost"
        drain_row = movements[-1]
        assert drain_row["from_column_id"] == intake
        assert drain_row["to_column_id"] == build
        assert drain_row["from_segment"] == "upstream"
        assert drain_row["to_segment"] == "downstream"
        assert drain_row["is_backward"] is False
        assert drain_row["source"] == "ui"
        assert drain_row["seconds_in_from_column"] is not None
        assert drain_row["seconds_in_from_column"] >= 0
        assert "Intake" in (drain_row["note"] or "")
        labels = [
            entry["label"]
            for entry in client.get(f"{API}/cards/{card['id']}/history", headers=master).json()[
                "timeline"
            ]
        ]
        assert any(label == "Moved from “Intake” to “Build”" for label in labels)

    # The WIP badge the UI repaints after a drain agrees with the move.
    wip = get_wip(client, master, board_id)
    assert wip["columns"][intake]["count"] == 0
    assert wip["columns"][build]["count"] == 3

    # Before the delete the history is complete: two rows per card, the second
    # one measuring how long the card sat in Intake.
    for card in cards:
        rows = db.q(
            "SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at, id",
            card["id"],
        )
        assert [row["to_column_id"] for row in rows] == [intake, build]

    deleted = client.delete(f"{API}/columns/{intake}", headers=master)
    assert deleted.status_code == 200, deleted.text
    assert "3 movement records" in deleted.json()["message"], (
        "the response says what the delete cost, it does not hide it"
    )
    board = get_board(client, master, board_id)
    assert "Intake" not in names_in_order(board)
    assert_dense(board)

    for card in cards:
        rows = db.q(
            "SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at, id",
            card["id"],
        )
        # The drain row survives the delete — the card's arrival in Build, the
        # time it spent before that and the audit trail are all still there.
        # Only the row that *ended* in the deleted column is cascaded away by
        # `card_movements.to_column_id ... ON DELETE CASCADE`, which is why the
        # delete response reports the loss.
        assert len(rows) == 1
        assert rows[0]["to_column_id"] == build
        assert rows[0]["from_column_id"] is None  # ON DELETE SET NULL
        assert rows[0]["seconds_in_from_column"] is not None
        assert rows[0]["source"] == "ui"
        events = db.q(
            "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'moved'",
            card["id"],
        )
        assert any(
            event["old_value"] == "Intake" and event["new_value"] == "Build" for event in events
        ), "card_events has no foreign key to columns, so the story is still readable"
        card_now = client.get(f"{API}/cards/{card['id']}", headers=master).json()
        assert card_now["column_id"] == build
        assert card_now["committed_at"] is not None


def test_drain_may_go_backwards_and_says_so_in_the_audit_trail(
    client, master, new_board, db: Database
) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    build, intake = columns["Build"]["id"], columns["Intake"]["id"]
    card = make_card(client, master, board_id, build, "Started too early")

    drained = client.post(
        f"{API}/columns/{build}/drain", json={"to_column_id": intake}, headers=master
    )
    assert drained.status_code == 200, drained.text
    assert drained.json()["moved"] == 1
    assert drained.json()["is_backward"] is True

    rows = db.q("SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at, id", card["id"])
    assert rows[-1]["is_backward"] == 1
    assert rows[-1]["from_column_id"] == build and rows[-1]["to_column_id"] == intake
    overrides = db.q(
        "SELECT * FROM card_events WHERE card_id = ? AND field = 'backward_override'",
        card["id"],
    )
    assert len(overrides) == 1, "a backward drain is an exception and is audited as one"

    # Commitment survives the retreat: the card was committed and stays committed.
    detail = client.get(f"{API}/cards/{card['id']}", headers=master).json()
    assert detail["column_id"] == intake
    assert detail["committed_at"] is not None

    # And now the mis-created column can finally be removed.
    assert client.delete(f"{API}/columns/{build}", headers=master).status_code == 200


def test_draining_an_envelope_column_opens_its_sealed_demands(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    envelope, intake = columns["Envelope"]["id"], columns["Intake"]["id"]
    sealed = make_card(client, master, board_id, envelope, "Unread demand", envelope_state="sealed")
    assert sealed["envelope_state"] == "sealed"

    drained = client.post(
        f"{API}/columns/{envelope}/drain", json={"to_column_id": intake}, headers=master
    )
    assert drained.status_code == 200, drained.text
    assert drained.json()["moved"] == 1
    assert drained.json()["opened_envelopes"] == 1

    # A sealed card outside an envelope column could neither move nor be opened,
    # so draining opens it rather than stranding it.
    detail = client.get(f"{API}/cards/{sealed['id']}", headers=master).json()
    assert detail["envelope_state"] == "opened"
    assert detail["opened_at"] is not None
    assert detail["column_id"] == intake
    assert client.delete(f"{API}/columns/{envelope}", headers=master).status_code == 200


def test_drain_refuses_a_foreign_column_and_itself(client, master, new_board) -> None:
    board_id = new_board()["id"]
    other_board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    intake = columns["Intake"]["id"]
    make_card(client, master, board_id, intake, "Stays put")
    foreign = add_column(
        client, master, other_board_id, name="Elsewhere", segment="downstream", kind="queue"
    )

    itself = client.post(
        f"{API}/columns/{intake}/drain", json={"to_column_id": intake}, headers=master
    )
    assert itself.status_code == 409
    assert itself.json()["error"]["code"] == "same_column_move"

    elsewhere = client.post(
        f"{API}/columns/{intake}/drain",
        json={"to_column_id": foreign["id"]},
        headers=master,
    )
    assert elsewhere.status_code == 400
    assert elsewhere.json()["error"]["code"] == "column_board_mismatch"

    missing = client.post(
        f"{API}/columns/{intake}/drain",
        json={"to_column_id": "no-such-column"},
        headers=master,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "column_not_found"

    # Every rejection left the card exactly where it was.
    assert get_wip(client, master, board_id)["columns"][intake]["count"] == 1

    empty = columns["Review"]["id"]
    drained = client.post(
        f"{API}/columns/{empty}/drain", json={"to_column_id": intake}, headers=master
    )
    assert drained.status_code == 200, drained.text
    assert drained.json()["moved"] == 0, "draining an empty column is a no-op, not an error"


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


def test_group_create_rename_wip_and_delete(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)

    created = client.post(
        f"{API}/boards/{board_id}/groups",
        json={
            "name": "Delivery",
            "wip_min": 1,
            "wip_max": 6,
            "color": "#3b7ea1",
            "column_ids": [columns["Build"]["id"], columns["Review"]["id"]],
        },
        headers=master,
    )
    assert created.status_code == 201, created.text
    group_id = created.json()["id"]

    board = get_board(client, master, board_id)
    grouped = {c["name"] for c in board["columns"] if c["group_id"] == group_id}
    assert grouped == {"Build", "Review"}

    renamed = client.patch(
        f"{API}/groups/{group_id}",
        json={"name": "Delivery & verification", "wip_max": 9},
        headers=master,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Delivery & verification"
    assert renamed.json()["wip_max"] == 9
    assert renamed.json()["wip_min"] == 1

    bad_range = client.patch(f"{API}/groups/{group_id}", json={"wip_min": 12}, headers=master)
    assert bad_range.status_code == 400
    assert bad_range.json()["error"]["code"] == "invalid_wip_range"

    # The span can be replaced, and the group header follows its first column.
    respanned = client.patch(
        f"{API}/groups/{group_id}",
        json={"column_ids": [columns["Intake"]["id"], columns["Shaping"]["id"]]},
        headers=master,
    )
    assert respanned.status_code == 200, respanned.text
    board = get_board(client, master, board_id)
    grouped = {c["name"] for c in board["columns"] if c["group_id"] == group_id}
    assert grouped == {"Intake", "Shaping"}

    deleted = client.delete(f"{API}/groups/{group_id}", headers=master)
    assert deleted.status_code == 200, deleted.text
    board = get_board(client, master, board_id)
    assert board["groups"] == []
    assert all(column["group_id"] is None for column in board["columns"])
    assert len(board["columns"]) == 6, "deleting a group keeps its columns on the board"
    assert_dense(board)


def test_a_non_contiguous_group_is_refused_and_names_the_columns(client, master, new_board) -> None:
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)

    broken = client.post(
        f"{API}/boards/{board_id}/groups",
        json={
            "name": "Broken",
            "column_ids": [columns["Intake"]["id"], columns["Build"]["id"]],
        },
        headers=master,
    )
    assert broken.status_code == 409
    body = broken.json()["error"]
    assert body["code"] == "group_not_contiguous"
    assert "Intake" in body["message"] and "Build" in body["message"]
    assert "Shaping" in body["message"], "the message names what sits in between"
    assert {c["name"] for c in body["details"]["columns"]} == {"Intake", "Build"}
    assert {c["name"] for c in body["details"]["separated_by"]} == {"Shaping"}
    # Rejected atomically: no half-written group is left behind.
    assert get_board(client, master, board_id)["groups"] == []

    ok = client.post(
        f"{API}/boards/{board_id}/groups",
        json={
            "name": "Discovery",
            "column_ids": [columns["Intake"]["id"], columns["Shaping"]["id"]],
        },
        headers=master,
    )
    assert ok.status_code == 201, ok.text
    group_id = ok.json()["id"]

    # A PATCH that breaks contiguity is refused the same way, and the group keeps
    # the span it had.
    broken_patch = client.patch(
        f"{API}/groups/{group_id}",
        json={"column_ids": [columns["Intake"]["id"], columns["Review"]["id"]]},
        headers=master,
    )
    assert broken_patch.status_code == 409
    assert broken_patch.json()["error"]["code"] == "group_not_contiguous"
    board = get_board(client, master, board_id)
    assert {c["name"] for c in board["columns"] if c["group_id"] == group_id} == {
        "Intake",
        "Shaping",
    }

    # Re-segmenting a member out of the run breaks the span too, and is refused
    # before the column is moved.
    split = client.patch(
        f"{API}/columns/{columns['Shaping']['id']}",
        json={"segment": "downstream"},
        headers=master,
    )
    assert split.status_code == 409
    assert split.json()["error"]["code"] == "group_not_contiguous"
    assert get_board(client, master, board_id)["columns"][2]["segment"] == "upstream"


# ---------------------------------------------------------------------------
# WIP limits at all three scopes (SPEC-v2 R2.2)
# ---------------------------------------------------------------------------


def test_wip_limits_are_editable_at_column_group_and_flow_scope(client, master, new_board) -> None:
    board_id = new_board(wip_min=None, wip_max=None)["id"]
    columns = build_flow(client, master, board_id)
    build, review = columns["Build"]["id"], columns["Review"]["id"]
    group = client.post(
        f"{API}/boards/{board_id}/groups",
        json={"name": "Delivery", "column_ids": [build, review]},
        headers=master,
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]

    for index in range(2):
        make_card(client, master, board_id, build, f"In build {index}")

    def state(scope: str, key: str) -> str:
        return get_wip(client, master, board_id)[scope][key]["state"]

    # -- column scope: the same two cards read four different ways -----------
    def patch_column(**body: Any) -> dict[str, Any]:
        response = client.patch(f"{API}/columns/{build}", json=body, headers=master)
        assert response.status_code == 200, response.text
        return response.json()

    assert patch_column(wip_min=3, wip_max=8)["wip_min"] == 3
    assert state("columns", build) == "under", "2 in a column asking for 3"
    patch_column(wip_min=1)
    assert state("columns", build) == "ok"
    patch_column(wip_max=2)
    assert state("columns", build) == "at_limit"
    patch_column(wip_max=1)
    assert state("columns", build) == "over"
    # "No limit" is expressed by clearing the maximum, not by a magic number.
    patch_column(wip_max=None)
    assert state("columns", build) == "ok"
    assert get_wip(client, master, board_id)["columns"][build]["label"] == "2"

    # -- group scope ---------------------------------------------------------
    def patch_group(**body: Any) -> dict[str, Any]:
        response = client.patch(f"{API}/groups/{group_id}", json=body, headers=master)
        assert response.status_code == 200, response.text
        return response.json()

    patch_group(wip_min=4, wip_max=9)
    assert state("groups", group_id) == "under"
    patch_group(wip_min=1)
    assert state("groups", group_id) == "ok"
    patch_group(wip_max=2)
    assert state("groups", group_id) == "at_limit"
    patch_group(wip_max=1)
    assert state("groups", group_id) == "over"

    # A column edit can flip the *group* without touching the group's policy:
    # this is exactly why the UI re-fetches /wip after every change.
    make_card(client, master, board_id, review, "In review")
    assert get_wip(client, master, board_id)["groups"][group_id]["count"] == 3

    # -- whole-flow scope ----------------------------------------------------
    def patch_board(**body: Any) -> dict[str, Any]:
        response = client.patch(f"{API}/boards/{board_id}", json=body, headers=master)
        assert response.status_code == 200, response.text
        return response.json()

    flow = get_wip(client, master, board_id)["flow"]
    assert flow["count"] == 3, "committed and not yet delivered (SPEC §7.1)"
    patch_board(wip_min=6, wip_max=9)
    assert get_wip(client, master, board_id)["flow"]["state"] == "under"
    patch_board(wip_min=1, wip_max=9)
    assert get_wip(client, master, board_id)["flow"]["state"] == "ok"
    patch_board(wip_max=3)
    assert get_wip(client, master, board_id)["flow"]["state"] == "at_limit"
    patch_board(wip_max=2)
    assert get_wip(client, master, board_id)["flow"]["state"] == "over"

    invalid = client.patch(
        f"{API}/boards/{board_id}", json={"wip_min": 8, "wip_max": 2}, headers=master
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "invalid_wip_range"


# ---------------------------------------------------------------------------
# RBAC (SPEC §4 — structure is master/administrator only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_structure_writes_are_refused_for_editors_and_viewers(
    client, auth, master, new_board, role: str
) -> None:
    headers = auth(role)
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    build, review = columns["Build"]["id"], columns["Review"]["id"]
    group = client.post(
        f"{API}/boards/{board_id}/groups",
        json={"name": "Delivery", "column_ids": [build, review]},
        headers=master,
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    make_card(client, master, board_id, build, "Someone else's work")

    attempts = [
        client.post(
            f"{API}/boards/{board_id}/columns",
            json={"name": "Sneaky", "segment": "downstream", "kind": "queue"},
            headers=headers,
        ),
        client.patch(f"{API}/columns/{review}", json={"name": "Renamed"}, headers=headers),
        client.patch(f"{API}/columns/{review}", json={"wip_max": 99}, headers=headers),
        client.delete(f"{API}/columns/{review}", headers=headers),
        client.post(
            f"{API}/boards/{board_id}/columns/reorder",
            json={"ordered_ids": [c["id"] for c in columns.values()]},
            headers=headers,
        ),
        client.post(f"{API}/columns/{build}/drain", json={"to_column_id": review}, headers=headers),
        client.post(f"{API}/boards/{board_id}/groups", json={"name": "Theirs"}, headers=headers),
        client.patch(f"{API}/groups/{group_id}", json={"wip_max": 99}, headers=headers),
        client.delete(f"{API}/groups/{group_id}", headers=headers),
        client.patch(f"{API}/boards/{board_id}", json={"wip_max": 99}, headers=headers),
    ]
    for response in attempts:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "insufficient_role"

    # Reading the structure stays open to them — the UI shows it read-only.
    assert client.get(f"{API}/boards/{board_id}", headers=headers).status_code == 200
    assert client.get(f"{API}/boards/{board_id}/wip", headers=headers).status_code == 200

    # Nothing leaked through.
    board = get_board(client, master, board_id)
    assert names_in_order(board) == [
        "Envelope",
        "Intake",
        "Shaping",
        "Build",
        "Review",
        "Delivered",
    ]
    assert get_wip(client, master, board_id)["columns"][build]["count"] == 1


def test_the_simulator_role_shapes_only_its_own_boards(client, auth, master, new_board) -> None:
    """SPEC §4: ``simulator`` owns ``kind='simulator'`` boards and nothing else.

    The structure routes declare a role gate wide enough to let this role
    through; the board-kind decision needs the board row and so stays in
    ``require_structure_write``.
    """
    simulator = auth("simulator")
    operational_id = new_board()["id"]
    build_flow(client, master, operational_id)
    sandbox_id = new_board(kind="simulator")["id"]

    refused = client.post(
        f"{API}/boards/{operational_id}/columns",
        json={"name": "Not yours", "segment": "downstream", "kind": "queue"},
        headers=simulator,
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "insufficient_role"

    allowed = client.post(
        f"{API}/boards/{sandbox_id}/columns",
        json={"name": "Scenario", "segment": "downstream", "kind": "queue"},
        headers=simulator,
    )
    assert allowed.status_code == 201, allowed.text
    assert (
        client.delete(f"{API}/columns/{allowed.json()['id']}", headers=simulator).status_code == 200
    )


@pytest.mark.parametrize("role", ["master", "administrator"])
def test_structure_writes_are_allowed_for_master_and_administrator(
    client, auth, master, new_board, role: str
) -> None:
    headers = auth(role)
    board_id = new_board()["id"]
    columns = build_flow(client, master, board_id)
    ids = {name: column["id"] for name, column in columns.items()}

    created = add_column(
        client, headers, board_id, name="Staging", segment="downstream", kind="queue"
    )
    assert (
        client.patch(
            f"{API}/columns/{created['id']}",
            json={"name": "Staging area", "wip_max": 4},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{API}/boards/{board_id}/columns/reorder",
            json={
                "ordered_ids": [
                    *[ids[n] for n in ("Envelope", "Intake", "Shaping", "Build", "Review")],
                    created["id"],
                    ids["Delivered"],
                ]
            },
            headers=headers,
        ).status_code
        == 200
    )

    group = client.post(
        f"{API}/boards/{board_id}/groups",
        json={"name": "Delivery", "column_ids": [ids["Build"], ids["Review"]]},
        headers=headers,
    )
    assert group.status_code == 201, group.text
    assert (
        client.patch(
            f"{API}/groups/{group.json()['id']}", json={"wip_max": 7}, headers=headers
        ).status_code
        == 200
    )
    assert client.delete(f"{API}/groups/{group.json()['id']}", headers=headers).status_code == 200

    make_card(client, master, board_id, created["id"], "Draining me")
    drained = client.post(
        f"{API}/columns/{created['id']}/drain",
        json={"to_column_id": ids["Review"]},
        headers=headers,
    )
    assert drained.status_code == 200, drained.text
    assert drained.json()["moved"] == 1
    assert client.delete(f"{API}/columns/{created['id']}", headers=headers).status_code == 200
    assert_dense(get_board(client, master, board_id))
