"""Providências become cards, and cards answer back (NG8-NG14).

The Pampufa calls the Nagare's integration API with its service token; this module
holds what those calls do, in the caller's transaction:

* :func:`ensure_product_link` — the product's project and board, created with the
  ``providencias`` flow the first time one of its providências arrives;
* :func:`card_for_providencia` — the card for one providência, created once
  (idempotent by ``providencia_id``) in the board's first column;
* :func:`resolve_at_origin` — the cause disappeared in the owning service: the card
  gets a comment and the ``resolvido-na-origem`` tag, and never moves by itself;
* :func:`close_card` — called when a card is delivered or archived: the reference
  closes and a webhook is queued for the Pampufa;
* :func:`swap_product_project` — a new flow is a new project: open cards are
  recreated on the new board and the old board becomes read-only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import SERVICE_CLASSES, settings
from .db import add_days, new_id, utcnow
from .errors import BadRequest, Conflict, NotFound
from .models import (
    PRODUCT_STANDALONE,
    Board,
    Card,
    CardEvent,
    CardMovement,
    Comment,
    ExternalRef,
    ProductLink,
    ProductLinkHistory,
    Project,
    User,
    as_dict,
)
from .pampufa import EVENT_ARCHIVED, EVENT_DELIVERED, EVENT_MOVED, enqueue_webhook
from .routers.boards import canonical_columns, create_board_from_template, next_card_key
from .tenancy import local_role_in, project_key_for, role_from_pampufa

__all__ = [
    "STAGES",
    "TAG_RESOLVED_AT_ORIGIN",
    "card_for_providencia",
    "card_url",
    "close_card",
    "ensure_product_link",
    "find_ref",
    "overview",
    "person_user",
    "product_people",
    "products_for",
    "resolve_at_origin",
    "stages_of",
    "swap_product_project",
]

SOURCE = "pampufa"
TEMPLATE = "providencias"
TAG_PROVIDENCIA = "providencia"
TAG_RESOLVED_AT_ORIGIN = "resolvido-na-origem"


def card_url(card: dict[str, Any]) -> str:
    """Where a person opens the card: the product's Tarefas module (N2), or the
    standalone Nagare's board (N3: the same React screens at its root)."""
    product = card.get("product_id")
    if settings.screens_url and product and product != PRODUCT_STANDALONE:
        return (
            f"{settings.screens_url}/{product}/tarefas/quadro/{card['board_id']}"
            f"?cartao={card['id']}"
        )
    return f"{settings.public_url}/quadro/{card['board_id']}?cartao={card['id']}"


# ---------------------------------------------------------------------------
# the product's project and board
# ---------------------------------------------------------------------------


async def ensure_product_link(
    s: AsyncSession, product_id: str, *, product_name: str | None, actor_id: str | None
) -> dict[str, Any]:
    """The product's link, provisioning project and board on first use."""
    link = as_dict(await s.get(ProductLink, product_id))
    if link is not None:
        return link

    now = utcnow()
    label = product_name or product_id
    key = project_key_for(product_id)
    project = Project(
        id=new_id(),
        product_id=product_id,
        name=f"Providências · {label}",
        key=key,
        color=None,
        description=f"Providências do produto {label} na plataforma Pampufa.",
        status="active",
        owner_id=None,
        started_at=now,
        target_date=None,
        created_at=now,
        archived=False,
    )
    s.add(project)
    await s.flush()
    board_id = await create_board_from_template(
        s, product_id=product_id, name=f"Providências · {label}", template=TEMPLATE
    )
    s.add(
        ProductLink(
            product_id=product_id,
            project_id=project.id,
            board_id=board_id,
            linked_at=now,
            linked_by=actor_id,
        )
    )
    s.add(
        ProductLinkHistory(
            id=new_id(),
            product_id=product_id,
            project_id=project.id,
            board_id=board_id,
            action="linked",
            reason="Primeira providência do produto",
            moved_cards=0,
            at=now,
            by=actor_id,
        )
    )
    await s.flush()
    return as_dict(await s.get(ProductLink, product_id)) or {}


# ---------------------------------------------------------------------------
# cards for providências
# ---------------------------------------------------------------------------


async def find_ref(s: AsyncSession, providencia_id: str) -> dict[str, Any] | None:
    return as_dict(
        await s.scalar(
            select(ExternalRef).where(
                ExternalRef.source == SOURCE, ExternalRef.external_id == providencia_id
            )
        )
    )


