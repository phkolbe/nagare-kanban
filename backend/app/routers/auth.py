"""Authentication and identity — ``/api/v1/auth``.

Three endpoints back the whole session lifecycle:

``POST /auth/login``
    Email + password against the PBKDF2 hash in ``users.password_hash``. On
    success it stamps ``users.last_login_at``, transparently upgrades a stale
    hash to the current iteration count, and returns a signed HS256 token.

``POST /auth/refresh``
    Trades a still-valid token for a fresh one so a working session never expires
    mid-flow. The database — not the old token — decides the role that is minted.

``GET /auth/me``
    The caller's user row plus the SPEC §4 capability matrix for their role and
    the currently effective feature flags, so the shell can gate its navigation
    in a single request after boot.

**Enumeration resistance.** Every credential failure — unknown email, wrong
password — answers with the same ``401 invalid_credentials`` body, and an unknown
email still pays for one PBKDF2 verification against a dummy hash so the response
time does not betray which addresses exist. Account-state failures (deactivated
account, the non-interactive ``system`` identity) are only reported *after* the
password has been verified, so they cannot be used as an oracle either.

**Brute-force resistance.** Failures are counted per email address in memory. The
fifth failure inside the sliding window locks that address for 30 seconds and
every further failure doubles the lockout up to 15 minutes; a successful login
clears the record. The state is process-local on purpose: this product ships as a
single uvicorn process against one sqlite file, and a table write per failed
password would be a free denial-of-service vector.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import (
    NON_INTERACTIVE_ROLES,
    ROLE_CAPABILITIES,
    ROLE_DESCRIPTIONS,
    ROLE_LABELS,
)
from ..deps import (
    CurrentUser,
    effective_flags,
    get_current_user,
    get_session,
    token_ttl,
    touch_last_login,
)
from ..errors import ApiError, Forbidden, Unauthorized
from ..models import User, as_dict
from ..schemas import ApiErrorBody, LoginRequest, TokenResponse, UserOut
from ..security import encode_token, hash_password, needs_rehash, verify_password

__all__ = ["MeOut", "login_throttle_state", "reset_login_throttle", "router"]

router = APIRouter(prefix="/auth", tags=["auth"])

#: Public error shapes for the OpenAPI document (the frontend switches on ``code``).
_AUTH_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ApiErrorBody, "description": "Invalid or missing credentials"},
    403: {"model": ApiErrorBody, "description": "Account disabled or non-interactive"},
    429: {"model": ApiErrorBody, "description": "Too many failed attempts"},
}


# ---------------------------------------------------------------------------
# response models
# ---------------------------------------------------------------------------


class MeOut(UserOut):
    """``GET /auth/me`` — the user plus everything the shell needs to render nav."""

    role_label: str
    role_description: str
    #: capability -> whether this role holds it (SPEC §4)
    capabilities: dict[str, bool]
    #: the capabilities this role actually holds, for terse checks in the UI
    granted: list[str]
    #: ``{flag_key: bool}`` — enabled *and* unlocked by the active plan (SPEC §3)
    flags: dict[str, bool]
    #: seconds until the presented token expires
    expires_in: int
    #: the language of the screens: the person's choice, else the default of the way in
    locale: str
    #: what the person chose (``None``: following the default)
    locale_chosen: str | None = None
    #: ``pampufa`` (platform sign-in) or ``local``
    via: str
    #: the product this request acts on
    product_id: str


class MePatch(BaseModel):
    """``PATCH /auth/me`` — the person's own preferences."""

    #: ``None`` goes back to the default of the way in
    locale: Literal["pt-BR", "en"] | None


# ---------------------------------------------------------------------------
# brute-force throttle
# ---------------------------------------------------------------------------

#: failures tolerated before the first lockout kicks in
MAX_FAILURES = 5
#: lockout applied at ``MAX_FAILURES``; doubles per further failure
BASE_LOCKOUT_SECONDS = 30
MAX_LOCKOUT_SECONDS = 15 * 60
#: a run of failures older than this is forgotten
FAILURE_WINDOW_SECONDS = 15 * 60


@dataclass(slots=True)
class _Attempts:
    failures: int = 0
    last_failure: float = 0.0
    locked_until: float = 0.0


