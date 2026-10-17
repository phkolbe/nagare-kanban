"""Authentication, RBAC and the commercial gate — the ADMIN agent's contract.

Everything here runs against the real seeded database through the real HTTP
surface. Three properties are worth stating up front because they shape the file:

* **The SPEC §4 matrix is restated literally** in :data:`SPEC_MATRIX`. Comparing
  the API against ``config.ROLE_CAPABILITIES`` alone would only prove the code
  agrees with itself; comparing it against a hand-copied table proves it agrees
  with the specification.
* **Every mutation is undone.** The database is session-scoped and shared with
  every other test module, so each test that creates a user, flips a flag or
  downgrades the plan restores the previous state in a fixture teardown.
* **Endpoints owned by other agents are probed, never required.** Cross-router
  assertions are negative (a denied role must get 403, which mutates nothing) and
  skip themselves while the module is still being written.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from backend.app.config import ROLE_CAPABILITIES, ROLES
from backend.app.routers.auth import MAX_FAILURES, reset_login_throttle
from backend.app.security import encode_token

from sqlhelper import Database

API = "/api/v1"
PASSWORD = "nagare123"

ROLE_EMAILS: dict[str, str] = {
    "master": "master@nagare.app",
    "administrator": "admin@nagare.app",
    "editor": "editor@nagare.app",
    "viewer": "viewer@nagare.app",
    "simulator": "simulator@nagare.app",
    "system": "system@nagare.app",
}

INTERACTIVE_ROLES: tuple[str, ...] = (
    "master",
    "administrator",
    "editor",
    "viewer",
    "simulator",
)

#: SPEC §4, copied by hand from the table. capability -> roles that hold it.
SPEC_MATRIX: dict[str, set[str]] = {
    "manage_plans_flags": {"master"},
    "manage_users": {"master", "administrator"},
    "manage_boards": {"master", "administrator"},
    "manage_cards": {"master", "administrator", "editor", "system"},
    "read_boards_metrics": {
        "master",
        "administrator",
        "editor",
        "viewer",
        "simulator",
        "system",
    },
    "simulator_boards": {"master", "administrator", "simulator"},
    "api_tokens": {"master", "administrator", "system"},
}

#: Flag keys that the Business plan unlocks but Free does not (SPEC §3).
BUSINESS_ONLY_FLAGS: tuple[str, ...] = (
    "metrics_advanced",
    "monte_carlo",
    "kmm_dashboard",
    "simulator",
    "rbac_advanced",
    "mcp_server",
)
TEAM_ONLY_FLAGS: tuple[str, ...] = (
    "unified_flow",
    "wip_group",
    "wip_flow",
    "envelope",
    "custom_fields",
    "api_access",
)
FREE_FLAGS: tuple[str, ...] = (
    "upstream_board",
    "downstream_board",
    "wip_column",
    "service_classes",
    "card_history",
    "metrics_basic",
    "multi_project",
)

#: The plan-gate refusals `deps.require_flag` can raise (SPEC §3 has no literal
#: code, so the assertion accepts the whole family rather than pinning one).
FLAG_DENIED_CODES = {"plan_upgrade_required", "feature_disabled", "feature_not_available"}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_throttle() -> Iterator[None]:
    """Brute-force state is process-global; never let it leak between tests."""
    reset_login_throttle()
    yield
    reset_login_throttle()


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


@pytest.fixture()
def make_user(client, master, db: Database) -> Iterator[Callable[..., dict[str, Any]]]:
    """Create users through the API and hard-delete them afterwards.

    Production deletes are soft, but a test that left rows behind would drift the
    seeded fixtures every other module asserts against.
    """
    created: list[str] = []

    def _make(role: str = "viewer", **overrides: Any) -> dict[str, Any]:
        body = {
            "email": f"t-{uuid.uuid4().hex[:10]}@nagare.test",
            "name": overrides.pop("name", f"Test {role.title()}"),
            "password": overrides.pop("password", PASSWORD),
            "role": role,
        }
        body.update(overrides)
        response = client.post(f"{API}/users", json=body, headers=master)
        assert response.status_code == 201, response.text
        payload = response.json()
        payload["password"] = body["password"]
        created.append(payload["id"])
        return payload

    yield _make

    for user_id in created:
        db.ex("DELETE FROM users WHERE id = ?", user_id)


@pytest.fixture()
def restore_workspace(db: Database) -> Iterator[None]:
    """Snapshot the workspace row and every flag; put them back afterwards."""
    workspace = db.q1("SELECT * FROM workspace WHERE id = 'workspace'")
    flags = db.q("SELECT key, is_enabled, min_plan FROM feature_flags")
    plans_before = {row["id"] for row in db.q("SELECT id FROM plans")}
    try:
        yield
    finally:
        if workspace:
            db.ex(
                "UPDATE workspace SET name = ?, active_plan_id = ?, timezone = ?, "
                "updated_at = ? WHERE id = 'workspace'",
                workspace["name"],
                workspace["active_plan_id"],
                workspace["timezone"],
                workspace["updated_at"],
            )
        for row in flags:
            db.ex(
                "UPDATE feature_flags SET is_enabled = ?, min_plan = ? WHERE key = ?",
                row["is_enabled"],
                row["min_plan"],
                row["key"],
            )
        for row in db.q("SELECT id FROM plans"):
            if row["id"] not in plans_before:
                db.ex("DELETE FROM plans WHERE id = ?", row["id"])


def _skip_if_missing(response: Any, what: str) -> None:
    """Skip when another agent's router is not mounted yet."""
    if response.status_code == 404 and "No API route" in response.text:
        pytest.skip(f"{what} is not implemented yet")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", INTERACTIVE_ROLES)
