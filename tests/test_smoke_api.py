"""End-to-end smoke tests plus the seed invariants every chart depends on.

The invariant tests are the important half: if the seed stops producing enough
history, or ``committed_at`` drifts away from the movement rows, the metrics look
plausible while being wrong. These assertions fail loudly instead.

Tests that need a router owned by another agent skip themselves while that module
is still being written, so this file stays a useful signal throughout the build.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.app.db import parse_iso

from sqlhelper import Database

NOW = datetime.now(UTC)


def _skip_if_missing(response, what: str) -> None:
    """Skip when the endpoint has not been written yet, fail on real errors."""
    if response.status_code == 404 and "No API route" in response.text:
        pytest.skip(f"{what} is not implemented yet")


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


def test_health_reports_a_seeded_database(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "Nagare Kanban API"
    assert body["database"] is True
    assert body["seeded"] is True
    assert body["time"].endswith("Z")


def test_openapi_document_is_served(client) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Nagare Kanban API"


def test_unknown_api_route_returns_the_error_envelope(client) -> None:
    response = client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "not_found"


def test_unknown_page_falls_back_to_the_spa(client) -> None:
    response = client.get("/board/anything")
    assert response.status_code == 200
    # Either the built shell or the placeholder — never a 404 for a UI route.
    assert response.headers["content-type"].startswith(("text/html", "text/plain"))


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,email",
    [
        ("master", "master@nagare.app"),
        ("administrator", "admin@nagare.app"),
        ("editor", "editor@nagare.app"),
        ("viewer", "viewer@nagare.app"),
        ("simulator", "simulator@nagare.app"),
    ],
)
def test_login_succeeds_for_every_interactive_role(client, role: str, email: str) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "nagare123"})
    _skip_if_missing(response, "POST /auth/login")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["user"]["role"] == role
    assert body["user"]["email"] == email
    assert "password_hash" not in body["user"]


def test_system_identity_cannot_sign_in(client) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "system@nagare.app", "password": "nagare123"}
    )
    _skip_if_missing(response, "POST /auth/login")
    assert response.status_code in (401, 403), response.text
    assert response.json()["error"]["code"]


def test_wrong_password_is_rejected(client) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": "master@nagare.app", "password": "wrong-password"}
    )
    _skip_if_missing(response, "POST /auth/login")
    assert response.status_code == 401
    assert response.json()["error"]["code"]


def test_protected_route_requires_a_token(client) -> None:
    response = client.get("/api/v1/auth/me")
    _skip_if_missing(response, "GET /auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"missing_token", "unauthorized", "invalid_token"}


def test_bearer_token_identifies_the_caller(client, auth) -> None:
    response = client.get("/api/v1/auth/me", headers=auth("editor"))
    _skip_if_missing(response, "GET /auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "editor@nagare.app"


# ---------------------------------------------------------------------------
# board + cards over HTTP
# ---------------------------------------------------------------------------


def test_board_view_loads(client, auth, board_id: str) -> None:
    response = client.get(f"/api/v1/boards/{board_id}/view", headers=auth("viewer"))
    _skip_if_missing(response, "GET /boards/{id}/view")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["board"]["id"] == board_id
    assert len(body["columns"]) >= 1
    assert len(body["cards"]) >= 1


def test_cards_list_returns_the_standard_envelope(client, auth, board_id: str) -> None:
    response = client.get(f"/api/v1/cards?board_id={board_id}", headers=auth("viewer"))
    _skip_if_missing(response, "GET /cards")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body and "total" in body
    assert body["total"] >= 1
    assert body["items"][0]["key"]


# ---------------------------------------------------------------------------
# seed invariants — these must hold for every chart to be meaningful
# ---------------------------------------------------------------------------


def test_seed_creates_every_role_and_project(db: Database) -> None:
    # Subset, not equality: the DB is seeded once per session and other tests in the
    # suite legitimately create their own users and projects in it. What matters is
    # that the seed contributed every role and every SPEC §11 project.
    roles = {row["role"] for row in db.q("SELECT role FROM users")}
    assert {"master", "administrator", "editor", "viewer", "simulator", "system"} <= roles
    keys = {row["key"] for row in db.q("SELECT key FROM projects")}
    assert {"NGR", "PAY", "MOB", "INF"} <= keys


def test_seed_creates_both_boards_with_the_spec_columns(
    db: Database, columns: list[dict[str, Any]], sim_board: dict[str, Any]
) -> None:
    names = [column["name"] for column in columns]
    assert names == [
        "Envelope",
        "Ideas",
        "Discovery",
        "Options",
        "Ready to Commit",
        "Analysis",
        "Analysis Done",
        "Build",
        "Build Done",
        "Verify",
        "Ready to Deploy",
        "Delivered",
    ]
    commitment = [c for c in columns if c["is_commitment_point"]]
    delivery = [c for c in columns if c["is_delivery_point"]]
    assert len(commitment) == 1 and commitment[0]["name"] == "Analysis"
    assert len(delivery) == 1 and delivery[0]["name"] == "Delivered"
    assert sim_board["kind"] == "simulator"
    assert db.scalar("SELECT COUNT(*) FROM columns WHERE board_id = ?", sim_board["id"]) == 12


def test_seed_has_at_least_220_cards(db: Database) -> None:
    assert int(db.scalar("SELECT COUNT(*) FROM cards") or 0) >= 220


def test_seed_has_twelve_months_of_movements(db: Database, board_id: str) -> None:
    total = int(db.scalar("SELECT COUNT(*) FROM card_movements") or 0)
    assert total >= 1000, "every chart is computed from movements; there must be many"

    oldest = db.scalar("SELECT MIN(moved_at) FROM card_movements WHERE board_id = ?", board_id)
    newest = db.scalar("SELECT MAX(moved_at) FROM card_movements WHERE board_id = ?", board_id)
    span_days = (parse_iso(newest) - parse_iso(oldest)).total_seconds() / 86400
    assert span_days >= 330, f"history spans only {span_days:.0f} days"
    assert parse_iso(newest) <= NOW + timedelta(hours=1), "no movement may be in the future"

    # Every card must open with a creation movement (from_column_id IS NULL).
    creations = int(
        db.scalar("SELECT COUNT(*) FROM card_movements WHERE from_column_id IS NULL") or 0
    )
    assert creations == int(db.scalar("SELECT COUNT(*) FROM cards") or 0)


def test_committed_and_delivered_agree_with_the_movement_history(
    db: Database, columns_by_name: dict[str, dict[str, Any]]
) -> None:
    commitment_id = columns_by_name["Analysis"]["id"]
    delivery_id = columns_by_name["Delivered"]["id"]

    mismatched_commit = db.q(
        "SELECT c.key FROM cards c JOIN card_movements m ON m.card_id = c.id "
        "WHERE m.to_column_id = ? AND c.committed_at IS NOT NULL "
        "AND c.committed_at <> m.moved_at",
        commitment_id,
    )
    assert mismatched_commit == [], "committed_at must equal the move into Analysis"

    mismatched_delivery = db.q(
        "SELECT c.key FROM cards c JOIN card_movements m ON m.card_id = c.id "
        "WHERE m.to_column_id = ? AND c.delivered_at <> m.moved_at",
        delivery_id,
    )
    assert mismatched_delivery == [], "delivered_at must equal the move into Delivered"

    assert (
        db.q("SELECT key FROM cards WHERE delivered_at IS NOT NULL AND committed_at IS NULL") == []
    ), "a delivered card must have been committed"
    assert (
        db.q("SELECT key FROM cards WHERE delivered_at IS NOT NULL AND delivered_at < committed_at")
        == []
    ), "delivery cannot precede commitment"
    assert (
        db.q("SELECT key FROM cards WHERE committed_at IS NOT NULL AND committed_at < created_at")
        == []
    ), "commitment cannot precede creation"
    assert (
        db.q("SELECT key FROM cards WHERE delivered_at IS NOT NULL AND column_id <> ?", delivery_id)
        == []
    ), "delivered cards must sit in the delivery column"
    assert (
        db.q("SELECT key FROM cards WHERE column_id = ? AND delivered_at IS NULL", delivery_id)
        == []
    ), "cards in the delivery column must carry delivered_at"


def test_seed_has_enough_delivered_work_for_throughput_and_forecasting(
    db: Database,
) -> None:
    delivered = int(db.scalar("SELECT COUNT(*) FROM cards WHERE delivered_at IS NOT NULL") or 0)
    assert delivered >= 120, f"only {delivered} delivered cards"

    cutoff = (NOW - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = int(db.scalar("SELECT COUNT(*) FROM cards WHERE delivered_at >= ?", cutoff) or 0)
    assert recent >= 25, "the last quarter must carry enough signal for a forecast"


def test_in_flight_work_is_spread_across_downstream_columns(db: Database) -> None:
    in_flight = db.q(
        "SELECT c.id, col.name AS column_name FROM cards c "
        "JOIN columns col ON col.id = c.column_id "
        "WHERE c.committed_at IS NOT NULL AND c.delivered_at IS NULL"
    )
    assert 25 <= len(in_flight) <= 45, f"expected ~35 cards in flight, found {len(in_flight)}"
    occupied = {row["column_name"] for row in in_flight}
    assert occupied >= {"Analysis", "Build", "Verify", "Ready to Deploy"}


def test_sealed_envelope_demands_exist_and_are_untouched(db: Database) -> None:
    sealed = db.q("SELECT * FROM cards WHERE envelope_state = 'sealed'")
    assert len(sealed) >= 20
    assert all(card["opened_at"] is None for card in sealed)
    assert all(card["committed_at"] is None for card in sealed)
    envelope_column = db.q1("SELECT id FROM columns WHERE kind = 'envelope' LIMIT 1")
    assert all(card["column_id"] == envelope_column["id"] for card in sealed)


def test_flow_efficiency_lands_between_25_and_40_percent(db: Database) -> None:
    """Queues must dominate activities, or the whole product is telling a lie."""
    rows = db.q(
        "SELECT col.kind AS kind, SUM(m.seconds_in_from_column) AS seconds "
        "FROM card_movements m JOIN columns col ON col.id = m.from_column_id "
        "WHERE m.seconds_in_from_column IS NOT NULL GROUP BY col.kind"
    )
    totals = {row["kind"]: float(row["seconds"] or 0) for row in rows}
    touch = totals.get("activity", 0.0)
    wait = totals.get("queue", 0.0)
    assert touch > 0 and wait > 0
    efficiency = touch / (touch + wait) * 100
    assert 25.0 <= efficiency <= 40.0, f"flow efficiency is {efficiency:.1f}%"


def test_demand_mix_matches_the_spec(db: Database) -> None:
    total = int(db.scalar("SELECT COUNT(*) FROM cards") or 0)
    mix = {
        row["service_class"]: row["n"]
        for row in db.q("SELECT service_class, COUNT(*) AS n FROM cards GROUP BY service_class")
    }
    assert set(mix) == {"expedite", "standard", "fixed_date", "intangible"}
    assert 0.03 <= mix["expedite"] / total <= 0.14
    assert 0.60 <= mix["standard"] / total <= 0.80
    assert 0.06 <= mix["fixed_date"] / total <= 0.18

    failure = int(db.scalar("SELECT COUNT(*) FROM cards WHERE demand_type = 'failure'") or 0)
    assert 0.06 <= failure / total <= 0.19, "failure demand should sit around 12%"


def test_fixed_date_work_mostly_hits_its_due_date(db: Database) -> None:
    rows = db.q(
        "SELECT due_date, delivered_at FROM cards "
        "WHERE service_class = 'fixed_date' AND delivered_at IS NOT NULL "
        "AND due_date IS NOT NULL"
    )
    assert len(rows) >= 8
    hits = sum(1 for row in rows if row["delivered_at"] <= row["due_date"])
    assert 0.65 <= hits / len(rows) <= 0.95, "due-date performance should sit near 80%"


def test_blockers_carry_categories_and_some_are_still_open(db: Database) -> None:
    blockers = db.q("SELECT * FROM blockers")
    assert len(blockers) >= 8
    assert all(blocker["category"] for blocker in blockers)
    open_blockers = [blocker for blocker in blockers if blocker["ended_at"] is None]
    assert open_blockers, "at least one card should be blocked right now"
    blocked_cards = {row["id"] for row in db.q("SELECT id FROM cards WHERE is_blocked = TRUE")}
    assert {blocker["card_id"] for blocker in open_blockers} <= blocked_cards


def test_wip_limits_produce_an_at_limit_and_an_over_column(db: Database, board_id: str) -> None:
    """The screenshots need a board that visibly demonstrates WIP policy."""
    rows = db.q(
        "SELECT col.name AS name, col.wip_min AS wip_min, col.wip_max AS wip_max, "
        "COUNT(c.id) AS count FROM columns col "
        "LEFT JOIN cards c ON c.column_id = col.id AND c.archived = FALSE "
        "WHERE col.board_id = ? GROUP BY col.id ORDER BY col.position",
        board_id,
    )
    states = set()
    for row in rows:
        count, wip_min, wip_max = row["count"], row["wip_min"], row["wip_max"]
        if wip_max is not None and count > wip_max:
            states.add("over")
        elif wip_max is not None and count == wip_max:
            states.add("at_limit")
        elif wip_min is not None and count < wip_min:
            states.add("under")
    assert "over" in states, "one column must sit over its WIP maximum"
    assert "at_limit" in states, "one column must sit exactly at its WIP maximum"


def test_feature_flags_and_plans_are_complete(db: Database) -> None:
    from backend.app.config import FEATURE_FLAGS, PLANS

    flags = {row["key"] for row in db.q("SELECT key FROM feature_flags")}
    assert flags == {flag["key"] for flag in FEATURE_FLAGS}
    assert all(row["is_enabled"] for row in db.q("SELECT is_enabled FROM feature_flags"))

    plans = {row["slug"]: row for row in db.q("SELECT * FROM plans")}
    assert set(plans) == {plan["slug"] for plan in PLANS}

    workspace = db.q1("SELECT * FROM workspace WHERE id = 'workspace'")
    assert workspace is not None
    assert workspace["name"] == "Nagare"
    assert workspace["active_plan_id"] == plans["business"]["id"]


def test_card_keys_follow_the_project_prefix(db: Database) -> None:
    rows = db.q(
        "SELECT c.key AS card_key, p.key AS project_key FROM cards c "
        "JOIN projects p ON p.id = c.project_id"
    )
    assert rows
    for row in rows:
        prefix, _, number = row["card_key"].partition("-")
        assert prefix == row["project_key"]
        assert number.isdigit() and int(number) >= 101


def test_seed_is_idempotent(db: Database) -> None:
    import asyncio

    from backend.app.seed import migrate_and_seed

    before = int(db.scalar("SELECT COUNT(*) FROM cards") or 0)
    summary = asyncio.run(migrate_and_seed(db.url))
    after = int(db.scalar("SELECT COUNT(*) FROM cards") or 0)
    assert summary["skipped"] is True
    assert before == after
