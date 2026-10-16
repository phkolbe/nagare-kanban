"""SQLAlchemy 2 models — the schema of record (SPEC §2).

Every table keeps the name and the columns the old ``schema.sql`` had, so the API
contract, the MCP server and the movement history read exactly as before. What
changed is underneath:

* ids are UUID v7 (native ``uuid`` on PostgreSQL), ordered by creation, which is
  also the tie-breaker wherever the old code ordered by sqlite's ``rowid``;
* instants are ``timestamptz``; JSON columns are ``jsonb``; booleans are booleans;
* the product that owns the data (``product_id``) is on boards, projects and
  cards, with the human keys unique **per product** (decision NG4). ``nagare`` is
  the product of the standalone use, outside the Pampufa platform.

Python code sees plain values: ``str`` ids, ISO ``Z`` strings, ``bool``, ``list``
and ``dict`` (see :mod:`backend.app.sqltypes`). :func:`as_dict` turns an instance
into the row dict every router has always worked with.
"""

from __future__ import annotations

import json
from typing import Any

from pampufa_sdk.db.base import uuid7
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .sqltypes import Id, Instant, Json

__all__ = [
    "PRODUCT_STANDALONE",
    "Base",
    "Blocker",
    "Board",
    "BoardColumn",
    "Card",
    "CardEvent",
    "CardMovement",
    "CardTask",
    "ColumnGroup",
    "Comment",
    "FeatureFlag",
    "Plan",
    "Project",
    "SimulationRun",
    "User",
    "Workspace",
    "as_dict",
    "json_dumps",
    "json_loads",
    "metadata",
    "new_uuid",
    "to_bool",
    "to_int",
]

#: The product of the standalone Nagare (local login, outside the platform).
PRODUCT_STANDALONE = "nagare"

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)


def new_uuid() -> str:
    """A fresh UUID v7 as dashed text — the id format for every table."""
    return str(uuid7())


class Base(DeclarativeBase):
    metadata = metadata


def _id() -> Mapped[str]:
    return mapped_column(Id, primary_key=True, default=new_uuid)


def _fk(target: str, ondelete: str, nullable: bool = True) -> Mapped[Any]:
    return mapped_column(Id, ForeignKey(target, ondelete=ondelete), nullable=nullable)


def _product() -> Mapped[str]:
    return mapped_column(
        String(40),
        nullable=False,
        default=PRODUCT_STANDALONE,
        server_default=PRODUCT_STANDALONE,
    )


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('master','administrator','editor','viewer','simulator','system')",
            name="role",
        ),
        Index("idx_users_role", "role"),
        Index("idx_users_active", "is_active"),
    )

    id: Mapped[str] = _id()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    last_login_at: Mapped[str | None] = mapped_column(Instant)


# ---------------------------------------------------------------------------
# Boards, groups, columns
# ---------------------------------------------------------------------------


class Board(Base):
    __tablename__ = "boards"
    __table_args__ = (
        UniqueConstraint("product_id", "slug"),
        CheckConstraint("kind IN ('operational','simulator')", name="kind"),
        CheckConstraint("wip_min IS NULL OR wip_min >= 0", name="wip_min"),
        CheckConstraint("wip_max IS NULL OR wip_max >= 0", name="wip_max"),
        CheckConstraint("sle_days IS NULL OR sle_days > 0", name="sle_days"),
        CheckConstraint(
            "sle_percentile IS NULL OR (sle_percentile > 0 AND sle_percentile <= 100)",
            name="sle_percentile",
        ),
        Index("idx_boards_kind", "kind", "archived"),
        Index("idx_boards_source", "source_board_id"),
        Index("idx_boards_product", "product_id", "archived"),
    )

    id: Mapped[str] = _id()
    product_id: Mapped[str] = _product()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="operational")
    source_board_id: Mapped[str | None] = _fk("boards.id", "SET NULL")
    count_envelope_in_metrics: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirm_moves: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    wip_min: Mapped[int | None] = mapped_column(Integer)
    wip_max: Mapped[int | None] = mapped_column(Integer)
    sle_days: Mapped[int | None] = mapped_column(Integer)
    sle_percentile: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ColumnGroup(Base):
    __tablename__ = "column_groups"
    __table_args__ = (
        CheckConstraint("wip_min IS NULL OR wip_min >= 0", name="wip_min"),
        CheckConstraint("wip_max IS NULL OR wip_max >= 0", name="wip_max"),
        Index("idx_groups_board", "board_id", "position"),
    )

    id: Mapped[str] = _id()
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wip_min: Mapped[int | None] = mapped_column(Integer)
    wip_max: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(String(40))


