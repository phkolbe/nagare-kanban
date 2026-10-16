"""Projects and sealed-envelope demand (SPEC §5 "Projects & demands", Req. 8).

A *demand* is a request that has been captured but deliberately **not** examined.
It lands in the board's ``envelope`` column with ``envelope_state='sealed'``, and
stays there — unassigned, unsized, unargued about — until somebody chooses to
open it. That deliberate act is the point: the moment of opening is recorded
(``opened_at``, an ``opened_envelope`` event and a real ``card_movements`` row),
so the time demand spent unexamined becomes measurable instead of invisible.

``board.count_envelope_in_metrics`` then decides whether that sealed interval is
part of customer lead time (SPEC §7) — which is only an answerable question
because the envelope stage is modelled as a column like any other.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    get_board_or_404,
    get_session,
    require_capability,
    require_flag,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..models import (
    PRODUCT_STANDALONE,
    Board,
    BoardColumn,
    Card,
    CardEvent,
    CardMovement,
    Project,
    User,
    as_dict,
)
from ..schemas import (
    CardOut,
    DemandCreate,
    ListResponse,
    MoveResult,
    OkResponse,
    ProjectCreate,
    ProjectDetailOut,
    ProjectOut,
    ProjectUpdate,
)
from .boards import (
    board_wip_status,
    canonical_columns,
    next_card_key,
    select_cards,
    wip_state,
)

router = APIRouter(tags=["projects"])

__all__ = ["router"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ZERO_COUNTS: dict[str, int] = {
    "card_count": 0,
    "sealed_count": 0,
    "opened_count": 0,
    "delivered_count": 0,
    "in_flight_count": 0,
}


def _count_when(condition: Any) -> Any:
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


async def _project_counts(s: AsyncSession) -> dict[str, dict[str, int]]:
    """One pass over ``cards`` gives every roll-up a project card needs."""
    rows = await s.execute(
        select(
            Card.project_id,
            func.count().label("card_count"),
            _count_when(Card.envelope_state == "sealed").label("sealed_count"),
            _count_when(Card.envelope_state == "opened").label("opened_count"),
            _count_when(Card.delivered_at.is_not(None)).label("delivered_count"),
            _count_when(Card.committed_at.is_not(None) & Card.delivered_at.is_(None)).label(
                "in_flight_count"
            ),
        )
        .where(Card.archived.is_(False), Card.project_id.is_not(None))
        .group_by(Card.project_id)
    )
    return {
        row.project_id: {key: int(row._mapping[key] or 0) for key in _ZERO_COUNTS} for row in rows
    }


async def _get_project_or_404(s: AsyncSession, project_id: str) -> dict[str, Any]:
    """Fetch a project by id or by its human key (``NGR``)."""
    row = as_dict(await s.get(Project, project_id))
    if row is None:
        row = as_dict(
            await s.scalar(select(Project).where(Project.key == str(project_id).upper()).limit(1))
        )
    if row is None:
        raise NotFound("Project", project_id, code="project_not_found")
    return row


def _require_project_write(user: CurrentUser) -> None:
    """Projects are workspace structure: master/administrator only (SPEC §4)."""
    if user.get("role") not in {"master", "administrator"}:
        raise Forbidden(
            "Your role cannot manage projects.",
            code="insufficient_role",
            details={"role": user.get("role"), "required": ["master", "administrator"]},
        )


async def _resolve_demand_board(s: AsyncSession, board_id: str | None) -> dict[str, Any]:
    """The board a new demand belongs to; the operational board when unambiguous."""
    if board_id:
        return await get_board_or_404(s, board_id)
    rows = (
        await s.scalars(
            select(Board)
            .where(Board.kind == "operational", Board.archived.is_(False))
            .order_by(Board.created_at)
            .limit(2)
        )
    ).all()
    if not rows:
        raise Conflict(
            "no_operational_board",
            "There is no operational board to receive this demand.",
            {},
        )
    if len(rows) > 1:
        raise BadRequest(
            "This workspace has more than one board — name the board for the demand.",
            code="board_id_required",
            details={"boards": [{"id": r.id, "name": r.name} for r in rows]},
        )
    return as_dict(rows[0]) or {}


def _first_open_column(columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Where an opened envelope goes: the first non-envelope upstream column."""
    target = next(
        (c for c in columns if c["segment"] == "upstream" and c["kind"] != "envelope"), None
    )
    if target is None:
        # A board with nothing but an envelope upstream still has to be able to
        # unseal work, so fall back to the first column of the delivery half.
        target = next((c for c in columns if c["kind"] != "envelope"), None)
    if target is None:
        raise Conflict(
            "no_open_column",
            "This board has no column to move an opened demand into.",
            {},
        )
    return target


