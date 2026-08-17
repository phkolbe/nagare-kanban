"""Pydantic v2 request/response models — the contract every router shares.

Names and field names match SPEC §5 exactly; do not rename anything here without
changing the SPEC first. Response models that carry computed, chart-shaped data
(``SummaryOut``, ``SeriesOut``, ``SimRunOut``…) allow extra keys on purpose: the
metric owners enrich them, and the frontend reads whatever is present.
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

__all__ = [
    # primitives
    "Schema", "OpenSchema", "ListResponse", "ApiErrorDetail", "ApiErrorBody",
    "HealthOut", "OkResponse",
    # enums
    "Role", "PlanSlug", "BoardKind", "Segment", "SegmentQuery", "ColumnKind",
    "ProjectStatus", "ServiceClass", "DemandType", "ValueArea", "EnvelopeState",
    "CardSize", "MovementSource", "EventType", "WipState", "SimulationKind",
    "BlockerCategory",
    # auth & users
    "LoginRequest", "TokenResponse", "UserOut", "UserCreate", "UserUpdate",
    "RoleInfo", "RoleCatalogueOut",
    # commercial
    "FlagOut", "FlagUpdate", "PlanOut", "PlanCreate", "PlanUpdate",
    "WorkspaceOut", "WorkspaceUpdate",
    # boards
    "BoardOut", "BoardCreate", "BoardUpdate", "BoardViewOut", "BoardCloneRequest",
    "ColumnOut", "ColumnCreate", "ColumnUpdate", "ColumnReorderRequest",
    "GroupOut", "GroupCreate", "GroupUpdate", "WipCell", "WipStatusOut",
    # projects
    "ProjectOut", "ProjectCreate", "ProjectUpdate", "ProjectDetailOut", "DemandCreate",
    # cards
    "CardOut", "CardCreate", "CardUpdate", "CardDetailOut", "MoveRequest", "MoveResult",
    "TaskOut", "TaskCreate", "TaskUpdate", "BlockRequest", "CommentOut", "CommentCreate",
    "MovementOut", "EventOut", "CardHistoryOut",
    # metrics
    "MetricValue", "SummaryOut", "SeriesPoint", "Series", "SeriesOut",
    "KmmMetric", "KmmLevel", "KmmOut",
    # simulator
    "SimBoardCreate", "SimGenerateRequest", "SimRunCreate", "SimRunOut",
    "SimCompareRequest", "SimCompareOut",
]

T = TypeVar("T")


class Schema(BaseModel):
    """Strict base: unknown keys are dropped, ORM-ish objects are accepted."""

    model_config = ConfigDict(from_attributes=True, extra="ignore", populate_by_name=True)


class OpenSchema(BaseModel):
    """Base for computed payloads whose owners may add fields (charts, runs)."""

    model_config = ConfigDict(from_attributes=True, extra="allow", populate_by_name=True)


# ---------------------------------------------------------------------------
# shared scalar types
# ---------------------------------------------------------------------------

EmailStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=5,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Password = Annotated[str, StringConstraints(min_length=6, max_length=200)]
HexColor = Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]

Role = Literal["master", "administrator", "editor", "viewer", "simulator", "system"]
PlanSlug = Literal["free", "team", "business", "enterprise"]
BoardKind = Literal["operational", "simulator"]
Segment = Literal["upstream", "downstream"]
SegmentQuery = Literal["upstream", "downstream", "unified"]
ColumnKind = Literal["envelope", "queue", "activity", "done"]
ProjectStatus = Literal["discovery", "active", "on_hold", "done", "cancelled"]
ServiceClass = Literal["expedite", "standard", "fixed_date", "intangible"]
DemandType = Literal["value", "failure", "maintenance"]
ValueArea = Literal["feature", "defect", "risk", "debt", "compliance"]
EnvelopeState = Literal["sealed", "opened"]
CardSize = Literal["xs", "s", "m", "l", "xl"]
MovementSource = Literal["ui", "api", "mcp", "seed", "simulation"]
EventType = Literal[
    "created", "moved", "updated", "blocked", "unblocked",
    "opened_envelope", "archived", "commented", "task_added", "task_completed",
]
WipState = Literal["under", "ok", "at_limit", "over"]
SimulationKind = Literal[
    "monte_carlo_when", "monte_carlo_how_many", "wip_experiment",
    "class_of_service_mix", "replay",
]
BlockerCategory = Literal[
    "dependency", "external_vendor", "waiting_on_customer", "technical",
    "people", "compliance", "environment", "other",
]


# ---------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------


class ListResponse(BaseModel, Generic[T]):
    """Every list endpoint returns ``{"items": [...], "total": n}`` (SPEC §5)."""

    model_config = ConfigDict(extra="ignore")

    items: list[T] = Field(default_factory=list)
    total: int = 0

    @classmethod
    def of(cls, items: list[T], total: int | None = None) -> "ListResponse[T]":
        """Build a response, defaulting ``total`` to ``len(items)``."""
        return cls(items=items, total=len(items) if total is None else total)


class ApiErrorDetail(Schema):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiErrorBody(Schema):
    """The exact error envelope produced by :mod:`backend.app.errors`."""

    error: ApiErrorDetail


class OkResponse(Schema):
    """Generic acknowledgement for deletes and idempotent actions."""

    ok: bool = True
    id: str | None = None
    message: str | None = None


class HealthOut(Schema):
    status: Literal["ok", "degraded"] = "ok"
    app: str
    version: str
    database: bool
    seeded: bool
    time: str


# ---------------------------------------------------------------------------
# auth & users
# ---------------------------------------------------------------------------


class LoginRequest(Schema):
    email: EmailStr
    # Deliberately NOT `Password`: the strength rule belongs on user creation. Applying
    # it here would answer a wrong-password attempt with 422 instead of 401, which both
    # breaks the documented auth contract and tells an attacker the policy for free.
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class UserOut(Schema):
    id: str
    email: str
    name: str
    role: Role
    is_active: bool = True
    created_at: str | None = None
    last_login_at: str | None = None


class TokenResponse(Schema):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class UserCreate(Schema):
    email: EmailStr
    name: NonEmptyStr
    password: Password
    role: Role = "viewer"
    is_active: bool = True


class UserUpdate(Schema):
    email: EmailStr | None = None
    name: NonEmptyStr | None = None
    password: Password | None = None
    role: Role | None = None
    is_active: bool | None = None


class RoleInfo(Schema):
    """One row of the SPEC §4 capability matrix."""

    key: Role
    label: str
    description: str
    capabilities: dict[str, bool]


class RoleCatalogueOut(Schema):
    roles: list[RoleInfo] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    capability_labels: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# feature flags, plans, workspace
# ---------------------------------------------------------------------------


class FlagOut(Schema):
    key: str
    name: str
    description: str | None = None
    is_enabled: bool = True
    min_plan: PlanSlug = "free"
    category: str = "general"
    #: enabled *and* unlocked by the workspace's active plan
    effective: bool | None = None
    unlocked_by_plan: bool | None = None


class FlagUpdate(Schema):
    is_enabled: bool | None = None
    min_plan: PlanSlug | None = None


class PlanOut(Schema):
    id: str
    name: str
    slug: str
    price_month: float = 0.0
    price_year: float = 0.0
    currency: str = "USD"
    tier_order: int = 0
    description: str | None = None
    flags: list[str] = Field(default_factory=list)
    is_active: bool = True


class PlanCreate(Schema):
    name: NonEmptyStr
    slug: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=2, max_length=40)]
    price_month: float = 0.0
    price_year: float = 0.0
    currency: str = "USD"
    tier_order: int = 0
    description: str | None = None
    flags: list[str] = Field(default_factory=list)
    is_active: bool = True


class PlanUpdate(Schema):
    name: NonEmptyStr | None = None
    price_month: float | None = None
    price_year: float | None = None
    currency: str | None = None
    tier_order: int | None = None
    description: str | None = None
    flags: list[str] | None = None
    is_active: bool | None = None


class WorkspaceOut(Schema):
    id: str = "workspace"
    name: str
    active_plan_id: str | None = None
    timezone: str = "UTC"
    updated_at: str | None = None
    plan: PlanOut | None = None


class WorkspaceUpdate(Schema):
    name: NonEmptyStr | None = None
    active_plan_id: str | None = None
    timezone: str | None = None


# ---------------------------------------------------------------------------
# boards, groups, columns
# ---------------------------------------------------------------------------


class GroupOut(Schema):
    id: str
    board_id: str
    name: str
    position: int = 0
    wip_min: int | None = None
    wip_max: int | None = None
    color: str | None = None


class GroupCreate(Schema):
    name: NonEmptyStr
    position: int | None = None
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    color: HexColor | None = None


class GroupUpdate(Schema):
    name: NonEmptyStr | None = None
    position: int | None = None
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    color: HexColor | None = None


class ColumnOut(Schema):
    id: str
    board_id: str
    group_id: str | None = None
    name: str
    position: int = 0
    segment: Segment
    kind: ColumnKind
    wip_min: int | None = None
    wip_max: int | None = None
    is_commitment_point: bool = False
    is_delivery_point: bool = False
    color: str | None = None
    description: str | None = None


class ColumnCreate(Schema):
    name: NonEmptyStr
    segment: Segment = "downstream"
    kind: ColumnKind = "queue"
    position: int | None = None
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    group_id: str | None = None
    color: HexColor | None = None
    description: str | None = None
    is_commitment_point: bool | None = None
    is_delivery_point: bool | None = None


class ColumnUpdate(Schema):
    name: NonEmptyStr | None = None
    segment: Segment | None = None
    kind: ColumnKind | None = None
    position: int | None = None
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    group_id: str | None = None
    color: HexColor | None = None
    description: str | None = None
    is_commitment_point: bool | None = None
    is_delivery_point: bool | None = None


class ColumnReorderRequest(Schema):
    ordered_ids: list[str] = Field(min_length=1)


class BoardOut(Schema):
    id: str
    name: str
    slug: str
    description: str | None = None
    kind: BoardKind = "operational"
    source_board_id: str | None = None
    count_envelope_in_metrics: bool = False
    confirm_moves: bool = True
    wip_min: int | None = None
    wip_max: int | None = None
    sle_days: int | None = None
    sle_percentile: int | None = None
    created_at: str | None = None
    archived: bool = False
    # Present on GET /boards/{id}; omitted from the collection listing.
    groups: list[GroupOut] | None = None
    columns: list[ColumnOut] | None = None
    card_count: int | None = None


class BoardCreate(Schema):
    name: NonEmptyStr
    description: str | None = None
    kind: BoardKind = "operational"
    #: ``default`` builds the SPEC §11 column set; ``blank`` creates no columns.
    template: Literal["default", "blank", "simple"] | None = "default"
    count_envelope_in_metrics: bool = False
    confirm_moves: bool = True
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    sle_days: Annotated[int, Field(gt=0)] | None = None
    sle_percentile: Annotated[int, Field(gt=0, le=100)] | None = None


class BoardUpdate(Schema):
    name: NonEmptyStr | None = None
    description: str | None = None
    count_envelope_in_metrics: bool | None = None
    confirm_moves: bool | None = None
    wip_min: Annotated[int, Field(ge=0)] | None = None
    wip_max: Annotated[int, Field(ge=0)] | None = None
    sle_days: Annotated[int, Field(gt=0)] | None = None
    sle_percentile: Annotated[int, Field(gt=0, le=100)] | None = None
    archived: bool | None = None


class BoardCloneRequest(Schema):
    name: NonEmptyStr
    kind: BoardKind = "simulator"
    copy_cards: bool = False


class WipCell(Schema):
    """WIP state for one column, one group, or the whole flow."""

    count: int = 0
    min: int | None = None
    max: int | None = None
    state: WipState = "ok"
    label: str | None = None


class WipStatusOut(Schema):
    board_id: str
    columns: dict[str, WipCell] = Field(default_factory=dict)
    groups: dict[str, WipCell] = Field(default_factory=dict)
    flow: WipCell = Field(default_factory=WipCell)


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------


class CardOut(Schema):
    id: str
    board_id: str
    column_id: str
    project_id: str | None = None
    key: str
    title: str
    description: str | None = None
    service_class: ServiceClass = "standard"
    assignee_id: str | None = None
    requester: str | None = None
    size: CardSize | None = None
    value_area: ValueArea = "feature"
    demand_type: DemandType = "value"
    due_date: str | None = None
    position: int = 0
    envelope_state: EnvelopeState = "opened"
    is_blocked: bool = False
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    opened_at: str | None = None
    committed_at: str | None = None
    delivered_at: str | None = None
    archived: bool = False
    created_by: str | None = None
    # denormalised helpers the board view needs to render a compact card
    project_key: str | None = None
    project_color: str | None = None
    assignee_name: str | None = None
    task_total: int | None = None
    task_done: int | None = None
    age_days: float | None = None
    blocked_since: str | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> Any:
        """Accept a JSON string straight from sqlite as well as a real list."""
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except ValueError:
                return []
            return parsed if isinstance(parsed, list) else []
        return value or []

    @field_validator("custom_fields", mode="before")
    @classmethod
    def _coerce_custom_fields(cls, value: Any) -> Any:
        if isinstance(value, str):
            import json

            try:
                parsed = json.loads(value)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value or {}


class CardCreate(Schema):
    board_id: str
    title: NonEmptyStr
    column_id: str | None = None
    project_id: str | None = None
    description: str | None = None
    service_class: ServiceClass = "standard"
    assignee_id: str | None = None
    requester: str | None = None
    size: CardSize | None = None
    value_area: ValueArea = "feature"
    demand_type: DemandType = "value"
    due_date: str | None = None
    envelope_state: EnvelopeState = "opened"
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class CardUpdate(Schema):
    title: NonEmptyStr | None = None
    description: str | None = None
    project_id: str | None = None
    service_class: ServiceClass | None = None
    assignee_id: str | None = None
    requester: str | None = None
    size: CardSize | None = None
    value_area: ValueArea | None = None
    demand_type: DemandType | None = None
    due_date: str | None = None
    position: int | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None
    archived: bool | None = None


class TaskOut(Schema):
    id: str
    card_id: str
    title: str
    is_done: bool = False
    assignee_id: str | None = None
    position: int = 0
    created_at: str | None = None
    completed_at: str | None = None


class TaskCreate(Schema):
    title: NonEmptyStr
    assignee_id: str | None = None
    position: int | None = None
    is_done: bool = False


class TaskUpdate(Schema):
    title: NonEmptyStr | None = None
    is_done: bool | None = None
    assignee_id: str | None = None
    position: int | None = None


class BlockerOut(Schema):
    id: str
    card_id: str
    reason: str
    category: str | None = None
    started_at: str
    ended_at: str | None = None
    created_by: str | None = None
    duration_seconds: float | None = None


class BlockRequest(Schema):
    reason: NonEmptyStr
    category: BlockerCategory = "other"


class CommentOut(Schema):
    id: str
    card_id: str
    author_id: str | None = None
    author_name: str | None = None
    body: str
    created_at: str


class CommentCreate(Schema):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]


class MovementOut(Schema):
    id: str
    card_id: str
    board_id: str
    from_column_id: str | None = None
    to_column_id: str
    from_segment: Segment | None = None
    to_segment: Segment
    moved_at: str
    moved_by: str | None = None
    seconds_in_from_column: int | None = None
    note: str | None = None
    is_backward: bool = False
    source: MovementSource = "ui"
    from_column_name: str | None = None
    to_column_name: str | None = None
    moved_by_name: str | None = None


class EventOut(Schema):
    id: str
    card_id: str
    board_id: str
    actor_id: str | None = None
    actor_name: str | None = None
    event_type: EventType
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    created_at: str


class CardDetailOut(CardOut):
    """``GET /cards/{id}`` — the card plus everything the drawer renders."""

    tasks: list[TaskOut] = Field(default_factory=list)
    blockers: list[BlockerOut] = Field(default_factory=list)
    comments: list[CommentOut] = Field(default_factory=list)
    movements: list[MovementOut] = Field(default_factory=list)
    column_name: str | None = None
    column_kind: ColumnKind | None = None


class CardHistoryOut(Schema):
    card_id: str
    movements: list[MovementOut] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)


class MoveRequest(Schema):
    to_column_id: str
    note: str | None = None
    #: master/administrator only — permits a backward move (SPEC §6.2)
    force: bool = False


class MoveResult(OpenSchema):
    card: CardOut
    movement: MovementOut
    #: true when an ``expedite`` card was allowed past a column WIP max
    wip_override: bool = False
    #: advisory messages, e.g. a ``starving`` column below its WIP min
    warnings: list[str] = Field(default_factory=list)
    wip: WipStatusOut | None = None
    committed: bool = False
    delivered: bool = False


class BoardViewOut(Schema):
    """``GET /boards/{id}/view`` — everything one board render needs."""

    board: BoardOut
    segment: SegmentQuery = "unified"
    groups: list[GroupOut] = Field(default_factory=list)
    columns: list[ColumnOut] = Field(default_factory=list)
    cards: list[CardOut] = Field(default_factory=list)
    wip: WipStatusOut
    commitment_column_id: str | None = None
    delivery_column_id: str | None = None
    total_cards: int = 0
    generated_at: str | None = None


# ---------------------------------------------------------------------------
# projects & demands
# ---------------------------------------------------------------------------


class ProjectOut(Schema):
    id: str
    name: str
    key: str
    color: str | None = None
    description: str | None = None
    status: ProjectStatus = "active"
    owner_id: str | None = None
    owner_name: str | None = None
    started_at: str | None = None
    target_date: str | None = None
    created_at: str | None = None
    archived: bool = False
    card_count: int | None = None
    sealed_count: int | None = None
    opened_count: int | None = None
    delivered_count: int | None = None
    in_flight_count: int | None = None


class ProjectCreate(Schema):
    name: NonEmptyStr
    key: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=8)]
    color: HexColor | None = None
    description: str | None = None
    status: ProjectStatus = "active"
    owner_id: str | None = None
    started_at: str | None = None
    target_date: str | None = None


class ProjectUpdate(Schema):
    name: NonEmptyStr | None = None
    color: HexColor | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    owner_id: str | None = None
    started_at: str | None = None
    target_date: str | None = None
    archived: bool | None = None


class ProjectDetailOut(ProjectOut):
    """Project plus its demand roll-up by envelope state."""

    demands: dict[str, int] = Field(default_factory=dict)
    cards: list[CardOut] = Field(default_factory=list)


class DemandCreate(Schema):
    """A sealed-envelope demand (SPEC §5, ``POST /projects/{id}/demands``)."""

    title: NonEmptyStr
    board_id: str | None = None
    description: str | None = None
    requester: str | None = None
    service_class: ServiceClass = "standard"
    value_area: ValueArea = "feature"
    demand_type: DemandType = "value"
    size: CardSize | None = None
    due_date: str | None = None
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    sealed: bool = True


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


class MetricValue(OpenSchema):
    """The uniform KPI envelope required by SPEC §7.1."""

    value: float | int | None = None
    unit: str = "count"
    trend_pct: float | None = None
    sample_size: int = 0
    previous_value: float | int | None = None
    low_confidence: bool = False
    label: str | None = None
    description: str | None = None


class SummaryOut(OpenSchema):
    """``/metrics/summary`` — a named bag of :class:`MetricValue` plus context."""

    board_id: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    percentiles: dict[str, float] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None


class SeriesPoint(OpenSchema):
    x: Any = None
    y: Any = None
    label: str | None = None


class Series(OpenSchema):
    key: str
    label: str | None = None
    color: str | None = None
    points: list[SeriesPoint] = Field(default_factory=list)


class SeriesOut(OpenSchema):
    """Generic chart payload: CFD, scatter, throughput, aging, histogram…"""

    board_id: str
    kind: str
    series: list[Series] = Field(default_factory=list)
    categories: list[Any] = Field(default_factory=list)
    percentiles: dict[str, float] = Field(default_factory=dict)
    x_label: str | None = None
    y_label: str | None = None
    unit: str | None = None
    sample_size: int = 0
    low_confidence: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None


class KmmMetric(OpenSchema):
    key: str
    label: str
    available: bool = False
    value: Any = None
    why: str | None = None


class KmmLevel(OpenSchema):
    level: str
    name: str
    focus: str
    metrics: list[KmmMetric] = Field(default_factory=list)
    readiness_pct: float = 0.0


class KmmOut(OpenSchema):
    board_id: str
    levels: list[KmmLevel] = Field(default_factory=list)
    current_level: str | None = None
    overall_readiness_pct: float = 0.0
    generated_at: str | None = None


# ---------------------------------------------------------------------------
# simulator
# ---------------------------------------------------------------------------


class SimBoardCreate(Schema):
    name: NonEmptyStr
    source_board_id: str | None = None
    template: Literal["default", "blank", "simple"] | None = None
    copy_cards: bool = False


class SimGenerateRequest(Schema):
    """Synthetic demand generation (SPEC §8.2) — deterministic given ``seed``."""

    weeks: Annotated[int, Field(ge=1, le=104)] = 12
    #: mean arrivals per week
    arrival_rate: Annotated[float, Field(gt=0, le=200)] = 8.0
    class_mix: dict[str, float] = Field(
        default_factory=lambda: {
            "expedite": 0.08,
            "standard": 0.70,
            "fixed_date": 0.12,
            "intangible": 0.10,
        }
    )
    seed: int = 20260816
    #: optional per-column WIP maxima applied before the replay
    wip_limits: dict[str, int] = Field(default_factory=dict)
    reset: bool = True


class SimRunCreate(Schema):
    board_id: str
    kind: SimulationKind
    name: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class SimRunOut(OpenSchema):
    id: str
    board_id: str
    name: str
    kind: SimulationKind
    params: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    created_by: str | None = None
    created_by_name: str | None = None


class SimCompareRequest(Schema):
    run_ids: list[str] = Field(min_length=2, max_length=8)


class SimCompareOut(OpenSchema):
    runs: list[SimRunOut] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    deltas: dict[str, Any] = Field(default_factory=dict)
    verdict: str | None = None