class BoardColumn(Base):
    __tablename__ = "columns"
    __table_args__ = (
        CheckConstraint("segment IN ('upstream','downstream')", name="segment"),
        CheckConstraint("kind IN ('envelope','queue','activity','done')", name="kind"),
        CheckConstraint("wip_min IS NULL OR wip_min >= 0", name="wip_min"),
        CheckConstraint("wip_max IS NULL OR wip_max >= 0", name="wip_max"),
        Index("idx_columns_board", "board_id", "position"),
        Index("idx_columns_group", "group_id"),
        Index("idx_columns_segment", "board_id", "segment", "position"),
    )

    id: Mapped[str] = _id()
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    group_id: Mapped[str | None] = _fk("column_groups.id", "SET NULL")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    segment: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    wip_min: Mapped[int | None] = mapped_column(Integer)
    wip_max: Mapped[int | None] = mapped_column(Integer)
    is_commitment_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_delivery_point: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    color: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("product_id", "key"),
        CheckConstraint(
            "status IN ('discovery','active','on_hold','done','cancelled')", name="status"
        ),
        Index("idx_projects_status", "status", "archived"),
        Index("idx_projects_product", "product_id", "archived"),
    )

    id: Mapped[str] = _id()
    product_id: Mapped[str] = _product()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key: Mapped[str] = mapped_column(String(20), nullable=False)
    color: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    owner_id: Mapped[str | None] = _fk("users.id", "SET NULL")
    started_at: Mapped[str | None] = mapped_column(Instant)
    target_date: Mapped[str | None] = mapped_column(Instant)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("product_id", "key"),
        CheckConstraint(
            "service_class IN ('expedite','standard','fixed_date','intangible')",
            name="service_class",
        ),
        CheckConstraint("size IS NULL OR size IN ('xs','s','m','l','xl')", name="size"),
        CheckConstraint(
            "value_area IN ('feature','defect','risk','debt','compliance')", name="value_area"
        ),
        CheckConstraint("demand_type IN ('value','failure','maintenance')", name="demand_type"),
        CheckConstraint("envelope_state IN ('sealed','opened')", name="envelope_state"),
        Index("idx_cards_board_column", "board_id", "column_id"),
        Index("idx_cards_project", "project_id"),
        Index("idx_cards_board_archived", "board_id", "archived"),
        Index("idx_cards_delivered", "board_id", "delivered_at"),
        Index("idx_cards_committed", "board_id", "committed_at"),
        Index("idx_cards_assignee", "assignee_id"),
        Index("idx_cards_service_class", "board_id", "service_class"),
        Index("idx_cards_envelope", "board_id", "envelope_state"),
    )

    id: Mapped[str] = _id()
    product_id: Mapped[str] = _product()
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    column_id: Mapped[str] = _fk("columns.id", "CASCADE", nullable=False)
    project_id: Mapped[str | None] = _fk("projects.id", "SET NULL")
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    service_class: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    assignee_id: Mapped[str | None] = _fk("users.id", "SET NULL")
    requester: Mapped[str | None] = mapped_column(String(200))
    size: Mapped[str | None] = mapped_column(String(4))
    value_area: Mapped[str] = mapped_column(String(20), nullable=False, default="feature")
    demand_type: Mapped[str] = mapped_column(String(20), nullable=False, default="value")
    due_date: Mapped[str | None] = mapped_column(Instant)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    envelope_state: Mapped[str] = mapped_column(String(10), nullable=False, default="opened")
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    opened_at: Mapped[str | None] = mapped_column(Instant)
    committed_at: Mapped[str | None] = mapped_column(Instant)
    delivered_at: Mapped[str | None] = mapped_column(Instant)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = _fk("users.id", "SET NULL")


# ---------------------------------------------------------------------------
# Movement history — the metrics backbone (SPEC §2.7)
# ---------------------------------------------------------------------------


class CardMovement(Base):
    __tablename__ = "card_movements"
    __table_args__ = (
        CheckConstraint(
            "from_segment IS NULL OR from_segment IN ('upstream','downstream')",
            name="from_segment",
        ),
        CheckConstraint("to_segment IN ('upstream','downstream')", name="to_segment"),
        CheckConstraint("source IN ('ui','api','mcp','seed','simulation')", name="source"),
        Index("idx_movements_card", "card_id", "moved_at"),
        Index("idx_movements_board_time", "board_id", "moved_at"),
        Index("idx_movements_to_column", "to_column_id", "moved_at"),
        Index("idx_movements_from_column", "from_column_id"),
    )

    id: Mapped[str] = _id()
    card_id: Mapped[str] = _fk("cards.id", "CASCADE", nullable=False)
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    from_column_id: Mapped[str | None] = _fk("columns.id", "SET NULL")
    to_column_id: Mapped[str] = _fk("columns.id", "CASCADE", nullable=False)
    from_segment: Mapped[str | None] = mapped_column(String(20))
    to_segment: Mapped[str] = mapped_column(String(20), nullable=False)
    moved_at: Mapped[str] = mapped_column(Instant, nullable=False)
    moved_by: Mapped[str | None] = _fk("users.id", "SET NULL")
    seconds_in_from_column: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    is_backward: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="ui")


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class CardEvent(Base):
    __tablename__ = "card_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created','moved','updated','blocked','unblocked',"
            "'opened_envelope','archived','commented','task_added','task_completed')",
            name="event_type",
        ),
        Index("idx_events_card", "card_id", "created_at"),
        Index("idx_events_board_time", "board_id", "created_at"),
        Index("idx_events_type", "event_type"),
    )

    id: Mapped[str] = _id()
    card_id: Mapped[str] = _fk("cards.id", "CASCADE", nullable=False)
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    actor_id: Mapped[str | None] = _fk("users.id", "SET NULL")
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    field: Mapped[str | None] = mapped_column(String(80))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)


