"""Card movement, history and audit — SPEC §6.

Every rule in SPEC §6 is enforced here, in one transaction per move:

1. forward-only against the **unified** column ordering (upstream before
   downstream), backward rejected with ``backward_move_blocked``;
2. ``force`` by a master/administrator records ``is_backward = 1`` plus an audit
   event;
3. entering the commitment column stamps ``committed_at`` — once, immutably;
4. entering the delivery column stamps ``delivered_at`` — once, immutably;
5. a sealed envelope card cannot move at all;
6. column, group and whole-flow WIP maxima are checked *inside* the same
   transaction as the insert, so two simultaneous moves can never both slip past
   the same limit; ``expedite`` bypasses the **column** maximum only;
7. WIP minima never block — they surface as ``starving`` warnings;
8. the ``card_movements`` row carries ``seconds_in_from_column``, measured from
   the previous movement (or from card creation for the first move).

The movement log is the substrate for every metric in the product: CFD, lead
time, flow efficiency, aging and the Monte Carlo simulator all replay these
rows. A missing or wrong row is not a cosmetic bug, it is a wrong forecast.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from ..db import Database, new_id, seconds_between, utcnow
from ..deps import CurrentUser, get_current_user, get_db, require_flag
from ..errors import BadRequest, Conflict, NotFound
from ..schemas import MovementSource, MoveRequest, MoveResult, OpenSchema
from .cards import (
    board_columns,
    card_payload,
    column_order,
    commitment_column,
    delivery_column,
    dwell_intervals,
    fetch_card,
    flow_totals,
    load_board,
    record_event,
    require_card_write,
    wip_status,
)

router = APIRouter(tags=["movements"])

__all__ = ["router", "move_card", "MoveBody"]

#: Roles allowed to force a backward move (SPEC §6.2).
FORCE_ROLES = frozenset({"master", "administrator"})


class MoveBody(MoveRequest):
    """``POST /cards/{id}/move`` body.

    Extends the shared :class:`MoveRequest` with the provenance of the move so
    that the MCP server and the simulator can label their rows correctly; the
    UI never needs to send it.
    """

    source: MovementSource | None = None


class HistoryOut(OpenSchema):
    """``GET /cards/{id}/history`` — movements and events merged into one story."""

    card_id: str
    movements: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    dwell: list[dict[str, Any]] = []
    totals: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def humanize_seconds(seconds: float | None) -> str | None:
    """Render a duration the way a person reads a board: ``3d 4h``, ``45m``."""
    if seconds is None:
        return None
    total = int(max(0.0, float(seconds)))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _quote(value: Any) -> str:
    return "—" if value in (None, "") else f"“{value}”"


# ---------------------------------------------------------------------------
# WIP policy evaluation for one move
# ---------------------------------------------------------------------------


def _breaches(current: int, projected: int, maximum: int | None) -> bool:
    """Would this move push a scope past its maximum?

    A move that does not increase the count in a scope never breaches it: a card
    shuffled inside a group that is already over its limit is not making things
    worse, and blocking it would trap work in an over-loaded group forever.
    """
    return maximum is not None and projected > maximum and projected > current


def _column_count(db: Database, column_id: str) -> int:
    return int(
        db.scalar(
            "SELECT COUNT(*) FROM cards WHERE column_id = ? AND archived = 0", column_id
        )
        or 0
    )


def _group_count(db: Database, board_id: str, group_id: str) -> int:
    return int(
        db.scalar(
            "SELECT COUNT(*) FROM cards c JOIN columns col ON col.id = c.column_id "
            "WHERE col.board_id = ? AND col.group_id = ? AND c.archived = 0",
            board_id,
            group_id,
        )
        or 0
    )


def _flow_count(db: Database, board_id: str) -> int:
    """WIP as SPEC §7.1 defines it: committed and not yet delivered."""
    return int(
        db.scalar(
            "SELECT COUNT(*) FROM cards WHERE board_id = ? AND archived = 0 "
            "AND committed_at IS NOT NULL AND delivered_at IS NULL",
            board_id,
        )
        or 0
    )


def _wip_conflict(policy: str, name: str, current: int, maximum: int, extra: dict[str, Any]) -> Conflict:
    return Conflict(
        "wip_limit_exceeded",
        f"The {policy} WIP limit on {_quote(name)} is full: {current} of {maximum} "
        f"— finish something before starting something.",
        {
            "policy": policy,
            "name": name,
            "current": current,
            "max": maximum,
            "projected": current + 1,
            **extra,
        },
    )


# ---------------------------------------------------------------------------
# the move itself
# ---------------------------------------------------------------------------


def move_card(
    db: Database,
    user: CurrentUser,
    card_ref: str,
    to_column_id: str,
    *,
    note: str | None = None,
    force: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    """Move one card forward through the flow, enforcing every SPEC §6 rule.

    Returns the :class:`~backend.app.schemas.MoveResult` payload: the updated
    card, the movement row that was written, the recomputed WIP status for the
    board, whether an expedite override was used, and any advisory warnings.
    """
    card = fetch_card(db, card_ref)
    board = require_card_write(db, user, card)
    columns = board_columns(db, board["id"])
    by_id = {column["id"]: column for column in columns}
    order = column_order(columns)

    target = by_id.get(to_column_id)
    if target is None:
        exists = db.q1("SELECT id, board_id FROM columns WHERE id = ?", to_column_id)
        if exists is None:
            raise NotFound("Column", to_column_id, code="column_not_found")
        raise BadRequest(
            "That column belongs to a different board.",
            code="column_board_mismatch",
            details={"board_id": board["id"], "column_id": to_column_id},
        )

    origin = by_id.get(card["column_id"])
    if origin is None:  # pragma: no cover - foreign keys make this unreachable
        raise BadRequest(
            "This card is not sitting in a column of its board.",
            code="card_column_mismatch",
            details={"card_id": card["id"], "column_id": card["column_id"]},
        )

    if card.get("archived"):
        raise Conflict(
            "card_archived",
            f"{card['key']} is archived; restore it before moving it.",
            {"card_id": card["id"]},
        )
    if card.get("envelope_state") == "sealed":
        raise Conflict(
            "envelope_sealed",
            f"{card['key']} is still a sealed envelope — open it before moving it.",
            {"card_id": card["id"], "envelope_state": "sealed"},
        )
    if target["id"] == origin["id"]:
        raise Conflict(
            "same_column_move",
            f"{card['key']} is already in {_quote(origin['name'])}.",
            {"card_id": card["id"], "column_id": origin["id"]},
        )

    is_backward = order[target["id"]] < order[origin["id"]]
    if is_backward and not (force and user.get("role") in FORCE_ROLES):
        raise Conflict(
            "backward_move_blocked",
            f"Cards flow forward only: {_quote(origin['name'])} sits after "
            f"{_quote(target['name'])} in this value stream.",
            {
                "card_id": card["id"],
                "from_column_id": origin["id"],
                "from_column": origin["name"],
                "to_column_id": target["id"],
                "to_column": target["name"],
                "from_position": order[origin["id"]],
                "to_position": order[target["id"]],
                "force_allowed_for": sorted(FORCE_ROLES),
                "forced": bool(force),
            },
        )

    commitment = commitment_column(columns)
    delivery = delivery_column(columns)
    now = utcnow()

    # Commitment is crossing the point of no return, not merely touching one
    # particular column: a card pushed straight from the replenishment queue
    # into Build is committed too, and metrics that assume otherwise silently
    # lose it. Delivery, by contrast, is exactly the terminal column.
    will_commit = (
        not card.get("committed_at")
        and commitment is not None
        and order[target["id"]] >= order[commitment["id"]]
    )
    will_deliver = (
        not card.get("delivered_at")
        and delivery is not None
        and target["id"] == delivery["id"]
    )

    expedite = card.get("service_class") == "expedite"
    wip_override = False
    warnings: list[str] = []

    with db.tx():
        # Re-read inside the write transaction: the WIP check and the insert
        # have to see the same snapshot, otherwise two moves can both find room
        # for the last slot in a column.
        fresh = db.q1("SELECT * FROM cards WHERE id = ?", card["id"])
        if fresh is None:  # pragma: no cover - deleted between the reads
            raise NotFound("Card", card["id"], code="card_not_found")
        if fresh["column_id"] != origin["id"]:
            raise Conflict(
                "card_moved_elsewhere",
                f"{card['key']} was moved by someone else while you were deciding.",
                {"card_id": card["id"], "column_id": fresh["column_id"]},
            )

        # -- column policy (expedite may bypass this one only) --------------
        column_current = _column_count(db, target["id"])
        if _breaches(column_current, column_current + 1, target.get("wip_max")):
            if not expedite:
                raise _wip_conflict(
                    "column",
                    target["name"],
                    column_current,
                    int(target["wip_max"]),
                    {"column_id": target["id"], "service_class": card.get("service_class")},
                )
            wip_override = True
            warnings.append(
                f"Expedite override: {_quote(target['name'])} is at its WIP maximum "
                f"({column_current}/{target['wip_max']}) and now holds "
                f"{column_current + 1}."
            )

        # -- group policy ---------------------------------------------------
        group_id = target.get("group_id")
        if group_id:
            group = db.q1("SELECT * FROM column_groups WHERE id = ?", group_id)
            if group is not None and group.get("wip_max") is not None:
                group_current = _group_count(db, board["id"], group_id)
                projected = group_current + (0 if origin.get("group_id") == group_id else 1)
                if _breaches(group_current, projected, group.get("wip_max")):
                    raise _wip_conflict(
                        "group",
                        group["name"],
                        group_current,
                        int(group["wip_max"]),
                        {"group_id": group_id, "column_id": target["id"]},
                    )

        # -- whole-flow policy ----------------------------------------------
        if board.get("wip_max") is not None:
            was_in_flight = bool(fresh["committed_at"]) and not fresh["delivered_at"]
            will_be_in_flight = bool(fresh["committed_at"] or will_commit) and not (
                fresh["delivered_at"] or will_deliver
            )
            flow_current = _flow_count(db, board["id"])
            projected = flow_current + (int(will_be_in_flight) - int(was_in_flight))
            if _breaches(flow_current, projected, board.get("wip_max")):
                raise _wip_conflict(
                    "flow",
                    board["name"],
                    flow_current,
                    int(board["wip_max"]),
                    {"board_id": board["id"], "column_id": target["id"]},
                )

        # -- measure the interval that is closing now ------------------------
        previous = db.q1(
            "SELECT moved_at FROM card_movements WHERE card_id = ? "
            "ORDER BY moved_at DESC, rowid DESC LIMIT 1",
            card["id"],
        )
        since = previous["moved_at"] if previous else fresh["created_at"]
        seconds_in_from_column = seconds_between(since, now)
        seconds_in_from_column = (
            None if seconds_in_from_column is None else int(round(max(0.0, seconds_in_from_column)))
        )

        movement_id = new_id()
        db.ex(
            "INSERT INTO card_movements (id, card_id, board_id, from_column_id, to_column_id, "
            "from_segment, to_segment, moved_at, moved_by, seconds_in_from_column, note, "
            "is_backward, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            movement_id,
            card["id"],
            board["id"],
            origin["id"],
            target["id"],
            origin["segment"],
            target["segment"],
            now,
            user["id"],
            seconds_in_from_column,
            note,
            1 if is_backward else 0,
            source or ("api" if user.get("role") == "system" else "ui"),
        )

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
        db.ex(
            "UPDATE cards SET column_id = ?, position = ?, "
            "committed_at = COALESCE(committed_at, ?), "
            "delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
            target["id"],
            position,
            now if will_commit else None,
            now if will_deliver else None,
            card["id"],
        )

        record_event(
            db,
            card,
            "moved",
            actor_id=user["id"],
            field="column",
            old_value=origin["name"],
            new_value=target["name"],
            at=now,
        )
        if is_backward:
            # SPEC §6.2 — a forced backward move is an exception to the flow
            # policy and must be visible as such in the audit trail.
            record_event(
                db,
                card,
                "moved",
                actor_id=user["id"],
                field="backward_override",
                old_value=origin["name"],
                new_value=target["name"],
                at=now,
            )
        if will_commit:
            record_event(
                db, card, "updated", actor_id=user["id"], field="committed_at",
                new_value=now, at=now,
            )
        if will_deliver:
            record_event(
                db, card, "updated", actor_id=user["id"], field="delivered_at",
                new_value=now, at=now,
            )

    # -- post-move advisory signals -----------------------------------------
    status = wip_status(db, board, columns)
    for column in (origin, target):
        cell = status["columns"].get(column["id"], {})
        if cell.get("state") == "under":
            warnings.append(
                f"{_quote(column['name'])} is starving: {cell['count']} in progress "
                f"against a WIP minimum of {cell['min']}."
            )
        elif cell.get("state") == "over" and column["id"] == target["id"] and not wip_override:
            warnings.append(
                f"{_quote(column['name'])} is over its WIP maximum "
                f"({cell['count']}/{cell['max']})."
            )
    flow_cell = status["flow"]
    if flow_cell.get("state") == "over":
        warnings.append(
            f"The whole flow is over its WIP maximum ({flow_cell['count']}/{flow_cell['max']})."
        )
    elif flow_cell.get("state") == "under":
        warnings.append(
            f"The flow is starving: {flow_cell['count']} in flight against a minimum of "
            f"{flow_cell['min']}."
        )
    if is_backward:
        warnings.append(
            f"Backward move recorded against flow policy and flagged in the audit trail "
            f"({origin['name']} → {target['name']})."
        )
    if card.get("is_blocked"):
        warnings.append(f"{card['key']} is still blocked; the blocker was carried over.")
    if will_deliver:
        open_tasks = int(
            db.scalar(
                "SELECT COUNT(*) FROM card_tasks WHERE card_id = ? AND is_done = 0", card["id"]
            )
            or 0
        )
        if open_tasks:
            warnings.append(
                f"Delivered with {open_tasks} task{'s' if open_tasks > 1 else ''} still open."
            )

    updated = fetch_card(db, card["id"])
    movement = db.q1(
        "SELECT m.*, fc.name AS from_column_name, tc.name AS to_column_name, "
        "u.name AS moved_by_name FROM card_movements m "
        "LEFT JOIN columns fc ON fc.id = m.from_column_id "
        "LEFT JOIN columns tc ON tc.id = m.to_column_id "
        "LEFT JOIN users u ON u.id = m.moved_by WHERE m.id = ?",
        movement_id,
    )
    payload = dict(movement or {})
    payload["is_backward"] = bool(payload.get("is_backward"))

    return {
        "card": card_payload(db, updated, now=now, board=board),
        "movement": payload,
        "wip_override": wip_override,
        "warnings": warnings,
        "wip": status,
        "committed": bool(will_commit),
        "delivered": bool(will_deliver),
        "from_column": {"id": origin["id"], "name": origin["name"], "kind": origin["kind"]},
        "to_column": {"id": target["id"], "name": target["name"], "kind": target["kind"]},
        "seconds_in_from_column": seconds_in_from_column,
        "duration_in_from_column": humanize_seconds(seconds_in_from_column),
        "is_backward": bool(is_backward),
    }


@router.post(
    "/cards/{card_id}/move",
    response_model=MoveResult,
    summary="Move a card forward through the flow (SPEC §6)",
)
def move(
    card_id: str,
    payload: MoveBody,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Move a card and return the card, the movement, the new WIP and warnings.

    Confirmation dialogs are a frontend concern driven by ``board.confirm_moves``
    (SPEC §6.9); this endpoint never asks, it decides.
    """
    return move_card(
        db,
        user,
        card_id,
        payload.to_column_id,
        note=payload.note,
        force=payload.force,
        source=payload.source,
    )


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

