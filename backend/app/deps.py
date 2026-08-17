"""FastAPI dependencies: database handle, authentication, RBAC, feature gating.

Every protected route composes three things::

    @router.post("/cards")
    def create_card(
        payload: CardCreate,
        db: Database = Depends(get_db),
        user: CurrentUser = Depends(require_roles("master", "administrator", "editor")),
        _: None = Depends(require_flag("multi_project")),
    ): ...

``CurrentUser`` is a dict that also answers attribute access, so both
``user["id"]`` and ``user.id`` work — routers written by different agents can use
whichever reads better.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from fastapi import Depends, Path, Request

from .config import (
    NON_INTERACTIVE_ROLES,
    ROLE_CAPABILITIES,
    plan_tier,
    settings,
)
from .db import Database, connect, utcnow
from .errors import ApiError, Forbidden, NotFound, Unauthorized
from .security import decode_token

__all__ = [
    "CurrentUser",
    "get_db",
    "get_current_user",
    "get_optional_user",
    "require_roles",
    "require_capability",
    "require_flag",
    "get_board_or_404",
    "require_board",
    "get_active_plan",
    "effective_flags",
    "is_flag_effective",
    "assert_can_login",
    "can_write_board",
    "require_board_write",
]


class CurrentUser(dict):
    """The authenticated user row. Supports ``user["id"]`` and ``user.id``."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc

    @property
    def is_admin(self) -> bool:
        """Master or administrator — the two roles that manage the workspace."""
        return self.get("role") in {"master", "administrator"}

    def has(self, capability: str) -> bool:
        """True when this user's role holds ``capability`` (SPEC §4 matrix)."""
        return self.get("role") in ROLE_CAPABILITIES.get(capability, ())


# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------


def get_db(request: Request) -> Iterator[Database]:
    """Yield a per-request :class:`Database`, closing it when the response ends.

    ``request.app.state.db_path`` lets tests point the whole app at a temp file
    without touching the process-wide settings.
    """
    path = getattr(request.app.state, "db_path", None) if request else None
    database = Database(connect(path))
    try:
        yield database
    finally:
        database.close()


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _load_user(db: Database, user_id: str) -> CurrentUser:
    row = db.q1(
        "SELECT id, email, name, role, is_active, created_at, last_login_at "
        "FROM users WHERE id = ?",
        user_id,
    )
    if row is None:
        raise Unauthorized("The account for this token no longer exists.", code="unknown_subject")
    if not row.get("is_active"):
        raise Forbidden("This account is deactivated.", code="account_disabled")
    row["is_active"] = bool(row["is_active"])
    return CurrentUser(row)


def get_current_user(
    request: Request, db: Database = Depends(get_db)
) -> CurrentUser:
    """Resolve the ``Authorization: Bearer <jwt>`` header into a user row."""
    token = _bearer_token(request)
    if not token:
        raise Unauthorized("Missing bearer token.", code="missing_token")
    claims = decode_token(token)  # raises TokenError (401) when untrustworthy
    user = _load_user(db, str(claims.get("sub")))
    # The role in the token is a hint; the database is authoritative, so a role
    # change takes effect immediately rather than at the next token refresh.
    user["token_role"] = claims.get("role")
    return user


def get_optional_user(
    request: Request, db: Database = Depends(get_db)
) -> CurrentUser | None:
    """Like :func:`get_current_user` but returns ``None`` instead of raising."""
    try:
        return get_current_user(request, db)
    except ApiError:
        return None


def assert_can_login(user: dict[str, Any]) -> None:
    """Reject interactive logins for non-human roles (SPEC §4: ``system``)."""
    if user.get("role") in NON_INTERACTIVE_ROLES:
        raise Forbidden(
            "The system identity is for API and MCP access only and cannot sign in.",
            code="interactive_login_forbidden",
            details={"role": user.get("role")},
        )
    if not user.get("is_active"):
        raise Forbidden("This account is deactivated.", code="account_disabled")