async def _occupancy(s: AsyncSession, column_id: str) -> int:
    return int(
        await s.scalar(
            select(func.count()).where(Card.column_id == column_id, Card.archived.is_(False))
        )
        or 0
    )


async def _assert_column_capacity(
    s: AsyncSession, column: dict[str, Any], service_class: str
) -> bool:
    """SPEC §6.6: a column WIP max blocks the move; ``expedite`` overrides it."""
    if column["wip_max"] is None:
        return False
    count = await _occupancy(s, column["id"])
    if count < int(column["wip_max"]):
        return False
    if service_class == "expedite":
        return True
    raise Conflict(
        "wip_limit_exceeded",
        f"“{column['name']}” is at its WIP limit.",
        {
            "policy": "column",
            "column_id": column["id"],
            "column_name": column["name"],
            "current": count,
            "max": int(column["wip_max"]),
            "state": wip_state(count + 1, column["wip_min"], column["wip_max"]),
        },
    )


async def _next_position(s: AsyncSession, column_id: str) -> int:
    highest = await s.scalar(
        select(func.coalesce(func.max(Card.position), -1)).where(Card.column_id == column_id)
    )
    return int(highest if highest is not None else -1) + 1


async def _card_out(s: AsyncSession, card_id: str) -> dict[str, Any]:
    """Re-read a card through the shared projection so the client gets full shape."""
    rows = await select_cards(s, card_id=card_id, archived=False, order="created")
    if rows:
        return rows[0]
    raise NotFound("Card", card_id, code="card_not_found")


async def _user_exists(s: AsyncSession, user_id: str) -> bool:
    return (await s.scalar(select(User.id).where(User.id == user_id))) is not None


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=ListResponse[ProjectOut], summary="List projects")
async def list_projects(
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, description="Match the project name or key"),
    archived: bool = Query(False),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Projects with their demand roll-up, in two queries regardless of size."""
    conditions: list[Any] = [Project.archived.is_(bool(archived))]
    if status_filter:
        conditions.append(Project.status == status_filter)
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(or_(Project.name.ilike(needle), Project.key.ilike(needle)))

    rows = await s.execute(
        select(Project, User.name.label("owner_name"))
        .outerjoin(User, User.id == Project.owner_id)
        .where(*conditions)
        .order_by(Project.key)
    )
    counts = await _project_counts(s)
    items = []
    for row in rows:
        merged = as_dict(row[0]) or {}
        merged["owner_name"] = row._mapping["owner_name"]
        merged.update(counts.get(merged["id"], _ZERO_COUNTS))
        items.append(merged)
    return {"items": items, "total": len(items)}


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    dependencies=[Depends(require_flag("multi_project"))],
)
async def create_project(
    payload: ProjectCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Create a project. The key becomes the prefix of every card key it owns."""
    _require_project_write(user)
    product_id = PRODUCT_STANDALONE
    if await s.scalar(
        select(Project.id).where(Project.key == payload.key, Project.product_id == product_id)
    ):
        raise Conflict(
            "project_key_taken",
            f"The project key “{payload.key}” is already in use.",
            {"key": payload.key},
        )
    if payload.owner_id and not await _user_exists(s, payload.owner_id):
        raise BadRequest(
            "That owner does not exist.",
            code="owner_not_found",
            details={"owner_id": payload.owner_id},
        )

    project = Project(
        id=new_id(),
        product_id=product_id,
        name=payload.name,
        key=payload.key,
        color=payload.color,
        description=payload.description,
        status=payload.status,
        owner_id=payload.owner_id,
        started_at=payload.started_at,
        target_date=payload.target_date,
        created_at=utcnow(),
        archived=False,
    )
    s.add(project)
    await s.commit()
    return {**(as_dict(project) or {}), **_ZERO_COUNTS}


