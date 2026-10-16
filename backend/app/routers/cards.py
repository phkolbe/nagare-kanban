"""Cards, tasks, blockers and comments — SPEC §5 "Cards".

This module owns the *write model* of a card. Everything downstream (metrics,
CFD, flow efficiency, the simulator) is derived from two tables this router is
responsible for keeping honest:

``card_movements``
    one row per column transition, including the creation row
    (``from_column_id IS NULL``) and ``seconds_in_from_column``.
``card_events``
    one row per audited change: a field edit writes one row *per field*, so the
    drawer can render "Ana changed size from M to L" without diffing snapshots.

The movement endpoint itself lives in :mod:`backend.app.routers.movements`; it
imports the helpers defined here so that the two files share exactly one
definition of column ordering, WIP evaluation and event writing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict
from sqlalchemy import Select, String, case, cast, delete, func, or_, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..config import (
    CARD_SIZES,
    DEMAND_TYPES,
    SERVICE_CLASSES,
    VALUE_AREAS,
)
from ..db import new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    assert_board_writable,
    can_write_board,
    get_current_user,
    get_session,
    require_board_write,
    require_board_write_target,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..integration import close_card
from ..models import (
    Blocker,
    Board,
    BoardColumn,
    Card,
    CardEvent,
    CardMovement,
    CardTask,
    ColumnGroup,
    Comment,
    Project,
    User,
    as_dict,
    json_dumps,
)
from ..schemas import (
    BlockerOut,
    BlockRequest,
    CardCreate,
    CardDetailOut,
    CardOut,
    CardUpdate,
    CommentCreate,
    CommentOut,
    EnvelopeState,
    ListResponse,
    OkResponse,
    SegmentQuery,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from ..sqltypes import from_iso, to_iso
from ..tenancy import in_product, same_product

router = APIRouter(tags=["cards"])

__all__ = [
    "board_columns",
    "card_payload",
    "card_select",
    "column_order",
    "commitment_column",
    "delivery_column",
    "dwell_intervals",
    "fetch_card",
    "flow_totals",
    "load_board",
    "movement_select",
    "next_card_key",
    "record_event",
    "require_card_write",
    "router",
    "sle_risk",
    "wip_status",
]


# ---------------------------------------------------------------------------
# local response models
#
# The shared schemas drop unknown keys (``extra="ignore"``). Cards carry a few
# computed extras — aging, touch/wait split, SLE risk — that the drawer and the
# board renderer read, so the card payloads are declared with ``extra="allow"``
# here rather than widening the shared contract for every consumer.
# ---------------------------------------------------------------------------


class CardItem(CardOut):
    """A card as returned by ``GET /cards`` — the base fields plus computed ones."""

    model_config = ConfigDict(extra="allow")


class CardDetail(CardDetailOut):
    """``GET /cards/{id}`` — card, children, movements and computed flow figures."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# projections
# ---------------------------------------------------------------------------

#: The card columns, in declaration order — the base of every card payload.
_CARD_FIELDS: tuple[str, ...] = tuple(attr.key for attr in Card.__mapper__.column_attrs)

#: Labels joined onto every card read, so a card looks the same in the list, in
#: the drawer and in a move response.
_EXTRA_LABELS: tuple[str, ...] = (
    "project_key",
    "project_name",
    "project_color",
    "assignee_name",
    "column_name",
    "column_kind",
    "column_segment",
    "column_position",
    "column_is_commitment_point",
    "column_is_delivery_point",
    "task_total",
    "task_done",
    "blocked_since",
)


def card_select() -> Select[Any]:
    """``SELECT`` of a card plus project, assignee, column and task/blocker counts."""
    task_total = (
        select(func.count()).where(CardTask.card_id == Card.id).correlate(Card).scalar_subquery()
    )
    task_done = (
        select(func.count())
        .where(CardTask.card_id == Card.id, CardTask.is_done.is_(True))
        .correlate(Card)
        .scalar_subquery()
    )
    blocked_since = (
        select(func.min(Blocker.started_at))
        .where(Blocker.card_id == Card.id, Blocker.ended_at.is_(None))
        .correlate(Card)
        .scalar_subquery()
    )
    return (
        select(
            Card,
            Project.key.label("project_key"),
            Project.name.label("project_name"),
            Project.color.label("project_color"),
            User.name.label("assignee_name"),
            BoardColumn.name.label("column_name"),
            BoardColumn.kind.label("column_kind"),
            BoardColumn.segment.label("column_segment"),
            BoardColumn.position.label("column_position"),
            BoardColumn.is_commitment_point.label("column_is_commitment_point"),
            BoardColumn.is_delivery_point.label("column_is_delivery_point"),
            task_total.label("task_total"),
            task_done.label("task_done"),
            blocked_since.label("blocked_since"),
        )
        .outerjoin(Project, Project.id == Card.project_id)
        .outerjoin(User, User.id == Card.assignee_id)
        .join(BoardColumn, BoardColumn.id == Card.column_id)
    )


def _card_row(result_row: Any) -> dict[str, Any]:
    """One row of :func:`card_select` as the flat dict the payload builders read."""
    data = as_dict(result_row[0]) or {}
    mapping = result_row._mapping
    for label in _EXTRA_LABELS:
        data[label] = mapping[label]
    return data


def movement_select() -> Select[Any]:
    """``SELECT`` of movements with the column and mover names resolved."""
    from_column = aliased(BoardColumn)
    to_column = aliased(BoardColumn)
    return (
        select(
            CardMovement,
            from_column.name.label("from_column_name"),
            to_column.name.label("to_column_name"),
            User.name.label("moved_by_name"),
        )
        .outerjoin(from_column, from_column.id == CardMovement.from_column_id)
        .outerjoin(to_column, to_column.id == CardMovement.to_column_id)
        .outerjoin(User, User.id == CardMovement.moved_by)
    )


def movement_row(result_row: Any) -> dict[str, Any]:
    data = as_dict(result_row[0]) or {}
    mapping = result_row._mapping
    for label in ("from_column_name", "to_column_name", "moved_by_name"):
        data[label] = mapping[label]
    return data


def _comment_select() -> Select[Any]:
    return select(Comment, User.name.label("author_name")).outerjoin(
        User, User.id == Comment.author_id
    )


