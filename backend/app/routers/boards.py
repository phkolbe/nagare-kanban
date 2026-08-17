"""Boards, columns, groups and WIP policies (SPEC §5 "Boards, columns, groups").

The board is the product's centre of gravity, so this module owns three things
that everything else depends on:

**The canonical column order.** A board's columns are one continuous flow —
every ``upstream`` column, then every ``downstream`` column — with dense
``position`` values ``0..n-1``. Every mutation funnels through
:func:`normalize_structure`, which rewrites the order, re-derives the commitment
and delivery points and re-validates group contiguity in one place. Callers can
never leave the board in a state where "forward only" (SPEC §6.1) is ambiguous.

**The derived points.** ``is_commitment_point`` is *not* an editable flag: it is
the first downstream column, recomputed whenever columns are added, removed,
reordered or moved between segments. ``is_delivery_point`` is the last ``done``
column. Storing them keeps the metrics SQL simple; deriving them keeps them true.

**WIP accounting.** Column and group cells report *literal occupancy* — what a
person standing at the board would count. The whole-flow cell reports committed
and not yet delivered work, which is the WIP that Little's Law talks about
(SPEC §7.1). Those two definitions genuinely differ (upstream and sealed demand
is on the board but not in the system), and conflating them is the classic way to
produce a flow dashboard that disagrees with itself.
"""

from __future__ import annotations

import re
from typing import Any, Sequence

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field, field_validator

from ..db import Database, new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    get_board_or_404,
    get_db,
    is_flag_effective,
    require_capability,
    require_flag,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..schemas import (
    BoardCloneRequest,
    BoardCreate,
    BoardOut,
    BoardUpdate,
    BoardViewOut,
    ColumnCreate,
    ColumnOut,
    ColumnReorderRequest,
    ColumnUpdate,
    GroupCreate,
    GroupOut,
    GroupUpdate,
    ListResponse,
    OkResponse,
    SegmentQuery,
    WipStatusOut,
)

router = APIRouter(tags=["boards"])

__all__ = [
    "router",
    "TEMPLATES",
    "board_wip_status",
    "canonical_columns",
    "normalize_structure",
    "wip_state",
    "select_cards",
    "decorate_card",
    "KeyAllocator",
    "next_card_key",
    "require_structure_write",
]


# ---------------------------------------------------------------------------
# board templates (SPEC §5 `POST /boards {template}`)
# ---------------------------------------------------------------------------

#: column tuple = (name, segment, kind, wip_min, wip_max, group, description)
_ColumnSpec = tuple[str, str, str, int | None, int | None, str | None, str]
#: group tuple = (name, wip_min, wip_max, color)
_GroupSpec = tuple[str, int | None, int | None, str]

TEMPLATES: dict[str, dict[str, Any]] = {
    "upstream_downstream": {
        "label": "Upstream & downstream",
        "description": (
            "The full value stream: discovery upstream, delivery downstream, with an "
            "explicit commitment point and a sealed-envelope intake."
        ),
        "board": {"wip_min": 10, "wip_max": 45, "sle_days": 25, "sle_percentile": 85},
        "groups": [
            ("Discovery", 6, 24, "#7d8ba1"),
            ("Delivery", 12, 40, "#3b7ea1"),
        ],
        "columns": [
            ("Envelope", "upstream", "envelope", None, None, None,
             "Demand captured but deliberately not yet examined. Sealed until someone opens it."),
            ("Ideas", "upstream", "queue", 3, 14, "Discovery",
             "Raw options waiting to be looked at. Pure queue time."),
            ("Discovery", "upstream", "activity", 2, 6, "Discovery",
             "Active problem framing: who wants this, what changes, what is the risk."),
            ("Options", "upstream", "queue", 2, 10, "Discovery",
             "Understood, shaped, and deferred until the last responsible moment."),
            ("Ready to Commit", "upstream", "queue", 5, 8, "Discovery",
             "Replenishment buffer feeding the commitment point."),
            ("Analysis", "downstream", "activity", 2, 6, "Delivery",
             "The commitment point. Once work enters here the team owes a delivery."),
            ("Analysis Done", "downstream", "queue", None, 6, "Delivery",
             "Analysed and waiting for build capacity."),
            ("Build", "downstream", "activity", 3, 8, "Delivery",
             "Implementation in progress."),
            ("Build Done", "downstream", "queue", None, 6, "Delivery",
             "Built and waiting for verification capacity."),
            ("Verify", "downstream", "activity", 2, 5, "Delivery",
             "Review, test and acceptance."),
            ("Ready to Deploy", "downstream", "queue", None, 10, "Delivery",
             "Verified and waiting on the release train."),
            ("Delivered", "downstream", "done", None, None, None,
             "Delivered to the customer. The delivery point."),
        ],
    },
    "simple_delivery": {
        "label": "Simple delivery",
        "description": "One backlog, one commitment point, three delivery states.",
        "board": {"wip_min": 2, "wip_max": 12, "sle_days": 14, "sle_percentile": 85},
        "groups": [("Delivery", 2, 12, "#3b7ea1")],
        "columns": [
            ("Backlog", "upstream", "queue", None, None, None,
             "Everything requested and not yet committed to. No promise has been made."),
            ("In Progress", "downstream", "activity", 1, 5, "Delivery",
             "The commitment point. Work here is owed to someone."),
            ("Review", "downstream", "queue", None, 3, "Delivery",
             "Waiting for review and acceptance."),
            ("Done", "downstream", "done", None, None, None,
             "Delivered. The delivery point."),
        ],
    },
    "discovery_only": {
        "label": "Discovery only",
        "description": (
            "An upstream board: capture demand, shape options and hand committed work "
            "to a separate delivery board."
        ),
        "board": {"wip_min": 4, "wip_max": 24, "sle_days": 30, "sle_percentile": 85},
        "groups": [("Discovery", 4, 24, "#7d8ba1")],
        "columns": [
            ("Envelope", "upstream", "envelope", None, None, None,
             "Demand captured but deliberately not yet examined. Sealed until someone opens it."),
            ("Ideas", "upstream", "queue", 3, 12, "Discovery",
             "Raw options waiting to be looked at."),
            ("Discovery", "upstream", "activity", 2, 5, "Discovery",
             "Active problem framing and option shaping."),
            ("Options", "upstream", "queue", None, 10, "Discovery",
             "Shaped and deferred until the last responsible moment."),
            ("Ready to Commit", "upstream", "queue", 3, 8, "Discovery",
             "Replenishment buffer feeding the commitment point."),
            ("Committed", "downstream", "done", None, None, None,
             "Handed over to delivery. Both the commitment point and the terminal state "
             "of a discovery-only board."),
        ],
    },
    "blank": {
        "label": "Blank",
        "description": "No columns. Build the flow by hand.",
        "board": {"wip_min": None, "wip_max": None, "sle_days": 14, "sle_percentile": 85},
        "groups": [],
        "columns": [],
    },
}

