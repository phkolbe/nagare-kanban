"""FastAPI dependencies: database session, authentication, RBAC, feature gating.

Every protected route composes three things::

    @router.post("/cards")
    async def create_card(
        payload: CardCreate,
        s: AsyncSession = Depends(get_session),
        user: CurrentUser = Depends(require_roles("master", "administrator", "editor")),
        _: None = Depends(require_flag("multi_project")),
    ): ...

``CurrentUser`` is a dict that also answers attribute access, so both
``user["id"]`` and ``user.id`` work.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import Depends, Path, Request
from pampufa_sdk.auth.token import TokenInvalido
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import (
    ROLE_CAPABILITIES,
    plan_tier,
    settings,
)
from .db import utcnow
from .errors import ApiError, Conflict, Forbidden, NotFound, Unauthorized
from .models import PRODUCT_STANDALONE, Board, FeatureFlag, Plan, User, Workspace, as_dict
from .pampufa import is_pampufa_token, user_for_identity
from .security import decode_token
from .tenancy import (
    current_product,
    local_product,
    read_product_header,
    role_from_pampufa,
    set_current_product,
)
from .texts import effective_locale, set_locale, set_via

__all__ = [
    "CurrentUser",
    "can_write_board",
    "effective_flags",
    "get_active_plan",
    "get_board_or_404",
    "get_current_user",
    "get_optional_user",
    "get_session",
    "is_flag_effective",
    "require_board",
    "require_board_write",
    "require_capability",
    "require_flag",
    "require_roles",
    "require_standalone",
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


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A per-request session; anything left uncommitted is rolled back.

    Writes are committed by the endpoint that owns them (``await s.commit()``).
    """
    async with request.app.state.database.sessions() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


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


_PUBLIC_USER_FIELDS = (
    "id",
    "email",
    "name",
    "role",
    "is_active",
    "created_at",
    "last_login_at",
    "locale",
)


async def _load_user(s: AsyncSession, user_id: str) -> CurrentUser:
    try:
        user = await s.get(User, user_id)
    except ValueError:  # a subject that is not a UUID cannot be one of ours
        user = None
    if user is None:
        raise Unauthorized("The account for this token no longer exists.", code="unknown_subject")
    if not user.is_active:
        raise Forbidden("This account is deactivated.", code="account_disabled")
    row = as_dict(user) or {}
    return CurrentUser({key: row[key] for key in _PUBLIC_USER_FIELDS})


async def get_current_user(request: Request, s: AsyncSession = Depends(get_session)) -> CurrentUser:
    """Resolve ``Authorization: Bearer <jwt>`` into a user, a product and a role.

    Two kinds of token are accepted (NG5): the local login (HS256, the database
    holds the role) and the Pampufa login (EdDSA, the role comes from the token's
    roles in the product named by ``X-Produto``). Either way the product of the
    request is fixed here, for every query that follows (see :mod:`.tenancy`), and so
    is its locale, for every text the flow engine writes (see :mod:`.texts`).
    """
    token = _bearer_token(request)
    if not token:
        raise Unauthorized("Missing bearer token.", code="missing_token")
    requested = read_product_header(request.headers)
    if is_pampufa_token(token):
        user = await _pampufa_user(request, s, token, requested)
    else:
        claims = decode_token(token)  # raises TokenError (401) when untrustworthy
        user = await _load_user(s, str(claims.get("sub")))
        # The role in the token is a hint; the database is authoritative, so a role
        # change takes effect immediately rather than at the next token refresh.
        user["token_role"] = claims.get("role")
        user["product_id"] = local_product(str(user["role"]), requested)
        user["via"] = "local"
    set_current_product(user["product_id"])
    # The language of every human-readable text this request generates (see .texts).
    set_locale(effective_locale(user.get("locale"), str(user.get("via"))))
    set_via(str(user.get("via")))
    return user