def _comment_row(result_row: Any) -> dict[str, Any]:
    data = as_dict(result_row[0]) or {}
    data["author_name"] = result_row._mapping["author_name"]
    return data


_SORTS: dict[str, Any] = {
    # sort key -> ORDER BY expression (direction is applied by the caller)
    "created_at": lambda: Card.created_at,
    "aging": lambda: func.coalesce(Card.committed_at, Card.opened_at, Card.created_at),
    "due_date": lambda: Card.due_date,
    "title": lambda: func.lower(Card.title),
    "key": lambda: Card.key,
}

#: Sorts whose natural reading is "biggest first".
_DESC_BY_DEFAULT = {"created_at", "aging"}

SortKey = Literal["created_at", "aging", "due_date", "title", "key", "position"]
SortOrder = Literal["asc", "desc"]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _split_csv(value: str | None) -> list[str]:
    """Parse ``a,b , c`` into ``["a", "b", "c"]`` (empty when nothing is given)."""
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _validate_enum(name: str, values: Iterable[str], allowed: Iterable[str]) -> list[str]:
    """Reject unknown filter values loudly instead of returning silently empty lists."""
    allowed_set = set(allowed)
    out = list(values)
    bad = [value for value in out if value not in allowed_set]
    if bad:
        raise BadRequest(
            f"Unsupported value for “{name}”: {', '.join(bad)}.",
            code="invalid_filter",
            details={"filter": name, "invalid": bad, "allowed": sorted(allowed_set)},
        )
    return out


def _days(seconds: float | None) -> float | None:
    """Seconds to days with two decimals — the unit every aging figure uses."""
    if seconds is None:
        return None
    return round(seconds / 86400.0, 2)


def _bool(value: Any) -> bool:
    return bool(value) and value not in (0, "0", "false", "False")


async def load_board(s: AsyncSession, board_id: str) -> dict[str, Any]:
    """Board row or 404 — cards are always evaluated in the context of a board."""
    row = same_product(as_dict(await s.get(Board, board_id)))
    if row is None:
        raise NotFound("Board", board_id, code="board_not_found")
    return row


async def board_columns(s: AsyncSession, board_id: str) -> list[dict[str, Any]]:
    """Every column of a board in **unified flow order**.

    SPEC §6.1 defines "forward" against the unified ordering: all upstream
    columns come before every downstream column, and within a segment the
    stored ``position`` decides. Sorting on the segment first keeps boards whose
    positions restart per segment working exactly like the seeded board, whose
    positions are already globally ordered.
    """
    rows = await s.scalars(
        select(BoardColumn)
        .where(BoardColumn.board_id == board_id)
        .order_by(
            case((BoardColumn.segment == "upstream", 0), else_=1),
            BoardColumn.position,
            BoardColumn.name,
        )
    )
    return [as_dict(row) or {} for row in rows]


def column_order(columns: list[dict[str, Any]]) -> dict[str, int]:
    """``{column_id: flow index}`` for the unified ordering."""
    return {column["id"]: index for index, column in enumerate(columns)}