#: Friendly aliases, including the three names `schemas.BoardCreate` declares.
TEMPLATE_ALIASES: dict[str, str] = {
    "default": "upstream_downstream",
    "full": "upstream_downstream",
    "unified": "upstream_downstream",
    "simple": "simple_delivery",
    "delivery": "simple_delivery",
    "discovery": "discovery_only",
    "upstream": "discovery_only",
    "empty": "blank",
    "none": "blank",
}


def resolve_template(value: str | None) -> str:
    """Map any accepted template alias onto a key of :data:`TEMPLATES`."""
    if value is None:
        return "blank"
    key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    key = TEMPLATE_ALIASES.get(key, key)
    if key not in TEMPLATES:
        raise ValueError(
            "Unknown board template. Choose one of: " + ", ".join(sorted(TEMPLATES))
        )
    return key


class BoardCreateRequest(BoardCreate):
    """``POST /boards`` body.

    ``schemas.BoardCreate`` predates the four named templates in SPEC §5, so the
    field is widened here and normalised through :func:`resolve_template`; the
    older ``default`` / ``simple`` / ``blank`` values keep working as aliases.
    """

    template: str | None = "upstream_downstream"

    @field_validator("template")
    @classmethod
    def _normalise_template(cls, value: str | None) -> str:
        return resolve_template(value)


class GroupWriteRequest(GroupCreate):
    """``POST /boards/{id}/groups`` — a group plus the columns it spans."""

    column_ids: list[str] | None = Field(default=None)


class GroupPatchRequest(GroupUpdate):
    """``PATCH /groups/{id}`` — ``column_ids`` replaces the span when present."""

    column_ids: list[str] | None = Field(default=None)


# ---------------------------------------------------------------------------
# structural helpers
# ---------------------------------------------------------------------------

SEGMENT_RANK: dict[str, int] = {"upstream": 0, "downstream": 1}
STRUCTURE_ROLES: frozenset[str] = frozenset({"master", "administrator"})


def require_structure_write(user: dict[str, Any], board: dict[str, Any]) -> None:
    """SPEC §4: only master/administrator shape a board — plus ``simulator`` on its own boards."""
    role = user.get("role")
    if role in STRUCTURE_ROLES:
        return
    if role == "simulator" and board.get("kind") == "simulator":
        return
    raise Forbidden(
        "Your role cannot change this board's structure.",
        code="insufficient_role",
        details={
            "role": role,
            "board_kind": board.get("kind"),
            "required": ["master", "administrator"],
        },
    )


def _column_sort_key(column: dict[str, Any]) -> tuple[int, int, int, str]:
    """Canonical flow order: upstream before downstream, envelope always first."""
    return (
        SEGMENT_RANK.get(column.get("segment"), 1),
        0 if column.get("kind") == "envelope" else 1,
        int(column.get("position") or 0),
        str(column.get("name") or ""),
    )


def canonical_columns(db: Database, board_id: str) -> list[dict[str, Any]]:
    """Every column of a board in flow order (does not write)."""
    rows = db.q("SELECT * FROM columns WHERE board_id = ?", board_id)
    return sorted(rows, key=_column_sort_key)


def _assert_order_valid(ordered: Sequence[dict[str, Any]]) -> None:
    """Reject an ordering that would break a structural invariant."""
    seen_downstream = False
    for index, column in enumerate(ordered):
        if column["segment"] == "downstream":
            seen_downstream = True
        elif seen_downstream:
            raise Conflict(
                "segment_order_violation",
                "Upstream columns must all come before downstream columns.",
                {"column_id": column["id"], "name": column["name"]},
            )
        if column["kind"] == "envelope":
            if column["segment"] != "upstream":
                raise Conflict(
                    "envelope_must_be_upstream",
                    "An envelope column belongs to the upstream segment.",
                    {"column_id": column["id"]},
                )
            if index != 0:
                raise Conflict(
                    "envelope_must_be_first",
                    "An envelope column may only be the first upstream column.",
                    {"column_id": column["id"], "requested_position": index},
                )


def _sync_group_positions(db: Database, board_id: str, ordered: Sequence[dict[str, Any]]) -> None:
    """Order group headers by the first column each one spans."""
    first_index: dict[str, int] = {}
    for index, column in enumerate(ordered):
        group_id = column.get("group_id")
        if group_id and group_id not in first_index:
            first_index[group_id] = index
    groups = db.q(
        "SELECT id, position FROM column_groups WHERE board_id = ? ORDER BY position, rowid",
        board_id,
    )
    # Groups that currently span no column keep their relative order at the end.
    ranked = sorted(
        groups,
        key=lambda g: (first_index.get(g["id"], len(ordered) + int(g["position"] or 0))),
    )
    for position, group in enumerate(ranked):
        if int(group["position"] or 0) != position:
            db.ex("UPDATE column_groups SET position = ? WHERE id = ?", position, group["id"])


def assert_groups_contiguous(board_id: str, ordered: Sequence[dict[str, Any]]) -> None:
    """A group must span **consecutive** columns (SPEC §9.2 group headers)."""
    runs: dict[str, list[int]] = {}
    for index, column in enumerate(ordered):
        group_id = column.get("group_id")
        if group_id:
            runs.setdefault(group_id, []).append(index)
    for group_id, indexes in runs.items():
        if indexes[-1] - indexes[0] != len(indexes) - 1:
            raise Conflict(
                "group_not_contiguous",
                "A column group must span consecutive columns in the flow.",
                {
                    "board_id": board_id,
                    "group_id": group_id,
                    "positions": indexes,
                    "column_ids": [ordered[i]["id"] for i in indexes],
                },
            )


def recompute_points(
    db: Database, board_id: str, ordered: Sequence[dict[str, Any]]
) -> tuple[str | None, str | None]:
    """Re-derive the commitment and delivery points; returns their column ids.

    Commitment point = the first downstream column (SPEC §2.4).
    Delivery point   = the last ``done`` column.
    """
    commitment_id = next((c["id"] for c in ordered if c["segment"] == "downstream"), None)
    delivery_id = next((c["id"] for c in reversed(ordered) if c["kind"] == "done"), None)

    db.ex(
        "UPDATE columns SET is_commitment_point = CASE WHEN id = ? THEN 1 ELSE 0 END "
        "WHERE board_id = ?",
        commitment_id,
        board_id,
    )
    db.ex(
        "UPDATE columns SET is_delivery_point = CASE WHEN id = ? THEN 1 ELSE 0 END "
        "WHERE board_id = ?",
        delivery_id,
        board_id,
    )
    for column in ordered:
        column["is_commitment_point"] = 1 if column["id"] == commitment_id else 0
        column["is_delivery_point"] = 1 if column["id"] == delivery_id else 0
    return commitment_id, delivery_id


