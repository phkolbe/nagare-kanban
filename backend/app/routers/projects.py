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

from ..db import Database, new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    get_board_or_404,
    get_db,
    require_capability,
    require_flag,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..models import json_dumps
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
    decorate_card,
    next_card_key,
    select_cards,
    wip_state,
)

router = APIRouter(tags=["projects"])

__all__ = ["router"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: One pass over `cards` gives every roll-up a project card needs.
_PROJECT_COUNTS_SQL = """
SELECT project_id,
       COUNT(*) AS card_count,
       SUM(CASE WHEN envelope_state = 'sealed' THEN 1 ELSE 0 END) AS sealed_count,
       SUM(CASE WHEN envelope_state = 'opened' THEN 1 ELSE 0 END) AS opened_count,
       SUM(CASE WHEN delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered_count,
       SUM(CASE WHEN committed_at IS NOT NULL AND delivered_at IS NULL THEN 1 ELSE 0 END)
           AS in_flight_count
FROM cards
WHERE archived = 0 AND project_id IS NOT NULL
GROUP BY project_id
"""

_ZERO_COUNTS: dict[str, int] = {
    "card_count": 0,
    "sealed_count": 0,
    "opened_count": 0,
    "delivered_count": 0,
    "in_flight_count": 0,
}


def _get_project_or_404(db: Database, project_id: str) -> dict[str, Any]:
    """Fetch a project by id or by its human key (``NGR``)."""
    row = db.q1("SELECT * FROM projects WHERE id = ?", project_id)
    if row is None:
        row = db.q1("SELECT * FROM projects WHERE key = ?", str(project_id).upper())
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


def _resolve_demand_board(db: Database, board_id: str | None) -> dict[str, Any]:
    """The board a new demand belongs to; the operational board when unambiguous."""
    if board_id:
        return get_board_or_404(db, board_id)
    rows = db.q(
        "SELECT * FROM boards WHERE kind = 'operational' AND archived = 0 "
        "ORDER BY created_at LIMIT 2"
    )
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
            details={"boards": [{"id": r["id"], "name": r["name"]} for r in rows]},
        )
    board = dict(rows[0])
    board["count_envelope_in_metrics"] = bool(board["count_envelope_in_metrics"])
    board["confirm_moves"] = bool(board["confirm_moves"])
    board["archived"] = bool(board["archived"])
    return board


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


def _assert_column_capacity(
    db: Database, column: dict[str, Any], service_class: str
) -> bool:
    """SPEC §6.6: a column WIP max blocks the move; ``expedite`` overrides it."""
    if column["wip_max"] is None:
        return False
    count = int(
        db.scalar("SELECT COUNT(*) FROM cards WHERE column_id = ? AND archived = 0", column["id"])
        or 0
    )
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


def _next_position(db: Database, column_id: str) -> int:
    return int(
        db.scalar("SELECT COALESCE(MAX(position), -1) + 1 FROM cards WHERE column_id = ?", column_id)
        or 0
    )


def _card_out(db: Database, card_id: str) -> dict[str, Any]:
    """Re-read a card through the shared projection so the client gets full shape."""
    rows = select_cards(db, column_ids=None, archived=False, order_by="c.created_at")
    for row in rows:
        if row["id"] == card_id:
            return row
    raise NotFound("Card", card_id, code="card_not_found")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=ListResponse[ProjectOut], summary="List projects")