# ---------------------------------------------------------------------------
# Card detail: tasks, blockers, comments
# ---------------------------------------------------------------------------


class CardTask(Base):
    __tablename__ = "card_tasks"
    __table_args__ = (Index("idx_tasks_card", "card_id", "position"),)

    id: Mapped[str] = _id()
    card_id: Mapped[str] = _fk("cards.id", "CASCADE", nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assignee_id: Mapped[str | None] = _fk("users.id", "SET NULL")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Instant)


class Blocker(Base):
    __tablename__ = "blockers"
    __table_args__ = (
        Index("idx_blockers_card", "card_id", "started_at"),
        Index("idx_blockers_open", "ended_at"),
    )

    id: Mapped[str] = _id()
    card_id: Mapped[str] = _fk("cards.id", "CASCADE", nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[str] = mapped_column(Instant, nullable=False)
    ended_at: Mapped[str | None] = mapped_column(Instant)
    created_by: Mapped[str | None] = _fk("users.id", "SET NULL")


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (Index("idx_comments_card", "card_id", "created_at"),)

    id: Mapped[str] = _id()
    card_id: Mapped[str] = _fk("cards.id", "CASCADE", nullable=False)
    author_id: Mapped[str | None] = _fk("users.id", "SET NULL")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)


# ---------------------------------------------------------------------------
# Commercial surface: flags, plans, workspace
# ---------------------------------------------------------------------------


class FeatureFlag(Base):
    __tablename__ = "feature_flags"
    __table_args__ = (
        CheckConstraint("min_plan IN ('free','team','business','enterprise')", name="min_plan"),
        Index("idx_flags_category", "category"),
    )

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_plan: Mapped[str] = mapped_column(String(20), nullable=False, default="free")
    category: Mapped[str] = mapped_column(String(40), nullable=False, default="general")


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (Index("idx_plans_tier", "tier_order"),)

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    price_month: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    price_year: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    tier_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list[Any]] = mapped_column(Json, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Workspace(Base):
    __tablename__ = "workspace"
    __table_args__ = (CheckConstraint("id = 'workspace'", name="singleton"),)

    id: Mapped[str] = mapped_column(String(20), primary_key=True, default="workspace")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active_plan_id: Mapped[str | None] = _fk("plans.id", "SET NULL")
    timezone: Mapped[str] = mapped_column(String(60), nullable=False, default="UTC")
    updated_at: Mapped[str] = mapped_column(Instant, nullable=False)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('monte_carlo_when','monte_carlo_how_many','wip_experiment',"
            "'class_of_service_mix','replay')",
            name="kind",
        ),
        Index("idx_runs_board", "board_id", "created_at"),
        Index("idx_runs_kind", "kind"),
    )

    id: Mapped[str] = _id()
    board_id: Mapped[str] = _fk("boards.id", "CASCADE", nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(Instant, nullable=False)
    created_by: Mapped[str | None] = _fk("users.id", "SET NULL")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def as_dict(obj: Base | None) -> dict[str, Any] | None:
    """The instance as the row dict the routers work with (``None`` passes through)."""
    if obj is None:
        return None
    return {attr.key: getattr(obj, attr.key) for attr in obj.__mapper__.column_attrs}


def to_bool(value: Any) -> bool:
    """Coerce ``0/1``, a JSON bool or a string to a real ``bool``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int | float):
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
    """A JSON value as Python, falling back to ``default`` on anything odd."""
    if value is None or value == "":
        return default() if callable(default) else default
    if isinstance(value, list | dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default() if callable(default) else default
    if parsed is None:
        return default() if callable(default) else default
    return parsed


def json_dumps(value: Any) -> str:
    """Serialise a value compactly and deterministically (audit trail, comparisons)."""
    if value is None:
        return "null"
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except (ValueError, json.JSONDecodeError):
            return json.dumps(value)
    return json.dumps(value, separators=(",", ":"), default=str)