def normalize_structure(
    db: Database, board_id: str, order: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Rewrite positions densely, re-derive the points, validate the groups.

    Every structural mutation ends here, so the invariants hold after a create, a
    delete, a reorder and a segment change alike. Pass ``order`` to impose an
    explicit sequence (the reorder endpoint); omit it to fall back on the
    canonical sort.
    """
    rows = db.q("SELECT * FROM columns WHERE board_id = ?", board_id)
    by_id = {row["id"]: row for row in rows}

    if order is None:
        ordered = sorted(rows, key=_column_sort_key)
    else:
        unknown = [cid for cid in order if cid not in by_id]
        if unknown:
            raise BadRequest(
                "Those columns do not belong to this board.",
                code="column_not_on_board",
                details={"board_id": board_id, "column_ids": unknown},
            )
        seen: set[str] = set()
        ordered = []
        for cid in order:
            if cid in seen:
                raise BadRequest(
                    "The requested order repeats a column.",
                    code="duplicate_column_in_order",
                    details={"column_id": cid},
                )
            seen.add(cid)
            ordered.append(by_id[cid])
        # Columns the caller left out keep their existing relative order at the end
        # of their segment, so a partial reorder is still a legal board.
        remainder = [row for row in sorted(rows, key=_column_sort_key) if row["id"] not in seen]
        ordered.extend(remainder)
        # Segment membership is a property of the column, not of the request, so a
        # request that interleaves the two halves is straightened rather than
        # rejected: the caller asked for a relative order, and it is honoured
        # inside each segment.
        rank = {row["id"]: index for index, row in enumerate(ordered)}
        ordered.sort(key=lambda c: (SEGMENT_RANK.get(c["segment"], 1), rank[c["id"]]))

    _assert_order_valid(ordered)

    for position, column in enumerate(ordered):
        if int(column["position"] or 0) != position:
            db.ex("UPDATE columns SET position = ? WHERE id = ?", position, column["id"])
            column["position"] = position

    recompute_points(db, board_id, ordered)
    assert_groups_contiguous(board_id, ordered)
    _sync_group_positions(db, board_id, ordered)
    return ordered


def _placement_order(
    existing: Sequence[dict[str, Any]], column: dict[str, Any], target_index: int | None
) -> list[str]:
    """Ids of ``existing`` with ``column`` slotted in at ``target_index``.

    The index is clamped into the column's own segment so a placement can never
    interleave upstream and downstream work, and an envelope column is always
    forced to the front.
    """
    others = [c for c in existing if c["id"] != column["id"]]
    upstream_count = sum(1 for c in others if c["segment"] == "upstream")
    has_envelope = any(c["kind"] == "envelope" for c in others)

    if column["kind"] == "envelope":
        index = 0
    else:
        if column["segment"] == "upstream":
            low, high = (1 if has_envelope else 0), upstream_count
        else:
            low, high = upstream_count, len(others)
        index = high if target_index is None else max(low, min(int(target_index), high))

    ids = [c["id"] for c in others]
    ids.insert(index, column["id"])
    return ids


# ---------------------------------------------------------------------------
# WIP accounting (SPEC §5 `GET /boards/{id}/wip`)
# ---------------------------------------------------------------------------


def wip_state(count: int, wip_min: int | None, wip_max: int | None) -> str:
    """``under | ok | at_limit | over`` for one cell.

    ``max`` is a policy: reaching it is a signal (``at_limit``), passing it is a
    breach (``over``). ``min`` is advisory only — a starving column (SPEC §6.7).
    """
    if wip_max is not None and count > wip_max:
        return "over"
    if wip_max is not None and count == wip_max:
        return "at_limit"
    if wip_min is not None and count < wip_min:
        return "under"
    return "ok"


def _cell(count: int, wip_min: Any, wip_max: Any, label: str | None = None) -> dict[str, Any]:
    minimum = int(wip_min) if wip_min is not None else None
    maximum = int(wip_max) if wip_max is not None else None
    state = wip_state(count, minimum, maximum)
    if label is None:
        label = f"{count}/{maximum}" if maximum is not None else str(count)
    return {"count": count, "min": minimum, "max": maximum, "state": state, "label": label}


def board_wip_status(
    db: Database,
    board: dict[str, Any],
    columns: Sequence[dict[str, Any]] | None = None,
    groups: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The full WIP block for a board: per column, per group and whole flow.

    Two SQL statements regardless of board size. Column and group cells count
    *literal occupancy*; the flow cell counts committed-and-not-yet-delivered
    work, which is the WIP that lead time and Little's Law are defined against.
    """
    board_id = board["id"]
    if columns is None:
        columns = canonical_columns(db, board_id)
    if groups is None:
        groups = db.q(
            "SELECT * FROM column_groups WHERE board_id = ? ORDER BY position", board_id
        )

    occupancy = {
        row["column_id"]: int(row["n"])
        for row in db.q(
            "SELECT column_id, COUNT(*) AS n FROM cards "
            "WHERE board_id = ? AND archived = 0 GROUP BY column_id",
            board_id,
        )
    }
    flow_count = int(
        db.scalar(
            "SELECT COUNT(*) FROM cards WHERE board_id = ? AND archived = 0 "
            "AND committed_at IS NOT NULL AND delivered_at IS NULL",
            board_id,
        )
        or 0
    )

    column_cells: dict[str, dict[str, Any]] = {}
    per_group: dict[str, int] = {}
    for column in columns:
        count = occupancy.get(column["id"], 0)
        column_cells[column["id"]] = _cell(count, column["wip_min"], column["wip_max"])
        if column.get("group_id"):
            per_group[column["group_id"]] = per_group.get(column["group_id"], 0) + count

    group_cells: dict[str, dict[str, Any]] = {}
    if is_flag_effective(db, "wip_group"):
        for group in groups:
            group_cells[group["id"]] = _cell(
                per_group.get(group["id"], 0), group["wip_min"], group["wip_max"]
            )

    if is_flag_effective(db, "wip_flow"):
        flow_cell = _cell(flow_count, board.get("wip_min"), board.get("wip_max"))
    else:
        # The count is still true and useful; only the policy is plan-locked.
        flow_cell = _cell(flow_count, None, None)

    return {
        "board_id": board_id,
        "columns": column_cells,
        "groups": group_cells,
        "flow": flow_cell,
    }


# ---------------------------------------------------------------------------
# card projection shared with the projects router
# ---------------------------------------------------------------------------

CARD_SELECT = """
SELECT c.*,
       p.key   AS project_key,
       p.name  AS project_name,
       p.color AS project_color,
       u.name  AS assignee_name,
       COALESCE(t.total, 0) AS task_total,
       COALESCE(t.done, 0)  AS task_done,
       bl.started_at        AS blocked_since,
       col.position         AS column_position
FROM cards c
JOIN columns col ON col.id = c.column_id
LEFT JOIN projects p ON p.id = c.project_id
LEFT JOIN users u ON u.id = c.assignee_id
LEFT JOIN (
    SELECT card_id, COUNT(*) AS total, SUM(is_done) AS done
    FROM card_tasks GROUP BY card_id
) t ON t.card_id = c.id
LEFT JOIN (
    SELECT card_id, MIN(started_at) AS started_at
    FROM blockers WHERE ended_at IS NULL GROUP BY card_id
) bl ON bl.card_id = c.id
"""


def _like(value: str) -> str:
    """Escape nothing, wrap everything: sqlite LIKE with ``%`` on both ends."""
    return f"%{value.strip()}%"


def card_filter_clauses(
    *,
    project_id: str | None = None,
    service_class: str | None = None,
    assignee_id: str | None = None,
    column_id: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    is_blocked: bool | None = None,
    demand_type: str | None = None,
    value_area: str | None = None,
    size: str | None = None,
    due_before: str | None = None,
    envelope_state: str | None = None,
) -> tuple[list[str], list[Any]]:
    """Translate the SPEC §5 card query string into SQL fragments."""
    where: list[str] = []
    params: list[Any] = []
    if project_id:
        where.append("c.project_id = ?")
        params.append(project_id)
    if service_class:
        where.append("c.service_class = ?")
        params.append(service_class)
    if assignee_id:
        where.append("c.assignee_id = ?")
        params.append(assignee_id)
    if column_id:
        where.append("c.column_id = ?")
        params.append(column_id)
    if tag:
        # `tags` is a JSON array of strings; matching the quoted token avoids the
        # JSON1 extension while still refusing partial-word collisions.
        where.append("c.tags LIKE ?")
        params.append(f'%"{tag.strip()}"%')
    if q:
        where.append("(c.title LIKE ? OR c.key LIKE ? OR c.description LIKE ?)")
        params.extend([_like(q), _like(q), _like(q)])
    if is_blocked is not None:
        where.append("c.is_blocked = ?")
        params.append(1 if is_blocked else 0)
    if demand_type:
        where.append("c.demand_type = ?")
        params.append(demand_type)
    if value_area:
        where.append("c.value_area = ?")
        params.append(value_area)
    if size:
        where.append("c.size = ?")
        params.append(size)
    if due_before:
        where.append("c.due_date IS NOT NULL AND c.due_date <= ?")
        params.append(due_before)
    if envelope_state:
        where.append("c.envelope_state = ?")
        params.append(envelope_state)
    return where, params


def decorate_card(row: dict[str, Any], now_iso: str) -> dict[str, Any]:
    """Add the derived fields a compact card renders, drop internal columns."""
    row = dict(row)
    row.pop("column_position", None)
    committed = row.get("committed_at")
    delivered = row.get("delivered_at")
    if delivered:
        age_seconds = None
    elif committed:
        age_seconds = seconds_between(committed, now_iso)
    else:
        age_seconds = seconds_between(row.get("opened_at") or row.get("created_at"), now_iso)
    row["age_days"] = round(age_seconds / 86400.0, 2) if age_seconds is not None else None
    row["task_total"] = int(row.get("task_total") or 0)
    row["task_done"] = int(row.get("task_done") or 0)
    return row


def select_cards(
    db: Database,
    *,
    board_id: str | None = None,
    column_ids: Sequence[str] | None = None,
    archived: bool = False,
    order_by: str = "col.position, c.position, c.created_at",
    limit: int | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """One query for every card the caller can see, already decorated."""
    where, params = card_filter_clauses(**filters)
    if board_id:
        where.insert(0, "c.board_id = ?")
        params.insert(0, board_id)
    where.append("c.archived = ?")
    params.append(1 if archived else 0)
    if column_ids is not None:
        if not column_ids:
            return []
        where.append(f"c.column_id IN ({', '.join('?' for _ in column_ids)})")
        params.extend(column_ids)

    sql = f"{CARD_SELECT} WHERE {' AND '.join(where)} ORDER BY {order_by}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    now_iso = utcnow()
    return [decorate_card(row, now_iso) for row in db.q(sql, params)]


# ---------------------------------------------------------------------------
# card keys (shared with the demands endpoint and the clone)
# ---------------------------------------------------------------------------


def next_card_key(db: Database, prefix: str) -> str:
    """The next free ``PREFIX-n`` card key. Numbering starts at 101 like the seed."""
    highest = 100
    for row in db.q("SELECT key FROM cards WHERE key LIKE ?", f"{prefix}-%"):
        tail = str(row["key"]).rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1}"


class KeyAllocator:
    """Hands out unique card keys during a bulk write (clone, demand import)."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._next: dict[str, int] = {}

    def take(self, prefix: str) -> str:
        prefix = (prefix or "CARD").upper()
        if prefix not in self._next:
            self._next[prefix] = int(next_card_key(self._db, prefix).rsplit("-", 1)[-1])
        number = self._next[prefix]
        self._next[prefix] = number + 1
        return f"{prefix}-{number}"


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "board"


def _unique_slug(db: Database, base: str) -> str:
    slug = _slugify(base)
    candidate = slug
    suffix = 2
    while db.q1("SELECT id FROM boards WHERE slug = ?", candidate):
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def _board_out(db: Database, board: dict[str, Any], *, structure: bool = True) -> dict[str, Any]:
    """Board row plus (optionally) its groups, columns and card count."""
    out = dict(board)
    out["card_count"] = int(
        db.scalar("SELECT COUNT(*) FROM cards WHERE board_id = ? AND archived = 0", board["id"])
        or 0
    )
    if structure:
        out["columns"] = canonical_columns(db, board["id"])
        out["groups"] = db.q(
            "SELECT * FROM column_groups WHERE board_id = ? ORDER BY position", board["id"]
        )
    return out


def _get_column_or_404(db: Database, column_id: str) -> dict[str, Any]:
    row = db.q1("SELECT * FROM columns WHERE id = ?", column_id)
    if row is None:
        raise NotFound("Column", column_id, code="column_not_found")
    return row


def _get_group_or_404(db: Database, group_id: str) -> dict[str, Any]:
    row = db.q1("SELECT * FROM column_groups WHERE id = ?", group_id)
    if row is None:
        raise NotFound("Column group", group_id, code="group_not_found")
    return row


def _assert_group_on_board(db: Database, board_id: str, group_id: str | None) -> None:
    if not group_id:
        return
    row = db.q1(
        "SELECT id FROM column_groups WHERE id = ? AND board_id = ?", group_id, board_id
    )
    if row is None:
        raise BadRequest(
            "That column group belongs to a different board.",
            code="group_not_on_board",
            details={"group_id": group_id, "board_id": board_id},
        )


def _validate_column_shape(segment: str, kind: str) -> None:
    """Cross-field rules the DDL cannot express."""
    if kind == "envelope" and segment != "upstream":
        raise Conflict(
            "envelope_must_be_upstream",
            "An envelope column belongs to the upstream segment.",
            {"segment": segment},
        )
    if kind == "done" and segment != "downstream":
        raise Conflict(
            "done_must_be_downstream",
            "A terminal 'done' column belongs to the downstream segment.",
            {"segment": segment},
        )


def _assert_single_envelope(db: Database, board_id: str, exclude_id: str | None = None) -> None:
    sql = "SELECT id, name FROM columns WHERE board_id = ? AND kind = 'envelope'"
    params: list[Any] = [board_id]
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    existing = db.q1(sql, params)
    if existing:
        raise Conflict(
            "envelope_column_exists",
            "A board has at most one envelope column, and it is always the first one.",
            {"column_id": existing["id"], "name": existing["name"]},
        )


def _assert_derived_points(
    payload_commitment: bool | None,
    payload_delivery: bool | None,
    column: dict[str, Any],
) -> None:
    """The two point flags are derived; refuse to pretend otherwise."""
    if payload_commitment is not None and bool(payload_commitment) != bool(
        column["is_commitment_point"]
    ):
        raise Conflict(
            "commitment_point_is_derived",
            "The commitment point is always the first downstream column. Change the "
            "column's segment or position instead of setting the flag.",
            {"column_id": column["id"], "is_commitment_point": bool(column["is_commitment_point"])},
        )
    if payload_delivery is not None and bool(payload_delivery) != bool(
        column["is_delivery_point"]
    ):
        raise Conflict(
            "delivery_point_is_derived",
            "The delivery point is always the last 'done' column. Change the column's "
            "kind or position instead of setting the flag.",
            {"column_id": column["id"], "is_delivery_point": bool(column["is_delivery_point"])},
        )


def _apply_template(db: Database, board_id: str, template: str) -> None:
    """Write a template's groups and columns onto a freshly created board."""
    spec = TEMPLATES[template]
    group_ids: dict[str, str] = {}
    for position, (name, wip_min, wip_max, color) in enumerate(spec["groups"]):
        group_id = new_id()
        group_ids[name] = group_id
        db.ex(
            "INSERT INTO column_groups (id, board_id, name, position, wip_min, wip_max, color) "
            "VALUES (?,?,?,?,?,?,?)",
            group_id,
            board_id,
            name,
            position,
            wip_min,
            wip_max,
            color,
        )
    for position, (name, segment, kind, wip_min, wip_max, group, description) in enumerate(
        spec["columns"]
    ):
        db.ex(
            "INSERT INTO columns (id, board_id, group_id, name, position, segment, kind, "
            "wip_min, wip_max, is_commitment_point, is_delivery_point, color, description) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,NULL,?)",
            new_id(),
            board_id,
            group_ids.get(group) if group else None,
            name,
            position,
            segment,
            kind,
            wip_min,
            wip_max,
            description,
        )


# ---------------------------------------------------------------------------
# boards
# ---------------------------------------------------------------------------


@router.get("/boards", response_model=ListResponse[BoardOut], summary="List boards")
def list_boards(
    kind: str | None = Query(None, pattern="^(operational|simulator)$"),
    q: str | None = Query(None, description="Match the board name or slug"),
    archived: bool = Query(False),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Every board the workspace has, newest first, with a live card count."""
    where = ["b.archived = ?"]
    params: list[Any] = [1 if archived else 0]
    if kind:
        where.append("b.kind = ?")
        params.append(kind)
    if q:
        where.append("(b.name LIKE ? OR b.slug LIKE ?)")
        params.extend([_like(q), _like(q)])
    rows = db.q(
        "SELECT b.*, (SELECT COUNT(*) FROM cards c WHERE c.board_id = b.id AND c.archived = 0) "
        f"AS card_count FROM boards b WHERE {' AND '.join(where)} "
        "ORDER BY b.kind, b.created_at DESC",
        params,
    )
    return {"items": rows, "total": len(rows)}


@router.post(
    "/boards",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a board from a template",
)
def create_board(
    payload: BoardCreateRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Create a board and lay out the chosen template's groups and columns."""
    if payload.kind == "simulator":
        if user.get("role") not in {"master", "administrator", "simulator"}:
            raise Forbidden(
                "Your role cannot create simulator boards.",
                code="insufficient_role",
                details={"role": user.get("role")},
            )
        require_flag("simulator")(db)
    elif user.get("role") not in STRUCTURE_ROLES:
        raise Forbidden(
            "Your role cannot create boards.",
            code="insufficient_role",
            details={"role": user.get("role"), "required": sorted(STRUCTURE_ROLES)},
        )

    template = payload.template or "blank"
    defaults = TEMPLATES[template]["board"]
    board_id = new_id()
    with db.tx():
        db.ex(
            "INSERT INTO boards (id, name, slug, description, kind, source_board_id, "
            "count_envelope_in_metrics, confirm_moves, wip_min, wip_max, sle_days, "
            "sle_percentile, created_at, archived) VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?,?,0)",
            board_id,
            payload.name,
            _unique_slug(db, payload.name),
            payload.description or TEMPLATES[template]["description"],
            payload.kind,
            1 if payload.count_envelope_in_metrics else 0,
            1 if payload.confirm_moves else 0,
            payload.wip_min if payload.wip_min is not None else defaults["wip_min"],
            payload.wip_max if payload.wip_max is not None else defaults["wip_max"],
            payload.sle_days if payload.sle_days is not None else defaults["sle_days"],
            payload.sle_percentile
            if payload.sle_percentile is not None
            else defaults["sle_percentile"],
            utcnow(),
        )
        _apply_template(db, board_id, template)
        normalize_structure(db, board_id)

    board = get_board_or_404(db, board_id)
    return _board_out(db, board)


@router.get("/boards/templates", summary="Board templates available to POST /boards")
def list_templates(
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """The template catalogue, so the "new board" dialog needs no hard-coded copy."""
    items = [
        {
            "key": key,
            "label": spec["label"],
            "description": spec["description"],
            "column_count": len(spec["columns"]),
            "columns": [
                {"name": c[0], "segment": c[1], "kind": c[2]} for c in spec["columns"]
            ],
            "groups": [g[0] for g in spec["groups"]],
        }
        for key, spec in TEMPLATES.items()
    ]
    return {"items": items, "total": len(items)}


@router.get("/boards/{id}", response_model=BoardOut, summary="One board with its structure")
def get_board(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """The board row plus its groups and columns, including every WIP policy."""
    return _board_out(db, get_board_or_404(db, id))


@router.patch("/boards/{id}", response_model=BoardOut, summary="Update board policies")
def update_board(
    id: str,
    payload: BoardUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Rename a board or change its WIP, SLE, envelope and confirmation policies."""
    board = get_board_or_404(db, id)
    require_structure_write(user, board)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return _board_out(db, board)
    if (
        fields.get("wip_min") is not None
        and fields.get("wip_max") is not None
        and fields["wip_min"] > fields["wip_max"]
    ):
        raise BadRequest(
            "The whole-flow WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details=fields,
        )
    assignments = []
    params: list[Any] = []
    for key, value in fields.items():
        assignments.append(f"{key} = ?")
        params.append(int(value) if isinstance(value, bool) else value)
    params.append(board["id"])
    db.ex(f"UPDATE boards SET {', '.join(assignments)} WHERE id = ?", params)
    return _board_out(db, get_board_or_404(db, board["id"]))


@router.delete("/boards/{id}", response_model=OkResponse, summary="Archive or delete a board")
def delete_board(
    id: str,
    hard: bool = Query(False, description="Master only: destroy the board and its history"),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Archive a board by default; ``?hard=true`` (master) deletes it outright.

    Archiving is the safe default because a board's movement history is the only
    record of how the system behaved — deleting it silently destroys every metric
    computed from it.
    """
    board = get_board_or_404(db, id)
    require_structure_write(user, board)
    if hard:
        if user.get("role") != "master":
            raise Forbidden(
                "Only the master account can permanently delete a board.",
                code="insufficient_role",
                details={"role": user.get("role")},
            )
        db.ex("DELETE FROM boards WHERE id = ?", board["id"])
        return {"ok": True, "id": board["id"], "message": "Board deleted with its history."}
    db.ex("UPDATE boards SET archived = 1 WHERE id = ?", board["id"])
    return {"ok": True, "id": board["id"], "message": "Board archived."}


@router.post(
    "/boards/{id}/clone",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Deep clone a board",
)
def clone_board(
    id: str,
    payload: BoardCloneRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Copy a board's structure — and optionally its cards and movement history.

    This is what a simulator board is built from (SPEC §8.1). Cards are copied
    together with their ``card_movements`` rows: the timestamps on a card
    (``committed_at``, ``delivered_at``) are *read off* that history by the metric
    layer, so a card copied without it would be a card that lies.
    """
    source = get_board_or_404(db, id)
    if payload.kind == "simulator":
        if user.get("role") not in {"master", "administrator", "simulator"}:
            raise Forbidden(
                "Your role cannot create simulator boards.",
                code="insufficient_role",
                details={"role": user.get("role")},
            )
        require_flag("simulator")(db)
    elif user.get("role") not in STRUCTURE_ROLES:
        raise Forbidden(
            "Your role cannot create boards.",
            code="insufficient_role",
            details={"role": user.get("role")},
        )

    new_board_id = new_id()
    now = utcnow()
    with db.tx():
        db.ex(
            "INSERT INTO boards (id, name, slug, description, kind, source_board_id, "
            "count_envelope_in_metrics, confirm_moves, wip_min, wip_max, sle_days, "
            "sle_percentile, created_at, archived) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            new_board_id,
            payload.name,
            _unique_slug(db, payload.name),
            source["description"],
            payload.kind,
            source["id"],
            1 if source["count_envelope_in_metrics"] else 0,
            1 if source["confirm_moves"] else 0,
            source["wip_min"],
            source["wip_max"],
            source["sle_days"],
            source["sle_percentile"],
            now,
        )

        group_map: dict[str, str] = {}
        for group in db.q(
            "SELECT * FROM column_groups WHERE board_id = ? ORDER BY position", source["id"]
        ):
            group_map[group["id"]] = new_id()
            db.ex(
                "INSERT INTO column_groups (id, board_id, name, position, wip_min, wip_max, "
                "color) VALUES (?,?,?,?,?,?,?)",
                group_map[group["id"]],
                new_board_id,
                group["name"],
                group["position"],
                group["wip_min"],
                group["wip_max"],
                group["color"],
            )

        column_map: dict[str, str] = {}
        for column in canonical_columns(db, source["id"]):
            column_map[column["id"]] = new_id()
            db.ex(
                "INSERT INTO columns (id, board_id, group_id, name, position, segment, kind, "
                "wip_min, wip_max, is_commitment_point, is_delivery_point, color, description) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                column_map[column["id"]],
                new_board_id,
                group_map.get(column["group_id"]) if column["group_id"] else None,
                column["name"],
                column["position"],
                column["segment"],
                column["kind"],
                column["wip_min"],
                column["wip_max"],
                column["is_commitment_point"],
                column["is_delivery_point"],
                column["color"],
                column["description"],
            )

        copied_cards = 0
        if payload.copy_cards:
            allocator = KeyAllocator(db)
            card_map: dict[str, str] = {}
            source_cards = db.q(
                "SELECT c.*, p.key AS project_key FROM cards c "
                "LEFT JOIN projects p ON p.id = c.project_id "
                "WHERE c.board_id = ? AND c.archived = 0",
                source["id"],
            )
            for card in source_cards:
                card_map[card["id"]] = new_id()
                prefix = card["project_key"] or _slugify(payload.name).upper()[:3] or "SIM"
                db.ex(
                    "INSERT INTO cards (id, board_id, column_id, project_id, key, title, "
                    "description, service_class, assignee_id, requester, size, value_area, "
                    "demand_type, due_date, position, envelope_state, is_blocked, tags, "
                    "custom_fields, created_at, opened_at, committed_at, delivered_at, "
                    "archived, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                    card_map[card["id"]],
                    new_board_id,
                    column_map[card["column_id"]],
                    card["project_id"],
                    allocator.take(prefix),
                    card["title"],
                    card["description"],
                    card["service_class"],
                    card["assignee_id"],
                    card["requester"],
                    card["size"],
                    card["value_area"],
                    card["demand_type"],
                    card["due_date"],
                    card["position"],
                    card["envelope_state"],
                    card["is_blocked"],
                    card["tags"],
                    card["custom_fields"],
                    card["created_at"],
                    card["opened_at"],
                    card["committed_at"],
                    card["delivered_at"],
                    card["created_by"],
                )
                copied_cards += 1

            if card_map:
                placeholders = ", ".join("?" for _ in card_map)
                movements = db.q(
                    "SELECT * FROM card_movements WHERE card_id IN "
                    f"({placeholders}) ORDER BY moved_at",
                    list(card_map),
                )
                db.exmany(
                    "INSERT INTO card_movements (id, card_id, board_id, from_column_id, "
                    "to_column_id, from_segment, to_segment, moved_at, moved_by, "
                    "seconds_in_from_column, note, is_backward, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            new_id(),
                            card_map[m["card_id"]],
                            new_board_id,
                            column_map.get(m["from_column_id"]) if m["from_column_id"] else None,
                            column_map[m["to_column_id"]],
                            m["from_segment"],
                            m["to_segment"],
                            m["moved_at"],
                            m["moved_by"],
                            m["seconds_in_from_column"],
                            m["note"],
                            m["is_backward"],
                            m["source"],
                        )
                        for m in movements
                        if m["to_column_id"] in column_map
                    ],
                )
                db.exmany(
                    "INSERT INTO card_events (id, card_id, board_id, actor_id, event_type, "
                    "field, old_value, new_value, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            new_id(),
                            new_card_id,
                            new_board_id,
                            user.get("id"),
                            "created",
                            "cloned_from",
                            old_card_id,
                            new_card_id,
                            now,
                        )
                        for old_card_id, new_card_id in card_map.items()
                    ],
                )

        normalize_structure(db, new_board_id)

    board = get_board_or_404(db, new_board_id)
    out = _board_out(db, board)
    out["card_count"] = copied_cards
    return out


# ---------------------------------------------------------------------------
# the board view — the single call the board UI makes
# ---------------------------------------------------------------------------


@router.get(
    "/boards/{id}/view",
    response_model=BoardViewOut,
    summary="Everything one board render needs",
)
def board_view(
    id: str,
    segment: SegmentQuery = Query("unified"),
    project_id: str | None = Query(None),
    service_class: str | None = Query(None),
    assignee_id: str | None = Query(None),
    column_id: str | None = Query(None),
    tag: str | None = Query(None),
    q: str | None = Query(None),
    is_blocked: bool | None = Query(None),
    demand_type: str | None = Query(None),
    value_area: str | None = Query(None),
    size: str | None = Query(None),
    due_before: str | None = Query(None),
    envelope_state: str | None = Query(None),
    archived: bool = Query(False),
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Board, groups, columns, cards and the full WIP block in four queries.

    The frontend never assembles a board from several round trips: one call
    returns the structure, the positioned cards for the requested segment and the
    WIP state of every policy, so the render is atomic and can never show cards
    against a stale column layout.
    """
    board = get_board_or_404(db, id)
    if segment == "unified":
        # Seeing both halves as one continuous flow is the Team-plan feature.
        require_flag("unified_flow")(db)

    columns = canonical_columns(db, board["id"])
    if segment == "unified":
        visible = columns
    else:
        visible = [c for c in columns if c["segment"] == segment]

    groups = db.q(
        "SELECT * FROM column_groups WHERE board_id = ? ORDER BY position", board["id"]
    )
    visible_group_ids = {c["group_id"] for c in visible if c["group_id"]}
    visible_groups = [g for g in groups if g["id"] in visible_group_ids]

    cards = select_cards(
        db,
        board_id=board["id"],
        column_ids=[c["id"] for c in visible],
        archived=archived,
        project_id=project_id,
        service_class=service_class,
        assignee_id=assignee_id,
        column_id=column_id,
        tag=tag,
        q=q,
        is_blocked=is_blocked,
        demand_type=demand_type,
        value_area=value_area,
        size=size,
        due_before=due_before,
        envelope_state=envelope_state,
    )

    wip = board_wip_status(db, board, columns=columns, groups=groups)
    commitment = next((c["id"] for c in columns if c["is_commitment_point"]), None)
    delivery = next((c["id"] for c in columns if c["is_delivery_point"]), None)

    return {
        "board": _board_out(db, board, structure=False),
        "segment": segment,
        "groups": visible_groups,
        "columns": visible,
        "cards": cards,
        "wip": wip,
        "commitment_column_id": commitment,
        "delivery_column_id": delivery,
        "total_cards": len(cards),
        "generated_at": utcnow(),
    }


@router.get(
    "/boards/{id}/wip",
    response_model=WipStatusOut,
    summary="WIP state per column, per group and for the whole flow",
)
def board_wip(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Counts against min/max with a ``under|ok|at_limit|over`` state each."""
    board = get_board_or_404(db, id)
    return board_wip_status(db, board)


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------


@router.post(
    "/boards/{id}/columns",
    response_model=ColumnOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a column",
)
def create_column(
    id: str,
    payload: ColumnCreate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Insert a column at ``position`` inside its own segment, then re-derive the points."""
    board = get_board_or_404(db, id)
    require_structure_write(user, board)
    _validate_column_shape(payload.segment, payload.kind)
    if payload.wip_min is not None and payload.wip_max is not None and payload.wip_min > payload.wip_max:
        raise BadRequest(
            "The column WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": payload.wip_min, "wip_max": payload.wip_max},
        )

    column_id = new_id()
    with db.tx():
        if payload.kind == "envelope":
            _assert_single_envelope(db, board["id"])
        _assert_group_on_board(db, board["id"], payload.group_id)
        existing = canonical_columns(db, board["id"])
        db.ex(
            "INSERT INTO columns (id, board_id, group_id, name, position, segment, kind, "
            "wip_min, wip_max, is_commitment_point, is_delivery_point, color, description) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?)",
            column_id,
            board["id"],
            payload.group_id,
            payload.name,
            len(existing),
            payload.segment,
            payload.kind,
            payload.wip_min,
            payload.wip_max,
            payload.color,
            payload.description,
        )
        fresh = {
            "id": column_id,
            "segment": payload.segment,
            "kind": payload.kind,
            "position": len(existing),
        }
        order = _placement_order(existing, fresh, payload.position)
        normalize_structure(db, board["id"], order)
        created = _get_column_or_404(db, column_id)
        _assert_derived_points(
            payload.is_commitment_point, payload.is_delivery_point, created
        )

    return created


@router.patch("/columns/{id}", response_model=ColumnOut, summary="Update a column")
def update_column(
    id: str,
    payload: ColumnUpdate,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Rename, re-limit, re-group, re-segment or move a column.

    A segment change is a structural event: it can hand the commitment point to a
    different column, which is why every path ends in
    :func:`normalize_structure` rather than trusting the caller.
    """
    column = _get_column_or_404(db, id)
    board = get_board_or_404(db, column["board_id"])
    require_structure_write(user, board)

    fields = payload.model_dump(exclude_unset=True)
    wants_commitment = fields.pop("is_commitment_point", None)
    wants_delivery = fields.pop("is_delivery_point", None)
    target_position = fields.pop("position", None)

    segment = fields.get("segment", column["segment"])
    kind = fields.get("kind", column["kind"])
    _validate_column_shape(segment, kind)

    wip_min = fields.get("wip_min", column["wip_min"])
    wip_max = fields.get("wip_max", column["wip_max"])
    if wip_min is not None and wip_max is not None and wip_min > wip_max:
        raise BadRequest(
            "The column WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": wip_min, "wip_max": wip_max},
        )

    with db.tx():
        if kind == "envelope" and column["kind"] != "envelope":
            _assert_single_envelope(db, board["id"], exclude_id=column["id"])
        if "group_id" in fields:
            _assert_group_on_board(db, board["id"], fields["group_id"])
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            db.ex(
                f"UPDATE columns SET {assignments} WHERE id = ?",
                list(fields.values()) + [column["id"]],
            )
        existing = canonical_columns(db, board["id"])
        fresh = next(c for c in existing if c["id"] == column["id"])
        if target_position is not None or fields.get("segment") or kind == "envelope":
            order = _placement_order(existing, fresh, target_position)
            normalize_structure(db, board["id"], order)
        else:
            normalize_structure(db, board["id"])

        updated = _get_column_or_404(db, column["id"])
        _assert_derived_points(wants_commitment, wants_delivery, updated)
    return updated


@router.delete("/columns/{id}", response_model=OkResponse, summary="Delete a column")
def delete_column(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Remove an **empty** column and re-derive the board's points."""
    column = _get_column_or_404(db, id)
    board = get_board_or_404(db, column["board_id"])
    require_structure_write(user, board)

    occupancy = int(
        db.scalar("SELECT COUNT(*) FROM cards WHERE column_id = ?", column["id"]) or 0
    )
    if occupancy:
        raise Conflict(
            "column_not_empty",
            "Move the cards out of this column before deleting it.",
            {"column_id": column["id"], "card_count": occupancy},
        )
    with db.tx():
        db.ex("DELETE FROM columns WHERE id = ?", column["id"])
        normalize_structure(db, board["id"])
    return {"ok": True, "id": column["id"], "message": "Column deleted."}


@router.post(
    "/boards/{id}/columns/reorder",
    response_model=ListResponse[ColumnOut],
    summary="Reorder columns",
)
def reorder_columns(
    id: str,
    payload: ColumnReorderRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Apply an explicit column order, keeping ``position`` dense and legal."""
    board = get_board_or_404(db, id)
    require_structure_write(user, board)
    with db.tx():
        ordered = normalize_structure(db, board["id"], payload.ordered_ids)
    return {"items": ordered, "total": len(ordered)}


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


def _assign_group_columns(
    db: Database, board_id: str, group_id: str, column_ids: Sequence[str]
) -> None:
    """Point exactly ``column_ids`` at ``group_id`` (validated against the board)."""
    for column_id in column_ids:
        row = db.q1(
            "SELECT id FROM columns WHERE id = ? AND board_id = ?", column_id, board_id
        )
        if row is None:
            raise BadRequest(
                "Those columns do not belong to this board.",
                code="column_not_on_board",
                details={"column_id": column_id, "board_id": board_id},
            )
    db.ex("UPDATE columns SET group_id = NULL WHERE group_id = ?", group_id)
    if column_ids:
        placeholders = ", ".join("?" for _ in column_ids)
        db.ex(
            f"UPDATE columns SET group_id = ? WHERE id IN ({placeholders})",
            [group_id, *column_ids],
        )


@router.post(
    "/boards/{id}/groups",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a column group",
    dependencies=[Depends(require_flag("wip_group"))],
)
def create_group(
    id: str,
    payload: GroupWriteRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Group consecutive columns under one header with its own WIP limits."""
    board = get_board_or_404(db, id)
    require_structure_write(user, board)
    if payload.wip_min is not None and payload.wip_max is not None and payload.wip_min > payload.wip_max:
        raise BadRequest(
            "The group WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": payload.wip_min, "wip_max": payload.wip_max},
        )

    group_id = new_id()
    with db.tx():
        position = payload.position
        if position is None:
            position = int(
                db.scalar(
                    "SELECT COUNT(*) FROM column_groups WHERE board_id = ?", board["id"]
                )
                or 0
            )
        db.ex(
            "INSERT INTO column_groups (id, board_id, name, position, wip_min, wip_max, color) "
            "VALUES (?,?,?,?,?,?,?)",
            group_id,
            board["id"],
            payload.name,
            position,
            payload.wip_min,
            payload.wip_max,
            payload.color,
        )
        if payload.column_ids:
            _assign_group_columns(db, board["id"], group_id, payload.column_ids)
        normalize_structure(db, board["id"])
    return _get_group_or_404(db, group_id)


@router.patch("/groups/{id}", response_model=GroupOut, summary="Update a column group")
def update_group(
    id: str,
    payload: GroupPatchRequest,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("wip_group")),
) -> dict[str, Any]:
    """Rename a group, change its limits, or replace the columns it spans."""
    group = _get_group_or_404(db, id)
    board = get_board_or_404(db, group["board_id"])
    require_structure_write(user, board)

    fields = payload.model_dump(exclude_unset=True)
    column_ids = fields.pop("column_ids", None)
    wip_min = fields.get("wip_min", group["wip_min"])
    wip_max = fields.get("wip_max", group["wip_max"])
    if wip_min is not None and wip_max is not None and wip_min > wip_max:
        raise BadRequest(
            "The group WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": wip_min, "wip_max": wip_max},
        )

    with db.tx():
        if fields:
            assignments = ", ".join(f"{key} = ?" for key in fields)
            db.ex(
                f"UPDATE column_groups SET {assignments} WHERE id = ?",
                list(fields.values()) + [group["id"]],
            )
        if column_ids is not None:
            _assign_group_columns(db, board["id"], group["id"], column_ids)
        normalize_structure(db, board["id"])
    return _get_group_or_404(db, group["id"])


@router.delete("/groups/{id}", response_model=OkResponse, summary="Delete a column group")
def delete_group(
    id: str,
    db: Database = Depends(get_db),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
    _: None = Depends(require_flag("wip_group")),
) -> dict[str, Any]:
    """Drop the header; its columns stay on the board, ungrouped."""
    group = _get_group_or_404(db, id)
    board = get_board_or_404(db, group["board_id"])
    require_structure_write(user, board)
    with db.tx():
        db.ex("UPDATE columns SET group_id = NULL WHERE group_id = ?", group["id"])
        db.ex("DELETE FROM column_groups WHERE id = ?", group["id"])
        normalize_structure(db, board["id"])
    return {"ok": True, "id": group["id"], "message": "Column group deleted."}