async def _assignee(s: AsyncSession, person: dict[str, Any] | None) -> str | None:
    user = await person_user(s, person)
    return user.id if user else None


async def person_user(s: AsyncSession, person: dict[str, Any] | None) -> User | None:
    """The Nagare user for a Pampufa person, created without a password if new.

    Same rule as the sign-in: the Pampufa ``sub`` first, then the e-mail (linked).
    """
    if not person or not (person.get("sub") or person.get("email")):
        return None
    sub = str(person.get("sub") or "") or None
    email = str(person.get("email") or "").strip().lower() or None
    user = None
    if sub:
        user = await s.scalar(select(User).where(User.external_subject == sub))
    if user is None and email:
        user = await s.scalar(select(User).where(User.email == email))
        if user is not None and sub and not user.external_subject:
            user.external_subject = sub
    if user is None:
        if not email:
            return None
        user = User(
            id=new_id(),
            email=email,
            name=str(person.get("nome") or email),
            password_hash=None,
            role="viewer",
            is_active=True,
            created_at=utcnow(),
            external_subject=sub,
        )
        s.add(user)
        await s.flush()
    return user


#: The product's people, mirrored from the Pampufa for a minute (the assignee picker
#: asks on every card drawer; the directory changes rarely).
_PEOPLE_TTL_S = 60
_people_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


async def product_people(s: AsyncSession, gateway: Any, product_id: str) -> list[dict[str, Any]]:
    """Who can take a card in the product: the Pampufa's ``nagare.*`` people (17/10/2026).

    Each person gets (or keeps) a Nagare user, so an assignee can be picked before
    the person ever opens the Nagare. Returns ``[{id, name, email, sub, roles}]``.
    """
    import time

    cached = _people_cache.get(product_id)
    if cached and time.monotonic() - cached[0] < _PEOPLE_TTL_S:
        return cached[1]
    out: list[dict[str, Any]] = []
    for person in await gateway.people(product_id):
        user = await person_user(s, person)
        if user is None or not user.is_active:
            continue
        out.append(
            {
                "id": user.id,
                "name": user.name,
                "email": user.email,
                "sub": user.external_subject,
                "roles": list(person.get("papeis") or []),
            }
        )
    await s.commit()
    out.sort(key=lambda p: (p["name"].lower(), p["email"]))
    _people_cache[product_id] = (time.monotonic(), out)
    return out


def forget_people(product_id: str | None = None) -> None:
    """Drop the mirror (tests; or after the Pampufa says the directory changed)."""
    if product_id is None:
        _people_cache.clear()
    else:
        _people_cache.pop(product_id, None)


# ---------------------------------------------------------------------------
# the PO's consolidated board (N2, decision of 17/10/2026)
# ---------------------------------------------------------------------------

#: The stages every product board is read in, whatever its own columns are called.
STAGES: tuple[str, ...] = ("entrada", "a_fazer", "fazendo", "aguardando", "entregue")
#: How far back the consolidated board shows delivered cards.
DELIVERED_WINDOW_DAYS = 14


def stages_of(columns: list[dict[str, Any]]) -> dict[str, str]:
    """``{column_id: stage}`` for a board's columns in flow order.

    Upstream is *entrada*; downstream, the queues before the first activity are
    *a_fazer* (the commitment side), activities are *fazendo*, queues after an
    activity are *aguardando* (waiting on someone), and the done column *entregue*.
    """
    out: dict[str, str] = {}
    seen_activity = False
    for column in columns:
        if column["kind"] == "done":
            out[column["id"]] = "entregue"
        elif column["segment"] == "upstream":
            out[column["id"]] = "entrada"
        elif column["kind"] == "activity":
            seen_activity = True
            out[column["id"]] = "fazendo"
        else:
            out[column["id"]] = "aguardando" if seen_activity else "a_fazer"
    return out


def _visible_products(user: dict[str, Any], linked: list[str]) -> list[str]:
    """Every linked product for the gestor and for the local master, administrator and
    viewer (18/10/2026); else the caller's own."""
    if user.get("via") != "pampufa":
        home = str(user.get("home_role") or user.get("role"))
        return [p for p in linked if local_role_in(home, p) is not None]
    roles: dict[str, list[str]] = user.get("pampufa_roles") or {}
    if "pampufa.gestor" in roles.get("pampufa", []):
        return linked
    return [p for p in linked if any(r.startswith("nagare.") for r in roles.get(p, []))]


