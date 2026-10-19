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
``GET /integration/overview``
    The PO's consolidated board: the product boards by stage, one lane per product.
``GET /integration/people``
    Who can take a card in the product: the Pampufa's ``nagare.*`` people; on the
    standalone Nagare, its own active users.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import CurrentUser, get_current_user, get_session, require_capability, require_roles
from ..errors import NotFound
from ..integration import (
    card_for_providencia,
    card_url,
    find_ref,
    overview,
    product_people,
    products_for,
    resolve_at_origin,
    swap_product_project,
)
from ..models import (
    PRODUCT_STANDALONE,
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


class PersonOut(BaseModel):
    id: str
    name: str
    email: str
    sub: str | None = None
    roles: list[str] = []


class PeopleOut(BaseModel):
    #: ``pampufa`` (the platform's directory) or ``local`` (the standalone Nagare)
    source: str
    people: list[PersonOut]


@router.get("/overview", summary="The product boards by stage (the PO's consolidated view)")
async def consolidated(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Read only. The gestor (and the master) see every linked product; anyone else,
    the products where they hold a ``nagare.*`` role (17/10/2026)."""
    return await overview(s, dict(user))


@router.get("/products", summary="The products the caller can open, and the role held in each")
async def products(
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The product picker of the standalone Nagare (18/10/2026): the local master works
    in every product; the administrator and the viewer only read the Pampufa ones."""
    return {"items": await products_for(s, dict(user))}


@router.get("/people", response_model=PeopleOut, summary="Who can take a card in this product")
async def people(
    request: Request,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """The assignee picker (decision of 17/10/2026): in a platform product, the people
    with a ``nagare.*`` role there, straight from the Pampufa — even those who never
    opened the Nagare; on the standalone Nagare, its own active users."""
    gateway = getattr(request.app.state, "pampufa", None)
    product_id = str(user["product_id"])
    if product_id != PRODUCT_STANDALONE and gateway is not None:
        return {"source": "pampufa", "people": await product_people(s, gateway, product_id)}
    rows = await s.scalars(
        select(User)
        .where(User.is_active.is_(True), User.role != "system")
        .order_by(User.name, User.email)
    )
    return {
        "source": "local",
        "people": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "sub": u.external_subject,
                "roles": [u.role],
            }
            for u in rows
        ],
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
