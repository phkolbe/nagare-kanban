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

from typing import Any, Iterable, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import ConfigDict

from ..config import (
    CARD_SIZES,
    DEMAND_TYPES,
    SERVICE_CLASSES,
    VALUE_AREAS,
)
from ..db import Database, new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    can_write_board,
    get_current_user,
    get_db,
    require_board_write,
    require_board_write_target,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..models import Card, json_dumps
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

router = APIRouter(tags=["cards"])

__all__ = [
    "router",
    # helpers shared with routers.movements
    "CARD_SELECT",
    "board_columns",
    "card_payload",
    "column_order",
    "commitment_column",
    "delivery_column",
    "dwell_intervals",
    "fetch_card",
    "flow_totals",
    "load_board",
    "next_card_key",
    "record_event",
    "require_card_write",
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
# SQL fragments
# ---------------------------------------------------------------------------

#: Every card read goes through this projection so a card looks the same in the
#: list, in the drawer and in a move response.
CARD_SELECT = """
SELECT c.*,
       p.key   AS project_key,
       p.name  AS project_name,
       p.color AS project_color,
       u.name  AS assignee_name,
       col.name    AS column_name,
       col.kind    AS column_kind,
       col.segment AS column_segment,
       col.position AS column_position,
       col.is_commitment_point AS column_is_commitment_point,
       col.is_delivery_point   AS column_is_delivery_point,
       (SELECT COUNT(*) FROM card_tasks t WHERE t.card_id = c.id) AS task_total,
       (SELECT COUNT(*) FROM card_tasks t WHERE t.card_id = c.id AND t.is_done = 1)
           AS task_done,
       (SELECT MIN(b.started_at) FROM blockers b
         WHERE b.card_id = c.id AND b.ended_at IS NULL) AS blocked_since
  FROM cards c
  LEFT JOIN projects p ON p.id = c.project_id
  LEFT JOIN users    u ON u.id = c.assignee_id
  JOIN columns     col ON col.id = c.column_id
"""

_SORTS: dict[str, str] = {
    # sort key -> ORDER BY expression (direction is applied by the caller)
    "created_at": "c.created_at",
    "aging": "COALESCE(c.committed_at, c.opened_at, c.created_at)",
    "due_date": "c.due_date",
    "title": "LOWER(c.title)",
    "key": "c.key",
    "position": "col.position, c.position",
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


def load_board(db: Database, board_id: str) -> dict[str, Any]:
    """Board row or 404 — cards are always evaluated in the context of a board."""
    row = db.q1("SELECT * FROM boards WHERE id = ?", board_id)
    if row is None:
        raise NotFound("Board", board_id, code="board_not_found")
    return row


def board_columns(db: Database, board_id: str) -> list[dict[str, Any]]:
    """Every column of a board in **unified flow order**.

    SPEC §6.1 defines "forward" against the unified ordering: all upstream
    columns come before every downstream column, and within a segment the
    stored ``position`` decides. Sorting on the segment first keeps boards whose
    positions restart per segment working exactly like the seeded board, whose
    positions are already globally ordered.
    """
    return db.q(
        "SELECT * FROM columns WHERE board_id = ? "
        "ORDER BY CASE segment WHEN 'upstream' THEN 0 ELSE 1 END, position, name",
        board_id,
    )


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


def fetch_card(db: Database, card_id: str) -> dict[str, Any]:
    """Enriched card row or 404."""
    row = db.q1(CARD_SELECT + " WHERE c.id = ?", card_id)
    if row is None:
        # Card keys (NGR-104) are the human handle; accepting one here makes the
        # API and the MCP server pleasant to drive without an id lookup first.
        row = db.q1(CARD_SELECT + " WHERE c.key = ?", card_id)
    if row is None:
        raise NotFound("Card", card_id, code="card_not_found")
    return row


def require_card_write(db: Database, user: CurrentUser, card: dict[str, Any]) -> dict[str, Any]:
    """Authorise a write against the card's board; returns the board row.

    SPEC §4: editors, administrators, masters and the system identity work
    operational boards; the ``simulator`` role owns simulator boards; viewers
    never write.
    """
    board = load_board(db, card["board_id"])
    require_board_write(user, board)
    return board


def record_event(
    db: Database,
    card: dict[str, Any],
    event_type: str,
    *,
    actor_id: str | None,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    at: str | None = None,
) -> str:
    """Append one row to ``card_events`` and return its id."""
    event_id = new_id()
    db.ex(
        "INSERT INTO card_events (id, card_id, board_id, actor_id, event_type, field, "
        "old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        event_id,
        card["id"],
        card["board_id"],
        actor_id,
        event_type,
        field,
        None if old_value is None else str(old_value),
        None if new_value is None else str(new_value),
        at or utcnow(),
    )
    return event_id


def next_card_key(db: Database, prefix: str) -> str:
    """Next ``PREFIX-nnn`` key, continuing the per-project counter.

    The seed starts each project at 101, so a fresh project's first card is
    ``KEY-101`` and an existing project continues from its highest number —
    including numbers that were skipped or deleted, which keeps keys stable and
    never reuses a retired reference.
    """
    highest = 100
    for row in db.q("SELECT key FROM cards WHERE key LIKE ?", f"{prefix}-%"):
        _, _, number = str(row["key"]).partition("-")
        if number.isdigit():
            highest = max(highest, int(number))
    return f"{prefix}-{highest + 1}"


def _key_prefix(project: dict[str, Any] | None, board: dict[str, Any]) -> str:
    """Key prefix: the project key, or a board-derived one for loose cards."""
    if project and project.get("key"):
        return str(project["key"]).upper()
    letters = [ch for ch in str(board.get("slug") or board.get("name") or "card") if ch.isalnum()]
    return ("".join(letters[:3]) or "CARD").upper()


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


def wip_status(
    db: Database, board: dict[str, Any], columns: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Live WIP for every column, every group and the whole flow.

    Column and group counts are *occupancy* (non-archived cards sitting there);
    the flow count is the SPEC §7.1 definition of WIP — committed and not yet
    delivered — because a whole-flow limit is a commitment-to-delivery policy,
    not a "how many rows are on the screen" policy.
    """
    board_id = board["id"]
    columns = columns if columns is not None else board_columns(db, board_id)
    counts = {
        row["column_id"]: int(row["n"])
        for row in db.q(
            "SELECT column_id, COUNT(*) AS n FROM cards "
            "WHERE board_id = ? AND archived = 0 GROUP BY column_id",
            board_id,
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
    for group in db.q(
        "SELECT * FROM column_groups WHERE board_id = ? ORDER BY position", board_id
    ):
        group_cells[group["id"]] = _cell(
            group_counts.get(group["id"], 0),
            group.get("wip_min"),
            group.get("wip_max"),
            group["name"],
        )

    flow_count = int(
        db.scalar(
            "SELECT COUNT(*) FROM cards WHERE board_id = ? AND archived = 0 "
            "AND committed_at IS NOT NULL AND delivered_at IS NULL",
            board_id,
        )
        or 0
    )
    return {
        "board_id": board_id,
        "columns": column_cells,
        "groups": group_cells,
        "flow": _cell(flow_count, board.get("wip_min"), board.get("wip_max"), "Whole flow"),
    }


# ---------------------------------------------------------------------------
# derived flow figures for a single card
# ---------------------------------------------------------------------------


def dwell_intervals(
    db: Database,
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
            column["id"]: column for column in board_columns(db, card["board_id"])
        }
    moves = db.q(
        "SELECT * FROM card_movements WHERE card_id = ? ORDER BY moved_at, rowid",
        card["id"],
    )
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
    db: Database,
    row: dict[str, Any],
    *,
    now: str | None = None,
    board: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn an enriched card row into the JSON shape every card endpoint returns."""
    now = now or utcnow()
    data = (Card.from_row(row) or Card()).to_dict()
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
                seconds_between(
                    row.get("committed_at"), row.get("delivered_at") or now
                )
            ),
            "is_in_flight": bool(row.get("committed_at")) and not row.get("delivered_at"),
        }
    )
    if row.get("due_date") and not row.get("delivered_at"):
        data["days_to_due"] = _days(seconds_between(now, row["due_date"]))
    if board is not None:
        data["sle"] = sle_risk(board, row, now)
    return data


