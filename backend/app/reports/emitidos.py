"""Issued reports and client links (wave 2 of the Reports, 25/11/2026): the rules.

* An **issued report** keeps the whole Document (decision R2): reopening it shows exactly what
  was issued, even after the board changed.
* A **client link** (R5, R18, R19) opens one issued report without signing in. Only issued
  copies of REP-02 (project status) and REP-03 (hours) can be shared (R6), and a link is
  always tied to projects: REP-02's own, or the projects REP-03 was filtered by (REP-03 for
  every project cannot be shared). The token is 32 random bytes shown once; the database
  keeps only its SHA-256. A link lasts 7 days unless chosen otherwise, and can be revoked.
* The public page answers a limited number of requests per client IP
  (:class:`PublicRateLimit`). Behind a proxy that hides the client (the home staging on
  Docker Desktop), every request has the same IP, so the limit is shared by everyone.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

from ..errors import ApiError, Conflict

__all__ = [
    "DEFAULT_LINK_DAYS",
    "LINK_TEMPLATES",
    "MAX_LINK_DAYS",
    "PublicRateLimit",
    "hash_token",
    "link_projects",
    "link_state",
    "new_token",
    "public_limit",
]

#: Only these issued reports can be shared with a client (R6).
LINK_TEMPLATES: frozenset[str] = frozenset({"project_status", "hours"})
#: How long a link lasts when nobody chooses (R19), and the longest choice.
DEFAULT_LINK_DAYS = 7
MAX_LINK_DAYS = 90


def new_token() -> tuple[str, str]:
    """A fresh link token (32 random bytes, URL-safe) and the hash the database keeps."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def link_projects(template: str, document: dict[str, Any]) -> list[str]:
    """The projects a client link to this issued Document is tied to (R18).

    409 when the report cannot be shared: another template (R6), or REP-03 without chosen
    projects.
    """
    if template not in LINK_TEMPLATES:
        raise Conflict(
            "report_link_not_allowed",
            "Only issued project status (REP-02) and hours (REP-03) reports can be shared "
            "with a client.",
            {"template": template, "allowed": sorted(LINK_TEMPLATES)},
        )
    scope = document.get("scope") or {}
    if template == "project_status":
        project = scope.get("project") or {}
        ids = [str(project["id"])] if project.get("id") else []
    else:
        ids = [str(p["id"]) for p in scope.get("projects") or [] if p.get("id")]
    if not ids:
        raise Conflict(
            "report_link_needs_projects",
            "A client link is always tied to projects: issue the hours report filtered by "
            "the client's projects first.",
            {"template": template},
        )
    return ids


def link_state(link: dict[str, Any], now: datetime) -> str:
    """``active``, ``expired`` or ``revoked``."""
    if link.get("revoked_at"):
        return "revoked"
    expires = link.get("expires_at")
    if expires is not None and _as_datetime(expires) <= now:
        return "expired"
    return "active"


def _as_datetime(value: Any) -> datetime:
    from ..db import parse_iso

    moment = parse_iso(value)
    if moment is None:  # pragma: no cover - the column is NOT NULL
        raise ValueError("no instant")
    return moment


class PublicRateLimit:
    """At most ``limit`` requests per client IP in a sliding ``window`` (seconds).

    The same in-memory, single-process approach as the sign-in throttle of
    :mod:`backend.app.routers.auth` (``nagare servir`` runs one process). Every request
    counts, found or not, so tokens cannot be guessed at speed.
    """

    def __init__(self, limit: int = 60, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_ip: str | None) -> None:
        """Count one request; 429 ``too_many_requests`` when over the limit."""
        key = client_ip or "-"
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            hits = self._hits.setdefault(key, deque())
            if len(hits) >= self.limit:
                retry_after = int(hits[0] + self.window - now) + 1
                raise ApiError(
                    "too_many_requests",
                    "Too many requests. Try again shortly.",
                    status=429,
                    details={"retry_after_seconds": retry_after},
                )
            hits.append(now)

    def _prune(self, now: float) -> None:
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def reset(self) -> None:
        """Forget every client (tests, or an operator)."""
        with self._lock:
            self._hits.clear()


#: The limiter of ``GET /public/reports/{token}``.
public_limit = PublicRateLimit()