_attempts: dict[str, _Attempts] = {}
_attempts_lock = threading.Lock()

_DUMMY_PASSWORD = "nagare-timing-equaliser"
_dummy_hash_cache: str | None = None


def _dummy_hash() -> str:
    """A real PBKDF2 hash used to burn the same CPU for unknown addresses."""
    global _dummy_hash_cache
    if _dummy_hash_cache is None:
        _dummy_hash_cache = hash_password(_DUMMY_PASSWORD)
    return _dummy_hash_cache


def _prune(now: float) -> None:
    """Drop records whose window has elapsed (called under the lock)."""
    stale = [
        key
        for key, record in _attempts.items()
        if record.locked_until <= now and now - record.last_failure > FAILURE_WINDOW_SECONDS
    ]
    for key in stale:
        del _attempts[key]


def _assert_not_locked(email: str) -> None:
    """Raise 429 while ``email`` is inside a lockout window."""
    now = time.monotonic()
    with _attempts_lock:
        _prune(now)
        record = _attempts.get(email)
        if record is None or record.locked_until <= now:
            return
        retry_after = int(record.locked_until - now) + 1
    raise ApiError(
        "too_many_login_attempts",
        "Too many failed sign-in attempts. Try again shortly.",
        status=429,
        details={"retry_after_seconds": retry_after},
    )


def _register_failure(email: str) -> None:
    """Count one failed attempt and extend the lockout when the run is long enough."""
    now = time.monotonic()
    with _attempts_lock:
        record = _attempts.get(email)
        if record is None or now - record.last_failure > FAILURE_WINDOW_SECONDS:
            record = _Attempts()
            _attempts[email] = record
        record.failures += 1
        record.last_failure = now
        if record.failures >= MAX_FAILURES:
            steps = record.failures - MAX_FAILURES
            lockout = min(BASE_LOCKOUT_SECONDS * (2**steps), MAX_LOCKOUT_SECONDS)
            record.locked_until = now + lockout


def _clear_failures(email: str) -> None:
    with _attempts_lock:
        _attempts.pop(email, None)


def reset_login_throttle(email: str | None = None) -> None:
    """Forget brute-force state for one address, or for all of them.

    Exposed for tests and for an operator who needs to unlock an account without
    restarting the process.
    """
    with _attempts_lock:
        if email is None:
            _attempts.clear()
        else:
            _attempts.pop(email.strip().lower(), None)


def login_throttle_state(email: str) -> dict[str, Any]:
    """Introspect the throttle for one address (diagnostics and tests)."""
    now = time.monotonic()
    with _attempts_lock:
        record = _attempts.get(email.strip().lower())
        if record is None:
            return {"failures": 0, "locked": False, "retry_after_seconds": 0}
        return {
            "failures": record.failures,
            "locked": record.locked_until > now,
            "retry_after_seconds": max(0, int(record.locked_until - now)),
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _public_user(row: dict[str, Any]) -> dict[str, Any]:
    """A user row without its password hash, with sqlite integers coerced."""
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "created_at": row.get("created_at"),
        "last_login_at": row.get("last_login_at"),
    }


def _capabilities_for(role: str) -> dict[str, bool]:
    """The SPEC §4 matrix row for ``role`` as ``{capability: bool}``."""
    return {name: role in roles for name, roles in ROLE_CAPABILITIES.items()}


def _issue(row: dict[str, Any]) -> dict[str, Any]:
    """Mint a token response for an authenticated user row."""
    ttl = token_ttl()
    return {
        "access_token": encode_token(sub=row["id"], role=row["role"], ttl_seconds=ttl),
        "token_type": "bearer",
        "expires_in": ttl,
        "user": _public_user(row),
    }