# ---------------------------------------------------------------------------
# role-based access control
# ---------------------------------------------------------------------------


def require_roles(*roles: str) -> Callable[..., CurrentUser]:
    """Dependency factory allowing only the listed roles."""
    allowed = frozenset(roles)

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.get("role") not in allowed:
            raise Forbidden(
                "Your role does not allow this action.",
                code="insufficient_role",
                details={"role": user.get("role"), "required": sorted(allowed)},
            )
        return user

    _guard.__name__ = f"require_roles_{'_'.join(sorted(allowed)) or 'none'}"
    return _guard


def require_capability(capability: str) -> Callable[..., CurrentUser]:
    """Dependency factory driven by the SPEC §4 capability matrix."""
    allowed = frozenset(ROLE_CAPABILITIES.get(capability, ()))
    if not allowed:
        raise KeyError(f"Unknown capability: {capability}")

    def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.get("role") not in allowed:
            raise Forbidden(
                "Your role does not allow this action.",
                code="insufficient_role",
                details={
                    "role": user.get("role"),
                    "capability": capability,
                    "required": sorted(allowed),
                },
            )
        return user

    _guard.__name__ = f"require_capability_{capability}"
    return _guard


# ---------------------------------------------------------------------------
# feature flags
# ---------------------------------------------------------------------------


def get_active_plan(db: Database) -> dict[str, Any] | None:
    """The workspace's active plan row (``None`` before seeding)."""
    row = db.q1(
        "SELECT p.* FROM workspace w JOIN plans p ON p.id = w.active_plan_id "
        "WHERE w.id = 'workspace'"
    )
    if row and isinstance(row.get("flags"), str):
        try:
            row["flags"] = json.loads(row["flags"])
        except (ValueError, json.JSONDecodeError):
            row["flags"] = []
    return row


def effective_flags(db: Database) -> dict[str, bool]:
    """``{key: bool}`` — enabled **and** unlocked by the active plan (SPEC §3)."""
    plan = get_active_plan(db)
    tier = plan_tier(plan.get("slug") if plan else None)
    out: dict[str, bool] = {}
    for row in db.q("SELECT key, is_enabled, min_plan FROM feature_flags ORDER BY key"):
        out[row["key"]] = bool(row["is_enabled"]) and plan_tier(row["min_plan"]) <= tier
    return out


def is_flag_effective(db: Database, key: str) -> bool:
    """Whether a single flag is effective right now."""
    row = db.q1("SELECT is_enabled, min_plan FROM feature_flags WHERE key = ?", key)
    if row is None:
        # An unknown flag never gates anything: a router that ships before its
        # flag row exists must keep working.
        return True
    plan = get_active_plan(db)
    tier = plan_tier(plan.get("slug") if plan else None)
    return bool(row["is_enabled"]) and plan_tier(row["min_plan"]) <= tier


def require_flag(key: str) -> Callable[..., None]:
    """Dependency factory that 403s when a feature is off or plan-locked."""

    def _guard(db: Database = Depends(get_db)) -> None:
        row = db.q1(
            "SELECT key, name, is_enabled, min_plan FROM feature_flags WHERE key = ?", key
        )
        if row is None:
            return
        plan = get_active_plan(db)
        plan_slug = plan.get("slug") if plan else None
        if not row["is_enabled"]:
            raise Forbidden(
                f"The feature “{row['name']}” is switched off for this workspace.",
                code="feature_disabled",
                details={"flag": key, "min_plan": row["min_plan"], "plan": plan_slug},
            )
        if plan_tier(row["min_plan"]) > plan_tier(plan_slug):
            raise Forbidden(
                f"“{row['name']}” requires the {str(row['min_plan']).title()} plan.",
                code="plan_upgrade_required",
                details={"flag": key, "min_plan": row["min_plan"], "plan": plan_slug},
            )

    _guard.__name__ = f"require_flag_{key}"
    return _guard


# ---------------------------------------------------------------------------
# boards
# ---------------------------------------------------------------------------


