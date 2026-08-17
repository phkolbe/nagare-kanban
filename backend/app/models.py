"""Dataclasses mirroring the DDL, with row <-> dict mapping.

sqlite stores booleans as ``0/1`` and JSON columns as text. These models are the
one place that translation happens, so routers can hand
``Card.from_row(row).to_dict()`` straight to pydantic and get real ``bool``,
``list`` and ``dict`` values.

* ``from_row(row)``  — sqlite row (dict) -> dataclass, coercing types
* ``to_row()``       — dataclass -> dict ready for an INSERT/UPDATE
* ``to_dict()``      — dataclass -> JSON-friendly dict for API responses
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields as dataclass_fields
from typing import Any, ClassVar, Iterable, Sequence, TypeVar

__all__ = [
    "Record",
    "User",
    "Board",
    "ColumnGroup",
    "Column",
    "Project",
    "Card",
    "CardMovement",
    "CardEvent",
    "CardTask",
    "Blocker",
    "Comment",
    "FeatureFlag",
    "Plan",
    "Workspace",
    "SimulationRun",
    "MODEL_BY_TABLE",
    "to_bool",
    "to_int",
    "json_loads",
    "json_dumps",
    "rows_to",
]

T = TypeVar("T", bound="Record")


# ---------------------------------------------------------------------------
# coercion helpers
# ---------------------------------------------------------------------------


def to_bool(value: Any) -> bool:
    """Coerce sqlite's ``0/1`` (or a JSON bool/string) to a real ``bool``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def to_int(value: Any) -> int | None:
    """Coerce to ``int`` when possible, otherwise ``None``."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def json_loads(value: Any, default: Any) -> Any:
    """Parse a JSON text column, falling back to ``default`` on anything odd."""
    if value is None or value == "":
        return default() if callable(default) else default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default() if callable(default) else default
    if parsed is None:
        return default() if callable(default) else default
    return parsed


def json_dumps(value: Any) -> str:
    """Serialise a JSON column value compactly and deterministically."""
    if value is None:
        return "null"
    if isinstance(value, str):
        # Already-serialised values pass through untouched when they parse.
        try:
            json.loads(value)
            return value
        except (ValueError, json.JSONDecodeError):
            return json.dumps(value)
    return json.dumps(value, separators=(",", ":"), default=str)


# ---------------------------------------------------------------------------
# base record
# ---------------------------------------------------------------------------


@dataclass
class Record:
    """Base for every table dataclass."""

    TABLE: ClassVar[str] = ""
    #: column name -> default value used when the stored JSON is unusable
    JSON_FIELDS: ClassVar[dict[str, Any]] = {}
    #: columns stored as INTEGER 0/1
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ()
    #: columns stored as INTEGER
    INT_FIELDS: ClassVar[tuple[str, ...]] = ()

    @classmethod
    def column_names(cls) -> tuple[str, ...]:
        """The table's column names, in declaration order."""
        return tuple(f.name for f in dataclass_fields(cls))

    @classmethod
    def from_row(cls: type[T], row: dict[str, Any] | None) -> T | None:
        """Build an instance from a sqlite row, ignoring unknown columns."""
        if row is None:
            return None
        known = set(cls.column_names())
        data: dict[str, Any] = {k: v for k, v in dict(row).items() if k in known}
        for name in cls.BOOL_FIELDS:
            if name in data:
                data[name] = to_bool(data[name])
        for name in cls.INT_FIELDS:
            if name in data:
                data[name] = to_int(data[name])
        for name, default in cls.JSON_FIELDS.items():
            if name in data:
                data[name] = json_loads(data[name], default)
        return cls(**data)  # type: ignore[arg-type]

    def to_row(self) -> dict[str, Any]:
        """Dict shaped for an INSERT/UPDATE: bools as 0/1, JSON as text."""
        out: dict[str, Any] = {}
        for f in dataclass_fields(self):
            value = getattr(self, f.name)
            if f.name in self.BOOL_FIELDS:
                out[f.name] = 1 if to_bool(value) else 0
            elif f.name in self.JSON_FIELDS:
                out[f.name] = json_dumps(value)
            else:
                out[f.name] = value
        return out

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly dict for API responses."""
        out: dict[str, Any] = {}
        for f in dataclass_fields(self):
            value = getattr(self, f.name)
            if f.name in self.BOOL_FIELDS:
                out[f.name] = to_bool(value)
            elif f.name in self.JSON_FIELDS:
                out[f.name] = (
                    value
                    if isinstance(value, (list, dict))
                    else json_loads(value, self.JSON_FIELDS[f.name])
                )
            else:
                out[f.name] = value
        return out

    # -- SQL convenience ----------------------------------------------------

    @classmethod
    def insert_sql(cls) -> str:
        """``INSERT INTO <table> (...) VALUES (?, ?, ...)`` for this record."""
        cols = cls.column_names()
        placeholders = ", ".join("?" for _ in cols)
        return f"INSERT INTO {cls.TABLE} ({', '.join(cols)}) VALUES ({placeholders})"

    def insert_params(self) -> list[Any]:
        """Positional parameters matching :meth:`insert_sql`."""
        row = self.to_row()
        return [row[name] for name in self.column_names()]


def rows_to(model: type[T], rows: Iterable[dict[str, Any]]) -> list[T]:
    """Map a sequence of sqlite rows onto ``model``."""
    out: list[T] = []
    for row in rows:
        built = model.from_row(row)
        if built is not None:
            out.append(built)
    return out


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


@dataclass
class User(Record):
    TABLE: ClassVar[str] = "users"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_active",)

    id: str = ""
    email: str = ""
    name: str = ""
    password_hash: str = ""
    role: str = "viewer"
    is_active: bool = True
    created_at: str = ""
    last_login_at: str | None = None

    def public(self) -> dict[str, Any]:
        """The user without the password hash — what the API is allowed to emit."""
        data = self.to_dict()
        data.pop("password_hash", None)
        return data


@dataclass
class Board(Record):
    TABLE: ClassVar[str] = "boards"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = (
        "count_envelope_in_metrics",
        "confirm_moves",
        "archived",
    )
    INT_FIELDS: ClassVar[tuple[str, ...]] = (
        "wip_min",
        "wip_max",
        "sle_days",
        "sle_percentile",
    )

    id: str = ""
    name: str = ""
    slug: str = ""
    description: str | None = None
    kind: str = "operational"
    source_board_id: str | None = None
    count_envelope_in_metrics: bool = False
    confirm_moves: bool = True
    wip_min: int | None = None
    wip_max: int | None = None
    sle_days: int | None = None
    sle_percentile: int | None = None
    created_at: str = ""
    archived: bool = False


@dataclass
class ColumnGroup(Record):
    TABLE: ClassVar[str] = "column_groups"
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("position", "wip_min", "wip_max")

    id: str = ""
    board_id: str = ""
    name: str = ""
    position: int = 0
    wip_min: int | None = None
    wip_max: int | None = None
    color: str | None = None


@dataclass
class Column(Record):
    TABLE: ClassVar[str] = "columns"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_commitment_point", "is_delivery_point")
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("position", "wip_min", "wip_max")

    id: str = ""
    board_id: str = ""
    group_id: str | None = None
    name: str = ""
    position: int = 0
    segment: str = "upstream"
    kind: str = "queue"
    wip_min: int | None = None
    wip_max: int | None = None
    is_commitment_point: bool = False
    is_delivery_point: bool = False
    color: str | None = None
    description: str | None = None


@dataclass
class Project(Record):
    TABLE: ClassVar[str] = "projects"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("archived",)

    id: str = ""
    name: str = ""
    key: str = ""
    color: str | None = None
    description: str | None = None
    status: str = "active"
    owner_id: str | None = None
    started_at: str | None = None
    target_date: str | None = None
    created_at: str = ""
    archived: bool = False


@dataclass
class Card(Record):
    TABLE: ClassVar[str] = "cards"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_blocked", "archived")
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("position",)
    JSON_FIELDS: ClassVar[dict[str, Any]] = {"tags": list, "custom_fields": dict}

    id: str = ""
    board_id: str = ""
    column_id: str = ""
    project_id: str | None = None
    key: str = ""
    title: str = ""
    description: str | None = None
    service_class: str = "standard"
    assignee_id: str | None = None
    requester: str | None = None
    size: str | None = None
    value_area: str = "feature"
    demand_type: str = "value"
    due_date: str | None = None
    position: int = 0
    envelope_state: str = "opened"
    is_blocked: bool = False
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    opened_at: str | None = None
    committed_at: str | None = None
    delivered_at: str | None = None
    archived: bool = False
    created_by: str | None = None

    @property
    def is_in_flight(self) -> bool:
        """Committed but not yet delivered — the WIP definition in SPEC §7.1."""
        return bool(self.committed_at) and not self.delivered_at


@dataclass
class CardMovement(Record):
    TABLE: ClassVar[str] = "card_movements"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_backward",)
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("seconds_in_from_column",)

    id: str = ""
    card_id: str = ""
    board_id: str = ""
    from_column_id: str | None = None
    to_column_id: str = ""
    from_segment: str | None = None
    to_segment: str = "upstream"
    moved_at: str = ""
    moved_by: str | None = None
    seconds_in_from_column: int | None = None
    note: str | None = None
    is_backward: bool = False
    source: str = "ui"


@dataclass
class CardEvent(Record):
    TABLE: ClassVar[str] = "card_events"

    id: str = ""
    card_id: str = ""
    board_id: str = ""
    actor_id: str | None = None
    event_type: str = "updated"
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    created_at: str = ""


@dataclass
class CardTask(Record):
    TABLE: ClassVar[str] = "card_tasks"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_done",)
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("position",)

    id: str = ""
    card_id: str = ""
    title: str = ""
    is_done: bool = False
    assignee_id: str | None = None
    position: int = 0
    created_at: str = ""
    completed_at: str | None = None


@dataclass
class Blocker(Record):
    TABLE: ClassVar[str] = "blockers"

    id: str = ""
    card_id: str = ""
    reason: str = ""
    category: str | None = None
    started_at: str = ""
    ended_at: str | None = None
    created_by: str | None = None

    @property
    def is_open(self) -> bool:
        """A blocker with no ``ended_at`` is still costing the card time."""
        return not self.ended_at


@dataclass
class Comment(Record):
    TABLE: ClassVar[str] = "comments"

    id: str = ""
    card_id: str = ""
    author_id: str | None = None
    body: str = ""
    created_at: str = ""


@dataclass
class FeatureFlag(Record):
    TABLE: ClassVar[str] = "feature_flags"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_enabled",)

    key: str = ""
    name: str = ""
    description: str | None = None
    is_enabled: bool = True
    min_plan: str = "free"
    category: str = "general"


@dataclass
class Plan(Record):
    TABLE: ClassVar[str] = "plans"
    BOOL_FIELDS: ClassVar[tuple[str, ...]] = ("is_active",)
    INT_FIELDS: ClassVar[tuple[str, ...]] = ("tier_order",)
    JSON_FIELDS: ClassVar[dict[str, Any]] = {"flags": list}

    id: str = ""
    name: str = ""
    slug: str = ""
    price_month: float = 0.0
    price_year: float = 0.0
    currency: str = "USD"
    tier_order: int = 0
    description: str | None = None
    flags: list[str] = field(default_factory=list)
    is_active: bool = True


@dataclass
class Workspace(Record):
    TABLE: ClassVar[str] = "workspace"

    id: str = "workspace"
    name: str = "Nagare"
    active_plan_id: str | None = None
    timezone: str = "UTC"
    updated_at: str = ""


@dataclass
class SimulationRun(Record):
    TABLE: ClassVar[str] = "simulation_runs"
    JSON_FIELDS: ClassVar[dict[str, Any]] = {"params": dict, "results": dict}

    id: str = ""
    board_id: str = ""
    name: str = ""
    kind: str = "monte_carlo_when"
    params: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    created_by: str | None = None


MODEL_BY_TABLE: dict[str, type[Record]] = {
    model.TABLE: model
    for model in (
        User,
        Board,
        ColumnGroup,
        Column,
        Project,
        Card,
        CardMovement,
        CardEvent,
        CardTask,
        Blocker,
        Comment,
        FeatureFlag,
        Plan,
        Workspace,
        SimulationRun,
    )
}


def insert(db: Any, record: Record) -> Record:
    """Insert a record through any object exposing ``ex(sql, *params)``."""
    db.ex(type(record).insert_sql(), record.insert_params())
    return record


def insert_many(db: Any, records: Sequence[Record]) -> int:
    """Bulk-insert records of a single type; returns the number written."""
    if not records:
        return 0
    model = type(records[0])
    db.exmany(model.insert_sql(), [r.insert_params() for r in records])
    return len(records)