#: Sort rank for entries that share a timestamp: a card is created, then moved,
#: and only afterwards edited.
_RANK = {"created": 0, "movement": 1}


def _event_label(event: dict[str, Any]) -> str:
    """A sentence a human can read in the drawer, for one audit row."""
    field = event.get("field")
    old, new = event.get("old_value"), event.get("new_value")
    kind = event["event_type"]
    if kind == "created":
        return f"Created in {_quote(new)}" if new else "Created"
    if kind == "moved":
        if field == "backward_override":
            return f"Backward move forced from {_quote(old)} to {_quote(new)}"
        return f"Moved from {_quote(old)} to {_quote(new)}"
    if kind == "blocked":
        return f"Blocked — {new}" if new else "Blocked"
    if kind == "unblocked":
        return f"Unblocked — {old}" if old else "Unblocked"
    if kind == "commented":
        return "Commented"
    if kind == "task_added":
        return f"Task added: {new}"
    if kind == "task_completed":
        return f"Task completed: {new}"
    if kind == "opened_envelope":
        return "Envelope opened"
    if kind == "archived":
        return "Archived"
    label = (field or "field").replace("_", " ")
    if old in (None, "") and new not in (None, ""):
        return f"Set {label} to {_quote(new)}"
    if new in (None, "") and old not in (None, ""):
        return f"Cleared {label} (was {_quote(old)})"
    return f"Changed {label} from {_quote(old)} to {_quote(new)}"


