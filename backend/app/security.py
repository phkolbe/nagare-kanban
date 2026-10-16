"""Password hashing and JSON Web Tokens, built from the standard library only.

Two primitives live here:

* **PBKDF2-SHA256** password hashing with a per-user salt, stored as
  ``pbkdf2$<iterations>$<salt_b64>$<hash_b64>``. The iteration count travels with
  the hash so it can be raised later without invalidating existing passwords.
* **HS256 JWT** encode/decode assembled from ``hmac`` + ``hashlib`` + ``base64``.
  Signature comparison is constant time (``hmac.compare_digest``).

No third-party crypto library is used, and none is needed: both algorithms are
fully specified by RFC 8018 and RFC 7519 and both are available in ``hashlib``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from .config import settings
from .errors import ApiError

__all__ = [
    "TokenError",
    "decode_token",
    "encode_token",
    "generate_secret",
    "hash_password",
    "needs_rehash",
    "token_expiry",
    "verify_password",
]

_PBKDF2_PREFIX = "pbkdf2"
_SALT_BYTES = 16
_HASH_BYTES = 32


class TokenError(ApiError):
    """A JWT could not be trusted. Always surfaces as HTTP 401."""

    def __init__(self, message: str, code: str = "invalid_token") -> None:
        super().__init__(code, message, status=401)


# ---------------------------------------------------------------------------
# base64url helpers (JWT uses unpadded base64url everywhere)
# ---------------------------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------


def hash_password(password: str, iterations: int | None = None) -> str:
    """Hash a plaintext password into ``pbkdf2$iters$salt$hash``."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    rounds = int(iterations or settings.pbkdf2_iterations)
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, _HASH_BYTES)
    return f"{_PBKDF2_PREFIX}${rounds}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of a plaintext password against a stored hash."""
    if not password or not stored:
        return False
    try:
        scheme, rounds_text, salt_text, hash_text = stored.split("$", 3)
    except ValueError:
        return False
    if scheme != _PBKDF2_PREFIX:
        return False
    try:
        rounds = int(rounds_text)
        salt = _b64d(salt_text)
        expected = _b64d(hash_text)
    except (ValueError, TypeError):
        return False
    if rounds <= 0 or not salt or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, len(expected))
    return hmac.compare_digest(candidate, expected)


def needs_rehash(stored: str | None) -> bool:
    """True when a stored hash uses fewer iterations than the current policy."""
    if not stored:
        return True
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != _PBKDF2_PREFIX:
        return True
    try:
        return int(parts[1]) < settings.pbkdf2_iterations
    except ValueError:
        return True


def generate_secret(nbytes: int = 32) -> str:
    """A URL-safe random secret (used for JWT secrets and API tokens)."""
    return secrets.token_urlsafe(nbytes)


# ---------------------------------------------------------------------------
# JWT (HS256)
# ---------------------------------------------------------------------------


def _sign(message: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()


def _json_compact(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def encode_token(
    sub: str,
    role: str,
    ttl_seconds: int | None = None,
    extra_claims: dict[str, Any] | None = None,
    secret: str | None = None,
) -> str:
    """Build a signed HS256 token for ``sub`` carrying ``role``.

    Claims: ``sub``, ``role``, ``iat``, ``exp``, ``iss``, ``jti`` plus anything in
    ``extra_claims`` (which can never override the reserved names above).
    """
    now = int(time.time())
    ttl = int(ttl_seconds if ttl_seconds is not None else settings.token_ttl_seconds)
    claims: dict[str, Any] = dict(extra_claims or {})
    claims.update(
        {
            "sub": str(sub),
            "role": role,
            "iat": now,
            "exp": now + ttl,
            "iss": settings.jwt_issuer,
            "jti": secrets.token_hex(8),
        }
    )
    header = {"alg": settings.jwt_algorithm, "typ": "JWT"}
    signing_input = f"{_b64e(_json_compact(header))}.{_b64e(_json_compact(claims))}".encode("ascii")
    signature = _sign(signing_input, secret or settings.jwt_secret)
    return f"{signing_input.decode('ascii')}.{_b64e(signature)}"


def decode_token(token: str, secret: str | None = None, verify_exp: bool = True) -> dict[str, Any]:
    """Verify a token's signature and expiry and return its claims.

    Raises :class:`TokenError` (HTTP 401) for anything malformed, unsigned with
    the wrong key, using an unexpected algorithm, or expired.
    """
    if not token or not isinstance(token, str):
        raise TokenError("No token supplied.")
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise TokenError("Malformed token.")
    header_text, claims_text, signature_text = parts

    try:
        header = json.loads(_b64d(header_text))
        claims = json.loads(_b64d(claims_text))
        signature = _b64d(signature_text)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise TokenError("Malformed token.") from None

    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("Malformed token.")
    # Refuse "alg": "none" and any algorithm substitution outright.
    if header.get("alg") != settings.jwt_algorithm:
        raise TokenError("Unsupported token algorithm.")

    expected = _sign(f"{header_text}.{claims_text}".encode("ascii"), secret or settings.jwt_secret)
    if not hmac.compare_digest(signature, expected):
        raise TokenError("Token signature does not match.")

    if verify_exp:
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            raise TokenError("Token has no expiry.")
        if int(time.time()) >= int(exp):
            raise TokenError("Token has expired.", code="token_expired")

    if not claims.get("sub"):
        raise TokenError("Token has no subject.")
    return claims


def token_expiry(ttl_seconds: int | None = None) -> int:
    """Seconds until a freshly minted token expires (for ``expires_in``)."""
    return int(ttl_seconds if ttl_seconds is not None else settings.token_ttl_seconds)
