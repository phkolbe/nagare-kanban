"""User administration — ``/api/v1/users``.

Mutating a user is guarded by the ``manage_users`` capability (master and
administrator, SPEC §4). Reading the directory is open to any signed-in identity:
assignee pickers, filter bars and the card drawer all need to resolve a name, and
the payload never contains a password hash.

Beyond the role check, five guardrails protect the workspace from locking itself
out or from a privilege escalation through the admin screen:

1. Only a ``master`` may **create** a master.
2. Only a ``master`` may **demote, deactivate, rename, re-mail or re-password** a
   master account — otherwise an administrator could simply reset the owner's
   password and take the workspace over.
3. Nobody may change **their own** role, in either direction.
4. Nobody may deactivate or delete **themselves**.
5. The **last active master** can never be demoted, deactivated or deleted.

Deletion is always soft (``is_active = 0``): movement history, audit rows and
card assignments reference users, so a hard delete would tear holes in the
metrics. A deactivated account is refused by ``/auth/login`` and by every
authenticated request, which makes soft delete an immediate revocation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ROLE_CAPABILITIES, ROLE_DESCRIPTIONS, ROLE_LABELS, ROLES
from ..db import new_id, utcnow
from ..deps import CurrentUser, get_session, require_capability, require_flag
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..models import User, as_dict
from ..schemas import (
    ApiErrorBody,
    ListResponse,
    OkResponse,
    RoleCatalogueOut,
    UserCreate,
    UserOut,
    UserUpdate,
)

__all__ = ["CAPABILITY_LABELS", "RoleCatalogueDetailOut", "router"]

router = APIRouter(prefix="/users", tags=["users"])

#: master + administrator (SPEC §4 "Manage users/roles")
manage_users = require_capability("manage_users")

#: Human labels for the capability keys in :data:`config.ROLE_CAPABILITIES`.
#: The admin UI renders the matrix straight from this, so the column headings
#: live next to the matrix instead of being retyped in JavaScript.
CAPABILITY_LABELS: dict[str, str] = {
    "manage_plans_flags": "Manage plans & flags",
    "manage_users": "Manage users/roles",
    "manage_boards": "Create/edit boards & columns",
    "manage_cards": "Create/edit/move cards",
    "read_boards_metrics": "Read boards & metrics",
    "simulator_boards": "Simulator boards (CRUD + run)",
    "api_tokens": "Integration/API token use",
}

#: Order the matrix is rendered in — identical to the table in SPEC §4.
CAPABILITY_ORDER: tuple[str, ...] = (
    "manage_plans_flags",
    "manage_users",
    "manage_boards",
    "manage_cards",
    "read_boards_metrics",
    "simulator_boards",
    "api_tokens",
)

#: Roles a caller must be a ``master`` to grant, revoke or edit.
PROTECTED_ROLES: frozenset[str] = frozenset({"master"})

_PRIVILEGE_ORDER: dict[str, int] = {
    "master": 0,
    "administrator": 1,
    "editor": 2,
    "simulator": 3,
    "viewer": 4,
    "system": 5,
}


class RoleCatalogueDetailOut(RoleCatalogueOut):
    """``GET /users/roles`` — the SPEC §4 matrix as data, plus admin context."""

    #: capability key -> the roles that hold it (the matrix read column-wise)
    matrix: dict[str, list[str]]
    #: role -> number of active users currently holding it
    user_counts: dict[str, int]
    #: roles that may only be granted by a master
    protected_roles: list[str]
    #: roles that can never sign in interactively
    non_interactive_roles: list[str]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _public(row: dict[str, Any]) -> dict[str, Any]:
    """Strip the password hash and coerce sqlite integers to booleans."""
    data = {key: value for key, value in row.items() if key != "password_hash"}
    data["is_active"] = bool(row.get("is_active", 1))
    return data


async def _load(s: AsyncSession, user_id: str) -> dict[str, Any]:
    row = as_dict(await s.get(User, user_id))
    if row is None:
        raise NotFound("User", user_id, code="user_not_found")
    return row


async def _email_taken(s: AsyncSession, email: str, *, excluding: str | None = None) -> bool:
    query = select(User.id).where(func.lower(User.email) == email.strip().lower())
    if excluding:
        query = query.where(User.id != excluding)
    return (await s.scalar(query)) is not None


async def _active_masters(s: AsyncSession, *, excluding: str | None = None) -> int:
    query = select(func.count()).where(User.role == "master", User.is_active.is_(True))
    if excluding:
        query = query.where(User.id != excluding)
    return int(await s.scalar(query) or 0)


def _assert_may_touch_master(actor: CurrentUser, target: dict[str, Any]) -> None:
    """Only a master may edit a master account (guardrail 2)."""
    if target["role"] in PROTECTED_ROLES and actor["role"] != "master":
        raise Forbidden(
            "Only a master can modify another master account.",
            code="master_account_protected",
            details={"role": actor["role"], "target_role": target["role"]},
        )


def _assert_may_grant(actor: CurrentUser, role: str) -> None:
    """Only a master may hand out the master role (guardrail 1)."""
    if role in PROTECTED_ROLES and actor["role"] != "master":
        raise Forbidden(
            "Only a master can grant the master role.",
            code="master_role_forbidden",
            details={"role": actor["role"], "requested_role": role},
        )


async def _assert_last_master_survives(
    s: AsyncSession, target: dict[str, Any], action: str
) -> None:
    """Refuse anything that would leave the workspace without an active master."""
    if target["role"] != "master" or not target["is_active"]:
        return
    if await _active_masters(s, excluding=target["id"]) == 0:
        raise Conflict(
            "last_master_protected",
            "This is the last active master; the workspace would be left without an owner.",
            {"action": action, "user_id": target["id"]},
        )


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ListResponse[UserOut],
    summary="List and search users",
)
async def list_users(
    s: AsyncSession = Depends(get_session),
    _actor: CurrentUser = Depends(require_capability("read_boards_metrics")),
    role: str | None = Query(None, description="Filter by exact role."),
    q: str | None = Query(None, description="Case-insensitive match on name or email."),
    is_active: bool | None = Query(None, description="Filter by activation state."),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> ListResponse[UserOut]:
    """The user directory, ordered by privilege then name."""
    conditions: list[Any] = []

    if role:
        if role not in ROLES:
            raise BadRequest(
                f"Unknown role '{role}'.",
                code="unknown_role",
                details={"role": role, "known": list(ROLES)},
            )
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active.is_(bool(is_active)))
    if q:
        needle = f"%{q.strip().lower()}%"
        conditions.append(
            or_(func.lower(User.name).like(needle), func.lower(User.email).like(needle))
        )

    total = int(await s.scalar(select(func.count()).select_from(User).where(*conditions)) or 0)
    privilege = case(_PRIVILEGE_ORDER, value=User.role, else_=99)
    users = await s.scalars(
        select(User)
        .where(*conditions)
        .order_by(privilege, func.lower(User.name))
        .limit(limit)
        .offset(offset)
    )
    return ListResponse[UserOut](
        items=[UserOut(**_public(as_dict(user) or {})) for user in users], total=total
    )


@router.get(
    "/roles",
    response_model=RoleCatalogueDetailOut,
    responses={403: {"model": ApiErrorBody, "description": "Plan or role restriction"}},
    summary="Role catalogue with the full SPEC §4 capability matrix",
)
async def role_catalogue(
    s: AsyncSession = Depends(get_session),
    _actor: CurrentUser = Depends(manage_users),
    _flag: None = Depends(require_flag("rbac_advanced")),
) -> dict[str, Any]:
    """The six roles and every capability, as data the admin UI can render blind."""
    counts = {
        role: int(n)
        for role, n in await s.execute(
            select(User.role, func.count()).where(User.is_active.is_(True)).group_by(User.role)
        )
    }
    roles = [
        {
            "key": name,
            "label": ROLE_LABELS.get(name, name.title()),
            "description": ROLE_DESCRIPTIONS.get(name, ""),
            "capabilities": {
                capability: name in ROLE_CAPABILITIES[capability] for capability in CAPABILITY_ORDER
            },
        }
        for name in ROLES
    ]
    return {
        "roles": roles,
        "capabilities": list(CAPABILITY_ORDER),
        "capability_labels": {key: CAPABILITY_LABELS[key] for key in CAPABILITY_ORDER},
        "matrix": {
            capability: [name for name in ROLES if name in ROLE_CAPABILITIES[capability]]
            for capability in CAPABILITY_ORDER
        },
        "user_counts": {name: counts.get(name, 0) for name in ROLES},
        "protected_roles": sorted(PROTECTED_ROLES),
        "non_interactive_roles": ["system"],
    }


@router.get("/{user_id}", response_model=UserOut, summary="Fetch one user")
async def get_user(
    user_id: str,
    s: AsyncSession = Depends(get_session),
    _actor: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> UserOut:
    """One user by id — the drawer resolves assignees through this."""
    return UserOut(**_public(await _load(s, user_id)))


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"model": ApiErrorBody, "description": "Role restriction"},
        409: {"model": ApiErrorBody, "description": "Email already registered"},
    },
    summary="Create a user",
)
async def create_user(
    payload: UserCreate,
    s: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(manage_users),
) -> UserOut:
    """Create an account with a PBKDF2-hashed password."""
    from ..security import hash_password  # local import keeps the module import cheap

    email = payload.email.strip().lower()
    _assert_may_grant(actor, payload.role)
    if await _email_taken(s, email):
        raise Conflict(
            "email_taken",
            "That email address is already registered.",
            {"email": email},
        )

    row = {
        "id": new_id(),
        "email": email,
        "name": payload.name.strip(),
        "password_hash": hash_password(payload.password),
        "role": payload.role,
        "is_active": bool(payload.is_active),
        "created_at": utcnow(),
        "last_login_at": None,
    }
    s.add(User(**row))
    await s.commit()
    return UserOut(**_public(row))


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    responses={
        403: {"model": ApiErrorBody, "description": "Role restriction"},
        409: {"model": ApiErrorBody, "description": "Guardrail violation"},
    },
    summary="Update a user, their role or their activation state",
)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    s: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(manage_users),
) -> UserOut:
    """Apply a partial update after every SPEC §4 guardrail has been checked."""
    from ..security import hash_password

    target = await _load(s, user_id)
    is_self = target["id"] == actor["id"]
    _assert_may_touch_master(actor, target)

    changes: dict[str, Any] = {}

    if payload.role is not None and payload.role != target["role"]:
        if is_self:
            raise Conflict(
                "self_role_change_blocked",
                "You cannot change your own role. Ask another master to do it.",
                {"user_id": user_id, "role": target["role"]},
            )
        _assert_may_grant(actor, payload.role)
        if target["role"] in PROTECTED_ROLES:
            # Demoting a master: _assert_may_touch_master already proved the actor
            # is a master, so only the "never the last one" rule is left.
            await _assert_last_master_survives(s, target, "demote")
        changes["role"] = payload.role

    if payload.is_active is not None and bool(payload.is_active) != bool(target["is_active"]):
        if not payload.is_active:
            if is_self:
                raise Conflict(
                    "self_deactivation_blocked",
                    "You cannot deactivate your own account.",
                    {"user_id": user_id},
                )
            await _assert_last_master_survives(s, target, "deactivate")
        changes["is_active"] = bool(payload.is_active)

    if payload.email is not None:
        email = payload.email.strip().lower()
        if email != str(target["email"]).lower():
            if await _email_taken(s, email, excluding=user_id):
                raise Conflict(
                    "email_taken",
                    "That email address is already registered.",
                    {"email": email},
                )
            changes["email"] = email

    if payload.name is not None:
        changes["name"] = payload.name.strip()

    if payload.password is not None:
        changes["password_hash"] = hash_password(payload.password)

    if not changes:
        return UserOut(**_public(target))

    await s.execute(update(User).where(User.id == user_id).values(**changes))
    await s.commit()
    return UserOut(**_public(await _load(s, user_id)))


@router.delete(
    "/{user_id}",
    response_model=OkResponse,
    responses={409: {"model": ApiErrorBody, "description": "Guardrail violation"}},
    summary="Deactivate a user (soft delete)",
)
async def delete_user(
    user_id: str,
    s: AsyncSession = Depends(get_session),
    actor: CurrentUser = Depends(manage_users),
) -> OkResponse:
    """Soft-delete: history keeps its author, and the session is revoked at once."""
    target = await _load(s, user_id)
    _assert_may_touch_master(actor, target)
    if target["id"] == actor["id"]:
        raise Conflict(
            "self_deactivation_blocked",
            "You cannot deactivate your own account.",
            {"user_id": user_id},
        )
    await _assert_last_master_survives(s, target, "delete")

    if target["is_active"]:
        await s.execute(update(User).where(User.id == user_id).values(is_active=False))
        await s.commit()
    return OkResponse(
        ok=True,
        id=user_id,
        message=f"{target['name']} was deactivated and can no longer sign in.",
    )