async def products_for(s: AsyncSession, user: dict[str, Any]) -> list[dict[str, Any]]:
    """The products the caller can open, with the role held in each (the product picker
    of the standalone Nagare, 18/10/2026): the standalone one first, then every product
    with a linked project and board."""
    links = (await s.scalars(select(ProductLink).order_by(ProductLink.product_id))).all()
    out: list[dict[str, Any]] = []
    if user.get("via") == "pampufa":
        roles = {scope: tuple(r) for scope, r in (user.get("pampufa_roles") or {}).items()}
        held_in = lambda product: role_from_pampufa(roles, product)  # noqa: E731
    else:
        home = str(user.get("home_role") or user.get("role"))
        held_in = lambda product: local_role_in(home, product)  # noqa: E731
        out.append({"id": PRODUCT_STANDALONE, "role": home, "project": None, "board": None})
    for link in links:
        role = held_in(link.product_id)
        if role is None:
            continue
        project = await s.get(Project, link.project_id)
        board = await s.get(Board, link.board_id)
        out.append(
            {
                "id": link.product_id,
                "role": role,
                "project": {"id": project.id, "key": project.key, "name": project.name}
                if project
                else None,
                "board": {"id": board.id, "name": board.name} if board else None,
            }
        )
    return out


async def overview(s: AsyncSession, user: dict[str, Any]) -> dict[str, Any]:
    """The four product boards read by stage, one lane per product, no card twice.

    Open cards of each product's linked board, plus the ones delivered in the last
    ``DELIVERED_WINDOW_DAYS``. Read only: a card opens on its own product's board.
    """
    links = (await s.scalars(select(ProductLink).order_by(ProductLink.product_id))).all()
    allowed = set(_visible_products(user, [link.product_id for link in links]))
    since = add_days(utcnow(), -DELIVERED_WINDOW_DAYS)
    lanes: list[dict[str, Any]] = []
    for link in links:
        if link.product_id not in allowed:
            continue
        board = as_dict(await s.get(Board, link.board_id)) or {}
        project = as_dict(await s.get(Project, link.project_id)) or {}
        columns = await canonical_columns(s, link.board_id)
        stage = stages_of(columns)
        by_id = {c["id"]: c for c in columns}
        rows = (
            await s.execute(
                select(Card, User.name)
                .outerjoin(User, User.id == Card.assignee_id)
                .where(
                    Card.board_id == link.board_id,
                    Card.archived.is_(False),
                    (Card.delivered_at.is_(None)) | (Card.delivered_at >= since),
                )
                .order_by(Card.created_at, Card.id)
            )
        ).all()
        cards = []
        for card, assignee_name in rows:
            data = as_dict(card) or {}
            column = by_id.get(data.get("column_id"))
            if column is None:
                continue
            # A sealed envelope keeps its content on the board too (the board hides it).
            sealed = data.get("envelope_state") == "sealed"
            cards.append(
                {
                    "id": data["id"],
                    "key": data["key"],
                    "title": None if sealed else data["title"],
                    "sealed": sealed,
                    "stage": stage[column["id"]],
                    "column": column["name"],
                    "service_class": data.get("service_class"),
                    "assignee_name": None if sealed else assignee_name,
                    "due_date": None if sealed else data.get("due_date"),
                    "is_blocked": bool(data.get("is_blocked")),
                    "created_at": data.get("created_at"),
                    "delivered_at": data.get("delivered_at"),
                    "tags": list(data.get("tags") or []),
                }
            )
        wip: dict[str, int | None] = {}
        for name in STAGES:
            limits = [c.get("wip_max") for c in columns if stage[c["id"]] == name]
            wip[name] = sum(limits) if limits and all(limits) else None
        lanes.append(
            {
                "product_id": link.product_id,
                "board": {"id": board.get("id"), "name": board.get("name")},
                "project": {"key": project.get("key"), "name": project.get("name")},
                "counts": {name: sum(1 for c in cards if c["stage"] == name) for name in STAGES},
                "wip": wip,
                "cards": cards,
            }
        )
    return {
        "stages": list(STAGES),
        "delivered_window_days": DELIVERED_WINDOW_DAYS,
        "products": lanes,
    }