def test_login_succeeds_for_every_interactive_role(client, role: str) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS[role], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"].count(".") == 2
    assert body["user"]["role"] == role
    assert body["user"]["email"] == ROLE_EMAILS[role]
    assert body["user"]["is_active"] is True
    assert "password_hash" not in body["user"] and "password" not in body["user"]

    # The token really identifies that user against a protected route.
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == body["user"]["id"]


def test_login_is_case_and_whitespace_insensitive_on_email(client) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": "  MASTER@Nagare.App  ", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == "master@nagare.app"


def test_login_stamps_last_login_at(client, db: Database) -> None:
    before = db.scalar("SELECT last_login_at FROM users WHERE email = ?", ROLE_EMAILS["editor"])
    response = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS["editor"], "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    after = db.scalar("SELECT last_login_at FROM users WHERE email = ?", ROLE_EMAILS["editor"])
    assert after is not None and after != "" and after.endswith("Z")
    assert after >= (before or "")
    assert response.json()["user"]["last_login_at"] == after


def test_system_identity_is_denied_interactive_login(client) -> None:
    """SPEC §4: `system` is an integration identity and can never sign in."""
    response = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS["system"], "password": PASSWORD}
    )
    assert response.status_code == 403, response.text
    code = response.json()["error"]["code"]
    assert code in {"interactive_login_denied", "interactive_login_forbidden"}