def _movement_label(
    movement: dict[str, Any], closed_seconds: float | None, closed_column: str | None
) -> str:
    """A sentence for one movement row, including how long the card waited."""
    if movement.get("from_column_id") is None:
        return f"Entered the board in {_quote(movement.get('to_column_name'))}"
    duration = humanize_seconds(closed_seconds)
    tail = f" after {duration} in {_quote(closed_column)}" if duration and closed_column else ""
    direction = "Pulled back" if movement.get("is_backward") else "Moved"
    return (
        f"{direction} from {_quote(movement.get('from_column_name'))} to "
        f"{_quote(movement.get('to_column_name'))}{tail}"
    )


@router.get(
    "/cards/{card_id}/history",
    response_model=HistoryOut,
    summary="Merged movement + audit history with dwell times and touch/wait totals",
    dependencies=[Depends(require_flag("card_history"))],
)
def card_history(
    card_id: str,
    order: Literal["asc", "desc"] = Query("asc"),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The card's whole story, in the shape the drawer's history tab renders.

    Movements and events are merged into one chronological ``timeline``; each
    entry carries the actor's name, a human-readable label and the running
    touch/wait totals at that point, so the reader can see flow efficiency
    decaying (or not) as the card travels. ``dwell`` aggregates the time spent
    per column, including the still-open interval the card is sitting in.
    """
    card = fetch_card(db, card_id)
    board = load_board(db, card["board_id"])
    columns = board_columns(db, board["id"])
    by_id = {column["id"]: column for column in columns}
    now = utcnow()

    movements = db.q(
        "SELECT m.*, fc.name AS from_column_name, tc.name AS to_column_name, "
        "u.name AS moved_by_name FROM card_movements m "
        "LEFT JOIN columns fc ON fc.id = m.from_column_id "
        "LEFT JOIN columns tc ON tc.id = m.to_column_id "
        "LEFT JOIN users u ON u.id = m.moved_by "
        "WHERE m.card_id = ? ORDER BY m.moved_at, m.rowid",
        card["id"],
    )
    events = db.q(
        "SELECT e.*, u.name AS actor_name FROM card_events e "
        "LEFT JOIN users u ON u.id = e.actor_id "
        "WHERE e.card_id = ? ORDER BY e.created_at, e.rowid",
        card["id"],
    )
    intervals = dwell_intervals(db, card, by_id, now)

    # -- per-column dwell ---------------------------------------------------
    dwell: dict[str, dict[str, Any]] = {}
    for interval in intervals:
        entry = dwell.setdefault(
            interval["column_id"],
            {
                "column_id": interval["column_id"],
                "column_name": interval["column_name"],
                "column_kind": interval["column_kind"],
                "segment": interval["segment"],
                "seconds": 0.0,
                "entries": 0,
                "is_current": False,
            },
        )
        entry["seconds"] += float(interval["seconds"])
        entry["entries"] += 1
        entry["is_current"] = entry["is_current"] or interval["is_open"]
    dwell_rows = sorted(
        dwell.values(),
        key=lambda row: by_id.get(row["column_id"], {}).get("position", 0),
    )
    for row in dwell_rows:
        row["seconds"] = round(row["seconds"], 3)
        row["days"] = round(row["seconds"] / 86400.0, 2)
        row["duration"] = humanize_seconds(row["seconds"])

    # -- merged timeline with running touch/wait ----------------------------
    entries: list[dict[str, Any]] = []
    for index, movement in enumerate(movements):
        closing = intervals[index - 1] if index > 0 and index - 1 < len(intervals) else None
        entries.append(
            {
                "id": movement["id"],
                "at": movement["moved_at"],
                "kind": "movement",
                "type": "created" if movement["from_column_id"] is None else "moved",
                "actor_id": movement.get("moved_by"),
                "actor_name": movement.get("moved_by_name"),
                "label": _movement_label(
                    movement,
                    None if closing is None else closing["seconds"],
                    None if closing is None else closing["column_name"],
                ),
                "note": movement.get("note"),
                "from_column_id": movement.get("from_column_id"),
                "from_column_name": movement.get("from_column_name"),
                "to_column_id": movement["to_column_id"],
                "to_column_name": movement.get("to_column_name"),
                "is_backward": bool(movement.get("is_backward")),
                "source": movement.get("source"),
                "seconds_in_from_column": movement.get("seconds_in_from_column"),
                "duration_in_from_column": humanize_seconds(
                    movement.get("seconds_in_from_column")
                ),
                "_closes": closing,
                "_rank": _RANK["movement"],
            }
        )
    for event in events:
        entries.append(
            {
                "id": event["id"],
                "at": event["created_at"],
                "kind": "event",
                "type": event["event_type"],
                "actor_id": event.get("actor_id"),
                "actor_name": event.get("actor_name"),
                "label": _event_label(event),
                "field": event.get("field"),
                "old_value": event.get("old_value"),
                "new_value": event.get("new_value"),
                "_closes": None,
                "_rank": _RANK.get(event["event_type"], 2),
            }
        )
    entries.sort(key=lambda entry: (entry["at"], entry["_rank"], str(entry["id"])))

    touch = wait = envelope = 0.0
    for entry in entries:
        closing = entry.pop("_closes", None)
        entry.pop("_rank", None)
        if closing is not None:
            kind = closing.get("column_kind")
            seconds = float(closing.get("seconds") or 0.0)
            if kind == "activity":
                touch += seconds
            elif kind == "queue":
                wait += seconds
            elif kind == "envelope":
                envelope += seconds
        entry["touch_seconds_total"] = round(touch, 3)
        entry["wait_seconds_total"] = round(wait, 3)
        entry["envelope_seconds_total"] = round(envelope, 3)
        entry["flow_efficiency_pct"] = (
            round(touch / (touch + wait) * 100.0, 2) if (touch + wait) > 0 else None
        )
    if order == "desc":
        entries.reverse()

    totals = flow_totals(card, intervals)
    totals.update(
        {
            "moves": len([m for m in movements if m["from_column_id"] is not None]),
            "backward_moves": len([m for m in movements if m.get("is_backward")]),
            "events": len(events),
            "columns_visited": len(dwell_rows),
            "total_seconds": round(sum(row["seconds"] for row in dwell_rows), 3),
            "touch_duration": humanize_seconds(totals["touch_seconds"]),
            "wait_duration": humanize_seconds(totals["wait_seconds"]),
        }
    )

    return {
        "card_id": card["id"],
        "card_key": card["key"],
        "board_id": board["id"],
        "board_name": board["name"],
        "movements": [
            {**dict(movement), "is_backward": bool(movement.get("is_backward"))}
            for movement in movements
        ],
        "events": [dict(event) for event in events],
        "timeline": entries,
        "dwell": dwell_rows,
        "totals": totals,
        "generated_at": now,
    }


@router.get(
    "/cards/{card_id}/movements",
    summary="Raw movement rows for one card, chronological",
)
def card_movements(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The unmerged movement log — what every metric replays."""
    card = fetch_card(db, card_id)
    rows = db.q(
        "SELECT m.*, fc.name AS from_column_name, tc.name AS to_column_name, "
        "u.name AS moved_by_name FROM card_movements m "
        "LEFT JOIN columns fc ON fc.id = m.from_column_id "
        "LEFT JOIN columns tc ON tc.id = m.to_column_id "
        "LEFT JOIN users u ON u.id = m.moved_by "
        "WHERE m.card_id = ? ORDER BY m.moved_at, m.rowid",
        card["id"],
    )
    items = []
    for row in rows:
        item = dict(row)
        item["is_backward"] = bool(row.get("is_backward"))
        item["duration_in_from_column"] = humanize_seconds(row.get("seconds_in_from_column"))
        items.append(item)
    return {"items": items, "total": len(items)}


@router.get(
    "/cards/{card_id}/events",
    summary="Raw audit events for one card, chronological",
    dependencies=[Depends(require_flag("card_history"))],
)
def card_events(
    card_id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    """The field-level audit trail, each row with its actor and a label."""
    card = fetch_card(db, card_id)
    rows = db.q(
        "SELECT e.*, u.name AS actor_name FROM card_events e "
        "LEFT JOIN users u ON u.id = e.actor_id "
        "WHERE e.card_id = ? ORDER BY e.created_at, e.rowid",
        card["id"],
    )
    items = [{**dict(row), "label": _event_label(row)} for row in rows]
    return {"items": items, "total": len(items)}
