"""Card types of the product (PPLAT-312, 12/11/2026) — ``/card-types``.

Everyone who reads the board reads the list; the product's administrator (``manage_boards``,
``nagare.admin`` on the Pampufa) edits it. A type in use is archived, never deleted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..card_types import key_from_name, materialise, product_types
from ..config import ROLE_CAPABILITIES, VALUE_AREAS
from ..db import new_id, utcnow
from ..deps import CurrentUser, get_session, require_capability
from ..errors import BadRequest, Conflict, NotFound
from ..models import Card, CardType
from ..tenancy import product_for_new_rows

router = APIRouter(tags=["card types"])


class CardTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class CardTypePatch(BaseModel):
    #: Empty (``null``) on a type of the default list goes back to the screens' label.
    name: str | None = Field(default=None, max_length=80)
    archived: bool | None = None


class CardTypeOrder(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=200)


async def _list(s: AsyncSession, user: CurrentUser) -> dict[str, Any]:
    product = product_for_new_rows()
    return {
        "product_id": product,
        "items": await product_types(s, product),
        "can_edit": user.get("role") in ROLE_CAPABILITIES["manage_boards"],
    }


async def _row(s: AsyncSession, key: str) -> CardType:
    row = await s.scalar(
        select(CardType).where(CardType.product_id == product_for_new_rows(), CardType.key == key)
    )
    if row is None:
        raise NotFound("Card type", key, code="card_type_not_found")
    return row


async def _active_others(s: AsyncSession, key: str) -> int:
    return int(
        await s.scalar(
            select(func.count())
            .select_from(CardType)
            .where(
                CardType.product_id == product_for_new_rows(),
                CardType.key != key,
                CardType.archived.is_(False),
            )
        )
        or 0
    )


def _clean(name: str | None) -> str | None:
    text = " ".join((name or "").split())
    return text or None


async def _name_taken(s: AsyncSession, name: str, key: str | None = None) -> None:
    rows = await s.scalars(select(CardType).where(CardType.product_id == product_for_new_rows()))
    for row in rows:
        if row.key != key and row.name and row.name.casefold() == name.casefold():
            raise Conflict(
                "card_type_name_taken",
                f"There is already a card type called “{name}”.",
                {"name": name, "key": row.key},
            )


@router.get("/card-types", summary="The product's card types (archived ones included)")
async def list_card_types(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    return await _list(s, user)


@router.post(
    "/card-types", status_code=status.HTTP_201_CREATED, summary="Add a card type to the product"
)
async def create_card_type(
    payload: CardTypeIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_boards")),
) -> dict[str, Any]:
    product = product_for_new_rows()
    name = _clean(payload.name)
    if name is None:
        raise BadRequest("Give the card type a name.", code="card_type_name_required")
    await materialise(s, product)
    await _name_taken(s, name)
    rows = (await s.scalars(select(CardType).where(CardType.product_id == product))).all()
    s.add(
        CardType(
            id=new_id(),
            product_id=product,
            key=key_from_name(name, {row.key for row in rows}),
            name=name,
            position=max((row.position for row in rows), default=-1) + 1,
            archived=False,
            created_at=utcnow(),
        )
    )
    await s.commit()
    return await _list(s, user)


@router.patch("/card-types/{key}", summary="Rename, archive or bring back a card type")
async def update_card_type(
    key: str,
    payload: CardTypePatch,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_boards")),
) -> dict[str, Any]:
    await materialise(s, product_for_new_rows())
    row = await _row(s, key)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes:
        name = _clean(changes["name"])
        if name is None and key not in VALUE_AREAS:
            raise BadRequest("Give the card type a name.", code="card_type_name_required")
        if name is not None:
            await _name_taken(s, name, key)
        row.name = name
    if changes.get("archived") is True and not row.archived:
        if await _active_others(s, key) == 0:
            raise Conflict(
                "card_type_last_active",
                "The product needs at least one card type in use.",
                {"key": key},
            )
        row.archived = True
    elif changes.get("archived") is False:
        row.archived = False
    await s.commit()
    return await _list(s, user)


@router.post("/card-types/order", summary="Put the product's card types in a new order")
async def order_card_types(
    payload: CardTypeOrder,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_boards")),
) -> dict[str, Any]:
    product = product_for_new_rows()
    await materialise(s, product)
    rows = {
        row.key: row
        for row in (await s.scalars(select(CardType).where(CardType.product_id == product)))
    }
    if sorted(payload.keys) != sorted(rows):
        raise BadRequest(
            "The new order must list every card type of the product once.",
            code="card_type_order_mismatch",
            details={"keys": sorted(rows)},
        )
    for position, key in enumerate(payload.keys):
        rows[key].position = position
    await s.commit()
    return await _list(s, user)


@router.delete("/card-types/{key}", summary="Remove a card type no card has")
async def delete_card_type(
    key: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_boards")),
) -> dict[str, Any]:
    product = product_for_new_rows()
    await materialise(s, product)
    row = await _row(s, key)
    used = int(
        await s.scalar(
            select(func.count())
            .select_from(Card)
            .where(Card.product_id == product, Card.value_area == key)
        )
        or 0
    )
    if used:
        raise Conflict(
            "card_type_in_use",
            f"{used} card(s) have this type: archive it instead, so they keep it.",
            {"key": key, "cards": used},
        )
    if not row.archived and await _active_others(s, key) == 0:
        raise Conflict(
            "card_type_last_active",
            "The product needs at least one card type in use.",
            {"key": key},
        )
    await s.delete(row)
    await s.commit()
    return await _list(s, user)