@router.get(
    "/projects/{id}",
    response_model=ProjectDetailOut,
    summary="A project with its demand breakdown",
)
async def get_project(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Project, demand counts by envelope state **and** by column segment, and its cards."""
    project = await _get_project_or_404(s, id)
    owner_name = (
        await s.scalar(select(User.name).where(User.id == project["owner_id"]))
        if project["owner_id"]
        else None
    )

    breakdown = (
        await s.execute(
            select(
                func.count().label("total"),
                _count_when(Card.envelope_state == "sealed").label("sealed"),
                _count_when(Card.envelope_state == "opened").label("opened"),
                _count_when(BoardColumn.segment == "upstream").label("upstream"),
                _count_when(BoardColumn.segment == "downstream").label("downstream"),
                _count_when(Card.delivered_at.is_not(None)).label("delivered"),
                _count_when(Card.committed_at.is_not(None) & Card.delivered_at.is_(None)).label(
                    "in_flight"
                ),
                _count_when(Card.is_blocked.is_(True)).label("blocked"),
            )
            .select_from(Card)
            .join(BoardColumn, BoardColumn.id == Card.column_id)
            .where(Card.project_id == project["id"], Card.archived.is_(False))
        )
    ).first()
    demands = {
        key: int(value or 0) for key, value in (breakdown._mapping.items() if breakdown else [])
    }

    cards = await select_cards(s, project_id=project["id"], archived=False, order="project")
    return {
        **project,
        "owner_name": owner_name,
        "card_count": demands.get("total", 0),
        "sealed_count": demands.get("sealed", 0),
        "opened_count": demands.get("opened", 0),
        "delivered_count": demands.get("delivered", 0),
        "in_flight_count": demands.get("in_flight", 0),
        "demands": demands,
        "cards": cards,
    }


@router.patch("/projects/{id}", response_model=ProjectOut, summary="Update a project")
async def update_project(
    id: str,
    payload: ProjectUpdate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("multi_project")),
) -> dict[str, Any]:
    """Change a project's name, colour, status, owner or dates."""
    project = await _get_project_or_404(s, id)
    _require_project_write(user)
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        if fields.get("owner_id") and not await _user_exists(s, fields["owner_id"]):
            raise BadRequest(
                "That owner does not exist.",
                code="owner_not_found",
                details={"owner_id": fields["owner_id"]},
            )
        await s.execute(update(Project).where(Project.id == project["id"]).values(**fields))
        await s.commit()
    return {**await _get_project_or_404(s, project["id"]), **_ZERO_COUNTS}


@router.delete("/projects/{id}", response_model=OkResponse, summary="Archive a project")
async def delete_project(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("multi_project")),
) -> dict[str, Any]:
    """Archive a project. Its cards keep their history and their project key."""
    project = await _get_project_or_404(s, id)
    _require_project_write(user)
    await s.execute(update(Project).where(Project.id == project["id"]).values(archived=True))
    await s.commit()
    return {"ok": True, "id": project["id"], "message": "Project archived."}


# ---------------------------------------------------------------------------
# demands (Req. 8)
# ---------------------------------------------------------------------------