def _event(
    s: AsyncSession,
    card_id: str,
    board_id: str,
    actor_id: str | None,
    event_type: str,
    field: str | None,
    old: Any,
    new: Any,
    at: str,
) -> None:
    s.add(
        CardEvent(
            id=new_id(),
            card_id=card_id,
            board_id=board_id,
            actor_id=actor_id,
            event_type=event_type,
            field=field,
            old_value=None if old is None else str(old),
            new_value=None if new is None else str(new),
            created_at=at,
        )
    )


async def _insert_card(
    s: AsyncSession,
    *,
    board: dict[str, Any],
    project: dict[str, Any],
    column: dict[str, Any],
    fields: dict[str, Any],
    actor_id: str | None,
    note: str,
    at: str,
) -> dict[str, Any]:
    """A card in ``column`` with its creation movement and event (no commit)."""
    position = await s.scalar(
        select(func.coalesce(func.max(Card.position), -1)).where(Card.column_id == column["id"])
    )
    card = Card(
        id=new_id(),
        product_id=board["product_id"],
        board_id=board["id"],
        column_id=column["id"],
        project_id=project["id"],
        key=await next_card_key(s, project["key"], board["product_id"]),
        position=int(position if position is not None else -1) + 1,
        envelope_state="opened",
        is_blocked=False,
        created_at=at,
        opened_at=at,
        committed_at=None,
        delivered_at=None,
        archived=False,
        created_by=actor_id,
        **fields,
    )
    s.add(card)
    await s.flush()
    s.add(
        CardMovement(
            id=new_id(),
            card_id=card.id,
            board_id=board["id"],
            from_column_id=None,
            to_column_id=column["id"],
            from_segment=None,
            to_segment=column["segment"],
            moved_at=at,
            moved_by=actor_id,
            seconds_in_from_column=None,
            note=note,
            is_backward=False,
            source="api",
        )
    )
    _event(s, card.id, board["id"], actor_id, "created", "column", None, column["name"], at)
    return as_dict(card) or {}


async def card_for_providencia(
    s: AsyncSession, payload: dict[str, Any], *, product_id: str, actor_id: str | None
) -> tuple[dict[str, Any], bool]:
    """The card for one providência: ``(card, created)``. Repeating returns the same card."""
    providencia_id = str(payload["providencia_id"])
    existing = await find_ref(s, providencia_id)
    if existing is not None:
        card = as_dict(await s.get(Card, existing["card_id"])) or {}
        if card.get("product_id") != product_id:
            # Providência ids are the Pampufa's UUIDs, so this should never happen; if it
            # does, nothing of the other product leaks out.
            raise Conflict(
                "providencia_in_other_product",
                "This providência already has a card in another product.",
                details={"providencia_id": providencia_id},
            )
        return card, False

    service_class = payload.get("classe_servico") or "standard"
    if service_class not in SERVICE_CLASSES:
        raise BadRequest(
            f"Unknown class of service “{service_class}”.",
            code="invalid_service_class",
            details={"allowed": list(SERVICE_CLASSES)},
        )

    link = await ensure_product_link(
        s, product_id, product_name=payload.get("produto_nome"), actor_id=actor_id
    )
    board = as_dict(await s.get(Board, link["board_id"])) or {}
    project = as_dict(await s.get(Project, link["project_id"])) or {}
    columns = await canonical_columns(s, board["id"])
    if not columns:
        raise Conflict(
            "board_has_no_columns",
            "The product's board has no columns to receive the providência.",
            {"board_id": board["id"]},
        )

    now = utcnow()
    servico = str(payload.get("servico") or "")
    tipo = str(payload.get("tipo") or "")
    url = payload.get("url")
    motivo = str(payload.get("motivo") or "")
    description = motivo
    if url:
        description = f"{motivo}\n\nResolver em: {url}" if motivo else f"Resolver em: {url}"
    tags = [tag for tag in (TAG_PROVIDENCIA, servico, tipo) if tag]
    card = await _insert_card(
        s,
        board=board,
        project=project,
        column=columns[0],
        fields={
            "title": str(payload.get("titulo") or motivo or tipo or "Providência")[:500],
            "description": description or None,
            "service_class": service_class,
            "assignee_id": await _assignee(s, payload.get("responsavel")),
            "requester": servico or None,
            "size": None,
            "value_area": "feature",
            "demand_type": "value",
            "due_date": payload.get("prazo"),
            "tags": tags,
            "custom_fields": {
                "pampufa": {
                    "providencia_id": providencia_id,
                    "servico": servico,
                    "tipo": tipo,
                    "url": url,
                }
            },
        },
        actor_id=actor_id,
        note="Providência da Pampufa",
        at=now,
    )
    s.add(
        ExternalRef(
            id=new_id(),
            card_id=card["id"],
            product_id=product_id,
            source=SOURCE,
            external_id=providencia_id,
            service=servico or None,
            kind=tipo or None,
            url=url,
            state="open",
            created_at=now,
        )
    )
    await s.flush()
    return card, True