def list_projects(
    status_filter: str | None = Query(None, alias="status"),
    q: str | None = Query(None, description="Match the project name or key"),
    archived: bool = Query(False),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Projects with their demand roll-up, in two queries regardless of size."""
    where = ["p.archived = ?"]
    params: list[Any] = [1 if archived else 0]
    if status_filter:
        where.append("p.status = ?")
        params.append(status_filter)
    if q:
        where.append("(p.name LIKE ? OR p.key LIKE ?)")
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])

    rows = db.q(
        "SELECT p.*, u.name AS owner_name FROM projects p "
        "LEFT JOIN users u ON u.id = p.owner_id "
        f"WHERE {' AND '.join(where)} ORDER BY p.key",
        params,
    )
    counts = {row["project_id"]: row for row in db.q(_PROJECT_COUNTS_SQL)}
    items = []
    for row in rows:
        merged = dict(row)
        merged.update({k: int(v or 0) for k, v in (counts.get(row["id"]) or _ZERO_COUNTS).items()
                       if k != "project_id"})
        for key, default in _ZERO_COUNTS.items():
            merged.setdefault(key, default)
        items.append(merged)
    return {"items": items, "total": len(items)}


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    dependencies=[Depends(require_flag("multi_project"))],
)
def create_project(
    payload: ProjectCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Create a project. The key becomes the prefix of every card key it owns."""
    _require_project_write(user)
    if db.q1("SELECT id FROM projects WHERE key = ?", payload.key):
        raise Conflict(
            "project_key_taken",
            f"The project key “{payload.key}” is already in use.",
            {"key": payload.key},
        )
    if payload.owner_id and not db.q1("SELECT id FROM users WHERE id = ?", payload.owner_id):
        raise BadRequest(
            "That owner does not exist.",
            code="owner_not_found",
            details={"owner_id": payload.owner_id},
        )

    project_id = new_id()
    db.ex(
        "INSERT INTO projects (id, name, key, color, description, status, owner_id, "
        "started_at, target_date, created_at, archived) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
        project_id,
        payload.name,
        payload.key,
        payload.color,
        payload.description,
        payload.status,
        payload.owner_id,
        payload.started_at,
        payload.target_date,
        utcnow(),
    )
    row = _get_project_or_404(db, project_id)
    return {**row, **_ZERO_COUNTS}


@router.get(
    "/projects/{id}",
    response_model=ProjectDetailOut,
    summary="A project with its demand breakdown",
)
def get_project(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Project, demand counts by envelope state **and** by column segment, and its cards."""
    project = _get_project_or_404(db, id)
    owner = (
        db.q1("SELECT name FROM users WHERE id = ?", project["owner_id"])
        if project["owner_id"]
        else None
    )

    breakdown = db.q1(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN c.envelope_state = 'sealed' THEN 1 ELSE 0 END) AS sealed, "
        "SUM(CASE WHEN c.envelope_state = 'opened' THEN 1 ELSE 0 END) AS opened, "
        "SUM(CASE WHEN col.segment = 'upstream' THEN 1 ELSE 0 END) AS upstream, "
        "SUM(CASE WHEN col.segment = 'downstream' THEN 1 ELSE 0 END) AS downstream, "
        "SUM(CASE WHEN c.delivered_at IS NOT NULL THEN 1 ELSE 0 END) AS delivered, "
        "SUM(CASE WHEN c.committed_at IS NOT NULL AND c.delivered_at IS NULL THEN 1 ELSE 0 END) "
        "AS in_flight, "
        "SUM(CASE WHEN c.is_blocked = 1 THEN 1 ELSE 0 END) AS blocked "
        "FROM cards c JOIN columns col ON col.id = c.column_id "
        "WHERE c.project_id = ? AND c.archived = 0",
        project["id"],
    ) or {}
    demands = {key: int(value or 0) for key, value in breakdown.items()}

    cards = select_cards(
        db,
        project_id=project["id"],
        archived=False,
        order_by="CASE c.envelope_state WHEN 'sealed' THEN 0 ELSE 1 END, "
        "col.position, c.position, c.created_at",
    )
    return {
        **project,
        "owner_name": owner["name"] if owner else None,
        "card_count": demands.get("total", 0),
        "sealed_count": demands.get("sealed", 0),
        "opened_count": demands.get("opened", 0),
        "delivered_count": demands.get("delivered", 0),
        "in_flight_count": demands.get("in_flight", 0),
        "demands": demands,
        "cards": cards,
    }


@router.patch("/projects/{id}", response_model=ProjectOut, summary="Update a project")
def update_project(
    id: str,
    payload: ProjectUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("multi_project")),
) -> dict[str, Any]:
    """Change a project's name, colour, status, owner or dates."""
    project = _get_project_or_404(db, id)
    _require_project_write(user)
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        if fields.get("owner_id") and not db.q1(
            "SELECT id FROM users WHERE id = ?", fields["owner_id"]
        ):
            raise BadRequest(
                "That owner does not exist.",
                code="owner_not_found",
                details={"owner_id": fields["owner_id"]},
            )
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        db.ex(f"UPDATE projects SET {assignments} WHERE id = ?", values + [project["id"]])
    return {**_get_project_or_404(db, project["id"]), **_ZERO_COUNTS}


@router.delete("/projects/{id}", response_model=OkResponse, summary="Archive a project")
def delete_project(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("multi_project")),
) -> dict[str, Any]:
    """Archive a project. Its cards keep their history and their project key."""
    project = _get_project_or_404(db, id)
    _require_project_write(user)
    db.ex("UPDATE projects SET archived = 1 WHERE id = ?", project["id"])
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
def create_demand(
    id: str,
    payload: DemandCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    """Create a sealed card in the board's envelope column.

    The card is intentionally bare: no assignee, no estimate, no discussion. Its
    creation is still recorded as a movement (``from_column_id = NULL``, SPEC
    §2.7) so the envelope interval is measurable from the first second.
    """
    project = _get_project_or_404(db, id)
    board = _resolve_demand_board(db, payload.board_id)
    columns = canonical_columns(db, board["id"])
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
    with db.tx():
        db.ex(
            "INSERT INTO cards (id, board_id, column_id, project_id, key, title, description, "
            "service_class, assignee_id, requester, size, value_area, demand_type, due_date, "
            "position, envelope_state, is_blocked, tags, custom_fields, created_at, opened_at, "
            "committed_at, delivered_at, archived, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?,0,?,?,?,?,NULL,NULL,0,?)",
            card_id,
            board["id"],
            column["id"],
            project["id"],
            next_card_key(db, project["key"]),
            payload.title,
            payload.description,
            payload.service_class,
            payload.requester,
            payload.size,
            payload.value_area,
            payload.demand_type,
            payload.due_date,
            _next_position(db, column["id"]),
            "sealed" if sealed else "opened",
            json_dumps(payload.tags or []),
            json_dumps(payload.custom_fields or {}),
            now,
            None if sealed else now,
            user.get("id"),
        )
        db.ex(
            "INSERT INTO card_movements (id, card_id, board_id, from_column_id, to_column_id, "
            "from_segment, to_segment, moved_at, moved_by, seconds_in_from_column, note, "
            "is_backward, source) VALUES (?,?,?,NULL,?,NULL,?,?,?,NULL,?,0,'api')",
            new_id(),
            card_id,
            board["id"],
            column["id"],
            column["segment"],
            now,
            user.get("id"),
            "Demand captured" if sealed else "Demand captured and opened immediately",
        )
        db.ex(
            "INSERT INTO card_events (id, card_id, board_id, actor_id, event_type, field, "
            "old_value, new_value, created_at) VALUES (?,?,?,?,'created',NULL,NULL,?,?)",
            new_id(),
            card_id,
            board["id"],
            user.get("id"),
            column["name"],
            now,
        )
        if not sealed:
            db.ex(
                "INSERT INTO card_events (id, card_id, board_id, actor_id, event_type, field, "
                "old_value, new_value, created_at) "
                "VALUES (?,?,?,?,'opened_envelope','envelope_state','sealed','opened',?)",
                new_id(),
                card_id,
                board["id"],
                user.get("id"),
                now,
            )
    return _card_out(db, card_id)


@router.post(
    "/cards/{id}/open-envelope",
    response_model=MoveResult,
    summary="Open a sealed demand",
    dependencies=[Depends(require_flag("envelope"))],
)
def open_envelope(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("manage_cards")),
) -> dict[str, Any]:
    """Unseal a demand and pull it into the first real upstream column.

    Opening is a *move*, not a flag flip: it writes a ``card_movements`` row with
    the seconds the demand spent sealed, which is exactly the interval the
    envelope toggle in SPEC §7 includes or excludes from customer lead time.
    """
    card = db.q1("SELECT * FROM cards WHERE id = ?", id)
    if card is None:
        card = db.q1("SELECT * FROM cards WHERE key = ?", str(id).upper())
    if card is None:
        raise NotFound("Card", id, code="card_not_found")
    if card["envelope_state"] != "sealed":
        raise Conflict(
            "envelope_already_opened",
            "This demand has already been opened.",
            {"card_id": card["id"], "opened_at": card["opened_at"]},
        )

    board = get_board_or_404(db, card["board_id"])
    columns = canonical_columns(db, board["id"])
    source = next((c for c in columns if c["id"] == card["column_id"]), None)
    target = _first_open_column(columns)
    if source is not None and int(target["position"]) <= int(source["position"]):
        raise Conflict(
            "no_open_column",
            "There is no column after the envelope to open this demand into.",
            {"card_id": card["id"], "column_id": card["column_id"]},
        )

    wip_override = _assert_column_capacity(db, target, card["service_class"])
    now = utcnow()
    previous = db.q1(
        "SELECT moved_at FROM card_movements WHERE card_id = ? ORDER BY moved_at DESC LIMIT 1",
        card["id"],
    )
    dwell = seconds_between(previous["moved_at"], now) if previous else None
    committed = bool(target["is_commitment_point"])
    movement_id = new_id()

    with db.tx():
        db.ex(
            "UPDATE cards SET envelope_state = 'opened', opened_at = ?, column_id = ?, "
            "position = ?, committed_at = COALESCE(committed_at, ?) WHERE id = ?",
            now,
            target["id"],
            _next_position(db, target["id"]),
            now if committed else None,
            card["id"],
        )
        db.ex(
            "INSERT INTO card_movements (id, card_id, board_id, from_column_id, to_column_id, "
            "from_segment, to_segment, moved_at, moved_by, seconds_in_from_column, note, "
            "is_backward, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'api')",
            movement_id,
            card["id"],
            board["id"],
            card["column_id"],
            target["id"],
            source["segment"] if source else None,
            target["segment"],
            now,
            user.get("id"),
            int(dwell) if dwell is not None else None,
            "Envelope opened",
        )
        db.ex(
            "INSERT INTO card_events (id, card_id, board_id, actor_id, event_type, field, "
            "old_value, new_value, created_at) "
            "VALUES (?,?,?,?,'opened_envelope','envelope_state','sealed','opened',?)",
            new_id(),
            card["id"],
            board["id"],
            user.get("id"),
            now,
        )

    movement = db.q1("SELECT * FROM card_movements WHERE id = ?", movement_id)
    fresh = _card_out(db, card["id"])
    warnings: list[str] = []
    if wip_override:
        warnings.append(
            f"“{target['name']}” is at its WIP limit; the expedite class of service "
            "was allowed past it."
        )
    if target["wip_min"] is not None:
        occupancy = int(
            db.scalar(
                "SELECT COUNT(*) FROM cards WHERE column_id = ? AND archived = 0", target["id"]
            )
            or 0
        )
        if occupancy < int(target["wip_min"]):
            warnings.append(f"“{target['name']}” is starving: {occupancy} below its minimum.")

    return {
        "card": fresh,
        "movement": movement,
        "wip_override": wip_override,
        "warnings": warnings,
        "wip": board_wip_status(db, board, columns=columns),
        "committed": committed,
        "delivered": bool(target["is_delivery_point"]),
    }
