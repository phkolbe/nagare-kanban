"""Card movement, WIP enforcement, audit trail and history — SPEC §6 in full.

This module is the CARDS agent's contract. Every test here builds its own
throwaway board (hard-deleted afterwards) rather than mutating the shared,
session-scoped "Nagare Flow" fixture that other test modules read from — WIP
counts and forward-only positions are exactly the kind of state a shared board
would make flaky to share across test functions.

Two board shapes cover everything SPEC §6 asks for:

``flow_board``     the ``simple_delivery`` template — Backlog (upstream,
                    queue) → In Progress (downstream, activity, the
                    commitment point) → Review (downstream, queue) → Done
                    (downstream, done, the delivery point) — with a
                    "Delivery" group spanning In Progress and Review.
``envelope_board``  the ``upstream_downstream`` template, needed only for the
                    sealed-envelope rule (``simple_delivery`` has no envelope
                    column).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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


def _make_board(client, master, **overrides: Any) -> dict[str, Any]:
    body = {
        "name": overrides.pop("name", f"Movement Test {uuid.uuid4().hex[:8]}"),
        "kind": overrides.pop("kind", "operational"),
        "template": overrides.pop("template", "simple_delivery"),
    }
    body.update(overrides)
    response = client.post(f"{API}/boards", json=body, headers=master)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def flow_board(client, master) -> Iterator[dict[str, Any]]:
    """A fresh ``simple_delivery`` board: Backlog → In Progress → Review → Done."""
    board = _make_board(client, master, template="simple_delivery")
    yield board
    client.delete(f"{API}/boards/{board['id']}?hard=true", headers=master)


@pytest.fixture()
def envelope_board(client, master) -> Iterator[dict[str, Any]]:
    """A fresh ``upstream_downstream`` board, for the sealed-envelope rule."""
    board = _make_board(client, master, template="upstream_downstream")
    yield board
    client.delete(f"{API}/boards/{board['id']}?hard=true", headers=master)


@pytest.fixture()
def project_for_filters(client, master) -> Iterator[dict[str, Any]]:
    body = {
        "name": f"Filter Project {uuid.uuid4().hex[:6]}",
        "key": f"F{uuid.uuid4().hex[:5]}".upper(),
    }
    response = client.post(f"{API}/projects", json=body, headers=master)
    assert response.status_code == 201, response.text
    project = response.json()
    yield project
    client.delete(f"{API}/projects/{project['id']}", headers=master)


def col_id(board: dict[str, Any], name: str) -> str:
    for column in board["columns"]:
        if column["name"] == name:
            return column["id"]
    raise KeyError(f"no column named {name!r} on board {board.get('id')}")


def make_card(
    client, headers: dict[str, str], board_id: str, column_id: str, **kwargs: Any
) -> dict[str, Any]:
    body = {"board_id": board_id, "title": kwargs.pop("title", "Card"), "column_id": column_id}
    body.update(kwargs)
    response = client.post(f"{API}/cards", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def move(client, headers: dict[str, str], card_id: str, to_column_id: str, **kwargs: Any):
    body = {"to_column_id": to_column_id}
    body.update(kwargs)
    return client.post(f"{API}/cards/{card_id}/move", json=body, headers=headers)


# ---------------------------------------------------------------------------
# card creation writes the opening movement (SPEC §2.7 / §6.8)
# ---------------------------------------------------------------------------


def test_card_creation_writes_a_movement_with_null_from_column(
    client, master, flow_board, db: Database
) -> None:
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog, title="Fresh")

    moves = db.q("SELECT * FROM card_movements WHERE card_id = ?", card["id"])
    assert len(moves) == 1
    assert moves[0]["from_column_id"] is None
    assert moves[0]["to_column_id"] == backlog
    assert moves[0]["source"] == "ui"
    assert moves[0]["seconds_in_from_column"] is None

    event = db.q1(
        "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'created'", card["id"]
    )
    assert event is not None


# ---------------------------------------------------------------------------
# forward-only enforcement (SPEC §6.1/§6.2)
# ---------------------------------------------------------------------------


def test_forward_moves_are_allowed_including_multi_column_jumps(client, master, flow_board) -> None:
    backlog = col_id(flow_board, "Backlog")
    done = col_id(flow_board, "Done")
    card = make_card(client, master, flow_board["id"], backlog)

    response = move(client, master, card["id"], done)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["card"]["column_id"] == done
    assert body["committed"] is True
    assert body["delivered"] is True


def test_backward_move_is_blocked_unless_forced_by_a_privileged_role(
    client, auth, flow_board
) -> None:
    editor = auth("editor")
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    card = make_card(client, editor, flow_board["id"], backlog)
    assert move(client, editor, card["id"], in_progress).status_code == 200

    backward = move(client, editor, card["id"], backlog)
    assert backward.status_code == 409
    error = backward.json()["error"]
    assert error["code"] == "backward_move_blocked"
    assert error["details"]["from_column_id"] == in_progress
    assert error["details"]["to_column_id"] == backlog
    assert error["details"]["forced"] is False

    # An editor cannot buy their way past the rule with `force`, either.
    still_blocked = move(client, editor, card["id"], backlog, force=True)
    assert still_blocked.status_code == 409
    assert still_blocked.json()["error"]["code"] == "backward_move_blocked"


def test_forced_backward_move_by_master_records_is_backward_and_an_audit_event(
    client, master, flow_board, db: Database
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    card = make_card(client, master, flow_board["id"], backlog)
    assert move(client, master, card["id"], in_progress).status_code == 200

    forced = move(client, master, card["id"], backlog, force=True)
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["is_backward"] is True
    assert body["movement"]["is_backward"] is True
    assert body["card"]["column_id"] == backlog

    stored = db.q1(
        "SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at DESC, id DESC",
        card["id"],
    )
    assert bool(stored["is_backward"]) is True

    audit = db.q1(
        "SELECT * FROM card_events WHERE card_id = ? AND field = 'backward_override'",
        card["id"],
    )
    assert audit is not None
    assert audit["old_value"] == "In Progress"
    assert audit["new_value"] == "Backlog"


def test_an_administrator_may_also_force_a_backward_move(client, auth, flow_board) -> None:
    admin = auth("administrator")
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    card = make_card(client, admin, flow_board["id"], backlog)
    move(client, admin, card["id"], in_progress)
    forced = move(client, admin, card["id"], backlog, force=True)
    assert forced.status_code == 200, forced.text


# ---------------------------------------------------------------------------
# WIP maxima at every scope (SPEC §6.6)
# ---------------------------------------------------------------------------


def test_column_wip_limit_rejects_the_move_naming_the_column_policy(
    client, master, flow_board
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    patched = client.patch(f"{API}/columns/{in_progress}", json={"wip_max": 1}, headers=master)
    assert patched.status_code == 200, patched.text

    first = make_card(client, master, flow_board["id"], backlog, title="A")
    assert move(client, master, first["id"], in_progress).status_code == 200

    second = make_card(client, master, flow_board["id"], backlog, title="B")
    blocked = move(client, master, second["id"], in_progress)
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "wip_limit_exceeded"
    assert error["details"]["policy"] == "column"
    assert error["details"]["column_id"] == in_progress
    assert error["details"]["current"] == 1
    assert error["details"]["max"] == 1


def test_group_wip_limit_rejects_the_move_naming_the_group_policy(
    client, master, flow_board
) -> None:
    in_progress = col_id(flow_board, "In Progress")
    review = col_id(flow_board, "Review")
    backlog = col_id(flow_board, "Backlog")
    group_id = flow_board["groups"][0]["id"]  # the template's single "Delivery" group
    patched = client.patch(f"{API}/groups/{group_id}", json={"wip_max": 2}, headers=master)
    assert patched.status_code == 200, patched.text

    a = make_card(client, master, flow_board["id"], backlog, title="A")
    assert move(client, master, a["id"], in_progress).status_code == 200
    b = make_card(client, master, flow_board["id"], backlog, title="B")
    assert move(client, master, b["id"], review).status_code == 200

    c = make_card(client, master, flow_board["id"], backlog, title="C")
    blocked = move(client, master, c["id"], in_progress)
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "wip_limit_exceeded"
    assert error["details"]["policy"] == "group"
    assert error["details"]["group_id"] == group_id
    assert error["details"]["current"] == 2
    assert error["details"]["max"] == 2


def test_flow_wip_limit_rejects_the_move_naming_the_flow_policy(client, master, flow_board) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    patched = client.patch(f"{API}/boards/{flow_board['id']}", json={"wip_max": 1}, headers=master)
    assert patched.status_code == 200, patched.text

    a = make_card(client, master, flow_board["id"], backlog, title="A")
    assert move(client, master, a["id"], in_progress).status_code == 200  # commits: flow WIP = 1

    b = make_card(client, master, flow_board["id"], backlog, title="B")
    blocked = move(client, master, b["id"], in_progress)
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "wip_limit_exceeded"
    assert error["details"]["policy"] == "flow"
    assert error["details"]["board_id"] == flow_board["id"]
    assert error["details"]["current"] == 1
    assert error["details"]["max"] == 1


def test_expedite_bypasses_only_the_column_wip_maximum(client, master, flow_board) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    client.patch(f"{API}/columns/{in_progress}", json={"wip_max": 1}, headers=master)

    first = make_card(client, master, flow_board["id"], backlog, title="A")
    assert move(client, master, first["id"], in_progress).status_code == 200

    urgent = make_card(
        client, master, flow_board["id"], backlog, title="Urgent", service_class="expedite"
    )
    allowed = move(client, master, urgent["id"], in_progress)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["wip_override"] is True
    assert any("xpedite" in warning for warning in body["warnings"])
    assert body["card"]["column_id"] == in_progress

    # The column is now genuinely over its maximum (2 cards against a max of 1)
    # — expedite bypassed the check, it did not raise the limit.
    wip = client.get(f"{API}/boards/{flow_board['id']}/wip", headers=master).json()
    assert wip["columns"][in_progress]["count"] == 2
    assert wip["columns"][in_progress]["state"] == "over"


def test_expedite_still_respects_group_and_flow_maxima(client, master, flow_board) -> None:
    """SPEC §6.6: the expedite bypass is column-scoped only."""
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    client.patch(f"{API}/boards/{flow_board['id']}", json={"wip_max": 1}, headers=master)

    first = make_card(client, master, flow_board["id"], backlog, title="A")
    assert move(client, master, first["id"], in_progress).status_code == 200  # flow WIP = 1

    urgent = make_card(
        client, master, flow_board["id"], backlog, title="Urgent", service_class="expedite"
    )
    blocked = move(client, master, urgent["id"], in_progress)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "wip_limit_exceeded"
    assert blocked.json()["error"]["details"]["policy"] == "flow"


# ---------------------------------------------------------------------------
# committed_at / delivered_at: set once, immutable (SPEC §6.3/§6.4)
# ---------------------------------------------------------------------------


def test_committed_and_delivered_are_stamped_once_and_never_change(
    client, master, flow_board
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    review = col_id(flow_board, "Review")
    done = col_id(flow_board, "Done")

    card = make_card(client, master, flow_board["id"], backlog)
    fresh = client.get(f"{API}/cards/{card['id']}", headers=master).json()
    assert fresh["committed_at"] is None and fresh["delivered_at"] is None

    committed = move(client, master, card["id"], in_progress)
    assert committed.json()["committed"] is True
    committed_at = committed.json()["card"]["committed_at"]
    assert committed_at is not None

    move(client, master, card["id"], review)
    after_review = client.get(f"{API}/cards/{card['id']}", headers=master).json()
    assert after_review["committed_at"] == committed_at, "committed_at must not move"

    delivered = move(client, master, card["id"], done)
    assert delivered.json()["delivered"] is True
    delivered_at = delivered.json()["card"]["delivered_at"]
    assert delivered_at is not None
    assert delivered.json()["card"]["committed_at"] == committed_at

    # A forced backward move afterwards must not touch either timestamp.
    forced_back = move(client, master, card["id"], review, force=True)
    assert forced_back.status_code == 200, forced_back.text
    assert forced_back.json()["card"]["committed_at"] == committed_at
    assert forced_back.json()["card"]["delivered_at"] == delivered_at


# ---------------------------------------------------------------------------
# seconds_in_from_column accuracy (SPEC §6.8)
# ---------------------------------------------------------------------------


def test_seconds_in_from_column_matches_the_backdated_history(
    client, master, flow_board, db: Database
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    card = make_card(client, master, flow_board["id"], backlog)

    # 1h 1m 1s ago: a duration humanize_seconds renders unambiguously as "1h 1m".
    backdated = (datetime.now(UTC) - timedelta(hours=1, minutes=1, seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    db.ex(
        "UPDATE card_movements SET moved_at = ? WHERE card_id = ? AND from_column_id IS NULL",
        backdated,
        card["id"],
    )

    response = move(client, master, card["id"], in_progress)
    assert response.status_code == 200, response.text
    body = response.json()
    seconds = body["seconds_in_from_column"]
    assert seconds is not None
    assert 3661 <= seconds <= 3675, "allowing a little slack for real test-run time"
    assert body["movement"]["seconds_in_from_column"] == seconds
    assert body["duration_in_from_column"] == "1h 1m"


# ---------------------------------------------------------------------------
# sealed envelope cannot move (SPEC §6.5)
# ---------------------------------------------------------------------------


def test_sealed_envelope_card_cannot_be_moved_until_opened(client, master, envelope_board) -> None:
    envelope_col = col_id(envelope_board, "Envelope")
    ideas_col = col_id(envelope_board, "Ideas")
    sealed = client.post(
        f"{API}/cards",
        json={
            "board_id": envelope_board["id"],
            "title": "Still sealed",
            "envelope_state": "sealed",
        },
        headers=master,
    )
    assert sealed.status_code == 201, sealed.text
    card = sealed.json()
    assert card["column_id"] == envelope_col

    blocked = move(client, master, card["id"], ideas_col)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "envelope_sealed"

    opened = client.post(f"{API}/cards/{card['id']}/open-envelope", headers=master)
    assert opened.status_code == 200, opened.text
    now_allowed = move(client, master, card["id"], col_id(envelope_board, "Discovery"))
    assert now_allowed.status_code == 200, now_allowed.text


# ---------------------------------------------------------------------------
# PATCH — one event per changed field
# ---------------------------------------------------------------------------


def test_patch_writes_exactly_one_card_event_per_changed_field(
    client, master, flow_board, db: Database
) -> None:
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog, title="Original", size="s")

    response = client.patch(
        f"{API}/cards/{card['id']}",
        json={"title": "Updated title", "size": "l", "service_class": "expedite"},
        headers=master,
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["changed_fields"]) == {"title", "size", "service_class"}

    events = db.q(
        "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'updated' "
        "ORDER BY created_at, id",
        card["id"],
    )
    assert {e["field"] for e in events} == {"title", "size", "service_class"}
    title_event = next(e for e in events if e["field"] == "title")
    assert title_event["old_value"] == "Original"
    assert title_event["new_value"] == "Updated title"

    # Re-submitting the same values changes nothing: no new events.
    before = len(events)
    unchanged = client.patch(
        f"{API}/cards/{card['id']}", json={"title": "Updated title"}, headers=master
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["changed_fields"] == []
    after = db.q(
        "SELECT * FROM card_events WHERE card_id = ? AND event_type = 'updated'", card["id"]
    )
    assert len(after) == before


# ---------------------------------------------------------------------------
# tasks, blockers, comments
# ---------------------------------------------------------------------------


def test_tasks_lifecycle_and_events(client, master, flow_board, db: Database) -> None:
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog)

    created = client.post(
        f"{API}/cards/{card['id']}/tasks", json={"title": "Write tests"}, headers=master
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["is_done"] is False

    completed = client.patch(f"{API}/tasks/{task['id']}", json={"is_done": True}, headers=master)
    assert completed.status_code == 200, completed.text
    assert completed.json()["is_done"] is True
    assert completed.json()["completed_at"] is not None

    deleted = client.delete(f"{API}/tasks/{task['id']}", headers=master)
    assert deleted.status_code == 200 and deleted.json()["ok"] is True

    event_types = {
        e["event_type"] for e in db.q("SELECT * FROM card_events WHERE card_id = ?", card["id"])
    }
    assert {"task_added", "task_completed"} <= event_types


def test_block_and_unblock_lifecycle(client, master, flow_board) -> None:
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog)

    blocked = client.post(
        f"{API}/cards/{card['id']}/block",
        json={"reason": "Waiting on design", "category": "dependency"},
        headers=master,
    )
    assert blocked.status_code == 201, blocked.text
    assert blocked.json()["ended_at"] is None

    again = client.post(
        f"{API}/cards/{card['id']}/block", json={"reason": "Another reason"}, headers=master
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "card_already_blocked"

    assert client.get(f"{API}/cards/{card['id']}", headers=master).json()["is_blocked"] is True

    unblocked = client.post(f"{API}/cards/{card['id']}/unblock", headers=master)
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["ended_at"] is not None

    assert client.get(f"{API}/cards/{card['id']}", headers=master).json()["is_blocked"] is False

    redundant = client.post(f"{API}/cards/{card['id']}/unblock", headers=master)
    assert redundant.status_code == 409
    assert redundant.json()["error"]["code"] == "card_not_blocked"


def test_comments_lifecycle_and_author_restriction(client, auth, flow_board) -> None:
    master = auth("master")
    editor = auth("editor")
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog)

    created = client.post(
        f"{API}/cards/{card['id']}/comments", json={"body": "First comment"}, headers=master
    )
    assert created.status_code == 201, created.text
    comment_id = created.json()["id"]

    hijack_attempt = client.patch(
        f"{API}/comments/{comment_id}", json={"body": "Hijacked"}, headers=editor
    )
    assert hijack_attempt.status_code == 403
    assert hijack_attempt.json()["error"]["code"] == "comment_not_yours"

    edited = client.patch(
        f"{API}/comments/{comment_id}", json={"body": "Edited by author"}, headers=master
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["body"] == "Edited by author"

    listed = client.get(f"{API}/cards/{card['id']}/comments", headers=master)
    assert listed.status_code == 200 and listed.json()["total"] == 1

    deleted = client.delete(f"{API}/comments/{comment_id}", headers=master)
    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert client.get(f"{API}/cards/{card['id']}/comments", headers=master).json()["total"] == 0


# ---------------------------------------------------------------------------
# GET /cards/{id}/history — ordering, dwell, touch/wait totals
# ---------------------------------------------------------------------------


def test_history_orders_entries_and_reports_touch_and_wait_totals(
    client, master, flow_board, db: Database
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    review = col_id(flow_board, "Review")
    done = col_id(flow_board, "Done")

    card = make_card(client, master, flow_board["id"], backlog)
    assert move(client, master, card["id"], in_progress).status_code == 200
    assert move(client, master, card["id"], review).status_code == 200
    assert move(client, master, card["id"], done).status_code == 200

    moves = db.q("SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at, id", card["id"])
    assert len(moves) == 4
    # Four moves executed back-to-back can legitimately land in the same
    # wall-clock second (moved_at has one-second resolution); the merged
    # timeline only orders by `(at, rank, id)`, and ties among same-kind
    # entries fall back to comparing ids, which is not chronological. Spacing
    # the recorded timestamps a second apart removes that ambiguity so this
    # test pins ordering rather than id-shuffle luck.
    base = datetime.now(UTC) - timedelta(hours=2)
    for index, row in enumerate(moves):
        stamp = (base + timedelta(seconds=index)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.ex("UPDATE card_movements SET moved_at = ? WHERE id = ?", stamp, row["id"])
    # Pin the dwell each move closes so touch/wait totals are deterministic:
    # 100s waiting in Backlog (queue), 300s working In Progress (activity),
    # 50s waiting in Review (queue). Done is terminal — its open interval
    # never counts towards touch or wait.
    db.ex("UPDATE card_movements SET seconds_in_from_column = 100 WHERE id = ?", moves[1]["id"])
    db.ex("UPDATE card_movements SET seconds_in_from_column = 300 WHERE id = ?", moves[2]["id"])
    db.ex("UPDATE card_movements SET seconds_in_from_column = 50 WHERE id = ?", moves[3]["id"])

    history = client.get(f"{API}/cards/{card['id']}/history", headers=master)
    assert history.status_code == 200, history.text
    body = history.json()

    timestamps = [entry["at"] for entry in body["timeline"]]
    assert timestamps == sorted(timestamps)
    move_entries = [e for e in body["timeline"] if e["kind"] == "movement"]
    assert [e["type"] for e in move_entries] == ["created", "moved", "moved", "moved"]

    totals = body["totals"]
    assert totals["touch_seconds"] == 300
    assert totals["wait_seconds"] == 150
    assert totals["flow_efficiency_pct"] == pytest.approx(66.67, abs=0.01)
    assert totals["moves"] == 3
    assert totals["backward_moves"] == 0

    dwell_by_column = {row["column_id"]: row for row in body["dwell"]}
    assert dwell_by_column[backlog]["seconds"] == 100
    assert dwell_by_column[in_progress]["seconds"] == 300
    assert dwell_by_column[review]["seconds"] == 50
    assert dwell_by_column[done]["is_current"] is True

    descending = client.get(
        f"{API}/cards/{card['id']}/history", params={"order": "desc"}, headers=master
    ).json()
    assert [e["at"] for e in descending["timeline"]] == list(reversed(timestamps))


def test_history_includes_a_forced_backward_move_in_backward_moves_total(
    client, master, flow_board
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    card = make_card(client, master, flow_board["id"], backlog)
    move(client, master, card["id"], in_progress)
    move(client, master, card["id"], backlog, force=True)

    history = client.get(f"{API}/cards/{card['id']}/history", headers=master).json()
    assert history["totals"]["backward_moves"] == 1
    backward_entries = [e for e in history["timeline"] if e.get("is_backward")]
    assert len(backward_entries) == 1


# ---------------------------------------------------------------------------
# GET /cards — every documented filter
# ---------------------------------------------------------------------------


def test_list_cards_supports_every_documented_filter(
    client, master, flow_board, project_for_filters, users
) -> None:
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")
    editor_id = users["editor"]["id"]

    plain = make_card(client, master, flow_board["id"], backlog, title="Plain ticket alpha")
    tagged = make_card(
        client,
        master,
        flow_board["id"],
        backlog,
        title="Tagged item",
        project_id=project_for_filters["id"],
        service_class="expedite",
        assignee_id=editor_id,
        tags=["urgent", "beta"],
        demand_type="failure",
        value_area="defect",
        size="l",
        due_date="2026-09-01T00:00:00Z",
    )
    assert move(client, master, tagged["id"], in_progress).status_code == 200
    assert (
        client.post(
            f"{API}/cards/{tagged['id']}/block", json={"reason": "blocked for test"}, headers=master
        ).status_code
        == 201
    )

    def search(**params: Any) -> set[str]:
        response = client.get(
            f"{API}/cards", params={"board_id": flow_board["id"], **params}, headers=master
        )
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert search(project_id=project_for_filters["id"]) == {tagged["id"]}
    assert search(service_class="expedite") == {tagged["id"]}
    assert search(assignee_id=editor_id) == {tagged["id"]}
    assert search(segment="downstream") == {tagged["id"]}
    assert search(segment="upstream") == {plain["id"]}
    assert search(column_id=in_progress) == {tagged["id"]}
    assert search(tag="beta") == {tagged["id"]}
    assert search(q="alpha") == {plain["id"]}
    assert search(is_blocked=True) == {tagged["id"]}
    assert search(demand_type="failure") == {tagged["id"]}
    assert search(value_area="defect") == {tagged["id"]}
    assert search(size="l") == {tagged["id"]}
    assert search(due_before="2026-12-31T00:00:00Z") == {tagged["id"]}
    assert search() == {plain["id"], tagged["id"]}

    archived = client.delete(f"{API}/cards/{plain['id']}", headers=master)
    assert archived.status_code == 200, archived.text
    assert search(archived=True) == {plain["id"]}
    assert search() == {tagged["id"]}


# ---------------------------------------------------------------------------
# RBAC on movement (SPEC §4)
# ---------------------------------------------------------------------------


def test_move_rbac_per_role(client, auth, flow_board) -> None:
    master = auth("master")
    backlog = col_id(flow_board, "Backlog")
    in_progress = col_id(flow_board, "In Progress")

    for role in ("editor", "administrator", "system", "master"):
        card = make_card(client, master, flow_board["id"], backlog, title=f"By {role}")
        response = move(client, auth(role), card["id"], in_progress)
        assert response.status_code == 200, f"{role}: {response.text}"

    for role in ("viewer", "simulator"):
        card = make_card(client, master, flow_board["id"], backlog, title=f"Denied for {role}")
        response = move(client, auth(role), card["id"], in_progress)
        assert response.status_code == 403, f"{role}: {response.text}"
        assert response.json()["error"]["code"] == "board_write_forbidden"


def test_simulator_role_cannot_write_an_operational_board(client, auth, flow_board) -> None:
    """SPEC §4: `simulator` only writes boards of kind='simulator'."""
    master = auth("master")
    backlog = col_id(flow_board, "Backlog")
    card = make_card(client, master, flow_board["id"], backlog)
    response = client.patch(
        f"{API}/cards/{card['id']}", json={"title": "Nope"}, headers=auth("simulator")
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "board_write_forbidden"