def _assert_interactive(row: dict[str, Any]) -> None:
    """Enforce the two account-state rules that block a UI session.

    Deliberately called *after* the password check: an attacker must already hold
    valid credentials before the API will confirm that an address is disabled or
    belongs to the integration identity.
    """
    if row["role"] in NON_INTERACTIVE_ROLES:
        raise Forbidden(
            "The system identity is for API and MCP access only and cannot sign in.",
            code="interactive_login_denied",
            details={"role": row["role"]},
        )
    if not row["is_active"]:
        raise Forbidden(
            "This account has been deactivated. Ask an administrator to restore it.",
            code="account_disabled",
            details={"email": row["email"]},
        )


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,
    responses=_AUTH_RESPONSES,
    summary="Exchange email and password for an access token",
)
async def login(
    payload: LoginRequest,
    request: Request,
    s: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Authenticate a human user and return a bearer token plus their profile."""
    email = payload.email.strip().lower()
    _assert_not_locked(email)

    row = as_dict(await s.scalar(select(User).where(func.lower(User.email) == email)))
    stored = row["password_hash"] if row else _dummy_hash()
    if not verify_password(payload.password, stored) or row is None:
        _register_failure(email)
        raise Unauthorized(
            "Email or password is incorrect.",
            code="invalid_credentials",
            details={"email": email},
        )

    # The credentials are good; from here on failures describe account state.
    _clear_failures(email)
    _assert_interactive(row)

    # Opportunistic upgrade: raising `pbkdf2_iterations` re-hashes on next login
    # instead of forcing a password reset.
    if needs_rehash(row["password_hash"]):
        await s.execute(
            update(User)
            .where(User.id == row["id"])
            .values(password_hash=hash_password(payload.password))
        )

    row["last_login_at"] = await touch_last_login(s, row["id"])
    await s.commit()
    response = _issue(row)
    response["user"]["last_login_at"] = row["last_login_at"]
    # The client address is logged by uvicorn; keeping it out of the body avoids
    # echoing anything an attacker controls back to the browser.
    _ = request.client
    return response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses=_AUTH_RESPONSES,
    summary="Trade a valid token for a fresh one",
)
async def refresh(
    user: CurrentUser = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Re-mint a token from the *database* row, so a role change lands at once."""
    row = as_dict(await s.get(User, user["id"]))
    if row is None:  # pragma: no cover - get_current_user already loaded it
        raise Unauthorized("The account for this token no longer exists.", code="unknown_subject")
    _assert_interactive(row)
    return _issue(row)


@router.get(
    "/me",
    response_model=MeOut,
    responses={401: _AUTH_RESPONSES[401], 403: _AUTH_RESPONSES[403]},
    summary="The signed-in user, their capabilities and the effective flags",
)
async def me(
    user: CurrentUser = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Everything the shell needs to draw itself for this identity."""
    return await _me(s, user)


@router.patch(
    "/me",
    response_model=MeOut,
    summary="Change the signed-in person's preferences (the language of the screens)",
)
async def update_me(
    payload: MePatch,
    user: CurrentUser = Depends(get_current_user),
    s: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The choice is the person's, on every device (decision of 17/10/2026)."""
    if user["role"] == "system":
        raise Forbidden("A service identity has no preferences.", code="system_has_no_preferences")
    await s.execute(update(User).where(User.id == user["id"]).values(locale=payload.locale))
    await s.commit()
    return await _me(s, {**user, "locale": payload.locale})


def default_locale(via: str) -> str:
    """Portuguese through the Pampufa, English on the Nagare's own login (N2)."""
    return "pt-BR" if via == "pampufa" else "en"


async def _me(s: AsyncSession, user: CurrentUser) -> dict[str, Any]:
    role = str(user["role"])
    capabilities = _capabilities_for(role)
    via = str(user.get("via") or "local")
    chosen = user.get("locale")
    return {
        **_public_user(dict(user)),
        "role_label": ROLE_LABELS.get(role, role.title()),
        "role_description": ROLE_DESCRIPTIONS.get(role, ""),
        "capabilities": capabilities,
        "granted": [name for name, held in capabilities.items() if held],
        "flags": await effective_flags(s),
        "expires_in": token_ttl(),
        "locale": chosen or default_locale(via),
        "locale_chosen": chosen,
        "via": via,
        "product_id": str(user.get("product_id") or ""),
    }


@router.get(
    "/capabilities",
    response_model=dict[str, bool],
    status_code=status.HTTP_200_OK,
    summary="Capability matrix row for the signed-in role",
)
async def my_capabilities(user: CurrentUser = Depends(get_current_user)) -> dict[str, bool]:
    """The caller's own SPEC §4 matrix row — a cheap poll for optimistic UI."""
    return _capabilities_for(str(user["role"]))