async def resolve_at_origin(
    s: AsyncSession,
    providencia_id: str,
    *,
    resolved_at: str | None,
    origin: str | None,
    actor_id: str | None,
) -> dict[str, Any]:
    """The cause vanished in the owning service (NG11): comment and tag, never move."""
    ref = await find_ref(s, providencia_id)
    if ref is None:
        raise NotFound("Providência", providencia_id, code="providencia_not_found")
    card = as_dict(await s.get(Card, ref["card_id"])) or {}
    if ref["state"] != "open":
        return {"card": card, "ref": ref, "changed": False}

    now = utcnow()
    when = resolved_at or now
    text = f"Resolvido na origem em {when[:10]}"
    if origin:
        text += f" ({origin})"
    text += ". Confira e entregue o cartão para concluir a providência."
    s.add(Comment(id=new_id(), card_id=card["id"], author_id=actor_id, body=text, created_at=now))
    _event(s, card["id"], card["board_id"], actor_id, "commented", "comment", None, text[:200], now)
    tags = list(card.get("tags") or [])
    if TAG_RESOLVED_AT_ORIGIN not in tags:
        new_tags = [*tags, TAG_RESOLVED_AT_ORIGIN]
        await s.execute(update(Card).where(Card.id == card["id"]).values(tags=new_tags))
        _event(
            s,
            card["id"],
            card["board_id"],
            actor_id,
            "updated",
            "tags",
            _dump(tags),
            _dump(new_tags),
            now,
        )
    await s.execute(
        update(ExternalRef)
        .where(ExternalRef.id == ref["id"])
        .values(state="resolved_at_origin", resolved_at_origin=when)
    )
    await s.flush()
    return {
        "card": as_dict(await s.get(Card, card["id"])) or card,
        "ref": as_dict(await s.get(ExternalRef, ref["id"])) or ref,
        "changed": True,
    }


def _dump(value: Any) -> str:
    from .models import json_dumps

    return json_dumps(value)


async def close_card(s: AsyncSession, card: dict[str, Any], *, delivered: bool) -> None:
    """A card was delivered (or archived): close its reference and tell the Pampufa.

    Runs in the transaction of the move or the archive, so the webhook exists if and
    only if the change does. Cards without a Pampufa reference are untouched.
    """
    ref = as_dict(
        await s.scalar(
            select(ExternalRef).where(
                ExternalRef.card_id == card["id"],
                ExternalRef.state.in_(("open", "resolved_at_origin")),
            )
        )
    )
    if ref is None:
        return
    now = utcnow()
    state = "delivered" if delivered else "discarded"
    await s.execute(
        update(ExternalRef).where(ExternalRef.id == ref["id"]).values(state=state, closed_at=now)
    )
    enqueue_webhook(
        s,
        EVENT_DELIVERED if delivered else EVENT_ARCHIVED,
        ref["product_id"],
        {
            "providencia_id": ref["external_id"],
            "cartao_id": card["id"],
            "cartao_chave": card.get("key"),
            "resolvido_na_origem": ref["state"] == "resolved_at_origin",
            "em": now,
        },
    )


# ---------------------------------------------------------------------------
# a new flow is a new project (NG14)
# ---------------------------------------------------------------------------