@router.post(
    "/projects/{id}/demands",
    response_model=CardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Capture a demand as a sealed envelope",
    dependencies=[Depends(require_flag("envelope"))],
)
async def create_demand(
    id: str,
    payload: DemandCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    """Create a sealed card in the board's envelope column.

    The card is intentionally bare: no assignee, no estimate, no discussion. Its
    creation is still recorded as a movement (``from_column_id = NULL``, SPEC
    §2.7) so the envelope interval is measurable from the first second.
    """
    project = await _get_project_or_404(s, id)
    board = await _resolve_demand_board(s, payload.board_id)
    columns = await canonical_columns(s, board["id"])
    if not columns:
        raise Conflict(
            "board_has_no_columns",
            "This board has no columns to receive a demand.",
            {"board_id": board["id"]},
        )

    sealed = bool(payload.sealed)
    if sealed:
        column = next((c for c in columns if c["kind"] == "envelope"), None)
        if column is None:
            raise Conflict(
                "envelope_column_missing",
                "This board has no envelope column, so it cannot hold sealed demand.",
                {"board_id": board["id"]},
            )
    else:
        column = _first_open_column(columns)

    now = utcnow()
    card_id = new_id()
    s.add(
        Card(
            id=card_id,
            product_id=board["product_id"],
            board_id=board["id"],
            column_id=column["id"],
            project_id=project["id"],
            key=await next_card_key(s, project["key"], board["product_id"]),
            title=payload.title,
            description=payload.description,
            service_class=payload.service_class,
            assignee_id=None,
            requester=payload.requester,
            size=payload.size,
            value_area=payload.value_area,
            demand_type=payload.demand_type,
            due_date=payload.due_date,
            position=await _next_position(s, column["id"]),
            envelope_state="sealed" if sealed else "opened",
            is_blocked=False,
            tags=list(payload.tags or []),
            custom_fields=dict(payload.custom_fields or {}),
            created_at=now,
            opened_at=None if sealed else now,
            committed_at=None,
            delivered_at=None,
            archived=False,
            created_by=user.get("id"),
        )
    )
    await s.flush()
    s.add(
        CardMovement(
            id=new_id(),
            card_id=card_id,
            board_id=board["id"],
            from_column_id=None,
            to_column_id=column["id"],
            from_segment=None,
            to_segment=column["segment"],
            moved_at=now,
            moved_by=user.get("id"),
            seconds_in_from_column=None,
            note="Demand captured" if sealed else "Demand captured and opened immediately",
            is_backward=False,
            source="api",
        )
    )
    s.add(
        CardEvent(
            id=new_id(),
            card_id=card_id,
            board_id=board["id"],
            actor_id=user.get("id"),
            event_type="created",
            field=None,
            old_value=None,
            new_value=column["name"],
            created_at=now,
        )
    )
    if not sealed:
        s.add(
            CardEvent(
                id=new_id(),
                card_id=card_id,
                board_id=board["id"],
                actor_id=user.get("id"),
                event_type="opened_envelope",
                field="envelope_state",
                old_value="sealed",
                new_value="opened",
                created_at=now,
            )
        )
    await s.commit()
    return await _card_out(s, card_id)


@router.post(
    "/cards/{id}/open-envelope",
    response_model=MoveResult,
    summary="Open a sealed demand",
    dependencies=[Depends(require_flag("envelope"))],
)
async def open_envelope(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    """Unseal a demand and pull it into the first real upstream column.

    Opening is a *move*, not a flag flip: it writes a ``card_movements`` row with
    the seconds the demand spent sealed, which is exactly the interval the
    envelope toggle in SPEC §7 includes or excludes from customer lead time.
    """
    card = as_dict(await s.get(Card, id))
    if card is None:
        card = as_dict(await s.scalar(select(Card).where(Card.key == str(id).upper()).limit(1)))
    if card is None:
        raise NotFound("Card", id, code="card_not_found")
    if card["envelope_state"] != "sealed":
        raise Conflict(
            "envelope_already_opened",
            "This demand has already been opened.",
            {"card_id": card["id"], "opened_at": card["opened_at"]},
        )

    board = await get_board_or_404(s, card["board_id"])
    columns = await canonical_columns(s, board["id"])
    source = next((c for c in columns if c["id"] == card["column_id"]), None)
    target = _first_open_column(columns)
    if source is not None and int(target["position"]) <= int(source["position"]):
        raise Conflict(
            "no_open_column",
            "There is no column after the envelope to open this demand into.",
            {"card_id": card["id"], "column_id": card["column_id"]},
        )

    wip_override = await _assert_column_capacity(s, target, card["service_class"])
    now = utcnow()
    previous = await s.scalar(
        select(CardMovement.moved_at)
        .where(CardMovement.card_id == card["id"])
        .order_by(CardMovement.moved_at.desc(), CardMovement.id.desc())
        .limit(1)
    )
    dwell = seconds_between(previous, now) if previous else None
    committed = bool(target["is_commitment_point"])
    movement_id = new_id()

    values: dict[str, Any] = {
        "envelope_state": "opened",
        "opened_at": now,
        "column_id": target["id"],
        "position": await _next_position(s, target["id"]),
    }
    if committed and not card["committed_at"]:
        values["committed_at"] = now
    await s.execute(update(Card).where(Card.id == card["id"]).values(**values))
    movement = CardMovement(
        id=movement_id,
        card_id=card["id"],
        board_id=board["id"],
        from_column_id=card["column_id"],
        to_column_id=target["id"],
        from_segment=source["segment"] if source else None,
        to_segment=target["segment"],
        moved_at=now,
        moved_by=user.get("id"),
        seconds_in_from_column=int(dwell) if dwell is not None else None,
        note="Envelope opened",
        is_backward=False,
        source="api",
    )
    s.add(movement)
    s.add(
        CardEvent(
            id=new_id(),
            card_id=card["id"],
            board_id=board["id"],
            actor_id=user.get("id"),
            event_type="opened_envelope",
            field="envelope_state",
            old_value="sealed",
            new_value="opened",
            created_at=now,
        )
    )
    await s.commit()

    fresh = await _card_out(s, card["id"])
    warnings: list[str] = []
    if wip_override:
        warnings.append(
            f"“{target['name']}” is at its WIP limit; the expedite class of service "
            "was allowed past it."
        )
    if target["wip_min"] is not None:
        occupancy = await _occupancy(s, target["id"])
        if occupancy < int(target["wip_min"]):
            warnings.append(f"“{target['name']}” is starving: {occupancy} below its minimum.")

    return {
        "card": fresh,
        "movement": as_dict(movement),
        "wip_override": wip_override,
        "warnings": warnings,
        "wip": await board_wip_status(s, board, columns=columns),
        "committed": committed,
        "delivered": bool(target["is_delivery_point"]),
    }