async def _pampufa_user(
    request: Request, s: AsyncSession, token: str, requested: str | None
) -> CurrentUser:
    gateway = getattr(request.app.state, "pampufa", None)
    if gateway is None:
        raise Unauthorized(
            "This Nagare is not connected to the Pampufa platform.",
            code="pampufa_not_configured",
        )
    try:
        identity = await gateway.validate(token)
    except TokenInvalido as error:
        # An expired token is renewed by the platform's client; anything else is refused.
        code = "token_expired" if error.codigo == "auth.token_expirado" else "invalid_token"
        raise Unauthorized(str(error), code=code) from error
    if requested is None:
        raise ApiError(
            "product_required",
            "Tell the Nagare which product this request is for (X-Produto header).",
            status=400,
            details={"header": "X-Produto"},
        )
    role = role_from_pampufa(identity.papeis, requested)
    if role is None:
        raise Forbidden(
            "You have no Nagare role in this product.",
            code="product_forbidden",
            details={"product": requested},
        )
    user = await user_for_identity(s, gateway, identity, token)
    if not user.is_active:
        raise Forbidden("This account is deactivated.", code="account_disabled")
    row = as_dict(user) or {}
    current = CurrentUser({key: row[key] for key in _PUBLIC_USER_FIELDS})
    current["role"] = role
    current["product_id"] = requested
    current["via"] = "pampufa"
    current["pampufa_sub"] = identity.sub
    current["pampufa_roles"] = {scope: list(roles) for scope, roles in identity.papeis.items()}
    return current


async def get_optional_user(
    request: Request, s: AsyncSession = Depends(get_session)
) -> CurrentUser | None:
    """Like :func:`get_current_user` but returns ``None`` instead of raising."""
    try:
        return await get_current_user(request, s)
    except ApiError:
        return None