def get_board_or_404(db: Database, board_id: str) -> dict[str, Any]:
    """Fetch a board row or raise a 404 in the standard envelope."""
    row = db.q1("SELECT * FROM boards WHERE id = ?", board_id)
    if row is None:
        # Slugs are unique and human-typeable; accepting one here makes the API
        # pleasant to drive by hand without weakening the id contract.
        row = db.q1("SELECT * FROM boards WHERE slug = ?", board_id)
    if row is None:
        raise NotFound("Board", board_id, code="board_not_found")
    row["count_envelope_in_metrics"] = bool(row["count_envelope_in_metrics"])
    row["confirm_moves"] = bool(row["confirm_moves"])
    row["archived"] = bool(row["archived"])
    return row


def require_board(param: str = "id") -> Callable[..., dict[str, Any]]:
    """Dependency factory resolving a path parameter into a board row."""

    if param == "board_id":

        def _from_board_id(
            board_id: str = Path(...), db: Database = Depends(get_db)
        ) -> dict[str, Any]:
            return get_board_or_404(db, board_id)

        return _from_board_id

    def _from_id(id: str = Path(...), db: Database = Depends(get_db)) -> dict[str, Any]:
        return get_board_or_404(db, id)

    return _from_id


# Roles that may write board content on *at least one* kind of board. A role outside
# this set can be refused before any lookup happens; see require_board_write_target.
_BOARD_WRITE_ROLES = frozenset({"master", "administrator", "editor", "simulator", "system"})


def can_write_board(user: dict[str, Any], board: dict[str, Any]) -> bool:
    """SPEC §4: ``simulator`` may only write boards with ``kind='simulator'``."""
    role = user.get("role")
    if role in {"master", "administrator"}:
        return True
    if role == "simulator":
        return board.get("kind") == "simulator"
    if role in {"editor", "system"}:
        # Editors and the integration identity work cards on operational boards;
        # simulator boards belong to the simulator role.
        return board.get("kind") == "operational"
    return False


def require_board_write(user: dict[str, Any], board: dict[str, Any]) -> None:
    """Raise 403 unless ``user`` may modify content on ``board``."""
    if not can_write_board(user, board):
        raise Forbidden(
            "Your role cannot modify this board.",
            code="board_write_forbidden",
            details={"role": user.get("role"), "board_kind": board.get("kind")},
        )


def require_board_write_target(
    user: dict[str, Any], board: dict[str, Any] | None, board_id: str
) -> dict[str, Any]:
    """Authorise a write *before* revealing whether ``board_id`` exists.

    Checking existence first turns any write endpoint into an oracle: a viewer who
    may never write anything could still enumerate valid board ids by telling 404
    apart from 403. So the decision order is authorisation, then existence.

    ``master`` and ``administrator`` may write every board, so for them a missing
    board is genuinely a 404 and saying so leaks nothing. Every other role's right
    depends on the board's ``kind`` — which is exactly what a missing row cannot
    tell us — so they get 403 rather than a confirmation that the id is unused.
    """
    role = user.get("role")
    if role not in _BOARD_WRITE_ROLES:
        raise Forbidden(
            "Your role cannot modify board content.",
            code="board_write_forbidden",
            details={"role": role},
        )
    if board is None:
        if role in {"master", "administrator"}:
            raise NotFound("Board", board_id, code="board_not_found")
        raise Forbidden(
            "Your role cannot modify this board.",
            code="board_write_forbidden",
            details={"role": role},
        )
    require_board_write(user, board)
    return board


def touch_last_login(db: Database, user_id: str) -> str:
    """Stamp ``users.last_login_at``; returns the timestamp written."""
    now = utcnow()
    db.ex("UPDATE users SET last_login_at = ? WHERE id = ?", now, user_id)
    return now


def token_ttl() -> int:
    """Configured access-token lifetime in seconds (for ``expires_in``)."""
    return settings.token_ttl_seconds
