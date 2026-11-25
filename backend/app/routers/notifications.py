"""The standalone Nagare's bell (24/11/2026, decision R23 of the Reports plan).

General notices for one person: the bell lists them, counts the unread ones and marks them
read. The first kind of notice (a scheduled report was issued) arrives with the Reports'
wave 3; until then the list is simply empty. Each new kind enters on request.

Only the standalone Nagare has this bell: inside the Pampufa the platform's own bell does
the job (:func:`~backend.app.deps.require_standalone`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..deps import CurrentUser, get_session, require_standalone
from ..errors import NotFound
from ..models import Notification, as_dict

router = APIRouter(prefix="/notifications", tags=["notifications"])

__all__ = ["create_notification", "router"]

_FIELDS = ("id", "product_id", "kind", "title", "body", "link", "created_at", "read_at")


def _out(row: Notification) -> dict[str, Any]:
    data = as_dict(row) or {}
    return {key: data.get(key) for key in _FIELDS}


def create_notification(
    s: AsyncSession,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    product_id: str = "nagare",
) -> Notification:
    """Add one notice to a person's bell, in the caller's transaction."""
    row = Notification(
        user_id=user_id,
        product_id=product_id,
        kind=kind,
        title=title,
        body=body,
        link=link,
        created_at=utcnow(),
    )
    s.add(row)
    return row


@router.get("", summary="The person's notices, newest first, and how many are unread")
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_standalone),
) -> dict[str, Any]:
    rows = (
        await s.scalars(
            select(Notification)
            .where(Notification.user_id == user["id"])
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit)
        )
    ).all()
    unread = await s.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user["id"], Notification.read_at.is_(None))
    )
    return {"items": [_out(r) for r in rows], "unread": int(unread or 0)}


@router.post("/read-all", summary="Mark every notice of the person as read")
async def read_all(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_standalone),
) -> dict[str, Any]:
    result = await s.execute(
        update(Notification)
        .where(Notification.user_id == user["id"], Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    await s.commit()
    return {"ok": True, "marked": int(result.rowcount or 0)}


@router.post("/{id}/read", summary="Mark one notice as read")
async def read_one(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_standalone),
) -> dict[str, Any]:
    row = await s.scalar(
        select(Notification).where(Notification.id == id, Notification.user_id == user["id"])
    )
    if row is None:
        raise NotFound("Notification", id, code="notification_not_found")
    if row.read_at is None:
        row.read_at = utcnow()
        await s.commit()
    return _out(row)