async def require_standalone(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Only the standalone Nagare: refuse a request signed with a Pampufa token.

    Administration (people, workspace, flags and plans) and the simulator are screens of
    the Nagare on its own (decisions N3-c and A9-c): inside the platform, people and roles
    belong to the Pampufa, and the users table and the workspace are not split by product.
    """
    if user.get("via") == "pampufa":
        raise Forbidden(
            "This is only available in the standalone Nagare, not through the Pampufa.",
            code="standalone_only",
        )
    return user


# ---------------------------------------------------------------------------
# role-based access control
# ---------------------------------------------------------------------------


def require_roles(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Dependency factory allowing only the listed roles."""
    allowed = frozenset(roles)

    async def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.get("role") not in allowed:
            raise Forbidden(
                "Your role does not allow this action.",
                code="insufficient_role",
                details={"role": user.get("role"), "required": sorted(allowed)},
            )
        return user

    _guard.__name__ = f"require_roles_{'_'.join(sorted(allowed)) or 'none'}"
    return _guard


def require_capability(capability: str) -> Callable[..., Awaitable[CurrentUser]]:
    """Dependency factory driven by the SPEC §4 capability matrix."""
    allowed = frozenset(ROLE_CAPABILITIES.get(capability, ()))
    if not allowed:
        raise KeyError(f"Unknown capability: {capability}")

    async def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
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


async def get_active_plan(s: AsyncSession) -> dict[str, Any] | None:
    """The workspace's active plan row (``None`` before seeding)."""
    plan = await s.scalar(
        select(Plan)
        .join(Workspace, Workspace.active_plan_id == Plan.id)
        .where(Workspace.id == "workspace")
    )
    return as_dict(plan)


async def effective_flags(s: AsyncSession) -> dict[str, bool]:
    """``{key: bool}`` — enabled **and** unlocked by the active plan (SPEC §3)."""
    plan = await get_active_plan(s)
    tier = plan_tier(plan.get("slug") if plan else None)
    flags = await s.scalars(select(FeatureFlag).order_by(FeatureFlag.key))
    return {flag.key: bool(flag.is_enabled) and plan_tier(flag.min_plan) <= tier for flag in flags}


async def is_flag_effective(s: AsyncSession, key: str) -> bool:
    """Whether a single flag is effective right now."""
    flag = await s.get(FeatureFlag, key)
    if flag is None:
        # An unknown flag never gates anything: a router that ships before its
        # flag row exists must keep working.
        return True
    plan = await get_active_plan(s)
    tier = plan_tier(plan.get("slug") if plan else None)
    return bool(flag.is_enabled) and plan_tier(flag.min_plan) <= tier


def require_flag(key: str) -> Callable[..., Awaitable[None]]:
    """Dependency factory that 403s when a feature is off or plan-locked."""

    async def _guard(s: AsyncSession = Depends(get_session)) -> None:
        flag = await s.get(FeatureFlag, key)
        if flag is None:
            return
        plan = await get_active_plan(s)
        plan_slug = plan.get("slug") if plan else None
        if not flag.is_enabled:
            raise Forbidden(
                f"The feature “{flag.name}” is switched off for this workspace.",
                code="feature_disabled",
                details={"flag": key, "min_plan": flag.min_plan, "plan": plan_slug},
            )
        if plan_tier(flag.min_plan) > plan_tier(plan_slug):
            raise Forbidden(
                f"“{flag.name}” requires the {str(flag.min_plan).title()} plan.",
                code="plan_upgrade_required",
                details={"flag": key, "min_plan": flag.min_plan, "plan": plan_slug},
            )

    _guard.__name__ = f"require_flag_{key}"
    return _guard


# ---------------------------------------------------------------------------
# boards
# ---------------------------------------------------------------------------


async def find_board(s: AsyncSession, board_ref: str) -> dict[str, Any] | None:
    """A board of the current product by id or, failing that, by slug.

    ``None`` when neither matches — a board of another product does not exist here.
    """
    conditions = [Board.slug == board_ref]
    if _looks_like_uuid(board_ref):
        conditions.insert(0, Board.id == board_ref)
    query = select(Board).where(or_(*conditions))
    product = current_product()
    if product is not None:
        query = query.where(Board.product_id == product)
    rows = (await s.scalars(query)).all()
    # An id match wins over a slug that happens to look the same.
    by_id = next((row for row in rows if row.id == board_ref), None)
    return as_dict(by_id or (rows[0] if rows else None))


async def get_board_or_404(s: AsyncSession, board_id: str) -> dict[str, Any]:
    """Fetch a board row or raise a 404 in the standard envelope.

    Slugs are unique and human-typeable; accepting one here makes the API pleasant
    to drive by hand without weakening the id contract.
    """
    row = await find_board(s, board_id)
    if row is None:
        raise NotFound("Board", board_id, code="board_not_found")
    return row


def require_board(param: str = "id") -> Callable[..., Awaitable[dict[str, Any]]]:
    """Dependency factory resolving a path parameter into a board row."""

    if param == "board_id":

        async def _from_board_id(
            board_id: str = Path(...), s: AsyncSession = Depends(get_session)
        ) -> dict[str, Any]:
            return await get_board_or_404(s, board_id)

        return _from_board_id

    async def _from_id(
        id: str = Path(...), s: AsyncSession = Depends(get_session)
    ) -> dict[str, Any]:
        return await get_board_or_404(s, id)

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


def is_product_board(board: dict[str, Any]) -> bool:
    """An operational board of a Pampufa product (NG13 applies to these only)."""
    return board.get("product_id") != PRODUCT_STANDALONE and board.get("kind") == "operational"


def assert_board_writable(board: dict[str, Any]) -> None:
    """A board swapped out of its product (NG14) keeps its history and takes no writes."""
    if board.get("read_only"):
        raise Conflict(
            "board_read_only",
            "This board was replaced by a new flow for its product and is read-only: its "
            "history and metrics stay, nothing on it can change.",
            {"board_id": board.get("id"), "product_id": board.get("product_id")},
        )


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
    assert_board_writable(board)
    return board


async def touch_last_login(s: AsyncSession, user_id: str) -> str:
    """Stamp ``users.last_login_at``; returns the timestamp written (not committed)."""
    now = utcnow()
    await s.execute(update(User).where(User.id == user_id).values(last_login_at=now))
    return now


def token_ttl() -> int:
    """Configured access-token lifetime in seconds (for ``expires_in``)."""
    return settings.token_ttl_seconds


def _looks_like_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True
