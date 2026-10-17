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
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import Field, field_validator
from sqlalchemy import String, case, cast, delete, func, insert, or_, select, type_coerce, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import new_id, seconds_between, utcnow
from ..deps import (
    CurrentUser,
    assert_board_writable,
    get_board_or_404,
    get_session,
    is_flag_effective,
    is_product_board,
    require_capability,
    require_flag,
    require_roles,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..models import (
    Blocker,
    Board,
    BoardColumn,
    Card,
    CardEvent,
    CardMovement,
    CardTask,
    ColumnGroup,
    Project,
    User,
    as_dict,
)
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
    Schema,
    SegmentQuery,
    WipStatusOut,
)
from ..tenancy import in_product, product_for_new_rows

router = APIRouter(tags=["boards"])

__all__ = [
    "TEMPLATES",
    "KeyAllocator",
    "board_wip_status",
    "canonical_columns",
    "decorate_card",
    "next_card_key",
    "normalize_structure",
    "require_structure_role",
    "require_structure_write",
    "router",
    "select_cards",
    "wip_state",
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
            (
                "Envelope",
                "upstream",
                "envelope",
                None,
                None,
                None,
                "Demand captured but deliberately not yet examined. Sealed until someone opens it.",
            ),
            (
                "Ideas",
                "upstream",
                "queue",
                3,
                14,
                "Discovery",
                "Raw options waiting to be looked at. Pure queue time.",
            ),
            (
                "Discovery",
                "upstream",
                "activity",
                2,
                6,
                "Discovery",
                "Active problem framing: who wants this, what changes, what is the risk.",
            ),
            (
                "Options",
                "upstream",
                "queue",
                2,
                10,
                "Discovery",
                "Understood, shaped, and deferred until the last responsible moment.",
            ),
            (
                "Ready to Commit",
                "upstream",
                "queue",
                5,
                8,
                "Discovery",
                "Replenishment buffer feeding the commitment point.",
            ),
            (
                "Analysis",
                "downstream",
                "activity",
                2,
                6,
                "Delivery",
                "The commitment point. Once work enters here the team owes a delivery.",
            ),
            (
                "Analysis Done",
                "downstream",
                "queue",
                None,
                6,
                "Delivery",
                "Analysed and waiting for build capacity.",
            ),
            ("Build", "downstream", "activity", 3, 8, "Delivery", "Implementation in progress."),
            (
                "Build Done",
                "downstream",
                "queue",
                None,
                6,
                "Delivery",
                "Built and waiting for verification capacity.",
            ),
            ("Verify", "downstream", "activity", 2, 5, "Delivery", "Review, test and acceptance."),
            (
                "Ready to Deploy",
                "downstream",
                "queue",
                None,
                10,
                "Delivery",
                "Verified and waiting on the release train.",
            ),
            (
                "Delivered",
                "downstream",
                "done",
                None,
                None,
                None,
                "Delivered to the customer. The delivery point.",
            ),
        ],
    },
    "simple_delivery": {
        "label": "Simple delivery",
        "description": "One backlog, one commitment point, three delivery states.",
        "board": {"wip_min": 2, "wip_max": 12, "sle_days": 14, "sle_percentile": 85},
        "groups": [("Delivery", 2, 12, "#3b7ea1")],
        "columns": [
            (
                "Backlog",
                "upstream",
                "queue",
                None,
                None,
                None,
                "Everything requested and not yet committed to. No promise has been made.",
            ),
            (
                "In Progress",
                "downstream",
                "activity",
                1,
                5,
                "Delivery",
                "The commitment point. Work here is owed to someone.",
            ),
            (
                "Review",
                "downstream",
                "queue",
                None,
                3,
                "Delivery",
                "Waiting for review and acceptance.",
            ),
            ("Done", "downstream", "done", None, None, None, "Delivered. The delivery point."),
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
            (
                "Envelope",
                "upstream",
                "envelope",
                None,
                None,
                None,
                "Demand captured but deliberately not yet examined. Sealed until someone opens it.",
            ),
            (
                "Ideas",
                "upstream",
                "queue",
                3,
                12,
                "Discovery",
                "Raw options waiting to be looked at.",
            ),
            (
                "Discovery",
                "upstream",
                "activity",
                2,
                5,
                "Discovery",
                "Active problem framing and option shaping.",
            ),
            (
                "Options",
                "upstream",
                "queue",
                None,
                10,
                "Discovery",
                "Shaped and deferred until the last responsible moment.",
            ),
            (
                "Ready to Commit",
                "upstream",
                "queue",
                3,
                8,
                "Discovery",
                "Replenishment buffer feeding the commitment point.",
            ),
            (
                "Committed",
                "downstream",
                "done",
                None,
                None,
                None,
                "Handed over to delivery. Both the commitment point and the terminal state "
                "of a discovery-only board.",
            ),
        ],
    },
    # The board every Pampufa product gets for its providências (NG12). Editable
    # until the first card arrives; locked afterwards (NG13).
    "providencias": {
        "label": "Providências (Pampufa)",
        "description": (
            "Providências da plataforma Pampufa: cada uma chega como cartão na Entrada e "
            "a sua entrega conclui a providência na plataforma."
        ),
        "board": {"wip_min": None, "wip_max": None, "sle_days": 14, "sle_percentile": 85},
        "groups": [],
        "columns": [
            (
                "Entrada",
                "upstream",
                "queue",
                None,
                None,
                None,
                "Providências que chegaram da plataforma e ainda não foram assumidas.",
            ),
            (
                "A fazer",
                "downstream",
                "queue",
                None,
                None,
                None,
                "O ponto de compromisso: daqui em diante alguém deve uma resposta.",
            ),
            ("Fazendo", "downstream", "activity", None, 3, None, "Em tratamento agora."),
            (
                "Aguardando terceiro",
                "downstream",
                "queue",
                None,
                None,
                None,
                "Depende de alguém de fora: cliente, contador, fornecedor, banco.",
            ),
            (
                "Entregue",
                "downstream",
                "done",
                None,
                None,
                None,
                "Resolvida. Entregar o cartão conclui a providência na Pampufa.",
            ),
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
        raise ValueError("Unknown board template. Choose one of: " + ", ".join(sorted(TEMPLATES)))
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


class ColumnDrainRequest(Schema):
    """``POST /columns/{id}/drain`` — where the emptied column's cards go."""

    to_column_id: str = Field(min_length=1)


class ColumnDrainOut(Schema):
    """What a drain did: the count is what the UI reports before retrying a delete."""

    ok: bool = True
    moved: int = 0
    from_column_id: str
    to_column_id: str
    is_backward: bool = False
    committed: int = 0
    delivered: int = 0
    opened_envelopes: int = 0
    message: str


# ---------------------------------------------------------------------------
# structural helpers
# ---------------------------------------------------------------------------

SEGMENT_RANK: dict[str, int] = {"upstream": 0, "downstream": 1}
STRUCTURE_ROLES: frozenset[str] = frozenset({"master", "administrator"})

#: The coarse gate every structure-mutating route **declares**.
#:
#: It is deliberately wider than :data:`STRUCTURE_ROLES` because ``simulator``
#: shapes boards with ``kind='simulator'`` (SPEC §4) — a decision that needs the
#: board row and therefore stays in :func:`require_structure_write`. Declaring it
#: here keeps the OpenAPI document honest: these routes are writes, and
#: advertising ``read_boards_metrics`` on them told every reader (and every
#: generated client) the opposite.
require_structure_role = require_roles("master", "administrator", "simulator")


def _quote(value: Any) -> str:
    """Render a name the way every error message in this product does."""
    return "—" if value in (None, "") else f"“{value}”"


def _name_list(columns: Sequence[dict[str, Any]]) -> str:
    """``“A”, “B” and “C”`` — readable in an error message."""
    names = [_quote(column.get("name")) for column in columns]
    if not names:
        return "—"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


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


#: Column and group fields that are *policy*, not structure: they stay editable after
#: a product board received its first card (NG13, decision of 15/10/2026).
_COLUMN_POLICY_FIELDS = frozenset({"name", "color", "description", "wip_min", "wip_max"})
_GROUP_POLICY_FIELDS = frozenset({"name", "color", "wip_min", "wip_max"})


async def flow_locked(s: AsyncSession, board: dict[str, Any]) -> bool:
    """A product board's flow is fixed once it holds (or held) a card."""
    if not is_product_board(board):
        return False
    first = await s.scalar(select(Card.id).where(Card.board_id == board["id"]).limit(1))
    return first is not None


async def assert_structure_unlocked(s: AsyncSession, board: dict[str, Any], change: str) -> None:
    """Refuse a structural change on a locked product board (NG13).

    Locked: creating, deleting or reordering columns and groups, changing a
    column's segment or kind, and therefore the commitment and delivery points.
    Still free: WIP limits, the SLE and column names and colours. A new flow is a
    new project linked to the product (NG14).
    """
    assert_board_writable(board)
    if await flow_locked(s, board):
        raise Conflict(
            "flow_locked",
            "This board's flow is locked: it already holds work. WIP limits, the SLE and "
            "column names and colours can still change; for a different flow, link a new "
            "project to the product.",
            {"board_id": board["id"], "product_id": board.get("product_id"), "change": change},
        )


def _column_sort_key(column: dict[str, Any]) -> tuple[int, int, int, str]:
    """Canonical flow order: upstream before downstream, envelope always first."""
    return (
        SEGMENT_RANK.get(column.get("segment"), 1),
        0 if column.get("kind") == "envelope" else 1,
        int(column.get("position") or 0),
        str(column.get("name") or ""),
    )


async def _columns_of(s: AsyncSession, board_id: str) -> list[dict[str, Any]]:
    rows = await s.scalars(select(BoardColumn).where(BoardColumn.board_id == board_id))
    return [as_dict(row) or {} for row in rows]


async def _groups_of(s: AsyncSession, board_id: str) -> list[dict[str, Any]]:
    rows = await s.scalars(
        select(ColumnGroup)
        .where(ColumnGroup.board_id == board_id)
        .order_by(ColumnGroup.position, ColumnGroup.id)
    )
    return [as_dict(row) or {} for row in rows]


async def canonical_columns(s: AsyncSession, board_id: str) -> list[dict[str, Any]]:
    """Every column of a board in flow order (does not write)."""
    return sorted(await _columns_of(s, board_id), key=_column_sort_key)


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


async def _sync_group_positions(
    s: AsyncSession,
    board_id: str,
    ordered: Sequence[dict[str, Any]],
    groups: Sequence[dict[str, Any]] | None = None,
) -> None:
    """Order group headers by the first column each one spans."""
    first_index: dict[str, int] = {}
    for index, column in enumerate(ordered):
        group_id = column.get("group_id")
        if group_id and group_id not in first_index:
            first_index[group_id] = index
    if groups is None:
        groups = await _groups_of(s, board_id)
    # Groups that currently span no column keep their relative order at the end.
    ranked = sorted(
        groups,
        key=lambda g: first_index.get(g["id"], len(ordered) + int(g["position"] or 0)),
    )
    for position, group in enumerate(ranked):
        if int(group["position"] or 0) != position:
            await s.execute(
                update(ColumnGroup).where(ColumnGroup.id == group["id"]).values(position=position)
            )


def assert_groups_contiguous(
    board_id: str,
    ordered: Sequence[dict[str, Any]],
    group_names: dict[str, str] | None = None,
) -> None:
    """A group must span **consecutive** columns (SPEC §9.2 group headers).

    The 409 names the columns on both sides of the argument — the ones the group
    claims and the ones sitting in between — because "not contiguous" on its own
    leaves the reader to diff two lists of uuids by hand.
    """
    runs: dict[str, list[int]] = {}
    for index, column in enumerate(ordered):
        group_id = column.get("group_id")
        if group_id:
            runs.setdefault(group_id, []).append(index)
    for group_id, indexes in runs.items():
        if indexes[-1] - indexes[0] == len(indexes) - 1:
            continue
        members = [ordered[i] for i in indexes]
        held = set(indexes)
        intruders = [ordered[i] for i in range(indexes[0], indexes[-1] + 1) if i not in held]
        name = (group_names or {}).get(group_id)
        label = _quote(name) if name else "That column group"
        raise Conflict(
            "group_not_contiguous",
            f"{label} must span consecutive columns: {_name_list(members)} "
            f"{'is' if len(members) == 1 else 'are'} separated by {_name_list(intruders)}.",
            {
                "board_id": board_id,
                "group_id": group_id,
                "group_name": name,
                "positions": indexes,
                "column_ids": [column["id"] for column in members],
                "columns": [
                    {"id": column["id"], "name": column["name"], "position": index}
                    for index, column in zip(indexes, members, strict=True)
                ],
                "separated_by": [
                    {"id": column["id"], "name": column["name"], "position": column["position"]}
                    for column in intruders
                ],
            },
        )


async def recompute_points(
    s: AsyncSession, board_id: str, ordered: Sequence[dict[str, Any]]
) -> tuple[str | None, str | None]:
    """Re-derive the commitment and delivery points; returns their column ids.

    Commitment point = the first downstream column (SPEC §2.4).
    Delivery point   = the last ``done`` column.
    """
    commitment_id = next((c["id"] for c in ordered if c["segment"] == "downstream"), None)
    delivery_id = next((c["id"] for c in reversed(ordered) if c["kind"] == "done"), None)

    await s.execute(
        update(BoardColumn)
        .where(BoardColumn.board_id == board_id)
        .values(is_commitment_point=False, is_delivery_point=False)
    )
    if commitment_id:
        await s.execute(
            update(BoardColumn)
            .where(BoardColumn.id == commitment_id)
            .values(is_commitment_point=True)
        )
    if delivery_id:
        await s.execute(
            update(BoardColumn).where(BoardColumn.id == delivery_id).values(is_delivery_point=True)
        )
    for column in ordered:
        column["is_commitment_point"] = column["id"] == commitment_id
        column["is_delivery_point"] = column["id"] == delivery_id
    return commitment_id, delivery_id


async def normalize_structure(
    s: AsyncSession, board_id: str, order: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Rewrite positions densely, re-derive the points, validate the groups.

    Every structural mutation ends here, so the invariants hold after a create, a
    delete, a reorder and a segment change alike. Pass ``order`` to impose an
    explicit sequence (the reorder endpoint); omit it to fall back on the
    canonical sort. Runs in the caller's transaction; the caller commits.
    """
    rows = await _columns_of(s, board_id)
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
            await s.execute(
                update(BoardColumn).where(BoardColumn.id == column["id"]).values(position=position)
            )
            column["position"] = position

    await recompute_points(s, board_id, ordered)
    groups = await _groups_of(s, board_id)
    assert_groups_contiguous(board_id, ordered, {group["id"]: group["name"] for group in groups})
    await _sync_group_positions(s, board_id, ordered, groups)
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


def _assert_position_in_range(position: int | None, highest: int, *, action: str) -> None:
    """Reject an insertion index no ordering could ever produce.

    ``position`` is an index into the board's **unified** flow, which is how the
    board renders and how the structure editor counts. Inside that range the
    index is clamped into the column's own segment by :func:`_placement_order`
    (asking for a downstream column "at the front" legitimately means "first
    downstream"); outside it, the caller is confused and silently clamping would
    hide the bug behind a column that quietly landed somewhere else.
    """
    if position is None:
        return
    if not 0 <= int(position) <= highest:
        raise BadRequest(
            f"Position {int(position)} is outside this board's flow: a column can "
            f"be {action} 0…{highest}.",
            code="invalid_position",
            details={"position": int(position), "min": 0, "max": highest},
        )


def _assert_envelope_placement(kind: str, position: int | None) -> None:
    """An envelope column is the first column or it is nothing (SPEC §2.4)."""
    if kind == "envelope" and position is not None and int(position) != 0:
        raise Conflict(
            "envelope_must_be_first",
            "An envelope column may only be the first upstream column, so it "
            "cannot be placed anywhere else in the flow.",
            {"requested_position": int(position), "allowed_position": 0},
        )


async def _assert_delivery_point_survives(
    s: AsyncSession, board_id: str, column: dict[str, Any], kind: str
) -> None:
    """Refuse a re-kind that would leave the board with nowhere to deliver.

    The delivery point is the last ``done`` column, and every delivered-count,
    lead-time and throughput figure is measured against it. Re-kinding the only
    ``done`` column would not fail loudly — it would quietly stop the board from
    ever delivering anything again, so it is refused here instead.
    """
    if column["kind"] != "done" or kind == "done":
        return
    survivor = await s.scalar(
        select(BoardColumn.id).where(
            BoardColumn.board_id == board_id,
            BoardColumn.kind == "done",
            BoardColumn.id != column["id"],
        )
    )
    if survivor is not None:
        return
    raise Conflict(
        "invalid_structure",
        f"{_quote(column['name'])} is this board's only ‘done’ column, so it is the "
        "delivery point. Add another ‘done’ column before changing this one, or the "
        "board would have nowhere to deliver work to.",
        {
            "reason": "delivery_point_required",
            "column_id": column["id"],
            "column": column["name"],
            "board_id": board_id,
            "from_kind": "done",
            "to_kind": kind,
        },
    )


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


async def board_wip_status(
    s: AsyncSession,
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
        columns = await canonical_columns(s, board_id)
    if groups is None:
        groups = [
            as_dict(group) or {}
            for group in await s.scalars(
                select(ColumnGroup)
                .where(ColumnGroup.board_id == board_id)
                .order_by(ColumnGroup.position)
            )
        ]

    occupancy = {
        column_id: int(n)
        for column_id, n in await s.execute(
            select(Card.column_id, func.count())
            .where(Card.board_id == board_id, Card.archived.is_(False))
            .group_by(Card.column_id)
        )
    }
    flow_count = int(
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

    column_cells: dict[str, dict[str, Any]] = {}
    per_group: dict[str, int] = {}
    for column in columns:
        count = occupancy.get(column["id"], 0)
        column_cells[column["id"]] = _cell(count, column["wip_min"], column["wip_max"])
        if column.get("group_id"):
            per_group[column["group_id"]] = per_group.get(column["group_id"], 0) + count

    group_cells: dict[str, dict[str, Any]] = {}
    if await is_flag_effective(s, "wip_group"):
        for group in groups:
            group_cells[group["id"]] = _cell(
                per_group.get(group["id"], 0), group["wip_min"], group["wip_max"]
            )

    if await is_flag_effective(s, "wip_flow"):
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

#: Named orderings for :func:`select_cards` (the board, a project page, a lookup).
CARD_ORDERS: dict[str, Any] = {
    "board": lambda: (BoardColumn.position, Card.position, Card.created_at),
    "created": lambda: (Card.created_at,),
    "project": lambda: (
        case((Card.envelope_state == "sealed", 0), else_=1),
        BoardColumn.position,
        Card.position,
        Card.created_at,
    ),
}


def _card_projection() -> Any:
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
            task_total.label("task_total"),
            task_done.label("task_done"),
            blocked_since.label("blocked_since"),
        )
        .join(BoardColumn, BoardColumn.id == Card.column_id)
        .outerjoin(Project, Project.id == Card.project_id)
        .outerjoin(User, User.id == Card.assignee_id)
    )


def _tag_condition(s: AsyncSession, tag: str) -> Any:
    """``tags`` holds a JSON array of strings: match the element, never a substring."""
    if s.get_bind().dialect.name == "postgresql":
        return type_coerce(Card.tags, JSONB).contains([tag])
    return cast(Card.tags, String).like(f'%"{tag}"%')


def card_filter_clauses(
    s: AsyncSession,
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
) -> list[Any]:
    """Translate the SPEC §5 card query string into SQL conditions."""
    conditions: list[Any] = []
    if project_id:
        conditions.append(Card.project_id == project_id)
    if service_class:
        conditions.append(Card.service_class == service_class)
    if assignee_id:
        conditions.append(Card.assignee_id == assignee_id)
    if column_id:
        conditions.append(Card.column_id == column_id)
    if tag:
        conditions.append(_tag_condition(s, tag.strip()))
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(
            or_(Card.title.ilike(needle), Card.key.ilike(needle), Card.description.ilike(needle))
        )
    if is_blocked is not None:
        conditions.append(Card.is_blocked.is_(bool(is_blocked)))
    if demand_type:
        conditions.append(Card.demand_type == demand_type)
    if value_area:
        conditions.append(Card.value_area == value_area)
    if size:
        conditions.append(Card.size == size)
    if due_before:
        conditions.append(Card.due_date.is_not(None))
        conditions.append(Card.due_date <= due_before)
    if envelope_state:
        conditions.append(Card.envelope_state == envelope_state)
    return conditions


def decorate_card(row: dict[str, Any], now_iso: str) -> dict[str, Any]:
    """Add the derived fields a compact card renders."""
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


async def select_cards(
    s: AsyncSession,
    *,
    board_id: str | None = None,
    column_ids: Sequence[str] | None = None,
    card_id: str | None = None,
    archived: bool = False,
    order: str = "board",
    limit: int | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """One query for every card the caller can see, already decorated."""
    conditions = card_filter_clauses(s, **filters)
    if board_id:
        conditions.insert(0, Card.board_id == board_id)
    if card_id:
        conditions.append(Card.id == card_id)
    conditions.append(Card.archived.is_(bool(archived)))
    if column_ids is not None:
        if not column_ids:
            return []
        conditions.append(Card.column_id.in_(list(column_ids)))

    query = _card_projection().where(*conditions).order_by(*CARD_ORDERS[order]())
    if limit is not None:
        query = query.limit(int(limit))
    now_iso = utcnow()
    out = []
    for row in await s.execute(query):
        data = as_dict(row[0]) or {}
        mapping = row._mapping
        for label in (
            "project_key",
            "project_name",
            "project_color",
            "assignee_name",
            "task_total",
            "task_done",
            "blocked_since",
        ):
            data[label] = mapping[label]
        out.append(decorate_card(data, now_iso))
    return out


# ---------------------------------------------------------------------------
# card keys (shared with the demands endpoint and the clone)
# ---------------------------------------------------------------------------


async def next_card_key(s: AsyncSession, prefix: str, product_id: str | None = None) -> str:
    """The next free ``PREFIX-n`` card key. Numbering starts at 101 like the seed."""
    query = select(Card.key).where(Card.key.like(f"{prefix}-%"))
    if product_id is not None:
        query = query.where(Card.product_id == product_id)
    highest = 100
    for key in await s.scalars(query):
        tail = str(key).rsplit("-", 1)[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1}"


class KeyAllocator:
    """Hands out unique card keys during a bulk write (clone, demand import)."""

    def __init__(self, s: AsyncSession, product_id: str | None = None) -> None:
        self._s = s
        self._product_id = product_id
        self._next: dict[str, int] = {}

    async def take(self, prefix: str) -> str:
        prefix = (prefix or "CARD").upper()
        if prefix not in self._next:
            key = await next_card_key(self._s, prefix, self._product_id)
            self._next[prefix] = int(key.rsplit("-", 1)[-1])
        number = self._next[prefix]
        self._next[prefix] = number + 1
        return f"{prefix}-{number}"


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug or "board"


async def _unique_slug(s: AsyncSession, base: str, product_id: str) -> str:
    slug = _slugify(base)
    candidate = slug
    suffix = 2
    while await s.scalar(
        select(Board.id).where(Board.slug == candidate, Board.product_id == product_id)
    ):
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


async def _card_count(s: AsyncSession, board_id: str) -> int:
    return int(
        await s.scalar(
            select(func.count()).where(Card.board_id == board_id, Card.archived.is_(False))
        )
        or 0
    )


async def _board_out(
    s: AsyncSession, board: dict[str, Any], *, structure: bool = True
) -> dict[str, Any]:
    """Board row plus (optionally) its groups, columns and card count."""
    out = dict(board)
    out["card_count"] = await _card_count(s, board["id"])
    # What the screens need to offer only the edits the lock allows (NG13, N2).
    out["product_board"] = is_product_board(board)
    out["flow_locked"] = await flow_locked(s, board)
    if structure:
        out["columns"] = await canonical_columns(s, board["id"])
        out["groups"] = [
            as_dict(group) or {}
            for group in await s.scalars(
                select(ColumnGroup)
                .where(ColumnGroup.board_id == board["id"])
                .order_by(ColumnGroup.position)
            )
        ]
    return out


async def _get_column_or_404(s: AsyncSession, column_id: str) -> dict[str, Any]:
    row = as_dict(await s.get(BoardColumn, column_id))
    if row is None:
        raise NotFound("Column", column_id, code="column_not_found")
    return row


async def _get_group_or_404(s: AsyncSession, group_id: str) -> dict[str, Any]:
    row = as_dict(await s.get(ColumnGroup, group_id))
    if row is None:
        raise NotFound("Column group", group_id, code="group_not_found")
    return row


async def _assert_group_on_board(s: AsyncSession, board_id: str, group_id: str | None) -> None:
    if not group_id:
        return
    row = await s.scalar(
        select(ColumnGroup.id).where(ColumnGroup.id == group_id, ColumnGroup.board_id == board_id)
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


async def _assert_single_envelope(
    s: AsyncSession, board_id: str, exclude_id: str | None = None
) -> None:
    query = select(BoardColumn).where(
        BoardColumn.board_id == board_id, BoardColumn.kind == "envelope"
    )
    if exclude_id:
        query = query.where(BoardColumn.id != exclude_id)
    existing = await s.scalar(query.limit(1))
    if existing is not None:
        raise Conflict(
            "envelope_column_exists",
            "A board has at most one envelope column, and it is always the first one.",
            {"column_id": existing.id, "name": existing.name},
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
    if payload_delivery is not None and bool(payload_delivery) != bool(column["is_delivery_point"]):
        raise Conflict(
            "delivery_point_is_derived",
            "The delivery point is always the last 'done' column. Change the column's "
            "kind or position instead of setting the flag.",
            {"column_id": column["id"], "is_delivery_point": bool(column["is_delivery_point"])},
        )


async def _apply_template(s: AsyncSession, board_id: str, template: str) -> None:
    """Write a template's groups and columns onto a freshly created board."""
    spec = TEMPLATES[template]
    group_ids: dict[str, str] = {}
    for position, (name, wip_min, wip_max, color) in enumerate(spec["groups"]):
        group_id = new_id()
        group_ids[name] = group_id
        s.add(
            ColumnGroup(
                id=group_id,
                board_id=board_id,
                name=name,
                position=position,
                wip_min=wip_min,
                wip_max=wip_max,
                color=color,
            )
        )
    await s.flush()
    for position, (name, segment, kind, wip_min, wip_max, group, description) in enumerate(
        spec["columns"]
    ):
        s.add(
            BoardColumn(
                id=new_id(),
                board_id=board_id,
                group_id=group_ids.get(group) if group else None,
                name=name,
                position=position,
                segment=segment,
                kind=kind,
                wip_min=wip_min,
                wip_max=wip_max,
                is_commitment_point=False,
                is_delivery_point=False,
                color=None,
                description=description,
            )
        )
    await s.flush()


async def _assert_may_create(s: AsyncSession, user: CurrentUser, kind: str) -> None:
    """Simulator boards for the simulator roles (flag-gated); the rest for structure roles."""
    if kind == "simulator":
        if user.get("role") not in {"master", "administrator", "simulator"}:
            raise Forbidden(
                "Your role cannot create simulator boards.",
                code="insufficient_role",
                details={"role": user.get("role")},
            )
        await require_flag("simulator")(s)
    elif user.get("role") not in STRUCTURE_ROLES:
        raise Forbidden(
            "Your role cannot create boards.",
            code="insufficient_role",
            details={"role": user.get("role"), "required": sorted(STRUCTURE_ROLES)},
        )


# ---------------------------------------------------------------------------
# boards
# ---------------------------------------------------------------------------


@router.get("/boards", response_model=ListResponse[BoardOut], summary="List boards")
async def list_boards(
    kind: str | None = Query(None, pattern="^(operational|simulator)$"),
    q: str | None = Query(None, description="Match the board name or slug"),
    archived: bool = Query(False),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Every board the workspace has, newest first, with a live card count."""
    conditions: list[Any] = [Board.archived.is_(bool(archived)), *in_product(Board)]
    if kind:
        conditions.append(Board.kind == kind)
    if q:
        needle = f"%{q.strip()}%"
        conditions.append(or_(Board.name.ilike(needle), Board.slug.ilike(needle)))
    card_count = (
        select(func.count())
        .where(Card.board_id == Board.id, Card.archived.is_(False))
        .correlate(Board)
        .scalar_subquery()
    )
    rows = await s.execute(
        select(Board, card_count.label("card_count"))
        .where(*conditions)
        .order_by(Board.kind, Board.created_at.desc())
    )
    items = [{**(as_dict(row[0]) or {}), "card_count": int(row[1] or 0)} for row in rows]
    return {"items": items, "total": len(items)}


@router.post(
    "/boards",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a board from a template",
)
async def create_board(
    payload: BoardCreateRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Create a board and lay out the chosen template's groups and columns."""
    await _assert_may_create(s, user, payload.kind)
    board_id = await create_board_from_template(
        s,
        product_id=product_for_new_rows(),
        name=payload.name,
        template=payload.template or "blank",
        kind=payload.kind,
        description=payload.description,
        count_envelope_in_metrics=bool(payload.count_envelope_in_metrics),
        confirm_moves=bool(payload.confirm_moves),
        wip_min=payload.wip_min,
        wip_max=payload.wip_max,
        sle_days=payload.sle_days,
        sle_percentile=payload.sle_percentile,
    )
    await s.commit()

    board = await get_board_or_404(s, board_id)
    return await _board_out(s, board)


async def create_board_from_template(
    s: AsyncSession,
    *,
    product_id: str,
    name: str,
    template: str,
    kind: str = "operational",
    description: str | None = None,
    count_envelope_in_metrics: bool = False,
    confirm_moves: bool = True,
    wip_min: int | None = None,
    wip_max: int | None = None,
    sle_days: int | None = None,
    sle_percentile: int | None = None,
) -> str:
    """Write a board and its template's structure in the caller's transaction."""
    defaults = TEMPLATES[template]["board"]
    board_id = new_id()
    s.add(
        Board(
            id=board_id,
            product_id=product_id,
            name=name,
            slug=await _unique_slug(s, name, product_id),
            description=description or TEMPLATES[template]["description"],
            kind=kind,
            source_board_id=None,
            count_envelope_in_metrics=count_envelope_in_metrics,
            confirm_moves=confirm_moves,
            wip_min=wip_min if wip_min is not None else defaults["wip_min"],
            wip_max=wip_max if wip_max is not None else defaults["wip_max"],
            sle_days=sle_days if sle_days is not None else defaults["sle_days"],
            sle_percentile=sle_percentile
            if sle_percentile is not None
            else defaults["sle_percentile"],
            created_at=utcnow(),
            archived=False,
        )
    )
    await s.flush()
    await _apply_template(s, board_id, template)
    await normalize_structure(s, board_id)
    return board_id


@router.get("/boards/templates", summary="Board templates available to POST /boards")
async def list_templates(
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """The template catalogue, so the "new board" dialog needs no hard-coded copy."""
    items = [
        {
            "key": key,
            "label": spec["label"],
            "description": spec["description"],
            "column_count": len(spec["columns"]),
            "columns": [{"name": c[0], "segment": c[1], "kind": c[2]} for c in spec["columns"]],
            "groups": [g[0] for g in spec["groups"]],
        }
        for key, spec in TEMPLATES.items()
    ]
    return {"items": items, "total": len(items)}


@router.get("/boards/{id}", response_model=BoardOut, summary="One board with its structure")
async def get_board(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """The board row plus its groups and columns, including every WIP policy."""
    return await _board_out(s, await get_board_or_404(s, id))


@router.patch("/boards/{id}", response_model=BoardOut, summary="Update board policies")
async def update_board(
    id: str,
    payload: BoardUpdate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Rename a board or change its WIP, SLE, envelope and confirmation policies."""
    board = await get_board_or_404(s, id)
    require_structure_write(user, board)
    assert_board_writable(board)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return await _board_out(s, board)
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
    await s.execute(update(Board).where(Board.id == board["id"]).values(**fields))
    await s.commit()
    return await _board_out(s, await get_board_or_404(s, board["id"]))


@router.delete("/boards/{id}", response_model=OkResponse, summary="Archive or delete a board")
async def delete_board(
    id: str,
    hard: bool = Query(False, description="Master only: destroy the board and its history"),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Archive a board by default; ``?hard=true`` (master) deletes it outright.

    Archiving is the safe default because a board's movement history is the only
    record of how the system behaved — deleting it silently destroys every metric
    computed from it.
    """
    board = await get_board_or_404(s, id)
    require_structure_write(user, board)
    if hard:
        if user.get("role") != "master":
            raise Forbidden(
                "Only the master account can permanently delete a board.",
                code="insufficient_role",
                details={"role": user.get("role")},
            )
        await s.execute(delete(Board).where(Board.id == board["id"]))
        await s.commit()
        return {"ok": True, "id": board["id"], "message": "Board deleted with its history."}
    await s.execute(update(Board).where(Board.id == board["id"]).values(archived=True))
    await s.commit()
    return {"ok": True, "id": board["id"], "message": "Board archived."}


@router.post(
    "/boards/{id}/clone",
    response_model=BoardOut,
    status_code=status.HTTP_201_CREATED,
    summary="Deep clone a board",
)
async def clone_board(
    id: str,
    payload: BoardCloneRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Copy a board's structure — and optionally its cards and movement history.

    This is what a simulator board is built from (SPEC §8.1). Cards are copied
    together with their ``card_movements`` rows: the timestamps on a card
    (``committed_at``, ``delivered_at``) are *read off* that history by the metric
    layer, so a card copied without it would be a card that lies.
    """
    source = await get_board_or_404(s, id)
    await _assert_may_create(s, user, payload.kind)
    product_id = source["product_id"]

    new_board_id = new_id()
    now = utcnow()
    s.add(
        Board(
            id=new_board_id,
            product_id=product_id,
            name=payload.name,
            slug=await _unique_slug(s, payload.name, product_id),
            description=source["description"],
            kind=payload.kind,
            source_board_id=source["id"],
            count_envelope_in_metrics=bool(source["count_envelope_in_metrics"]),
            confirm_moves=bool(source["confirm_moves"]),
            wip_min=source["wip_min"],
            wip_max=source["wip_max"],
            sle_days=source["sle_days"],
            sle_percentile=source["sle_percentile"],
            created_at=now,
            archived=False,
        )
    )
    await s.flush()

    group_map: dict[str, str] = {}
    for group in await s.scalars(
        select(ColumnGroup)
        .where(ColumnGroup.board_id == source["id"])
        .order_by(ColumnGroup.position)
    ):
        group_map[group.id] = new_id()
        s.add(
            ColumnGroup(
                id=group_map[group.id],
                board_id=new_board_id,
                name=group.name,
                position=group.position,
                wip_min=group.wip_min,
                wip_max=group.wip_max,
                color=group.color,
            )
        )
    await s.flush()

    column_map: dict[str, str] = {}
    for column in await canonical_columns(s, source["id"]):
        column_map[column["id"]] = new_id()
        s.add(
            BoardColumn(
                id=column_map[column["id"]],
                board_id=new_board_id,
                group_id=group_map.get(column["group_id"]) if column["group_id"] else None,
                name=column["name"],
                position=column["position"],
                segment=column["segment"],
                kind=column["kind"],
                wip_min=column["wip_min"],
                wip_max=column["wip_max"],
                is_commitment_point=bool(column["is_commitment_point"]),
                is_delivery_point=bool(column["is_delivery_point"]),
                color=column["color"],
                description=column["description"],
            )
        )
    await s.flush()

    copied_cards = 0
    if payload.copy_cards:
        allocator = KeyAllocator(s, product_id)
        card_map: dict[str, str] = {}
        source_cards = await s.execute(
            select(Card, Project.key.label("project_key"))
            .outerjoin(Project, Project.id == Card.project_id)
            .where(Card.board_id == source["id"], Card.archived.is_(False))
        )
        for row in source_cards:
            card = as_dict(row[0]) or {}
            card_map[card["id"]] = new_id()
            prefix = row[1] or _slugify(payload.name).upper()[:3] or "SIM"
            s.add(
                Card(
                    **{
                        **card,
                        "id": card_map[card["id"]],
                        "product_id": product_id,
                        "board_id": new_board_id,
                        "column_id": column_map[card["column_id"]],
                        "key": await allocator.take(prefix),
                        "archived": False,
                    }
                )
            )
            copied_cards += 1
        await s.flush()

        if card_map:
            movements = await s.scalars(
                select(CardMovement)
                .where(CardMovement.card_id.in_(list(card_map)))
                .order_by(CardMovement.moved_at, CardMovement.id)
            )
            movement_rows = [
                {
                    "id": new_id(),
                    "card_id": card_map[m.card_id],
                    "board_id": new_board_id,
                    "from_column_id": column_map.get(m.from_column_id)
                    if m.from_column_id
                    else None,
                    "to_column_id": column_map[m.to_column_id],
                    "from_segment": m.from_segment,
                    "to_segment": m.to_segment,
                    "moved_at": m.moved_at,
                    "moved_by": m.moved_by,
                    "seconds_in_from_column": m.seconds_in_from_column,
                    "note": m.note,
                    "is_backward": m.is_backward,
                    "source": m.source,
                }
                for m in movements
                if m.to_column_id in column_map
            ]
            if movement_rows:
                await s.execute(insert(CardMovement), movement_rows)
            await s.execute(
                insert(CardEvent),
                [
                    {
                        "id": new_id(),
                        "card_id": new_card_id,
                        "board_id": new_board_id,
                        "actor_id": user.get("id"),
                        "event_type": "created",
                        "field": "cloned_from",
                        "old_value": old_card_id,
                        "new_value": new_card_id,
                        "created_at": now,
                    }
                    for old_card_id, new_card_id in card_map.items()
                ],
            )

    await normalize_structure(s, new_board_id)
    await s.commit()

    board = await get_board_or_404(s, new_board_id)
    out = await _board_out(s, board)
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
async def board_view(
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
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Board, groups, columns, cards and the full WIP block in four queries.

    The frontend never assembles a board from several round trips: one call
    returns the structure, the positioned cards for the requested segment and the
    WIP state of every policy, so the render is atomic and can never show cards
    against a stale column layout.
    """
    board = await get_board_or_404(s, id)
    if segment == "unified":
        # Seeing both halves as one continuous flow is the Team-plan feature.
        await require_flag("unified_flow")(s)

    columns = await canonical_columns(s, board["id"])
    visible = columns if segment == "unified" else [c for c in columns if c["segment"] == segment]

    groups = [
        as_dict(group) or {}
        for group in await s.scalars(
            select(ColumnGroup)
            .where(ColumnGroup.board_id == board["id"])
            .order_by(ColumnGroup.position)
        )
    ]
    visible_group_ids = {c["group_id"] for c in visible if c["group_id"]}
    visible_groups = [g for g in groups if g["id"] in visible_group_ids]

    cards = await select_cards(
        s,
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

    wip = await board_wip_status(s, board, columns=columns, groups=groups)
    commitment = next((c["id"] for c in columns if c["is_commitment_point"]), None)
    delivery = next((c["id"] for c in columns if c["is_delivery_point"]), None)

    return {
        "board": await _board_out(s, board, structure=False),
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
async def board_wip(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_capability("read_boards_metrics")),
) -> dict[str, Any]:
    """Counts against min/max with a ``under|ok|at_limit|over`` state each."""
    board = await get_board_or_404(s, id)
    return await board_wip_status(s, board)


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------


@router.post(
    "/boards/{id}/columns",
    response_model=ColumnOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a column",
)
async def create_column(
    id: str,
    payload: ColumnCreate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Insert a column at ``position`` inside its own segment, then re-derive the points.

    ``position`` is optional: omitting it appends the column to the end of its own
    segment, which is what "add a column" means without a chosen insertion point.
    """
    board = await get_board_or_404(s, id)
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "create_column")
    _validate_column_shape(payload.segment, payload.kind)
    _assert_envelope_placement(payload.kind, payload.position)
    if (
        payload.wip_min is not None
        and payload.wip_max is not None
        and payload.wip_min > payload.wip_max
    ):
        raise BadRequest(
            "The column WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": payload.wip_min, "wip_max": payload.wip_max},
        )

    column_id = new_id()
    if payload.kind == "envelope":
        await _assert_single_envelope(s, board["id"])
    await _assert_group_on_board(s, board["id"], payload.group_id)
    existing = await canonical_columns(s, board["id"])
    # A new column may be inserted anywhere from the front to just past the
    # last existing one, so the range is one wider than the board.
    _assert_position_in_range(payload.position, len(existing), action="inserted at")
    s.add(
        BoardColumn(
            id=column_id,
            board_id=board["id"],
            group_id=payload.group_id,
            name=payload.name,
            position=len(existing),
            segment=payload.segment,
            kind=payload.kind,
            wip_min=payload.wip_min,
            wip_max=payload.wip_max,
            is_commitment_point=False,
            is_delivery_point=False,
            color=payload.color,
            description=payload.description,
        )
    )
    await s.flush()
    fresh = {
        "id": column_id,
        "segment": payload.segment,
        "kind": payload.kind,
        "position": len(existing),
    }
    order = _placement_order(existing, fresh, payload.position)
    await normalize_structure(s, board["id"], order)
    created = await _get_column_or_404(s, column_id)
    _assert_derived_points(payload.is_commitment_point, payload.is_delivery_point, created)
    await s.commit()
    return created


@router.patch("/columns/{id}", response_model=ColumnOut, summary="Update a column")
async def update_column(
    id: str,
    payload: ColumnUpdate,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Rename, re-limit, re-group, re-segment or move a column.

    A segment change is a structural event: it can hand the commitment point to a
    different column, which is why every path ends in
    :func:`normalize_structure` rather than trusting the caller. Changing
    ``kind`` or ``group_id`` goes through the same funnel, so the delivery point
    and the group headers follow a re-kind exactly as the commitment point
    follows a re-segment.
    """
    column = await _get_column_or_404(s, id)
    board = await get_board_or_404(s, column["board_id"])
    require_structure_write(user, board)

    fields = payload.model_dump(exclude_unset=True)
    wants_commitment = fields.pop("is_commitment_point", None)
    wants_delivery = fields.pop("is_delivery_point", None)
    target_position = fields.pop("position", None)
    structural = (
        bool(set(fields) - _COLUMN_POLICY_FIELDS)
        or target_position is not None
        or wants_commitment is not None
        or wants_delivery is not None
    )
    if structural:
        await assert_structure_unlocked(s, board, "update_column")
    else:
        assert_board_writable(board)

    segment = fields.get("segment", column["segment"])
    kind = fields.get("kind", column["kind"])
    _validate_column_shape(segment, kind)
    _assert_envelope_placement(kind, target_position)
    await _assert_delivery_point_survives(s, board["id"], column, kind)

    wip_min = fields.get("wip_min", column["wip_min"])
    wip_max = fields.get("wip_max", column["wip_max"])
    if wip_min is not None and wip_max is not None and wip_min > wip_max:
        raise BadRequest(
            "The column WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": wip_min, "wip_max": wip_max},
        )

    if kind == "envelope" and column["kind"] != "envelope":
        await _assert_single_envelope(s, board["id"], exclude_id=column["id"])
    if "group_id" in fields:
        await _assert_group_on_board(s, board["id"], fields["group_id"])
    if fields:
        await s.execute(update(BoardColumn).where(BoardColumn.id == column["id"]).values(**fields))
    existing = await canonical_columns(s, board["id"])
    _assert_position_in_range(target_position, max(len(existing) - 1, 0), action="moved to")
    fresh = next((c for c in existing if c["id"] == column["id"]), None)
    if fresh is None:  # pragma: no cover - deleted between the two reads
        raise NotFound("Column", column["id"], code="column_not_found")
    if target_position is not None or fields.get("segment") or kind == "envelope":
        order = _placement_order(existing, fresh, target_position)
        await normalize_structure(s, board["id"], order)
    else:
        await normalize_structure(s, board["id"])

    updated = await _get_column_or_404(s, column["id"])
    _assert_derived_points(wants_commitment, wants_delivery, updated)
    await s.commit()
    return updated


@router.delete("/columns/{id}", response_model=OkResponse, summary="Delete a column")
async def delete_column(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Remove an **empty** column and re-derive the board's points.

    The 409 carries the card count so the caller can offer the real remedy —
    ``POST /columns/{id}/drain`` — instead of a dead end.

    Deleting a column is not free even once it is empty: ``card_movements``
    declares ``to_column_id ... ON DELETE CASCADE``, so every movement that ever
    *ended* in this column goes with it, and the cards that passed through lose
    that interval from their dwell history. The count of rows about to be
    removed is reported back in the message rather than left to be discovered
    later in a chart that quietly changed shape.
    """
    column = await _get_column_or_404(s, id)
    board = await get_board_or_404(s, column["board_id"])
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "delete_column")

    occupancy = int(await s.scalar(select(func.count()).where(Card.column_id == column["id"])) or 0)
    if occupancy:
        raise Conflict(
            "column_not_empty",
            f"{_quote(column['name'])} still holds {occupancy} "
            f"card{'s' if occupancy != 1 else ''}. Move them to another column "
            "before deleting it.",
            {
                "column_id": column["id"],
                "column": column["name"],
                "board_id": board["id"],
                "card_count": occupancy,
                "drain_endpoint": f"/api/v1/columns/{column['id']}/drain",
            },
        )
    history = int(
        await s.scalar(select(func.count()).where(CardMovement.to_column_id == column["id"])) or 0
    )
    await s.execute(delete(BoardColumn).where(BoardColumn.id == column["id"]))
    await normalize_structure(s, board["id"])
    await s.commit()
    message = f"Column {_quote(column['name'])} deleted."
    if history:
        message += (
            f" {history} movement record{'s' if history != 1 else ''} that ended in it "
            "went with it."
        )
    return {"ok": True, "id": column["id"], "message": message}


@router.post(
    "/boards/{id}/columns/reorder",
    response_model=ListResponse[ColumnOut],
    summary="Reorder columns",
)
async def reorder_columns(
    id: str,
    payload: ColumnReorderRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Apply an explicit column order, keeping ``position`` dense and legal.

    ``ordered_ids`` must name **every** column on the board. A partial list is
    refused rather than completed with a guess: the omitted columns would be
    swept to the end of their segment, so a caller that forgot half the board
    would silently re-shuffle work it never meant to touch — and, because
    ``position`` is dense, would have no way to tell afterwards.
    """
    board = await get_board_or_404(s, id)
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "reorder_columns")

    columns = await canonical_columns(s, board["id"])
    known = {column["id"]: column for column in columns}
    requested = list(payload.ordered_ids)
    unknown = [column_id for column_id in requested if column_id not in known]
    if unknown:
        raise BadRequest(
            "Those columns do not belong to this board.",
            code="column_not_on_board",
            details={"board_id": board["id"], "column_ids": unknown},
        )
    missing = [column for column in columns if column["id"] not in set(requested)]
    if missing:
        raise BadRequest(
            f"A reorder must list every column on this board. Missing: {_name_list(missing)}.",
            code="incomplete_column_order",
            details={
                "board_id": board["id"],
                "expected": len(columns),
                "received": len(requested),
                "missing": [{"id": column["id"], "name": column["name"]} for column in missing],
            },
        )

    ordered = await normalize_structure(s, board["id"], requested)
    await s.commit()
    return {"items": ordered, "total": len(ordered)}


@router.post(
    "/columns/{id}/drain",
    response_model=ColumnDrainOut,
    summary="Move every card out of a column so it can be deleted",
)
async def drain_column(
    id: str,
    payload: ColumnDrainRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Empty a column into another column of the same board, in one transaction.

    This is the remedy behind ``409 column_not_empty``: the user chose "move the
    cards, then delete". Every card gets a real ``card_movements`` row and a
    ``moved`` ``card_event``, because a card that changes column without one
    would leave a hole in the history that every metric replays — CFD, dwell,
    flow efficiency and the simulator all rebuild themselves from those rows.

    Three deliberate departures from :func:`~backend.app.routers.movements.move_card`:

    * **backward is allowed.** A column created by mistake sits wherever it was
      created; if draining could only go forward, that column could never be
      removed. Backward drains are recorded with ``is_backward`` and an audit
      event, exactly like a forced move, so nothing is hidden.
    * **WIP maxima do not block it.** This is structural repair, not
      replenishment; refusing to empty a column because the destination is full
      would trap the board in the broken shape the user is trying to fix.
    * **sealed demands are opened.** A sealed card outside an envelope column
      can neither move nor be opened, so leaving it sealed would strand it.

    Crossing the commitment point or landing on the delivery point stamps
    ``committed_at`` / ``delivered_at`` the same way a move does — otherwise the
    board would show work downstream that the flow metrics cannot see.
    """
    column = await _get_column_or_404(s, id)
    board = await get_board_or_404(s, column["board_id"])
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "drain_column")

    target = await _get_column_or_404(s, payload.to_column_id)
    if target["board_id"] != board["id"]:
        raise BadRequest(
            "That column belongs to a different board.",
            code="column_board_mismatch",
            details={"board_id": board["id"], "column_id": target["id"]},
        )
    if target["id"] == column["id"]:
        raise Conflict(
            "same_column_move",
            f"{_quote(column['name'])} cannot be drained into itself — choose "
            "another column to hold its cards.",
            {"column_id": column["id"]},
        )

    columns = await canonical_columns(s, board["id"])
    order = {c["id"]: index for index, c in enumerate(columns)}
    is_backward = order[target["id"]] < order[column["id"]]
    commitment_index = next(
        (index for index, c in enumerate(columns) if c["segment"] == "downstream"), None
    )
    delivery_id = next((c["id"] for c in reversed(columns) if c["kind"] == "done"), None)
    crosses_commitment = commitment_index is not None and order[target["id"]] >= commitment_index
    reaches_delivery = delivery_id is not None and target["id"] == delivery_id
    opens_envelope = column["kind"] == "envelope"

    now = utcnow()
    note = f"Column {_quote(column['name'])} drained into {_quote(target['name'])}."
    committed = delivered = opened = 0

    cards = [
        as_dict(card) or {}
        for card in await s.scalars(
            select(Card)
            .where(Card.column_id == column["id"])
            .order_by(Card.position, Card.created_at)
        )
    ]
    if cards:
        last_moved = {
            card_id: moved_at
            for card_id, moved_at in await s.execute(
                select(CardMovement.card_id, func.max(CardMovement.moved_at))
                .where(CardMovement.card_id.in_([card["id"] for card in cards]))
                .group_by(CardMovement.card_id)
            )
        }
        highest = await s.scalar(
            select(func.coalesce(func.max(Card.position), -1)).where(Card.column_id == target["id"])
        )
        position = int(highest if highest is not None else -1)
        movements: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        def event(card_id: str, event_type: str, field: str, old: Any, new: Any) -> None:
            events.append(
                {
                    "id": new_id(),
                    "card_id": card_id,
                    "board_id": board["id"],
                    "actor_id": user["id"],
                    "event_type": event_type,
                    "field": field,
                    "old_value": old,
                    "new_value": new,
                    "created_at": now,
                }
            )

        for card in cards:
            position += 1
            elapsed = seconds_between(last_moved.get(card["id"]) or card["created_at"], now)
            movements.append(
                {
                    "id": new_id(),
                    "card_id": card["id"],
                    "board_id": board["id"],
                    "from_column_id": column["id"],
                    "to_column_id": target["id"],
                    "from_segment": column["segment"],
                    "to_segment": target["segment"],
                    "moved_at": now,
                    "moved_by": user["id"],
                    "seconds_in_from_column": (
                        None if elapsed is None else round(max(0.0, elapsed))
                    ),
                    "note": note,
                    "is_backward": bool(is_backward),
                    "source": "ui",
                }
            )
            event(card["id"], "moved", "column", column["name"], target["name"])
            if is_backward:
                # Same field the forced-move path writes, so the card's history
                # reads "Backward move forced from X to Y".
                event(card["id"], "moved", "backward_override", column["name"], target["name"])

            will_commit = crosses_commitment and not card["committed_at"]
            will_deliver = reaches_delivery and not card["delivered_at"]
            will_open = opens_envelope and card["envelope_state"] == "sealed"
            values: dict[str, Any] = {"column_id": target["id"], "position": position}
            if will_commit:
                committed += 1
                event(card["id"], "updated", "committed_at", None, now)
                values["committed_at"] = now
            if will_deliver:
                delivered += 1
                event(card["id"], "updated", "delivered_at", None, now)
                values["delivered_at"] = now
            if will_open:
                opened += 1
                event(card["id"], "opened_envelope", "envelope_state", "sealed", "opened")
                values["envelope_state"] = "opened"
                if not card["opened_at"]:
                    values["opened_at"] = now
            await s.execute(update(Card).where(Card.id == card["id"]).values(**values))

        await s.execute(insert(CardMovement), movements)
        await s.execute(insert(CardEvent), events)
    await s.commit()

    moved = len(cards)
    if moved:
        message = (
            f"Moved {moved} card{'s' if moved != 1 else ''} from "
            f"{_quote(column['name'])} to {_quote(target['name'])}"
            f"{' against the flow' if is_backward else ''}."
        )
    else:
        message = f"{_quote(column['name'])} was already empty."
    return {
        "ok": True,
        "moved": moved,
        "from_column_id": column["id"],
        "to_column_id": target["id"],
        "is_backward": is_backward,
        "committed": committed,
        "delivered": delivered,
        "opened_envelopes": opened,
        "message": message,
    }


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


async def _assign_group_columns(
    s: AsyncSession, board_id: str, group_id: str, column_ids: Sequence[str]
) -> None:
    """Point exactly ``column_ids`` at ``group_id`` (validated against the board)."""
    for column_id in column_ids:
        row = await s.scalar(
            select(BoardColumn.id).where(
                BoardColumn.id == column_id, BoardColumn.board_id == board_id
            )
        )
        if row is None:
            raise BadRequest(
                "Those columns do not belong to this board.",
                code="column_not_on_board",
                details={"column_id": column_id, "board_id": board_id},
            )
    await s.execute(
        update(BoardColumn).where(BoardColumn.group_id == group_id).values(group_id=None)
    )
    if column_ids:
        await s.execute(
            update(BoardColumn)
            .where(BoardColumn.id.in_(list(column_ids)))
            .values(group_id=group_id)
        )


@router.post(
    "/boards/{id}/groups",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a column group",
    dependencies=[Depends(require_flag("wip_group"))],
)
async def create_group(
    id: str,
    payload: GroupWriteRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
) -> dict[str, Any]:
    """Group consecutive columns under one header with its own WIP limits."""
    board = await get_board_or_404(s, id)
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "create_group")
    if (
        payload.wip_min is not None
        and payload.wip_max is not None
        and payload.wip_min > payload.wip_max
    ):
        raise BadRequest(
            "The group WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": payload.wip_min, "wip_max": payload.wip_max},
        )

    group_id = new_id()
    position = payload.position
    if position is None:
        position = int(
            await s.scalar(select(func.count()).where(ColumnGroup.board_id == board["id"])) or 0
        )
    s.add(
        ColumnGroup(
            id=group_id,
            board_id=board["id"],
            name=payload.name,
            position=position,
            wip_min=payload.wip_min,
            wip_max=payload.wip_max,
            color=payload.color,
        )
    )
    await s.flush()
    if payload.column_ids:
        await _assign_group_columns(s, board["id"], group_id, payload.column_ids)
    await normalize_structure(s, board["id"])
    await s.commit()
    return await _get_group_or_404(s, group_id)


@router.patch("/groups/{id}", response_model=GroupOut, summary="Update a column group")
async def update_group(
    id: str,
    payload: GroupPatchRequest,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
    _: None = Depends(require_flag("wip_group")),
) -> dict[str, Any]:
    """Rename a group, change its limits, or replace the columns it spans."""
    group = await _get_group_or_404(s, id)
    board = await get_board_or_404(s, group["board_id"])
    require_structure_write(user, board)

    fields = payload.model_dump(exclude_unset=True)
    column_ids = fields.pop("column_ids", None)
    if column_ids is not None or set(fields) - _GROUP_POLICY_FIELDS:
        await assert_structure_unlocked(s, board, "update_group")
    else:
        assert_board_writable(board)
    wip_min = fields.get("wip_min", group["wip_min"])
    wip_max = fields.get("wip_max", group["wip_max"])
    if wip_min is not None and wip_max is not None and wip_min > wip_max:
        raise BadRequest(
            "The group WIP minimum cannot exceed its maximum.",
            code="invalid_wip_range",
            details={"wip_min": wip_min, "wip_max": wip_max},
        )

    if fields:
        await s.execute(update(ColumnGroup).where(ColumnGroup.id == group["id"]).values(**fields))
    if column_ids is not None:
        await _assign_group_columns(s, board["id"], group["id"], column_ids)
    await normalize_structure(s, board["id"])
    await s.commit()
    return await _get_group_or_404(s, group["id"])


@router.delete("/groups/{id}", response_model=OkResponse, summary="Delete a column group")
async def delete_group(
    id: str,
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(require_structure_role),
    _: None = Depends(require_flag("wip_group")),
) -> dict[str, Any]:
    """Drop the header; its columns stay on the board, ungrouped."""
    group = await _get_group_or_404(s, id)
    board = await get_board_or_404(s, group["board_id"])
    require_structure_write(user, board)
    await assert_structure_unlocked(s, board, "delete_group")
    await s.execute(
        update(BoardColumn).where(BoardColumn.group_id == group["id"]).values(group_id=None)
    )
    await s.execute(delete(ColumnGroup).where(ColumnGroup.id == group["id"]))
    await normalize_structure(s, board["id"])
    await s.commit()
    return {"ok": True, "id": group["id"], "message": "Column group deleted."}