def test_wrong_password_is_rejected(client) -> None:
    response = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS["master"], "password": "not-the-password"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_unknown_email_is_indistinguishable_from_a_wrong_password(client) -> None:
    """No enumeration oracle: same status, same code, same message."""
    unknown = client.post(
        f"{API}/auth/login", json={"email": "nobody@nagare.test", "password": PASSWORD}
    )
    reset_login_throttle()
    wrong = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS["viewer"], "password": "wrong-password"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["code"] == wrong.json()["error"]["code"]
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_inactive_user_cannot_log_in(client, master, make_user) -> None:
    user = make_user("editor")
    assert (
        client.post(
            f"{API}/auth/login", json={"email": user["email"], "password": user["password"]}
        ).status_code
        == 200
    )

    assert client.delete(f"{API}/users/{user['id']}", headers=master).status_code == 200

    response = client.post(
        f"{API}/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "account_disabled"


def test_deactivating_a_user_revokes_their_live_token(client, master, make_user) -> None:
    user = make_user("editor")
    login = client.post(
        f"{API}/auth/login", json={"email": user["email"], "password": user["password"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 200

    client.delete(f"{API}/users/{user['id']}", headers=master)

    revoked = client.get(f"{API}/auth/me", headers=headers)
    assert revoked.status_code == 403
    assert revoked.json()["error"]["code"] == "account_disabled"


def test_repeated_failures_lock_the_address_then_release_it(client) -> None:
    """Per-email backoff after five failures (never leaking whether it exists)."""
    email = "ghost@nagare.test"
    for _ in range(MAX_FAILURES):
        response = client.post(f"{API}/auth/login", json={"email": email, "password": "nope"})
        assert response.status_code == 401

    locked = client.post(f"{API}/auth/login", json={"email": email, "password": "nope"})
    assert locked.status_code == 429, locked.text
    body = locked.json()["error"]
    assert body["code"] == "too_many_login_attempts"
    assert body["details"]["retry_after_seconds"] >= 1

    # Even the correct password is refused while the lockout stands…
    blocked = client.post(
        f"{API}/auth/login", json={"email": ROLE_EMAILS["viewer"], "password": "wrong"}
    )
    assert blocked.status_code == 401, "the lockout is per address, not global"

    # …and clearing the record restores normal service.
    reset_login_throttle(email)
    after = client.post(f"{API}/auth/login", json={"email": email, "password": "nope"})
    assert after.status_code == 401


def test_a_successful_login_clears_the_failure_counter(client) -> None:
    email = ROLE_EMAILS["simulator"]
    for _ in range(MAX_FAILURES - 1):
        assert (
            client.post(f"{API}/auth/login", json={"email": email, "password": "wrong"}).status_code
            == 401
        )
    assert (
        client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD}).status_code
        == 200
    )
    for _ in range(MAX_FAILURES - 1):
        assert (
            client.post(f"{API}/auth/login", json={"email": email, "password": "wrong"}).status_code
            == 401
        ), "the counter must have restarted from zero"


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def test_protected_routes_require_a_bearer_token(client) -> None:
    for path in ("/auth/me", "/users", "/flags/effective", "/workspace"):
        response = client.get(f"{API}{path}")
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] in {"missing_token", "unauthorized"}
        assert response.headers.get("www-authenticate") == "Bearer"


def test_expired_tokens_are_rejected(client, users) -> None:
    token = encode_token(sub=users["master"]["id"], role="master", ttl_seconds=-60)
    response = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_tampered_and_forged_tokens_are_rejected(client, users) -> None:
    valid = encode_token(sub=users["master"]["id"], role="master")
    header, claims, signature = valid.split(".")

    flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
    cases = {
        "tampered signature": f"{header}.{claims}.{flipped}",
        "wrong secret": encode_token(
            sub=users["master"]["id"], role="master", secret="not-the-real-secret"
        ),
        "garbage": "not-a-jwt",
        "empty signature": f"{header}.{claims}.",
    }

    # A privilege-escalating claim swap must fail: the signature no longer matches.
    payload = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    payload["role"] = "master"
    payload["sub"] = users["viewer"]["id"]
    swapped = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    cases["swapped claims"] = f"{header}.{swapped}.{signature}"

    # "alg": "none" must not be honoured.
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').decode().rstrip("=")
    cases["alg none"] = f"{none_header}.{claims}."

    for label, token in cases.items():
        response = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, f"{label} was accepted"
        assert response.json()["error"]["code"] in {"invalid_token", "unauthorized"}, label


def test_refresh_returns_a_usable_token(client, master) -> None:
    response = client.post(f"{API}/auth/refresh", headers=master)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert response.json()["user"]["role"] == "master"
    assert (
        client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
        == 200
    )


def test_refresh_requires_a_valid_token_and_refuses_the_system_identity(client, users) -> None:
    assert client.post(f"{API}/auth/refresh").status_code == 401

    system_token = encode_token(sub=users["system"]["id"], role="system")
    response = client.post(
        f"{API}/auth/refresh", headers={"Authorization": f"Bearer {system_token}"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] in {
        "interactive_login_denied",
        "interactive_login_forbidden",
    }


@pytest.mark.parametrize("role", ROLES)
def test_me_reports_the_capability_matrix_for_every_role(client, auth, role: str) -> None:
    response = client.get(f"{API}/auth/me", headers=auth(role))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == ROLE_EMAILS[role]
    assert body["role"] == role
    assert body["role_label"] and body["role_description"]
    assert body["capabilities"] == {
        capability: role in holders for capability, holders in SPEC_MATRIX.items()
    }
    assert set(body["granted"]) == {c for c, holders in SPEC_MATRIX.items() if role in holders}
    assert body["flags"]["metrics_basic"] is True
    assert "password_hash" not in body


# ---------------------------------------------------------------------------
# users: CRUD, search, soft delete
# ---------------------------------------------------------------------------


def test_user_lifecycle_create_search_patch_soft_delete(
    client, master, make_user, db: Database
) -> None:
    user = make_user("viewer", name="Hiroko Sato")

    found = client.get(f"{API}/users?q=hiroko", headers=master)
    assert found.status_code == 200
    assert [item["id"] for item in found.json()["items"]] == [user["id"]]
    assert found.json()["total"] == 1

    by_role = client.get(f"{API}/users?role=viewer", headers=master).json()
    assert user["id"] in {item["id"] for item in by_role["items"]}
    assert all(item["role"] == "viewer" for item in by_role["items"])

    patched = client.patch(
        f"{API}/users/{user['id']}",
        json={"name": "Hiroko Sato-Reyes", "role": "editor"},
        headers=master,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Hiroko Sato-Reyes"
    assert patched.json()["role"] == "editor"

    # A password change takes effect immediately.
    assert (
        client.patch(
            f"{API}/users/{user['id']}", json={"password": "another-secret"}, headers=master
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{API}/auth/login", json={"email": user["email"], "password": "another-secret"}
        ).status_code
        == 200
    )

    deleted = client.delete(f"{API}/users/{user['id']}", headers=master)
    assert deleted.status_code == 200 and deleted.json()["ok"] is True
    assert db.scalar("SELECT is_active FROM users WHERE id = ?", user["id"]) == 0
    assert db.scalar("SELECT COUNT(*) FROM users WHERE id = ?", user["id"]) == 1, "soft delete"

    active_only = client.get(f"{API}/users?is_active=true", headers=master).json()
    assert user["id"] not in {item["id"] for item in active_only["items"]}
    inactive = client.get(f"{API}/users?is_active=false", headers=master).json()
    assert user["id"] in {item["id"] for item in inactive["items"]}


def test_duplicate_email_is_a_conflict(client, master, make_user) -> None:
    user = make_user("viewer")
    response = client.post(
        f"{API}/users",
        json={
            "email": user["email"].upper(),
            "name": "Clone",
            "password": PASSWORD,
            "role": "viewer",
        },
        headers=master,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


def test_unknown_user_is_a_404(client, master) -> None:
    response = client.patch(f"{API}/users/{uuid.uuid4()}", json={"name": "Nobody"}, headers=master)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


# ---------------------------------------------------------------------------
# users: guardrails
# ---------------------------------------------------------------------------


def test_only_a_master_may_create_a_master(client, auth, db: Database) -> None:
    body = {
        "email": f"t-{uuid.uuid4().hex[:10]}@nagare.test",
        "name": "Would-be Master",
        "password": PASSWORD,
        "role": "master",
    }
    denied = client.post(f"{API}/users", json=body, headers=auth("administrator"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "master_role_forbidden"

    created = client.post(f"{API}/users", json=body, headers=auth("master"))
    assert created.status_code == 201, created.text
    try:
        assert created.json()["role"] == "master"
    finally:
        db.ex("DELETE FROM users WHERE id = ?", created.json()["id"])


def test_an_administrator_cannot_touch_a_master_account(client, auth, users) -> None:
    master_id = users["master"]["id"]
    admin = auth("administrator")

    for payload in ({"role": "viewer"}, {"is_active": False}, {"password": "hijacked"}):
        response = client.patch(f"{API}/users/{master_id}", json=payload, headers=admin)
        assert response.status_code == 403, payload
        assert response.json()["error"]["code"] == "master_account_protected"

    assert client.delete(f"{API}/users/{master_id}", headers=admin).status_code == 403


def test_nobody_may_change_their_own_role(client, auth, users) -> None:
    response = client.patch(
        f"{API}/users/{users['master']['id']}", json={"role": "viewer"}, headers=auth("master")
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "self_role_change_blocked"

    admin = client.patch(
        f"{API}/users/{users['administrator']['id']}",
        json={"role": "master"},
        headers=auth("administrator"),
    )
    assert admin.status_code in (403, 409)
    assert admin.json()["error"]["code"] in {
        "self_role_change_blocked",
        "master_role_forbidden",
    }


def test_nobody_may_deactivate_or_delete_themselves(client, auth, users) -> None:
    admin_id = users["administrator"]["id"]
    patched = client.patch(
        f"{API}/users/{admin_id}", json={"is_active": False}, headers=auth("administrator")
    )
    assert patched.status_code == 409
    assert patched.json()["error"]["code"] == "self_deactivation_blocked"
    assert (
        client.delete(f"{API}/users/{admin_id}", headers=auth("administrator")).status_code == 409
    )


def test_the_workspace_can_never_lose_its_last_master(
    client, master, auth, users, db: Database
) -> None:
    """The workspace must always keep at least one active master.

    Note on the shape of this test: ``users._assert_last_master_survives`` cannot be
    reached through the API, and that is a property of the design rather than a gap.
    Demoting, deactivating or deleting a master requires a *master* actor (guardrail 2,
    ``master_account_protected``), and self-service is refused separately — so whenever
    the target is a master there is, by construction, a second active master doing the
    asking and the count can never fall to zero. The remaining guard is defence in depth
    for any future non-API caller. What is reachable, and what actually protects the
    workspace, is asserted below.
    """
    seeded_id = users["master"]["id"]
    assert db.scalar("SELECT COUNT(*) FROM users WHERE role='master' AND is_active=TRUE") == 1

    # A non-master with manage_users may not touch a master account at all.
    for payload in ({"role": "editor"}, {"is_active": False}):
        blocked = client.patch(
            f"{API}/users/{seeded_id}", json=payload, headers=auth("administrator")
        )
        assert blocked.status_code == 403, blocked.text
        assert blocked.json()["error"]["code"] == "master_account_protected"
    assert (
        client.delete(f"{API}/users/{seeded_id}", headers=auth("administrator")).status_code == 403
    )

    # The last master cannot dissolve their own authority either.
    demote_self = client.patch(f"{API}/users/{seeded_id}", json={"role": "editor"}, headers=master)
    assert demote_self.status_code == 409
    assert demote_self.json()["error"]["code"] == "self_role_change_blocked"

    deactivate_self = client.patch(
        f"{API}/users/{seeded_id}", json={"is_active": False}, headers=master
    )
    assert deactivate_self.status_code == 409
    assert deactivate_self.json()["error"]["code"] == "self_deactivation_blocked"
    assert client.delete(f"{API}/users/{seeded_id}", headers=master).status_code == 409

    # A second master may be promoted and demoted freely — the count never hits zero.
    second = client.post(
        f"{API}/users",
        json={
            "email": f"t-{uuid.uuid4().hex[:10]}@nagare.test",
            "name": "Second Master",
            "password": PASSWORD,
            "role": "master",
        },
        headers=master,
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    try:
        assert db.scalar("SELECT COUNT(*) FROM users WHERE role='master' AND is_active=TRUE") == 2
        demoted = client.patch(f"{API}/users/{second_id}", json={"role": "editor"}, headers=master)
        assert demoted.status_code == 200, demoted.text
        assert demoted.json()["role"] == "editor"
    finally:
        db.ex("DELETE FROM users WHERE id = ?", second_id)

    # Whatever happened above, the seeded master is still an active master.
    row = db.q1("SELECT role, is_active FROM users WHERE id = ?", seeded_id)
    assert row["role"] == "master" and row["is_active"] == 1
    assert db.scalar("SELECT COUNT(*) FROM users WHERE role='master' AND is_active=TRUE") >= 1


# ---------------------------------------------------------------------------
# role catalogue
# ---------------------------------------------------------------------------


def test_role_catalogue_publishes_the_full_spec_matrix(client, master) -> None:
    response = client.get(f"{API}/users/roles", headers=master)
    assert response.status_code == 200, response.text
    body = response.json()

    assert [role["key"] for role in body["roles"]] == list(ROLES)
    assert set(body["capabilities"]) == set(SPEC_MATRIX)
    assert set(body["capability_labels"]) == set(SPEC_MATRIX)
    assert all(label for label in body["capability_labels"].values())

    for role in body["roles"]:
        assert role["label"] and role["description"]
        assert role["capabilities"] == {
            capability: role["key"] in holders for capability, holders in SPEC_MATRIX.items()
        }

    assert {key: set(value) for key, value in body["matrix"].items()} == SPEC_MATRIX
    assert body["user_counts"]["master"] >= 1
    assert body["protected_roles"] == ["master"]
    assert body["non_interactive_roles"] == ["system"]

    # The catalogue and the running guard agree — the UI can trust either.
    assert {key: set(value) for key, value in body["matrix"].items()} == {
        key: set(value) for key, value in ROLE_CAPABILITIES.items()
    }


# ---------------------------------------------------------------------------
# the capability matrix, asserted against real endpoints
# ---------------------------------------------------------------------------


def _matrix_cases() -> list[tuple[str, str, str, dict[str, Any] | None]]:
    """(capability, method, path, body) tuples covering every ADMIN endpoint."""
    return [
        ("manage_plans_flags", "GET", "/flags", None),
        ("manage_plans_flags", "GET", "/plans", None),
        ("manage_plans_flags", "PATCH", "/flags/metrics_basic", {"is_enabled": True}),
        ("manage_users", "GET", "/users/roles", None),
        ("manage_users", "POST", "/users", {}),
        ("read_boards_metrics", "GET", "/users", None),
        ("read_boards_metrics", "GET", "/flags/effective", None),
        ("read_boards_metrics", "GET", "/workspace", None),
    ]


@pytest.mark.parametrize("capability,method,path,body", _matrix_cases())
@pytest.mark.parametrize("role", ROLES)
def test_capability_matrix_is_enforced_by_the_real_endpoints(
    client,
    auth,
    db: Database,
    role: str,
    capability: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    payload = dict(body) if body is not None else None
    if path == "/users" and method == "POST":
        payload = {
            "email": f"t-{uuid.uuid4().hex[:10]}@nagare.test",
            "name": f"Matrix {role}",
            "password": PASSWORD,
            "role": "viewer",
        }

    response = client.request(method, f"{API}{path}", json=payload, headers=auth(role))
    allowed = role in SPEC_MATRIX[capability]

    if allowed:
        assert response.status_code in (200, 201), f"{role} {method} {path}: {response.text}"
        if method == "POST" and path == "/users":
            db.ex("DELETE FROM users WHERE id = ?", response.json()["id"])
    else:
        assert response.status_code == 403, f"{role} {method} {path}: {response.text}"
        assert response.json()["error"]["code"] in {
            "insufficient_role",
            "master_role_forbidden",
            "master_account_protected",
        }


@pytest.mark.parametrize("role", ["editor", "viewer", "simulator", "system"])
def test_non_admin_roles_cannot_reach_the_commercial_surface(client, auth, role: str) -> None:
    """A denied write must never touch state, so these probes are side-effect free."""
    denied = [
        client.patch(f"{API}/flags/simulator", json={"is_enabled": False}, headers=auth(role)),
        client.post(
            f"{API}/plans",
            json={"name": "Sneaky", "slug": f"sneaky-{role}", "tier_order": 9},
            headers=auth(role),
        ),
        client.patch(f"{API}/workspace", json={"name": "Hijacked"}, headers=auth(role)),
        client.delete(f"{API}/users/anything", headers=auth(role)),
    ]
    for response in denied:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "insufficient_role"


@pytest.mark.parametrize(
    "capability,method,path,body",
    [
        ("manage_boards", "POST", "/boards", {"name": "Denied board", "template": "blank"}),
        ("manage_cards", "POST", "/cards", {"board_id": "x", "title": "Denied card"}),
    ],
)
@pytest.mark.parametrize("role", ["viewer", "simulator"])
def test_roles_without_write_capability_are_refused_by_other_routers(
    client, auth, role: str, capability: str, method: str, path: str, body: dict[str, Any]
) -> None:
    """Cross-agent check: only the negative case, so nothing is ever created."""
    response = client.request(method, f"{API}{path}", json=body, headers=auth(role))
    _skip_if_missing(response, f"{method} {path}")
    assert role not in SPEC_MATRIX[capability]
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# flags, plans and the effective-flag rule
# ---------------------------------------------------------------------------


def test_flags_are_returned_raw_and_grouped_by_category(client, master) -> None:
    response = client.get(f"{API}/flags", headers=master)
    assert response.status_code == 200, response.text
    body = response.json()

    keys = {flag["key"] for flag in body["items"]}
    assert keys >= set(FREE_FLAGS) | set(TEAM_ONLY_FLAGS) | set(BUSINESS_ONLY_FLAGS)
    assert body["total"] == len(body["items"])
    # NG15: the workspace runs on Enterprise, with everything on.
    assert body["plan"]["slug"] == "enterprise"

    grouped = {flag["key"] for group in body["categories"] for flag in group["flags"]}
    assert grouped == keys, "every flag must appear in exactly one category group"
    for group in body["categories"]:
        assert group["label"]
        assert group["effective_count"] <= group["enabled_count"] <= len(group["flags"])

    for flag in body["items"]:
        assert flag["effective"] == (flag["is_enabled"] and flag["unlocked_by_plan"])


def test_effective_flags_are_readable_by_every_role_and_match_the_rule(
    client, auth, master
) -> None:
    raw = {flag["key"]: flag for flag in client.get(f"{API}/flags", headers=master).json()["items"]}
    for role in ROLES:
        effective = client.get(f"{API}/flags/effective", headers=auth(role))
        assert effective.status_code == 200, role
        body = effective.json()
        assert set(body) == set(raw)
        assert all(isinstance(value, bool) for value in body.values())
        assert body == {key: flag["effective"] for key, flag in raw.items()}


def test_disabling_a_flag_makes_it_ineffective_and_re_enabling_restores_it(
    client, master, restore_workspace
) -> None:
    patched = client.patch(f"{API}/flags/monte_carlo", json={"is_enabled": False}, headers=master)
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_enabled"] is False
    assert patched.json()["effective"] is False
    assert patched.json()["unlocked_by_plan"] is True, "the plan still unlocks it"

    assert client.get(f"{API}/flags/effective", headers=master).json()["monte_carlo"] is False

    restored = client.patch(f"{API}/flags/monte_carlo", json={"is_enabled": True}, headers=master)
    assert restored.json()["effective"] is True


def test_raising_a_flags_minimum_plan_locks_it(
    client, master, db: Database, restore_workspace
) -> None:
    # Enterprise is the top tier, so step the workspace down to Business first.
    business = db.q1("SELECT id FROM plans WHERE slug = 'business'")
    switched = client.patch(
        f"{API}/workspace", json={"active_plan_id": business["id"]}, headers=master
    )
    assert switched.status_code == 200, switched.text
    response = client.patch(
        f"{API}/flags/metrics_basic", json={"min_plan": "enterprise"}, headers=master
    )
    assert response.status_code == 200, response.text
    assert response.json()["unlocked_by_plan"] is False
    assert response.json()["effective"] is False
    assert client.get(f"{API}/flags/effective", headers=master).json()["metrics_basic"] is False


def test_unknown_flag_is_a_404(client, master) -> None:
    response = client.patch(f"{API}/flags/no-such-flag", json={"is_enabled": True}, headers=master)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "flag_not_found"


def test_plan_catalogue_and_editing(client, master, restore_workspace) -> None:
    listed = client.get(f"{API}/plans", headers=master)
    assert listed.status_code == 200, listed.text
    slugs = [plan["slug"] for plan in listed.json()["items"]]
    assert slugs == sorted(
        slugs, key=lambda s: {"free": 0, "team": 1, "business": 2, "enterprise": 3}[s]
    )
    assert set(slugs) == {"free", "team", "business", "enterprise"}

    created = client.post(
        f"{API}/plans",
        json={
            "name": "Scale",
            "slug": "scale",
            "price_month": 49.0,
            "price_year": 490.0,
            "tier_order": 4,
            "description": "Everything, for larger portfolios.",
            "flags": ["monte_carlo", "simulator"],
        },
        headers=master,
    )
    assert created.status_code == 201, created.text
    assert created.json()["flags"] == ["monte_carlo", "simulator"]

    duplicate = client.post(
        f"{API}/plans", json={"name": "Scale again", "slug": "scale"}, headers=master
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "plan_slug_taken"

    patched = client.patch(
        f"{API}/plans/{created.json()['id']}", json={"price_month": 59.0}, headers=master
    )
    assert patched.status_code == 200
    assert patched.json()["price_month"] == 59.0

    bogus = client.post(
        f"{API}/plans",
        json={"name": "Bogus", "slug": "bogus", "flags": ["not_a_flag"]},
        headers=master,
    )
    assert bogus.status_code == 400
    assert bogus.json()["error"]["code"] == "unknown_flag"


def test_workspace_reports_its_plan_and_only_a_master_may_switch_it(
    client, auth, master, db: Database, restore_workspace
) -> None:
    workspace = client.get(f"{API}/workspace", headers=auth("viewer"))
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["name"] == "Nagare"
    assert workspace.json()["plan"]["slug"] == "enterprise"

    team = db.q1("SELECT id FROM plans WHERE slug = 'team'")
    denied = client.patch(
        f"{API}/workspace", json={"active_plan_id": team["id"]}, headers=auth("administrator")
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "plan_change_forbidden"

    # An administrator may still rename the workspace.
    renamed = client.patch(
        f"{API}/workspace", json={"name": "Nagare HQ"}, headers=auth("administrator")
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Nagare HQ"

    switched = client.patch(f"{API}/workspace", json={"active_plan_id": team["id"]}, headers=master)
    assert switched.status_code == 200, switched.text
    assert switched.json()["plan"]["slug"] == "team"

    missing = client.patch(
        f"{API}/workspace", json={"active_plan_id": str(uuid.uuid4())}, headers=master
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "plan_not_found"


def test_plan_downgrade_flips_effective_flags_and_closes_the_endpoint(
    client, auth, master, db: Database, restore_workspace
) -> None:
    """The headline commercial behaviour: plan tier gates features instantly."""
    before = client.get(f"{API}/flags/effective", headers=master).json()
    assert all(before[key] for key in BUSINESS_ONLY_FLAGS)
    assert client.get(f"{API}/users/roles", headers=master).status_code == 200

    free = db.q1("SELECT id FROM plans WHERE slug = 'free'")
    downgraded = client.patch(
        f"{API}/workspace", json={"active_plan_id": free["id"]}, headers=master
    )
    assert downgraded.status_code == 200, downgraded.text
    flags = downgraded.json()["flags"]

    # The same response that switched the plan already reports the new gate.
    assert all(flags[key] is False for key in BUSINESS_ONLY_FLAGS)
    assert all(flags[key] is False for key in TEAM_ONLY_FLAGS)
    assert all(flags[key] is True for key in FREE_FLAGS)
    assert client.get(f"{API}/flags/effective", headers=auth("viewer")).json() == flags

    # …and the endpoint the flag guards is now closed, for the master too.
    denied = client.get(f"{API}/users/roles", headers=master)
    assert denied.status_code == 403, denied.text
    error = denied.json()["error"]
    assert error["code"] in FLAG_DENIED_CODES
    assert error["details"].get("flag") == "rbac_advanced"
    assert error["details"].get("min_plan") == "business"

    # Upgrading again reopens it, with no restart and no cache to clear.
    business = db.q1("SELECT id FROM plans WHERE slug = 'business'")
    upgraded = client.patch(
        f"{API}/workspace", json={"active_plan_id": business["id"]}, headers=master
    )
    assert upgraded.status_code == 200
    assert all(upgraded.json()["flags"][key] for key in BUSINESS_ONLY_FLAGS)
    assert client.get(f"{API}/users/roles", headers=master).status_code == 200