async def swap_product_project(
    s: AsyncSession,
    product_id: str,
    *,
    project_id: str,
    board_id: str,
    reason: str,
    actor_id: str | None,
) -> dict[str, Any]:
    """Link ``project``/``board`` to the product in place of the current ones.

    Every open card (not delivered, not archived) of the old board is recreated in
    the new board's first column, keeping its providência; the old card is archived
    with the reason, and the old board becomes read-only — history and metrics stay.
    """
    current = as_dict(await s.get(ProductLink, product_id))
    project = as_dict(await s.get(Project, project_id))
    board = as_dict(await s.get(Board, board_id))
    if project is None or project["product_id"] != product_id:
        raise NotFound("Project", project_id, code="project_not_found")
    if board is None or board["product_id"] != product_id:
        raise NotFound("Board", board_id, code="board_not_found")
    if board["kind"] != "operational" or board["read_only"] or board["archived"]:
        raise Conflict(
            "board_not_linkable",
            "Only an active operational board can receive a product's providências.",
            {"board_id": board_id},
        )
    if current is not None and current["board_id"] == board_id:
        raise Conflict(
            "already_linked",
            "That board already receives this product's providências.",
            {"board_id": board_id},
        )
    columns = await canonical_columns(s, board_id)
    if not columns:
        raise Conflict(
            "board_has_no_columns",
            "The new board has no columns to receive the open cards.",
            {"board_id": board_id},
        )

    now = utcnow()
    moved = 0
    if current is not None:
        open_cards = (
            await s.scalars(
                select(Card)
                .where(
                    Card.board_id == current["board_id"],
                    Card.archived.is_(False),
                    Card.delivered_at.is_(None),
                )
                .order_by(Card.created_at, Card.id)
            )
        ).all()
        for old in open_cards:
            data = as_dict(old) or {}
            new_card = await _insert_card(
                s,
                board=board,
                project=project,
                column=columns[0],
                fields={
                    field: data[field]
                    for field in (
                        "title",
                        "description",
                        "service_class",
                        "assignee_id",
                        "requester",
                        "size",
                        "value_area",
                        "demand_type",
                        "due_date",
                        "tags",
                        "custom_fields",
                    )
                },
                actor_id=actor_id,
                note=f"Recriado de {data['key']} na troca de fluxo",
                at=now,
            )
            _event(
                s,
                new_card["id"],
                board_id,
                actor_id,
                "updated",
                "migrated_from",
                data["key"],
                new_card["key"],
                now,
            )
            ref = await s.scalar(select(ExternalRef).where(ExternalRef.card_id == data["id"]))
            if ref is not None:
                await s.execute(
                    update(ExternalRef)
                    .where(ExternalRef.id == ref.id)
                    .values(card_id=new_card["id"])
                )
                # The Pampufa keeps the card's key and link: tell it where the card went.
                enqueue_webhook(
                    s,
                    EVENT_MOVED,
                    product_id,
                    {
                        "providencia_id": ref.external_id,
                        "cartao_id": new_card["id"],
                        "cartao_chave": new_card["key"],
                        "cartao_url": card_url(new_card),
                        "cartao_anterior_chave": data["key"],
                        "em": now,
                    },
                )
            body = f"Movido para {new_card['key']} (novo fluxo do produto): {reason}"
            s.add(
                Comment(
                    id=new_id(), card_id=data["id"], author_id=actor_id, body=body, created_at=now
                )
            )
            await s.execute(update(Card).where(Card.id == data["id"]).values(archived=True))
            _event(
                s,
                data["id"],
                data["board_id"],
                actor_id,
                "archived",
                "migrated_to",
                data["key"],
                new_card["key"],
                now,
            )
            moved += 1
        await s.execute(update(Board).where(Board.id == current["board_id"]).values(read_only=True))
        s.add(
            ProductLinkHistory(
                id=new_id(),
                product_id=product_id,
                project_id=current["project_id"],
                board_id=current["board_id"],
                action="unlinked",
                reason=reason,
                moved_cards=moved,
                at=now,
                by=actor_id,
            )
        )
        await s.execute(
            update(ProductLink)
            .where(ProductLink.product_id == product_id)
            .values(project_id=project_id, board_id=board_id, linked_at=now, linked_by=actor_id)
        )
    else:
        s.add(
            ProductLink(
                product_id=product_id,
                project_id=project_id,
                board_id=board_id,
                linked_at=now,
                linked_by=actor_id,
            )
        )
    s.add(
        ProductLinkHistory(
            id=new_id(),
            product_id=product_id,
            project_id=project_id,
            board_id=board_id,
            action="linked",
            reason=reason,
            moved_cards=moved,
            at=now,
            by=actor_id,
        )
    )
    await s.flush()
    return {"link": as_dict(await s.get(ProductLink, product_id)), "moved_cards": moved}
