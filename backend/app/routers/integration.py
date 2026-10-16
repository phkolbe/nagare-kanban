"""Pampufa integration API — ``/api/v1/integration`` (NG8-NG14).

Called by the Pampufa with its service token (role ``system``) and the product in
``X-Produto``:

``POST /integration/providencias``
    Create the card for a providência (idempotent by ``providencia_id``).
``GET /integration/providencias/{id}``
    Where that card stands.
``POST /integration/providencias/{id}/resolved-at-origin``
    The cause vanished in the owning service: comment and tag the card (NG11).

And by people who administer a product's board:

``GET /integration/product`` / ``PUT /integration/product``
    The project and board that receive the product's providências, and the swap to
    a new flow (NG14).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, get_session, require_capability, require_roles
from ..errors import NotFound
from ..integration import (
    card_for_providencia,
    card_url,
    find_ref,
    resolve_at_origin,
    swap_product_project,
)
from ..models import (
    Board,
    BoardColumn,
    Card,
    ProductLink,
    ProductLinkHistory,
    Project,
    User,
    as_dict,
)

router = APIRouter(prefix="/integration", tags=["integration"])

__all__ = ["router"]

system_only = require_roles("system")


class Person(BaseModel):
    """Who answers for the providência (from the Pampufa's routing rule, NG9)."""

    sub: str | None = None
    email: str | None = None
    nome: str | None = None


class ProvidenciaIn(BaseModel):
    providencia_id: str = Field(min_length=1, max_length=80)
    servico: str = Field(min_length=1, max_length=40)
    tipo: str = Field(min_length=1, max_length=80)
    titulo: str | None = Field(default=None, max_length=500)
    motivo: str | None = None
    url: str | None = None
    produto_nome: str | None = Field(default=None, max_length=120)
    responsavel: Person | None = None
    prazo: str | None = None
    classe_servico: str | None = None


class ResolvedIn(BaseModel):
    em: str | None = None
    origem: str | None = Field(default=None, max_length=200)


class LinkIn(BaseModel):
    project_id: str
    board_id: str
    motivo: str = Field(min_length=3, max_length=500)


async def _summary(s: AsyncSession, card: dict[str, Any]) -> dict[str, Any]:
    column = as_dict(await s.get(BoardColumn, card["column_id"])) if card.get("column_id") else None
    assignee = as_dict(await s.get(User, card["assignee_id"])) if card.get("assignee_id") else None
    return {
        "id": card["id"],
        "key": card["key"],
        "title": card["title"],
        "board_id": card["board_id"],
        "column": column["name"] if column else None,
        "column_kind": column["kind"] if column else None,
        "delivered_at": card.get("delivered_at"),
        "archived": bool(card.get("archived")),
        "tags": list(card.get("tags") or []),
        "assignee": (
            {
                "id": assignee["id"],
                "name": assignee["name"],
                "email": assignee["email"],
                "sub": assignee.get("external_subject"),
            }
            if assignee
            else None
        ),
        "url": card_url(card),
    }


@router.post(
    "/providencias",
    status_code=status.HTTP_201_CREATED,
    summary="Create the card for a Pampufa providência (idempotent)",
)
async def create_from_providencia(
    payload: ProvidenciaIn,
    response: Response,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(system_only),
) -> dict[str, Any]:
    """The providência lands in the first column of its product's board (NG8).

    Repeating the call with the same ``providencia_id`` returns the same card with
    ``200``: the Pampufa can retry without ever creating a second card.
    """
    card, created = await card_for_providencia(
        s, payload.model_dump(), product_id=user["product_id"], actor_id=user["id"]
    )
    await s.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return {"created": created, "card": await _summary(s, card)}


@router.get("/providencias/{providencia_id}", summary="Where a providência's card stands")
async def get_providencia(
    providencia_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(system_only),
) -> dict[str, Any]:
    ref = await find_ref(s, providencia_id)
    if ref is None or ref["product_id"] != user["product_id"]:
        raise NotFound("Providência", providencia_id, code="providencia_not_found")
    card = as_dict(await s.get(Card, ref["card_id"])) or {}
    return {"state": ref["state"], "card": await _summary(s, card)}


@router.post(
    "/providencias/{providencia_id}/resolved-at-origin",
    summary="The cause vanished in the owning service (comment and tag, never move)",
)
async def resolved_at_origin(
    providencia_id: str,
    payload: ResolvedIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(system_only),
) -> dict[str, Any]:
    """NG11: the card stays where it is; whoever owns it confirms and delivers it."""
    ref = await find_ref(s, providencia_id)
    if ref is None or ref["product_id"] != user["product_id"]:
        raise NotFound("Providência", providencia_id, code="providencia_not_found")
    result = await resolve_at_origin(
        s, providencia_id, resolved_at=payload.em, origin=payload.origem, actor_id=user["id"]
    )
    await s.commit()
    summary = await _summary(s, result["card"])
    return {"changed": result["changed"], "state": result["ref"]["state"], "card": summary}


# ---------------------------------------------------------------------------
# the product's project and board
# ---------------------------------------------------------------------------


async def _link_out(s: AsyncSession, product_id: str) -> dict[str, Any]:
    link = as_dict(await s.get(ProductLink, product_id))
    history = [
        as_dict(row) or {}
        for row in await s.scalars(
            select(ProductLinkHistory)
            .where(ProductLinkHistory.product_id == product_id)
            .order_by(ProductLinkHistory.at, ProductLinkHistory.id)
        )
    ]
    if link is None:
        return {"product_id": product_id, "link": None, "history": history}
    project = as_dict(await s.get(Project, link["project_id"])) or {}
    board = as_dict(await s.get(Board, link["board_id"])) or {}
    return {
        "product_id": product_id,
        "link": {
            **link,
            "project": {
                "id": project.get("id"),
                "key": project.get("key"),
                "name": project.get("name"),
            },
            "board": {"id": board.get("id"), "name": board.get("name"), "slug": board.get("slug")},
        },
        "history": history,
    }


@router.get("/product", summary="The project and board that receive this product's providências")
async def get_product_link(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    return await _link_out(s, user["product_id"])


@router.put("/product", summary="Link a new project and board to the product (new flow)")
async def put_product_link(
    payload: LinkIn,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_roles("master", "administrator")),
) -> dict[str, Any]:
    """NG14: open cards are recreated on the new board, the old one becomes read-only."""
    result = await swap_product_project(
        s,
        user["product_id"],
        project_id=payload.project_id,
        board_id=payload.board_id,
        reason=payload.motivo,
        actor_id=user["id"],
    )
    await s.commit()
    return {**await _link_out(s, user["product_id"]), "moved_cards": result["moved_cards"]}