def commitment_column(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The column flagged as the commitment point, else the first downstream one."""
    for column in columns:
        if _bool(column.get("is_commitment_point")):
            return column
    for column in columns:
        if column.get("segment") == "downstream":
            return column
    return None


def delivery_column(columns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The column flagged as the delivery point, else the last ``done`` column."""
    for column in columns:
        if _bool(column.get("is_delivery_point")):
            return column
    done = [column for column in columns if column.get("kind") == "done"]
    return done[-1] if done else None


async def fetch_card(s: AsyncSession, card_id: str) -> dict[str, Any]:
    """Enriched card row or 404."""
    scope = in_product(Card)
    row = (await s.execute(card_select().where(Card.id == card_id, *scope))).first()
    if row is None:
        # Card keys (NGR-104) are the human handle; accepting one here makes the
        # API and the MCP server pleasant to drive without an id lookup first.
        row = (await s.execute(card_select().where(Card.key == card_id, *scope))).first()
    if row is None:
        raise NotFound("Card", card_id, code="card_not_found")
    return _card_row(row)


async def require_card_write(
    s: AsyncSession, user: CurrentUser, card: dict[str, Any]
) -> dict[str, Any]:
    """Authorise a write against the card's board; returns the board row.

    SPEC §4: editors, administrators, masters and the system identity work
    operational boards; the ``simulator`` role owns simulator boards; viewers
    never write.
    """
    board = await load_board(s, card["board_id"])
    require_board_write(user, board)
    assert_board_writable(board)
    return board


def record_event(
    s: AsyncSession,
    card: dict[str, Any],
    event_type: str,
    *,
    actor_id: str | None,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    at: str | None = None,
) -> str:
    """Append one row to ``card_events`` (in the caller's transaction); returns its id."""
    event_id = new_id()
    s.add(
        CardEvent(
            id=event_id,
            card_id=card["id"],
            board_id=card["board_id"],
            actor_id=actor_id,
            event_type=event_type,
            field=field,
            old_value=None if old_value is None else str(old_value),
            new_value=None if new_value is None else str(new_value),
            created_at=at or utcnow(),
        )
    )
    return event_id


async def next_card_key(s: AsyncSession, prefix: str, product_id: str | None = None) -> str:
    """Next ``PREFIX-nnn`` key, continuing the per-project counter.

    The seed starts each project at 101, so a fresh project's first card is
    ``KEY-101`` and an existing project continues from its highest number —
    including numbers that were skipped or deleted, which keeps keys stable and
    never reuses a retired reference. Keys are unique per product.
    """
    query = select(Card.key).where(Card.key.like(f"{prefix}-%"))
    if product_id is not None:
        query = query.where(Card.product_id == product_id)
    highest = 100
    for key in await s.scalars(query):
        _, _, number = str(key).partition("-")
        if number.isdigit():
            highest = max(highest, int(number))
    return f"{prefix}-{highest + 1}"


def _key_prefix(project: dict[str, Any] | None, board: dict[str, Any]) -> str:
    """Key prefix: the project key, or a board-derived one for loose cards."""
    if project and project.get("key"):
        return str(project["key"]).upper()
    letters = [ch for ch in str(board.get("slug") or board.get("name") or "card") if ch.isalnum()]
    return ("".join(letters[:3]) or "CARD").upper()


async def next_position(s: AsyncSession, column_id: str) -> int:
    """The slot at the bottom of a column."""
    highest = await s.scalar(
        select(func.coalesce(func.max(Card.position), -1)).where(Card.column_id == column_id)
    )
    return int(highest if highest is not None else -1) + 1


# ---------------------------------------------------------------------------
# WIP evaluation (SPEC §5 "state ∈ {under, ok, at_limit, over}")
# ---------------------------------------------------------------------------


def wip_state(count: int, minimum: int | None, maximum: int | None) -> str:
    """Classify a count against its policy."""
    if maximum is not None and count > maximum:
        return "over"
    if maximum is not None and count == maximum:
        return "at_limit"
    if minimum is not None and count < minimum:
        return "under"
    return "ok"


def _cell(count: int, minimum: int | None, maximum: int | None, label: str) -> dict[str, Any]:
    return {
        "count": count,
        "min": minimum,
        "max": maximum,
        "state": wip_state(count, minimum, maximum),
        "label": label,
    }


async def flow_count(s: AsyncSession, board_id: str) -> int:
    """WIP as SPEC §7.1 defines it: committed and not yet delivered."""
    return int(
        await s.scalar(
            select(func.count()).where(
                Card.board_id == board_id,
                Card.archived.is_(False),
                Card.committed_at.is_not(None),
                Card.delivered_at.is_(None),
            )
        )
        or 0
    )


async def wip_status(
    s: AsyncSession, board: dict[str, Any], columns: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Live WIP for every column, every group and the whole flow.

    Column and group counts are *occupancy* (non-archived cards sitting there);
    the flow count is the SPEC §7.1 definition of WIP — committed and not yet
    delivered — because a whole-flow limit is a commitment-to-delivery policy,
    not a "how many rows are on the screen" policy.
    """
    board_id = board["id"]
    columns = columns if columns is not None else await board_columns(s, board_id)
    counts = {
        column_id: int(n)
        for column_id, n in await s.execute(
            select(Card.column_id, func.count())
            .where(Card.board_id == board_id, Card.archived.is_(False))
            .group_by(Card.column_id)
        )
    }

    column_cells: dict[str, dict[str, Any]] = {}
    group_counts: dict[str, int] = {}
    for column in columns:
        count = counts.get(column["id"], 0)
        column_cells[column["id"]] = _cell(
            count, column.get("wip_min"), column.get("wip_max"), column["name"]
        )
        if column.get("group_id"):
            group_counts[column["group_id"]] = group_counts.get(column["group_id"], 0) + count

    group_cells: dict[str, dict[str, Any]] = {}
    for group in await s.scalars(
        select(ColumnGroup).where(ColumnGroup.board_id == board_id).order_by(ColumnGroup.position)
    ):
        group_cells[group.id] = _cell(
            group_counts.get(group.id, 0), group.wip_min, group.wip_max, group.name
        )

    return {
        "board_id": board_id,
        "columns": column_cells,
        "groups": group_cells,
        "flow": _cell(
            await flow_count(s, board_id), board.get("wip_min"), board.get("wip_max"), "Whole flow"
        ),
    }


# ---------------------------------------------------------------------------
# derived flow figures for a single card
# ---------------------------------------------------------------------------


async def dwell_intervals(
    s: AsyncSession,
    card: dict[str, Any],
    columns_by_id: dict[str, dict[str, Any]] | None = None,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Per-column occupancy intervals rebuilt from the movement log.

    Each movement opens an interval in ``to_column_id`` that closes when the
    next movement happens; the last interval stays open and is measured against
    ``now``. ``seconds_in_from_column`` recorded on the *next* movement is
    preferred over recomputing from timestamps so that a card whose history was
    back-dated (seed, simulation replay) reports what was actually written.
    """
    now = now or utcnow()
    if columns_by_id is None:
        columns_by_id = {
            column["id"]: column for column in await board_columns(s, card["board_id"])
        }
    moves = [
        as_dict(move) or {}
        for move in await s.scalars(
            select(CardMovement)
            .where(CardMovement.card_id == card["id"])
            .order_by(CardMovement.moved_at, CardMovement.id)
        )
    ]
    intervals: list[dict[str, Any]] = []
    for index, move in enumerate(moves):
        column = columns_by_id.get(move["to_column_id"], {})
        following = moves[index + 1] if index + 1 < len(moves) else None
        if following is not None:
            seconds = following.get("seconds_in_from_column")
            if seconds is None:
                seconds = seconds_between(move["moved_at"], following["moved_at"])
            left_at = following["moved_at"]
            is_open = False
        else:
            seconds = seconds_between(move["moved_at"], now)
            left_at = None
            is_open = True
        intervals.append(
            {
                "movement_id": move["id"],
                "column_id": move["to_column_id"],
                "column_name": column.get("name"),
                "column_kind": column.get("kind"),
                "segment": column.get("segment") or move.get("to_segment"),
                "entered_at": move["moved_at"],
                "left_at": left_at,
                "is_open": is_open,
                "seconds": max(0.0, float(seconds or 0.0)),
            }
        )
    return intervals


def flow_totals(card: dict[str, Any], intervals: list[dict[str, Any]]) -> dict[str, Any]:
    """Touch/wait/envelope split and flow efficiency for one card (SPEC §7.1).

    ``activity`` columns accrue touch time, ``queue`` columns accrue wait time,
    ``envelope`` time is tracked separately (the board decides whether it counts
    towards lead time) and time sitting in a ``done`` column is not flow time at
    all — the card has already been delivered.
    """
    touch = wait = envelope = 0.0
    for interval in intervals:
        kind = interval.get("column_kind")
        seconds = float(interval.get("seconds") or 0.0)
        if kind == "activity":
            touch += seconds
        elif kind == "queue":
            wait += seconds
        elif kind == "envelope":
            envelope += seconds
    active = touch + wait
    return {
        "touch_seconds": round(touch, 3),
        "wait_seconds": round(wait, 3),
        "envelope_seconds": round(envelope, 3),
        "touch_days": _days(touch),
        "wait_days": _days(wait),
        "flow_efficiency_pct": round(touch / active * 100.0, 2) if active > 0 else None,
        "discovery_lead_time_days": _days(
            seconds_between(card.get("created_at"), card.get("committed_at"))
        ),
        "system_lead_time_days": _days(
            seconds_between(card.get("committed_at"), card.get("delivered_at"))
        ),
        "customer_lead_time_days": _days(
            seconds_between(
                card.get("opened_at") or card.get("created_at"), card.get("delivered_at")
            )
        ),
    }


def sle_risk(board: dict[str, Any], card: dict[str, Any], now: str | None = None) -> dict[str, Any]:
    """Where this card stands against the board's Service Level Expectation.

    The clock starts at the commitment point (falling back to when the demand
    was opened, then created, so an upstream card still reports something
    meaningful) and stops at delivery, or at ``now`` while the card is in play.
    """
    now = now or utcnow()
    days = board.get("sle_days")
    percentile = board.get("sle_percentile")
    start = card.get("committed_at") or card.get("opened_at") or card.get("created_at")
    delivered = card.get("delivered_at")
    elapsed_seconds = seconds_between(start, delivered or now) or 0.0
    elapsed_days = round(elapsed_seconds / 86400.0, 2)

    if not days:
        level = "unknown"
        ratio = None
        remaining = None
    else:
        ratio = round(elapsed_days / float(days), 3)
        remaining = round(float(days) - elapsed_days, 2)
        if delivered:
            level = "met" if elapsed_days <= float(days) else "breached"
        elif ratio >= 1.0:
            level = "breached"
        elif ratio >= 0.8:
            level = "at_risk"
        elif ratio >= 0.5:
            level = "watch"
        else:
            level = "on_track"
    return {
        "sle_days": days,
        "sle_percentile": percentile,
        "started_at": start,
        "elapsed_days": elapsed_days,
        "remaining_days": remaining,
        "ratio": ratio,
        "level": level,
        "delivered": bool(delivered),
    }


def card_payload(
    row: dict[str, Any],
    *,
    now: str | None = None,
    board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn an enriched card row into the JSON shape every card endpoint returns."""
    now = now or utcnow()
    data = {field: row.get(field) for field in _CARD_FIELDS}
    data["tags"] = list(row.get("tags") or [])
    data["custom_fields"] = dict(row.get("custom_fields") or {})
    data["is_blocked"] = _bool(row.get("is_blocked"))
    data["archived"] = _bool(row.get("archived"))
    data.update(
        {
            "project_key": row.get("project_key"),
            "project_name": row.get("project_name"),
            "project_color": row.get("project_color"),
            "assignee_name": row.get("assignee_name"),
            "column_name": row.get("column_name"),
            "column_kind": row.get("column_kind"),
            "segment": row.get("column_segment"),
            "task_total": int(row.get("task_total") or 0),
            "task_done": int(row.get("task_done") or 0),
            "blocked_since": row.get("blocked_since"),
            "age_days": _days(seconds_between(row.get("created_at"), now)),
            "aging_days": _days(
                seconds_between(row.get("committed_at"), row.get("delivered_at") or now)
            ),
            "is_in_flight": bool(row.get("committed_at")) and not row.get("delivered_at"),
        }
    )
    if row.get("due_date") and not row.get("delivered_at"):
        data["days_to_due"] = _days(seconds_between(now, row["due_date"]))
    if board is not None:
        data["sle"] = sle_risk(board, row, now)
    return data


async def _movement_rows(s: AsyncSession, card_id: str) -> list[dict[str, Any]]:
    """Movements for one card, newest last, with names resolved for display."""
    rows = await s.execute(
        movement_select()
        .where(CardMovement.card_id == card_id)
        .order_by(CardMovement.moved_at, CardMovement.id)
    )
    return [movement_row(row) for row in rows]


def _movement_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["is_backward"] = _bool(row.get("is_backward"))
    seconds = row.get("seconds_in_from_column")
    data["seconds_in_from_column"] = None if seconds is None else int(seconds)
    data["days_in_from_column"] = _days(None if seconds is None else float(seconds))
    return data


def _task_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["is_done"] = _bool(row.get("is_done"))
    return data


def _blocker_payload(row: dict[str, Any], now: str) -> dict[str, Any]:
    data = dict(row)
    data["duration_seconds"] = seconds_between(row["started_at"], row.get("ended_at") or now)
    data["is_open"] = row.get("ended_at") is None
    return data


async def _tasks(s: AsyncSession, card_id: str) -> list[dict[str, Any]]:
    rows = await s.scalars(
        select(CardTask)
        .where(CardTask.card_id == card_id)
        .order_by(CardTask.position, CardTask.created_at)
    )
    return [as_dict(row) or {} for row in rows]


async def _blockers(s: AsyncSession, card_id: str) -> list[dict[str, Any]]:
    rows = await s.scalars(
        select(Blocker).where(Blocker.card_id == card_id).order_by(Blocker.started_at)
    )
    return [as_dict(row) or {} for row in rows]


async def _comments(s: AsyncSession, card_id: str) -> list[dict[str, Any]]:
    rows = await s.execute(
        _comment_select().where(Comment.card_id == card_id).order_by(Comment.created_at)
    )
    return [_comment_row(row) for row in rows]


async def _user_exists(s: AsyncSession, user_id: str) -> bool:
    return (await s.scalar(select(User.id).where(User.id == user_id))) is not None


async def _project_exists(s: AsyncSession, project_id: str) -> bool:
    query = select(Project.id).where(Project.id == project_id, *in_product(Project))
    return (await s.scalar(query)) is not None


def _tag_condition(s: AsyncSession, tag: str) -> Any:
    """``tags`` holds a JSON array of strings: match the element, never a substring."""
    if s.get_bind().dialect.name == "postgresql":
        return type_coerce(Card.tags, JSONB).contains([tag])
    # Matching the quoted form keeps "api" from matching "api-gateway".
    return cast(Card.tags, String).like(f'%"{tag}"%')


# ---------------------------------------------------------------------------
# GET /cards
# ---------------------------------------------------------------------------


@router.get(
    "/cards",
    response_model=ListResponse[CardItem],
    summary="List cards with the full filter set",
)
async def list_cards(
    board_id: str | None = Query(None, description="Restrict to one board."),
    project_id: str | None = Query(None, description="Project id, or 'none' for unassigned."),
    service_class: str | None = Query(None, description="Comma-separated classes of service."),
    assignee_id: str | None = Query(None, description="User id, or 'unassigned'."),
    segment: SegmentQuery | None = Query(None),
    column_id: str | None = Query(None, description="Comma-separated column ids."),
    tag: str | None = Query(None, description="Comma-separated tags; a card matching any wins."),
    q: str | None = Query(None, description="Free text over key, title and description."),
    is_blocked: bool | None = Query(None),
    demand_type: str | None = Query(None),
    value_area: str | None = Query(None),
    size: str | None = Query(None),
    due_before: str | None = Query(None, description="ISO date/timestamp upper bound."),
    archived: bool | None = Query(None, description="Defaults to active cards only."),
    envelope_state: EnvelopeState | None = Query(None),
    in_flight: bool | None = Query(None, description="Committed and not yet delivered."),
    sort: SortKey = Query("created_at"),
    order: SortOrder | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every documented filter, plus sorting and pagination.

    Returns the standard ``{items, total}`` envelope where ``total`` is the size
    of the *filtered* set, not of the page — the board and the filter bar both
    show "showing 50 of 214".
    """
    conditions: list[Any] = in_product(Card)

    if board_id:
        conditions.append(Card.board_id == board_id)
    if project_id:
        if project_id in {"none", "null", "unassigned"}:
            conditions.append(Card.project_id.is_(None))
        else:
            conditions.append(Card.project_id == project_id)
    if assignee_id:
        if assignee_id in {"none", "null", "unassigned"}:
            conditions.append(Card.assignee_id.is_(None))
        else:
            conditions.append(Card.assignee_id == assignee_id)
    if segment and segment != "unified":
        conditions.append(BoardColumn.segment == segment)

    columns = _split_csv(column_id)
    if columns:
        conditions.append(Card.column_id.in_(columns))

    for name, raw, allowed in (
        ("service_class", service_class, SERVICE_CLASSES),
        ("demand_type", demand_type, DEMAND_TYPES),
        ("value_area", value_area, VALUE_AREAS),
        ("size", size, CARD_SIZES),
    ):
        values = _validate_enum(name, _split_csv(raw), allowed)
        if values:
            conditions.append(getattr(Card, name).in_(values))

    tags = _split_csv(tag)
    if tags:
        conditions.append(or_(*(_tag_condition(s, value) for value in tags)))

    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(Card.title.ilike(needle), Card.description.ilike(needle), Card.key.ilike(needle))
        )
    if is_blocked is not None:
        conditions.append(Card.is_blocked.is_(bool(is_blocked)))
    if envelope_state:
        conditions.append(Card.envelope_state == envelope_state)
    if due_before:
        conditions.append(Card.due_date.is_not(None))
        conditions.append(Card.due_date <= due_before)
    if in_flight is not None:
        conditions.append(
            (Card.committed_at.is_not(None) & Card.delivered_at.is_(None))
            if in_flight
            else (Card.committed_at.is_(None) | Card.delivered_at.is_not(None))
        )
    conditions.append(Card.archived.is_(bool(archived)))

    total = int(
        await s.scalar(
            select(func.count())
            .select_from(Card)
            .join(BoardColumn, BoardColumn.id == Card.column_id)
            .where(*conditions)
        )
        or 0
    )

    direction = order or ("desc" if sort in _DESC_BY_DEFAULT else "asc")
    # "Most aged first" means the *oldest* commitment first, so the aging sort
    # reads the opposite way round to the timestamp it is built on.
    sql_direction = direction
    if sort == "aging":
        sql_direction = "asc" if direction == "desc" else "desc"

    expressions = [BoardColumn.position, Card.position] if sort == "position" else [_SORTS[sort]()]
    ordering: list[Any] = []
    if sort in {"due_date", "aging"}:
        ordering.append(case((expressions[0].is_(None), 1), else_=0))
    ordering.extend(
        expression.desc() if sql_direction == "desc" else expression.asc()
        for expression in expressions
    )
    ordering.append(Card.key.asc())

    rows = await s.execute(
        card_select().where(*conditions).order_by(*ordering).limit(limit).offset(offset)
    )
    now = utcnow()
    return {"items": [card_payload(_card_row(row), now=now) for row in rows], "total": total}


# ---------------------------------------------------------------------------
# POST /cards
# ---------------------------------------------------------------------------


@router.post(
    "/cards",
    response_model=CardItem,
    status_code=201,
    summary="Create a card (writes its creation movement and event)",
)
async def create_card(
    payload: CardCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a card and open its history.

    A card is born with a ``card_movements`` row whose ``from_column_id`` is
    ``NULL``: every later interval is measured from it, so no card can ever have
    a hole at the start of its history. A card created at or past the commitment
    point is stamped as committed immediately — otherwise the delivery half of
    the board would contain cards that are, by the metric definition, not in
    flight.
    """
    # Authorisation precedes existence: see deps.require_board_write_target.
    board = require_board_write_target(
        user, same_product(as_dict(await s.get(Board, payload.board_id))), payload.board_id
    )

    columns = await board_columns(s, board["id"])
    if not columns:
        raise BadRequest(
            "This board has no columns yet, so it cannot hold cards.",
            code="board_has_no_columns",
            details={"board_id": board["id"]},
        )
    order = column_order(columns)
    by_id = {column["id"]: column for column in columns}

    if payload.column_id:
        target = by_id.get(payload.column_id)
        if target is None:
            raise BadRequest(
                "That column does not belong to this board.",
                code="column_board_mismatch",
                details={"board_id": board["id"], "column_id": payload.column_id},
            )
    elif payload.envelope_state == "sealed":
        target = next((c for c in columns if c["kind"] == "envelope"), None)
        if target is None:
            raise BadRequest(
                "This board has no envelope column to hold a sealed demand.",
                code="no_envelope_column",
                details={"board_id": board["id"]},
            )
    else:
        target = next((c for c in columns if c["kind"] != "envelope"), columns[0])

    if payload.envelope_state == "sealed" and target["kind"] != "envelope":
        raise BadRequest(
            "A sealed demand must start in an envelope column.",
            code="sealed_requires_envelope_column",
            details={"column_id": target["id"], "column_kind": target["kind"]},
        )

    project = None
    if payload.project_id:
        project = same_product(as_dict(await s.get(Project, payload.project_id)))
        if project is None:
            raise NotFound("Project", payload.project_id, code="project_not_found")
    if payload.assignee_id and not await _user_exists(s, payload.assignee_id):
        raise NotFound("User", payload.assignee_id, code="user_not_found")

    commitment = commitment_column(columns)
    delivery = delivery_column(columns)
    now = utcnow()
    committed_at = (
        now if commitment is not None and order[target["id"]] >= order[commitment["id"]] else None
    )
    delivered_at = now if delivery is not None and target["id"] == delivery["id"] else None
    opened_at = None if payload.envelope_state == "sealed" else now

    card_id = new_id()
    prefix = _key_prefix(project, board)
    position = await next_position(s, target["id"])
    key = await next_card_key(s, prefix, board["product_id"])

    s.add(
        Card(
            id=card_id,
            product_id=board["product_id"],
            board_id=board["id"],
            column_id=target["id"],
            project_id=payload.project_id,
            key=key,
            title=payload.title,
            description=payload.description,
            service_class=payload.service_class,
            assignee_id=payload.assignee_id,
            requester=payload.requester,
            size=payload.size,
            value_area=payload.value_area,
            demand_type=payload.demand_type,
            due_date=payload.due_date,
            position=position,
            envelope_state=payload.envelope_state,
            is_blocked=False,
            tags=list(payload.tags),
            custom_fields=dict(payload.custom_fields),
            created_at=now,
            opened_at=opened_at,
            committed_at=committed_at,
            delivered_at=delivered_at,
            archived=False,
            created_by=user["id"],
        )
    )
    await s.flush()
    s.add(
        CardMovement(
            id=new_id(),
            card_id=card_id,
            board_id=board["id"],
            from_column_id=None,
            to_column_id=target["id"],
            from_segment=None,
            to_segment=target["segment"],
            moved_at=now,
            moved_by=user["id"],
            seconds_in_from_column=None,
            note=None,
            is_backward=False,
            source="api" if user.get("role") == "system" else "ui",
        )
    )
    record_event(
        s,
        {"id": card_id, "board_id": board["id"]},
        "created",
        actor_id=user["id"],
        field="column",
        new_value=target["name"],
        at=now,
    )
    await s.commit()

    return card_payload(await fetch_card(s, card_id), now=now, board=board)


# ---------------------------------------------------------------------------
# GET /cards/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/cards/{card_id}",
    response_model=CardDetail,
    summary="One card with tasks, blockers, comments, movements and flow figures",
)
async def get_card(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Everything the detail drawer renders in a single round trip."""
    row = await fetch_card(s, card_id)
    board = await load_board(s, row["board_id"])
    columns = await board_columns(s, board["id"])
    by_id = {column["id"]: column for column in columns}
    now = utcnow()

    intervals = await dwell_intervals(s, row, by_id, now)
    data = card_payload(row, now=now, board=board)
    data.update(
        {
            "tasks": [_task_payload(task) for task in await _tasks(s, row["id"])],
            "blockers": [
                _blocker_payload(blocker, now) for blocker in await _blockers(s, row["id"])
            ],
            "comments": await _comments(s, row["id"]),
            "movements": [_movement_payload(move) for move in await _movement_rows(s, row["id"])],
            "intervals": intervals,
            "flow": flow_totals(row, intervals),
            "board": {
                "id": board["id"],
                "name": board["name"],
                "slug": board["slug"],
                "kind": board["kind"],
                "confirm_moves": _bool(board.get("confirm_moves")),
                "sle_days": board.get("sle_days"),
                "sle_percentile": board.get("sle_percentile"),
            },
            "can_edit": can_write_board(user, board),
        }
    )
    return data


# ---------------------------------------------------------------------------
# PATCH /cards/{id}
# ---------------------------------------------------------------------------

#: Editable fields. ``column_id`` is missing on purpose: a column change is a
#: *move* and must go through POST /cards/{id}/move so that it is measured,
#: audited and WIP-checked.
_EDITABLE: tuple[str, ...] = (
    "title",
    "description",
    "project_id",
    "service_class",
    "assignee_id",
    "requester",
    "size",
    "value_area",
    "demand_type",
    "due_date",
    "position",
    "tags",
    "custom_fields",
    "archived",
)

_JSON_FIELDS = {"tags", "custom_fields"}
_INSTANT_FIELDS = {"due_date"}


def _display(field: str, value: Any) -> str | None:
    """Render a field value for the audit trail."""
    if value is None:
        return None
    if field in _JSON_FIELDS:
        return json_dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _normalised(field: str, value: Any) -> Any:
    """The value as the database will hand it back (instants in canonical ISO form)."""
    if field in _INSTANT_FIELDS and value is not None:
        moment = from_iso(value)
        return to_iso(moment) if moment is not None else None
    return value


@router.patch(
    "/cards/{card_id}",
    response_model=CardItem,
    summary="Edit a card — one audit event per changed field",
)
async def update_card(
    card_id: str,
    payload: CardUpdate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply a partial update, writing one ``card_events`` row per changed field."""
    row = await fetch_card(s, card_id)
    board = await require_card_write(s, user, row)

    changes = payload.model_dump(exclude_unset=True)
    unknown = [field for field in changes if field not in _EDITABLE]
    if unknown:
        raise BadRequest(
            f"These fields cannot be edited here: {', '.join(sorted(unknown))}.",
            code="field_not_editable",
            details={"fields": sorted(unknown)},
        )

    if changes.get("project_id") and not await _project_exists(s, changes["project_id"]):
        raise NotFound("Project", changes["project_id"], code="project_not_found")
    if changes.get("assignee_id") and not await _user_exists(s, changes["assignee_id"]):
        raise NotFound("User", changes["assignee_id"], code="user_not_found")

    now = utcnow()
    applied: list[dict[str, Any]] = []
    values: dict[str, Any] = {}

    for field in _EDITABLE:
        if field not in changes:
            continue
        new_value = _normalised(field, changes[field])
        old_value = row.get(field)
        if field in _JSON_FIELDS:
            unchanged = json_dumps(old_value) == json_dumps(new_value)
        elif field == "archived":
            unchanged = bool(old_value) == bool(new_value)
            new_value = bool(new_value)
        else:
            unchanged = old_value == new_value
        if unchanged:
            continue
        values[field] = new_value
        record_event(
            s,
            row,
            "archived" if field == "archived" and new_value else "updated",
            actor_id=user["id"],
            field=field,
            old_value=_display(field, old_value),
            new_value=_display(field, new_value),
            at=now,
        )
        applied.append(
            {"field": field, "old": _display(field, old_value), "new": _display(field, new_value)}
        )

    if values:
        await s.execute(update(Card).where(Card.id == row["id"]).values(**values))
    if values.get("archived") is True:
        # Archiving a providência card discards the providência (decision of 16/10/2026).
        await close_card(s, row, delivered=False)
    await s.commit()

    data = card_payload(await fetch_card(s, row["id"]), board=board)
    data["changed_fields"] = [change["field"] for change in applied]
    return data


@router.delete(
    "/cards/{card_id}",
    response_model=OkResponse,
    summary="Archive a card (soft delete — history is never destroyed)",
)
async def delete_card(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Archive rather than erase: the movement log feeds historic metrics.

    A hard delete would silently rewrite every chart that already counted this
    card, so ``DELETE`` sets ``archived`` and records an ``archived`` event.
    """
    row = await fetch_card(s, card_id)
    await require_card_write(s, user, row)
    if _bool(row.get("archived")):
        return {"ok": True, "id": row["id"], "message": "Card was already archived."}
    await s.execute(update(Card).where(Card.id == row["id"]).values(archived=True))
    record_event(
        s,
        row,
        "archived",
        actor_id=user["id"],
        field="archived",
        old_value="false",
        new_value="true",
    )
    await close_card(s, row, delivered=False)
    await s.commit()
    return {"ok": True, "id": row["id"], "message": f"{row['key']} archived."}


# ---------------------------------------------------------------------------
# tasks (SPEC §2.9 / Req. 5)
# ---------------------------------------------------------------------------


@router.get(
    "/cards/{card_id}/tasks",
    response_model=ListResponse[TaskOut],
    summary="List a card's tasks",
)
async def list_tasks(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Checklist items in display order."""
    row = await fetch_card(s, card_id)
    tasks = await _tasks(s, row["id"])
    return {"items": [_task_payload(task) for task in tasks], "total": len(tasks)}


@router.post(
    "/cards/{card_id}/tasks",
    response_model=TaskOut,
    status_code=201,
    summary="Add a task to a card",
)
async def create_task(
    card_id: str,
    payload: TaskCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Append a checklist item and record a ``task_added`` event."""
    row = await fetch_card(s, card_id)
    await require_card_write(s, user, row)
    if payload.assignee_id and not await _user_exists(s, payload.assignee_id):
        raise NotFound("User", payload.assignee_id, code="user_not_found")

    now = utcnow()
    task_id = new_id()
    if payload.position is not None:
        position = payload.position
    else:
        highest = await s.scalar(
            select(func.coalesce(func.max(CardTask.position), -1)).where(
                CardTask.card_id == row["id"]
            )
        )
        position = int(highest if highest is not None else -1) + 1
    task = CardTask(
        id=task_id,
        card_id=row["id"],
        title=payload.title,
        is_done=bool(payload.is_done),
        assignee_id=payload.assignee_id,
        position=position,
        created_at=now,
        completed_at=now if payload.is_done else None,
    )
    s.add(task)
    record_event(
        s, row, "task_added", actor_id=user["id"], field="task", new_value=payload.title, at=now
    )
    if payload.is_done:
        record_event(
            s,
            row,
            "task_completed",
            actor_id=user["id"],
            field="task",
            new_value=payload.title,
            at=now,
        )
    await s.commit()
    return _task_payload(as_dict(task) or {})


@router.patch("/tasks/{task_id}", response_model=TaskOut, summary="Edit or complete a task")
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit a checklist item; completing one records a ``task_completed`` event."""
    task = as_dict(await s.get(CardTask, task_id))
    if task is None:
        raise NotFound("Task", task_id, code="task_not_found")
    card = await fetch_card(s, task["card_id"])
    await require_card_write(s, user, card)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("assignee_id") and not await _user_exists(s, changes["assignee_id"]):
        raise NotFound("User", changes["assignee_id"], code="user_not_found")

    now = utcnow()
    values: dict[str, Any] = {}
    for field in ("title", "assignee_id", "position"):
        if field in changes and changes[field] != task.get(field):
            values[field] = changes[field]
            record_event(
                s,
                card,
                "updated",
                actor_id=user["id"],
                field=f"task.{field}",
                old_value=task.get(field),
                new_value=changes[field],
                at=now,
            )
    if "is_done" in changes and _bool(changes["is_done"]) != _bool(task["is_done"]):
        done = _bool(changes["is_done"])
        values["is_done"] = done
        values["completed_at"] = now if done else None
        record_event(
            s,
            card,
            "task_completed" if done else "updated",
            actor_id=user["id"],
            field="task",
            old_value=None if done else task["title"],
            new_value=task["title"] if done else None,
            at=now,
        )
    if values:
        await s.execute(update(CardTask).where(CardTask.id == task_id).values(**values))
    await s.commit()
    return _task_payload(as_dict(await s.get(CardTask, task_id, populate_existing=True)) or {})


@router.delete("/tasks/{task_id}", response_model=OkResponse, summary="Delete a task")
async def delete_task(
    task_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a checklist item and note the removal on the card's audit trail."""
    task = as_dict(await s.get(CardTask, task_id))
    if task is None:
        raise NotFound("Task", task_id, code="task_not_found")
    card = await fetch_card(s, task["card_id"])
    await require_card_write(s, user, card)
    await s.execute(delete(CardTask).where(CardTask.id == task_id))
    record_event(
        s, card, "updated", actor_id=user["id"], field="task.deleted", old_value=task["title"]
    )
    await s.commit()
    return {"ok": True, "id": task_id, "message": "Task deleted."}


# ---------------------------------------------------------------------------
# blockers
# ---------------------------------------------------------------------------


async def _open_blockers(s: AsyncSession, card_id: str) -> list[dict[str, Any]]:
    rows = await s.scalars(
        select(Blocker)
        .where(Blocker.card_id == card_id, Blocker.ended_at.is_(None))
        .order_by(Blocker.started_at)
    )
    return [as_dict(row) or {} for row in rows]


@router.post(
    "/cards/{card_id}/block",
    response_model=BlockerOut,
    status_code=201,
    summary="Block a card",
)
async def block_card(
    card_id: str,
    payload: BlockRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Open a blocker interval; blocked time is a first-class flow metric."""
    row = await fetch_card(s, card_id)
    await require_card_write(s, user, row)
    open_blockers = await _open_blockers(s, row["id"])
    if open_blockers:
        open_blocker = open_blockers[0]
        raise Conflict(
            "card_already_blocked",
            f"{row['key']} is already blocked — unblock it before recording a new reason.",
            {
                "card_id": row["id"],
                "blocker_id": open_blocker["id"],
                "reason": open_blocker["reason"],
            },
        )

    now = utcnow()
    blocker = Blocker(
        id=new_id(),
        card_id=row["id"],
        reason=payload.reason,
        category=payload.category,
        started_at=now,
        ended_at=None,
        created_by=user["id"],
    )
    s.add(blocker)
    await s.execute(update(Card).where(Card.id == row["id"]).values(is_blocked=True))
    record_event(
        s, row, "blocked", actor_id=user["id"], field="blocker", new_value=payload.reason, at=now
    )
    await s.commit()
    return _blocker_payload(as_dict(blocker) or {}, now)


@router.post(
    "/cards/{card_id}/unblock",
    response_model=BlockerOut,
    summary="Unblock a card, closing the blocker interval",
)
async def unblock_card(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Close every open blocker on the card and clear the flag."""
    row = await fetch_card(s, card_id)
    await require_card_write(s, user, row)
    open_blockers = await _open_blockers(s, row["id"])
    if not open_blockers:
        raise Conflict(
            "card_not_blocked",
            f"{row['key']} is not currently blocked.",
            {"card_id": row["id"]},
        )

    now = utcnow()
    await s.execute(
        update(Blocker)
        .where(Blocker.card_id == row["id"], Blocker.ended_at.is_(None))
        .values(ended_at=now)
    )
    await s.execute(update(Card).where(Card.id == row["id"]).values(is_blocked=False))
    record_event(
        s,
        row,
        "unblocked",
        actor_id=user["id"],
        field="blocker",
        old_value=open_blockers[0]["reason"],
        at=now,
    )
    await s.commit()
    closed = as_dict(await s.get(Blocker, open_blockers[0]["id"], populate_existing=True)) or {}
    return _blocker_payload(closed, now)


@router.get(
    "/cards/{card_id}/blockers",
    response_model=ListResponse[BlockerOut],
    summary="A card's blocker history",
)
async def list_blockers(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every blocker ever recorded on the card, oldest first."""
    row = await fetch_card(s, card_id)
    now = utcnow()
    blockers = await _blockers(s, row["id"])
    return {
        "items": [_blocker_payload(blocker, now) for blocker in blockers],
        "total": len(blockers),
    }


# ---------------------------------------------------------------------------
# comments
# ---------------------------------------------------------------------------


@router.get(
    "/cards/{card_id}/comments",
    response_model=ListResponse[CommentOut],
    summary="A card's comments",
)
async def list_comments(
    card_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Comments in the order they were written."""
    row = await fetch_card(s, card_id)
    comments = await _comments(s, row["id"])
    return {"items": comments, "total": len(comments)}


@router.post(
    "/cards/{card_id}/comments",
    response_model=CommentOut,
    status_code=201,
    summary="Comment on a card",
)
async def create_comment(
    card_id: str,
    payload: CommentCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a comment and record a ``commented`` event."""
    row = await fetch_card(s, card_id)
    await require_card_write(s, user, row)
    now = utcnow()
    comment_id = new_id()
    s.add(
        Comment(
            id=comment_id,
            card_id=row["id"],
            author_id=user["id"],
            body=payload.body,
            created_at=now,
        )
    )
    record_event(
        s,
        row,
        "commented",
        actor_id=user["id"],
        field="comment",
        new_value=payload.body[:200],
        at=now,
    )
    await s.commit()
    return {
        "id": comment_id,
        "card_id": row["id"],
        "author_id": user["id"],
        "author_name": user.get("name"),
        "body": payload.body,
        "created_at": now,
    }


def _own_comment_or_admin(user: CurrentUser, comment: dict[str, Any]) -> None:
    """Only the author (or an administrator) may edit or remove a comment."""
    if comment.get("author_id") == user["id"] or user.get("role") in {"master", "administrator"}:
        return
    raise Forbidden(
        "You can only change your own comments.",
        code="comment_not_yours",
        details={"comment_id": comment.get("id")},
    )


@router.patch("/comments/{comment_id}", response_model=CommentOut, summary="Edit a comment")
async def update_comment(
    comment_id: str,
    payload: CommentCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Rewrite a comment body."""
    comment = as_dict(await s.get(Comment, comment_id))
    if comment is None:
        raise NotFound("Comment", comment_id, code="comment_not_found")
    card = await fetch_card(s, comment["card_id"])
    await require_card_write(s, user, card)
    _own_comment_or_admin(user, comment)
    await s.execute(update(Comment).where(Comment.id == comment_id).values(body=payload.body))
    record_event(
        s,
        card,
        "updated",
        actor_id=user["id"],
        field="comment",
        old_value=comment["body"][:200],
        new_value=payload.body[:200],
    )
    await s.commit()
    updated = (await s.execute(_comment_select().where(Comment.id == comment_id))).first()
    return _comment_row(updated) if updated is not None else {}


@router.delete("/comments/{comment_id}", response_model=OkResponse, summary="Delete a comment")
async def delete_comment(
    comment_id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a comment; the audit trail keeps the fact that it existed."""
    comment = as_dict(await s.get(Comment, comment_id))
    if comment is None:
        raise NotFound("Comment", comment_id, code="comment_not_found")
    card = await fetch_card(s, comment["card_id"])
    await require_card_write(s, user, card)
    _own_comment_or_admin(user, comment)
    await s.execute(delete(Comment).where(Comment.id == comment_id))
    record_event(
        s,
        card,
        "updated",
        actor_id=user["id"],
        field="comment.deleted",
        old_value=comment["body"][:200],
    )
    await s.commit()
    return {"ok": True, "id": comment_id, "message": "Comment deleted."}