def _movement_rows(db: Database, card_id: str) -> list[dict[str, Any]]:
    """Movements for one card, newest last, with names resolved for display."""
    return db.q(
        "SELECT m.*, fc.name AS from_column_name, tc.name AS to_column_name, "
        "u.name AS moved_by_name "
        "FROM card_movements m "
        "LEFT JOIN columns fc ON fc.id = m.from_column_id "
        "LEFT JOIN columns tc ON tc.id = m.to_column_id "
        "LEFT JOIN users u ON u.id = m.moved_by "
        "WHERE m.card_id = ? ORDER BY m.moved_at, m.rowid",
        card_id,
    )


def _movement_payload(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["is_backward"] = _bool(row.get("is_backward"))
    seconds = row.get("seconds_in_from_column")
    data["seconds_in_from_column"] = None if seconds is None else int(seconds)
    data["days_in_from_column"] = _days(None if seconds is None else float(seconds))
    data.pop("rowid", None)
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


# ---------------------------------------------------------------------------
# GET /cards
# ---------------------------------------------------------------------------


@router.get(
    "/cards",
    response_model=ListResponse[CardItem],
    summary="List cards with the full filter set",
)
def list_cards(
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
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every documented filter, plus sorting and pagination.

    Returns the standard ``{items, total}`` envelope where ``total`` is the size
    of the *filtered* set, not of the page — the board and the filter bar both
    show "showing 50 of 214".
    """
    where: list[str] = []
    params: list[Any] = []

    if board_id:
        where.append("c.board_id = ?")
        params.append(board_id)
    if project_id:
        if project_id in {"none", "null", "unassigned"}:
            where.append("c.project_id IS NULL")
        else:
            where.append("c.project_id = ?")
            params.append(project_id)
    if assignee_id:
        if assignee_id in {"none", "null", "unassigned"}:
            where.append("c.assignee_id IS NULL")
        else:
            where.append("c.assignee_id = ?")
            params.append(assignee_id)
    if segment and segment != "unified":
        where.append("col.segment = ?")
        params.append(segment)

    columns = _split_csv(column_id)
    if columns:
        where.append(f"c.column_id IN ({', '.join('?' for _ in columns)})")
        params.extend(columns)

    for name, raw, allowed in (
        ("service_class", service_class, SERVICE_CLASSES),
        ("demand_type", demand_type, DEMAND_TYPES),
        ("value_area", value_area, VALUE_AREAS),
        ("size", size, CARD_SIZES),
    ):
        values = _validate_enum(name, _split_csv(raw), allowed)
        if values:
            where.append(f"c.{name} IN ({', '.join('?' for _ in values)})")
            params.extend(values)

    tags = _split_csv(tag)
    if tags:
        # ``tags`` is a JSON array of strings; matching the quoted form keeps
        # "api" from matching "api-gateway".
        where.append("(" + " OR ".join("c.tags LIKE ?" for _ in tags) + ")")
        params.extend(f'%"{value}"%' for value in tags)

    if q:
        needle = f"%{q.strip()}%"
        where.append("(c.title LIKE ? OR c.description LIKE ? OR c.key LIKE ?)")
        params.extend([needle, needle, needle])
    if is_blocked is not None:
        where.append("c.is_blocked = ?")
        params.append(1 if is_blocked else 0)
    if envelope_state:
        where.append("c.envelope_state = ?")
        params.append(envelope_state)
    if due_before:
        where.append("c.due_date IS NOT NULL AND c.due_date <= ?")
        params.append(due_before)
    if in_flight is not None:
        where.append(
            "c.committed_at IS NOT NULL AND c.delivered_at IS NULL"
            if in_flight
            else "(c.committed_at IS NULL OR c.delivered_at IS NOT NULL)"
        )
    where.append("c.archived = ?")
    params.append(1 if archived else 0)

    clause = " WHERE " + " AND ".join(where) if where else ""

    total = int(
        db.scalar(
            "SELECT COUNT(*) FROM cards c JOIN columns col ON col.id = c.column_id" + clause,
            params,
        )
        or 0
    )

    direction = order or ("desc" if sort in _DESC_BY_DEFAULT else "asc")
    # "Most aged first" means the *oldest* commitment first, so the aging sort
    # reads the opposite way round to the timestamp it is built on.
    sql_direction = direction
    if sort == "aging":
        sql_direction = "asc" if direction == "desc" else "desc"
    nulls_last = ""
    if sort in {"due_date", "aging"}:
        nulls_last = f"CASE WHEN {_SORTS[sort]} IS NULL THEN 1 ELSE 0 END, "
    order_by = (
        f" ORDER BY {nulls_last}{_SORTS[sort]} {sql_direction.upper()}, c.key ASC"
        f" LIMIT ? OFFSET ?"
    )

    rows = db.q(CARD_SELECT + clause + order_by, [*params, limit, offset])
    now = utcnow()
    return {"items": [card_payload(db, row, now=now) for row in rows], "total": total}


# ---------------------------------------------------------------------------
# POST /cards
# ---------------------------------------------------------------------------


@router.post(
    "/cards",
    response_model=CardItem,
    status_code=201,
    summary="Create a card (writes its creation movement and event)",
)
def create_card(
    payload: CardCreate,
    db: Database = Depends(get_db),
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
        user, db.q1("SELECT * FROM boards WHERE id = ?", payload.board_id), payload.board_id
    )

    columns = board_columns(db, board["id"])
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
        project = db.q1("SELECT * FROM projects WHERE id = ?", payload.project_id)
        if project is None:
            raise NotFound("Project", payload.project_id, code="project_not_found")
    if payload.assignee_id and not db.q1(
        "SELECT id FROM users WHERE id = ?", payload.assignee_id
    ):
        raise NotFound("User", payload.assignee_id, code="user_not_found")

    commitment = commitment_column(columns)
    delivery = delivery_column(columns)
    now = utcnow()
    committed_at = (
        now
        if commitment is not None and order[target["id"]] >= order[commitment["id"]]
        else None
    )
    delivered_at = now if delivery is not None and target["id"] == delivery["id"] else None
    opened_at = None if payload.envelope_state == "sealed" else now

    card_id = new_id()
    prefix = _key_prefix(project, board)
    position = (
        int(
            db.scalar(
                "SELECT COALESCE(MAX(position), -1) FROM cards WHERE column_id = ?",
                target["id"],
            )
            or -1
        )
        + 1
    )

    with db.tx():
        key = next_card_key(db, prefix)
        db.ex(
            "INSERT INTO cards (id, board_id, column_id, project_id, key, title, description, "
            "service_class, assignee_id, requester, size, value_area, demand_type, due_date, "
            "position, envelope_state, is_blocked, tags, custom_fields, created_at, opened_at, "
            "committed_at, delivered_at, archived, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,0,?)",
            card_id,
            board["id"],
            target["id"],
            payload.project_id,
            key,
            payload.title,
            payload.description,
            payload.service_class,
            payload.assignee_id,
            payload.requester,
            payload.size,
            payload.value_area,
            payload.demand_type,
            payload.due_date,
            position,
            payload.envelope_state,
            json_dumps(payload.tags),
            json_dumps(payload.custom_fields),
            now,
            opened_at,
            committed_at,
            delivered_at,
            user["id"],
        )
        db.ex(
            "INSERT INTO card_movements (id, card_id, board_id, from_column_id, to_column_id, "
            "from_segment, to_segment, moved_at, moved_by, seconds_in_from_column, note, "
            "is_backward, source) VALUES (?,?,?,NULL,?,NULL,?,?,?,NULL,?,0,?)",
            new_id(),
            card_id,
            board["id"],
            target["id"],
            target["segment"],
            now,
            user["id"],
            None,
            "api" if user.get("role") == "system" else "ui",
        )
        record_event(
            db,
            {"id": card_id, "board_id": board["id"]},
            "created",
            actor_id=user["id"],
            field="column",
            new_value=target["name"],
            at=now,
        )

    return card_payload(db, fetch_card(db, card_id), now=now, board=board)


# ---------------------------------------------------------------------------
# GET /cards/{id}
# ---------------------------------------------------------------------------


@router.get(
    "/cards/{card_id}",
    response_model=CardDetail,
    summary="One card with tasks, blockers, comments, movements and flow figures",
)
def get_card(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Everything the detail drawer renders in a single round trip."""
    row = fetch_card(db, card_id)
    board = load_board(db, row["board_id"])
    columns = board_columns(db, board["id"])
    by_id = {column["id"]: column for column in columns}
    now = utcnow()

    intervals = dwell_intervals(db, row, by_id, now)
    data = card_payload(db, row, now=now, board=board)
    data.update(
        {
            "tasks": [
                _task_payload(task)
                for task in db.q(
                    "SELECT * FROM card_tasks WHERE card_id = ? ORDER BY position, created_at",
                    row["id"],
                )
            ],
            "blockers": [
                _blocker_payload(blocker, now)
                for blocker in db.q(
                    "SELECT * FROM blockers WHERE card_id = ? ORDER BY started_at", row["id"]
                )
            ],
            "comments": [
                dict(comment)
                for comment in db.q(
                    "SELECT cm.*, u.name AS author_name FROM comments cm "
                    "LEFT JOIN users u ON u.id = cm.author_id "
                    "WHERE cm.card_id = ? ORDER BY cm.created_at",
                    row["id"],
                )
            ],
            "movements": [_movement_payload(move) for move in _movement_rows(db, row["id"])],
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

#: Editable fields and the SQL column each maps to. ``column_id`` is missing on
#: purpose: a column change is a *move* and must go through POST /cards/{id}/move
#: so that it is measured, audited and WIP-checked.
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


def _display(field: str, value: Any) -> str | None:
    """Render a field value for the audit trail."""
    if value is None:
        return None
    if field in _JSON_FIELDS:
        return json_dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@router.patch(
    "/cards/{card_id}",
    response_model=CardItem,
    summary="Edit a card — one audit event per changed field",
)
def update_card(
    card_id: str,
    payload: CardUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply a partial update, writing one ``card_events`` row per changed field."""
    row = fetch_card(db, card_id)
    board = require_card_write(db, user, row)

    changes = payload.model_dump(exclude_unset=True)
    unknown = [field for field in changes if field not in _EDITABLE]
    if unknown:
        raise BadRequest(
            f"These fields cannot be edited here: {', '.join(sorted(unknown))}.",
            code="field_not_editable",
            details={"fields": sorted(unknown)},
        )

    if changes.get("project_id"):
        if not db.q1("SELECT id FROM projects WHERE id = ?", changes["project_id"]):
            raise NotFound("Project", changes["project_id"], code="project_not_found")
    if changes.get("assignee_id"):
        if not db.q1("SELECT id FROM users WHERE id = ?", changes["assignee_id"]):
            raise NotFound("User", changes["assignee_id"], code="user_not_found")

    current = Card.from_row(row) or Card()
    now = utcnow()
    applied: list[dict[str, Any]] = []

    with db.tx():
        for field in _EDITABLE:
            if field not in changes:
                continue
            new_value = changes[field]
            old_value = getattr(current, field)
            if field in _JSON_FIELDS:
                unchanged = json_dumps(old_value) == json_dumps(new_value)
                stored: Any = json_dumps(new_value)
            elif field == "archived":
                unchanged = bool(old_value) == bool(new_value)
                stored = 1 if new_value else 0
            else:
                unchanged = old_value == new_value
                stored = new_value
            if unchanged:
                continue
            db.ex(f"UPDATE cards SET {field} = ? WHERE id = ?", stored, row["id"])
            record_event(
                db,
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

    data = card_payload(db, fetch_card(db, row["id"]), board=board)
    data["changed_fields"] = [change["field"] for change in applied]
    return data


@router.delete(
    "/cards/{card_id}",
    response_model=OkResponse,
    summary="Archive a card (soft delete — history is never destroyed)",
)
def delete_card(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Archive rather than erase: the movement log feeds historic metrics.

    A hard delete would silently rewrite every chart that already counted this
    card, so ``DELETE`` sets ``archived = 1`` and records an ``archived`` event.
    """
    row = fetch_card(db, card_id)
    require_card_write(db, user, row)
    if _bool(row.get("archived")):
        return {"ok": True, "id": row["id"], "message": "Card was already archived."}
    with db.tx():
        db.ex("UPDATE cards SET archived = 1 WHERE id = ?", row["id"])
        record_event(
            db,
            row,
            "archived",
            actor_id=user["id"],
            field="archived",
            old_value="false",
            new_value="true",
        )
    return {"ok": True, "id": row["id"], "message": f"{row['key']} archived."}


# ---------------------------------------------------------------------------
# tasks (SPEC §2.9 / Req. 5)
# ---------------------------------------------------------------------------


@router.get(
    "/cards/{card_id}/tasks",
    response_model=ListResponse[TaskOut],
    summary="List a card's tasks",
)
def list_tasks(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Checklist items in display order."""
    row = fetch_card(db, card_id)
    tasks = db.q(
        "SELECT * FROM card_tasks WHERE card_id = ? ORDER BY position, created_at", row["id"]
    )
    return {"items": [_task_payload(task) for task in tasks], "total": len(tasks)}


@router.post(
    "/cards/{card_id}/tasks",
    response_model=TaskOut,
    status_code=201,
    summary="Add a task to a card",
)
def create_task(
    card_id: str,
    payload: TaskCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Append a checklist item and record a ``task_added`` event."""
    row = fetch_card(db, card_id)
    require_card_write(db, user, row)
    if payload.assignee_id and not db.q1(
        "SELECT id FROM users WHERE id = ?", payload.assignee_id
    ):
        raise NotFound("User", payload.assignee_id, code="user_not_found")

    now = utcnow()
    task_id = new_id()
    position = (
        payload.position
        if payload.position is not None
        else int(
            db.scalar(
                "SELECT COALESCE(MAX(position), -1) FROM card_tasks WHERE card_id = ?",
                row["id"],
            )
            or -1
        )
        + 1
    )
    with db.tx():
        db.ex(
            "INSERT INTO card_tasks (id, card_id, title, is_done, assignee_id, position, "
            "created_at, completed_at) VALUES (?,?,?,?,?,?,?,?)",
            task_id,
            row["id"],
            payload.title,
            1 if payload.is_done else 0,
            payload.assignee_id,
            position,
            now,
            now if payload.is_done else None,
        )
        record_event(
            db, row, "task_added", actor_id=user["id"], field="task",
            new_value=payload.title, at=now,
        )
        if payload.is_done:
            record_event(
                db, row, "task_completed", actor_id=user["id"], field="task",
                new_value=payload.title, at=now,
            )
    return _task_payload(db.q1("SELECT * FROM card_tasks WHERE id = ?", task_id) or {})


@router.patch("/tasks/{task_id}", response_model=TaskOut, summary="Edit or complete a task")
def update_task(
    task_id: str,
    payload: TaskUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit a checklist item; completing one records a ``task_completed`` event."""
    task = db.q1("SELECT * FROM card_tasks WHERE id = ?", task_id)
    if task is None:
        raise NotFound("Task", task_id, code="task_not_found")
    card = fetch_card(db, task["card_id"])
    require_card_write(db, user, card)

    changes = payload.model_dump(exclude_unset=True)
    if changes.get("assignee_id") and not db.q1(
        "SELECT id FROM users WHERE id = ?", changes["assignee_id"]
    ):
        raise NotFound("User", changes["assignee_id"], code="user_not_found")

    now = utcnow()
    with db.tx():
        for field in ("title", "assignee_id", "position"):
            if field in changes and changes[field] != task.get(field):
                db.ex(f"UPDATE card_tasks SET {field} = ? WHERE id = ?", changes[field], task_id)
                record_event(
                    db, card, "updated", actor_id=user["id"], field=f"task.{field}",
                    old_value=task.get(field), new_value=changes[field], at=now,
                )
        if "is_done" in changes and _bool(changes["is_done"]) != _bool(task["is_done"]):
            done = _bool(changes["is_done"])
            db.ex(
                "UPDATE card_tasks SET is_done = ?, completed_at = ? WHERE id = ?",
                1 if done else 0,
                now if done else None,
                task_id,
            )
            record_event(
                db,
                card,
                "task_completed" if done else "updated",
                actor_id=user["id"],
                field="task",
                old_value=None if done else task["title"],
                new_value=task["title"] if done else None,
                at=now,
            )
    return _task_payload(db.q1("SELECT * FROM card_tasks WHERE id = ?", task_id) or {})


@router.delete("/tasks/{task_id}", response_model=OkResponse, summary="Delete a task")
def delete_task(
    task_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a checklist item and note the removal on the card's audit trail."""
    task = db.q1("SELECT * FROM card_tasks WHERE id = ?", task_id)
    if task is None:
        raise NotFound("Task", task_id, code="task_not_found")
    card = fetch_card(db, task["card_id"])
    require_card_write(db, user, card)
    with db.tx():
        db.ex("DELETE FROM card_tasks WHERE id = ?", task_id)
        record_event(
            db, card, "updated", actor_id=user["id"], field="task.deleted",
            old_value=task["title"],
        )
    return {"ok": True, "id": task_id, "message": "Task deleted."}


# ---------------------------------------------------------------------------
# blockers
# ---------------------------------------------------------------------------


@router.post(
    "/cards/{card_id}/block",
    response_model=BlockerOut,
    status_code=201,
    summary="Block a card",
)
def block_card(
    card_id: str,
    payload: BlockRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Open a blocker interval; blocked time is a first-class flow metric."""
    row = fetch_card(db, card_id)
    require_card_write(db, user, row)
    open_blocker = db.q1(
        "SELECT * FROM blockers WHERE card_id = ? AND ended_at IS NULL ORDER BY started_at",
        row["id"],
    )
    if open_blocker is not None:
        raise Conflict(
            "card_already_blocked",
            f"{row['key']} is already blocked — unblock it before recording a new reason.",
            {"card_id": row["id"], "blocker_id": open_blocker["id"],
             "reason": open_blocker["reason"]},
        )

    now = utcnow()
    blocker_id = new_id()
    with db.tx():
        db.ex(
            "INSERT INTO blockers (id, card_id, reason, category, started_at, ended_at, "
            "created_by) VALUES (?,?,?,?,?,NULL,?)",
            blocker_id,
            row["id"],
            payload.reason,
            payload.category,
            now,
            user["id"],
        )
        db.ex("UPDATE cards SET is_blocked = 1 WHERE id = ?", row["id"])
        record_event(
            db, row, "blocked", actor_id=user["id"], field="blocker",
            new_value=payload.reason, at=now,
        )
    blocker = db.q1("SELECT * FROM blockers WHERE id = ?", blocker_id) or {}
    return _blocker_payload(blocker, now)


@router.post(
    "/cards/{card_id}/unblock",
    response_model=BlockerOut,
    summary="Unblock a card, closing the blocker interval",
)
def unblock_card(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Close every open blocker on the card and clear the flag."""
    row = fetch_card(db, card_id)
    require_card_write(db, user, row)
    open_blockers = db.q(
        "SELECT * FROM blockers WHERE card_id = ? AND ended_at IS NULL ORDER BY started_at",
        row["id"],
    )
    if not open_blockers:
        raise Conflict(
            "card_not_blocked",
            f"{row['key']} is not currently blocked.",
            {"card_id": row["id"]},
        )

    now = utcnow()
    with db.tx():
        db.ex(
            "UPDATE blockers SET ended_at = ? WHERE card_id = ? AND ended_at IS NULL",
            now,
            row["id"],
        )
        db.ex("UPDATE cards SET is_blocked = 0 WHERE id = ?", row["id"])
        record_event(
            db, row, "unblocked", actor_id=user["id"], field="blocker",
            old_value=open_blockers[0]["reason"], at=now,
        )
    closed = db.q1("SELECT * FROM blockers WHERE id = ?", open_blockers[0]["id"]) or {}
    return _blocker_payload(closed, now)


@router.get(
    "/cards/{card_id}/blockers",
    response_model=ListResponse[BlockerOut],
    summary="A card's blocker history",
)
def list_blockers(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Every blocker ever recorded on the card, oldest first."""
    row = fetch_card(db, card_id)
    now = utcnow()
    blockers = db.q(
        "SELECT * FROM blockers WHERE card_id = ? ORDER BY started_at", row["id"]
    )
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
def list_comments(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Comments in the order they were written."""
    row = fetch_card(db, card_id)
    comments = db.q(
        "SELECT c.*, u.name AS author_name FROM comments c "
        "LEFT JOIN users u ON u.id = c.author_id "
        "WHERE c.card_id = ? ORDER BY c.created_at",
        row["id"],
    )
    return {"items": [dict(comment) for comment in comments], "total": len(comments)}


@router.post(
    "/cards/{card_id}/comments",
    response_model=CommentOut,
    status_code=201,
    summary="Comment on a card",
)
def create_comment(
    card_id: str,
    payload: CommentCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a comment and record a ``commented`` event."""
    row = fetch_card(db, card_id)
    require_card_write(db, user, row)
    now = utcnow()
    comment_id = new_id()
    with db.tx():
        db.ex(
            "INSERT INTO comments (id, card_id, author_id, body, created_at) VALUES (?,?,?,?,?)",
            comment_id,
            row["id"],
            user["id"],
            payload.body,
            now,
        )
        record_event(
            db, row, "commented", actor_id=user["id"], field="comment",
            new_value=payload.body[:200], at=now,
        )
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
def update_comment(
    comment_id: str,
    payload: CommentCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Rewrite a comment body."""
    comment = db.q1("SELECT * FROM comments WHERE id = ?", comment_id)
    if comment is None:
        raise NotFound("Comment", comment_id, code="comment_not_found")
    card = fetch_card(db, comment["card_id"])
    require_card_write(db, user, card)
    _own_comment_or_admin(user, comment)
    with db.tx():
        db.ex("UPDATE comments SET body = ? WHERE id = ?", payload.body, comment_id)
        record_event(
            db, card, "updated", actor_id=user["id"], field="comment",
            old_value=comment["body"][:200], new_value=payload.body[:200],
        )
    updated = db.q1(
        "SELECT c.*, u.name AS author_name FROM comments c "
        "LEFT JOIN users u ON u.id = c.author_id WHERE c.id = ?",
        comment_id,
    )
    return dict(updated or {})


@router.delete("/comments/{comment_id}", response_model=OkResponse, summary="Delete a comment")
def delete_comment(
    comment_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a comment; the audit trail keeps the fact that it existed."""
    comment = db.q1("SELECT * FROM comments WHERE id = ?", comment_id)
    if comment is None:
        raise NotFound("Comment", comment_id, code="comment_not_found")
    card = fetch_card(db, comment["card_id"])
    require_card_write(db, user, card)
    _own_comment_or_admin(user, comment)
    with db.tx():
        db.ex("DELETE FROM comments WHERE id = ?", comment_id)
        record_event(
            db, card, "updated", actor_id=user["id"], field="comment.deleted",
            old_value=comment["body"][:200],
        )
    return {"ok": True, "id": comment_id, "message": "Comment deleted."}
