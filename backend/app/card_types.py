"""A product's card types (PPLAT-312, 12/11/2026): the keys ``cards.value_area`` takes.

A product without rows in ``card_types`` uses the software list of the standalone Nagare
(:data:`~backend.app.config.VALUE_AREAS`), with the labels of the screens. The first edit
copies that list into rows, so it becomes the product's own. The legal products (SR Adv and
SR GJ) start with the Sarah's list (migration ``ng_0007``).

A type is never deleted while a card has it: it is archived, keeps its name on those cards
and leaves the pickers.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import VALUE_AREAS
from .db import new_id, utcnow
from .errors import BadRequest
from .models import CardType, as_dict

__all__ = [
    "KEY_PATTERN",
    "check_type",
    "default_type",
    "key_from_name",
    "materialise",
    "product_types",
]

#: What a type key looks like (it lives in ``cards.value_area``, ``String(20)``).
KEY_PATTERN = r"^[a-z0-9_]{1,20}$"

#: The type a providência card gets, when the product has it (else the first one).
PROVIDENCIA_TYPES: tuple[str, ...] = ("administrativo", "feature")


def _builtin(position: int, key: str) -> dict[str, Any]:
    return {"key": key, "name": None, "position": position, "archived": False, "builtin": True}


def _out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": row["key"],
        "name": row.get("name"),
        "position": int(row.get("position") or 0),
        "archived": bool(row.get("archived")),
        "builtin": False,
    }


async def product_types(s: AsyncSession, product_id: str) -> list[dict[str, Any]]:
    """The product's types in order, archived ones included (``builtin``: the default list)."""
    rows = (
        await s.scalars(
            select(CardType)
            .where(CardType.product_id == product_id)
            .order_by(CardType.position, CardType.key)
        )
    ).all()
    if not rows:
        return [_builtin(i, key) for i, key in enumerate(VALUE_AREAS)]
    return [_out(as_dict(row) or {}) for row in rows]


async def default_type(s: AsyncSession, product_id: str, prefer: tuple[str, ...] = ()) -> str:
    """The type a new card gets when none is given: a preferred one, else the first active."""
    active = [t["key"] for t in await product_types(s, product_id) if not t["archived"]]
    for key in prefer:
        if key in active:
            return key
    return active[0] if active else VALUE_AREAS[0]


async def check_type(
    s: AsyncSession, product_id: str, key: str, *, current: str | None = None
) -> str:
    """``key`` if a card of the product may take it now (an active type, or the one it has)."""
    if key == current:
        return key
    types = await product_types(s, product_id)
    active = [t["key"] for t in types if not t["archived"]]
    if key not in active:
        raise BadRequest(
            f"“{key}” is not a card type of this product.",
            code="invalid_card_type",
            details={"value_area": key, "allowed": active},
        )
    return key


async def materialise(s: AsyncSession, product_id: str) -> None:
    """Copy the default list into rows, so the product can change it (no-op if it has one)."""
    exists = await s.scalar(
        select(func.count()).select_from(CardType).where(CardType.product_id == product_id)
    )
    if exists:
        return
    now = utcnow()
    for position, key in enumerate(VALUE_AREAS):
        s.add(
            CardType(
                id=new_id(),
                product_id=product_id,
                key=key,
                name=None,
                position=position,
                archived=False,
                created_at=now,
            )
        )
    await s.flush()


def key_from_name(name: str, taken: set[str]) -> str:
    """A stable key for a new type: the name without accents, ``_`` for the rest, unique."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "_", plain.lower()).strip("_")[:20].strip("_") or "tipo"
    key, n = base, 2
    while key in taken:
        suffix = f"_{n}"
        key = f"{base[: 20 - len(suffix)]}{suffix}"
        n += 1
    return key
