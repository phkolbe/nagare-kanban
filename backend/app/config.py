"""Settings and the canonical catalogues shared by seed, routers and tests.

The feature-flag and plan tables in the database are *derived* from the constants
in this module (see :mod:`backend.app.seed`), so this file — together with
SPEC §3 — is the single place where a new flag or plan is introduced.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# repo root:  backend/app/config.py -> backend/app -> backend -> <root>
BASE_DIR: Path = Path(__file__).resolve().parents[2]
APP_DIR: Path = Path(__file__).resolve().parent

# The environment wins over the file; the file only fills in what is not set, like
# the Pampufa services (``.env`` is environment only, never business settings).
if (BASE_DIR / ".env").is_file() and not os.environ.get("NAGARE_IGNORE_DOTENV"):
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    """Runtime configuration. Every value is overridable through the environment."""

    app_name: str = "Nagare Kanban API"
    version: str = "1.0.0"

    # --- storage -----------------------------------------------------------
    # PostgreSQL in development and production (postgresql+asyncpg://...). The
    # default SQLite file keeps a cold clone runnable as a demo, as before.
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "NAGARE_DATABASE_URL",
            f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'nagare.db').as_posix()}",
        )
    )
    #: Upgrade the schema on boot (the old behaviour). ``nagare migrar`` does it explicitly.
    auto_migrate: bool = field(default_factory=lambda: _env_bool("NAGARE_AUTO_MIGRATE", True))

    # --- auth --------------------------------------------------------------
    # A dev default keeps a cold clone runnable; production must set the env var.
    jwt_secret: str = field(
        default_factory=lambda: os.environ.get(
            "NAGARE_JWT_SECRET", "nagare-dev-secret-do-not-use-in-production"
        )
    )
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "nagare-kanban"
    token_ttl_seconds: int = field(default_factory=lambda: _env_int("NAGARE_TOKEN_TTL", 12 * 3600))
    pbkdf2_iterations: int = 260_000

    # --- http --------------------------------------------------------------
    host: str = field(default_factory=lambda: os.environ.get("NAGARE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("NAGARE_PORT", 8770))
    dev: bool = field(default_factory=lambda: _env_bool("NAGARE_DEV", True))

    # --- frontend ----------------------------------------------------------
    frontend_dir: Path = field(default_factory=lambda: BASE_DIR / "frontend")

    # --- Pampufa integration (NG1-NG17) ---------------------------------------
    #: Base URL of the ``pampufa`` service. Empty: the Nagare runs standalone and
    #: refuses Pampufa tokens.
    pampufa_url: str = field(
        default_factory=lambda: os.environ.get("NAGARE_PAMPUFA_URL", "").rstrip("/")
    )
    pampufa_issuer: str = field(
        default_factory=lambda: os.environ.get("NAGARE_PAMPUFA_EMISSOR", "pampufa")
    )
    #: The audience Pampufa tokens must carry for the Nagare to accept them.
    pampufa_audience: str = "nagare"
    #: Service credential the Nagare uses to call the Pampufa (webhook of delivery).
    pampufa_client_id: str = field(
        default_factory=lambda: os.environ.get("NAGARE_PAMPUFA_CLIENTE_ID", "nagare")
    )
    pampufa_client_secret: str = field(
        default_factory=lambda: os.environ.get("NAGARE_PAMPUFA_CLIENTE_SEGREDO", "")
    )
    #: Seconds between webhook delivery rounds.
    webhook_interval_s: int = field(default_factory=lambda: _env_int("NAGARE_WEBHOOK_INTERVALO", 5))
    #: Where people open a card (the link the Pampufa shows). The React screen of N2
    #: will live under the platform's gateway; until then it is this server.
    public_url: str = field(
        default_factory=lambda: os.environ.get(
            "NAGARE_URL_PUBLICA", "http://127.0.0.1:8770"
        ).rstrip("/")
    )

    @property
    def pampufa_enabled(self) -> bool:
        return bool(self.pampufa_url)

    @property
    def pampufa_jwks_url(self) -> str:
        return os.environ.get(
            "NAGARE_PAMPUFA_JWKS_URL", f"{self.pampufa_url}/.well-known/jwks.json"
        )

    # --- demo --------------------------------------------------------------
    demo_password: str = "nagare123"
    demo_login_email: str = "master@nagare.app"
    seed_random_seed: int = 20260816

    @property
    def cors_origins(self) -> list[str]:
        """Localhost origins allowed to call the API during development."""
        ports = {self.port, 8770, 5173, 3000, 8000}
        origins: list[str] = []
        for p in sorted(ports):
            origins.append(f"http://127.0.0.1:{p}")
            origins.append(f"http://localhost:{p}")
        return origins

    def ensure_dirs(self) -> None:
        """Create the directories the app writes to (the SQLite folder, in the demo)."""
        if self.database_url.startswith("sqlite"):
            Path(self.database_url.split("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()


def get_settings() -> Settings:
    """Return the process-wide settings object."""
    return settings


# ---------------------------------------------------------------------------
# Plans (SPEC §3)
# ---------------------------------------------------------------------------

PLANS: list[dict[str, Any]] = [
    {
        "slug": "free",
        "name": "Free",
        "tier_order": 0,
        "price_month": 0.0,
        "price_year": 0.0,
        "currency": "USD",
        "description": "One board, the essentials of visualisation and WIP limits.",
    },
    {
        "slug": "team",
        "name": "Team",
        "tier_order": 1,
        "price_month": 12.0,
        "price_year": 120.0,
        "currency": "USD",
        "description": "Unified flow, sealed-envelope demand, group and flow WIP, API access.",
    },
    {
        "slug": "business",
        "name": "Business",
        "tier_order": 2,
        "price_month": 29.0,
        "price_year": 290.0,
        "currency": "USD",
        "description": "Advanced flow analytics, Monte Carlo forecasting, KMM dashboard, simulator.",
    },
    {
        "slug": "enterprise",
        "name": "Enterprise",
        "tier_order": 3,
        "price_month": 79.0,
        "price_year": 790.0,
        "currency": "USD",
        "description": "Everything in Business plus portfolio roll-up, SSO-ready RBAC and support SLAs.",
    },
]

PLAN_TIERS: dict[str, int] = {p["slug"]: int(p["tier_order"]) for p in PLANS}

# ---------------------------------------------------------------------------
# Feature flags (SPEC §3)
# ---------------------------------------------------------------------------

FEATURE_FLAGS: list[dict[str, Any]] = [
    {
        "key": "upstream_board",
        "name": "Upstream board",
        "description": "Visualise the discovery half of the value stream before the commitment point.",
        "min_plan": "free",
        "category": "board",
    },
    {
        "key": "downstream_board",
        "name": "Downstream board",
        "description": "Visualise the delivery half of the value stream after the commitment point.",
        "min_plan": "free",
        "category": "board",
    },
    {
        "key": "unified_flow",
        "name": "Unified end-to-end view",
        "description": "Show upstream and downstream as one continuous flow with the commitment rail.",
        "min_plan": "team",
        "category": "board",
    },
    {
        "key": "wip_column",
        "name": "Per-column WIP limits",
        "description": "Minimum and maximum work-in-progress limits on individual columns.",
        "min_plan": "free",
        "category": "wip",
    },
    {
        "key": "wip_group",
        "name": "Per-group WIP limits",
        "description": "WIP limits spanning a group of columns, e.g. all of Discovery.",
        "min_plan": "team",
        "category": "wip",
    },
    {
        "key": "wip_flow",
        "name": "Whole-flow WIP limits",
        "description": "A single limit across the entire committed flow.",
        "min_plan": "team",
        "category": "wip",
    },
    {
        "key": "envelope",
        "name": "Sealed-envelope demands",
        "description": "Capture demand without revealing detail until it is deliberately opened.",
        "min_plan": "team",
        "category": "work",
    },
    {
        "key": "service_classes",
        "name": "Classes of service",
        "description": "Expedite, standard, fixed date and intangible policies per card.",
        "min_plan": "free",
        "category": "work",
    },
    {
        "key": "custom_fields",
        "name": "Custom card fields",
        "description": "Arbitrary structured fields stored per card.",
        "min_plan": "team",
        "category": "work",
    },
    {
        "key": "card_history",
        "name": "Movement history & audit",
        "description": "Full per-card movement log and field-level audit trail.",
        "min_plan": "free",
        "category": "work",
    },
    {
        "key": "metrics_basic",
        "name": "Lead time, throughput, WIP",
        "description": "The three foundational flow metrics.",
        "min_plan": "free",
        "category": "metrics",
    },
    {
        "key": "metrics_advanced",
        "name": "CFD, flow efficiency, aging, SLE",
        "description": "Cumulative flow, wait/touch split, aging WIP and service level expectations.",
        "min_plan": "business",
        "category": "metrics",
    },
    {
        "key": "monte_carlo",
        "name": "Probabilistic forecasting",
        "description": "Monte Carlo 'when will it be done' and 'how many by' forecasts.",
        "min_plan": "business",
        "category": "forecasting",
    },
    {
        "key": "kmm_dashboard",
        "name": "Kanban Maturity Model dashboard",
        "description": "ML0-ML6 readiness derived from the metrics you can actually produce.",
        "min_plan": "business",
        "category": "metrics",
    },
    {
        "key": "simulator",
        "name": "Simulator boards",
        "description": "Isolated boards for synthetic demand, WIP experiments and replays.",
        "min_plan": "business",
        "category": "forecasting",
    },
    {
        "key": "api_access",
        "name": "REST API tokens",
        "description": "Programmatic access for the system identity.",
        "min_plan": "team",
        "category": "integration",
    },
    {
        "key": "mcp_server",
        "name": "MCP integration",
        "description": "Expose boards and metrics to AI agents over the Model Context Protocol.",
        "min_plan": "business",
        "category": "integration",
    },
    {
        "key": "rbac_advanced",
        "name": "Full role matrix",
        "description": "The complete six-role permission matrix instead of admin/member.",
        "min_plan": "business",
        "category": "administration",
    },
    {
        "key": "multi_project",
        "name": "Multiple projects",
        "description": "Group demand into projects and roll metrics up per project.",
        "min_plan": "free",
        "category": "work",
    },
]

FLAG_KEYS: tuple[str, ...] = tuple(f["key"] for f in FEATURE_FLAGS)
FLAG_BY_KEY: dict[str, dict[str, Any]] = {f["key"]: f for f in FEATURE_FLAGS}

# Flag categories in the order the admin switchboard should render them.
FLAG_CATEGORIES: tuple[str, ...] = (
    "board",
    "wip",
    "work",
    "metrics",
    "forecasting",
    "integration",
    "administration",
)


def plan_tier(slug: str | None) -> int:
    """Tier order for a plan slug; unknown/None plans are treated as ``free``."""
    if not slug:
        return 0
    return PLAN_TIERS.get(slug, 0)


def flags_for_plan(slug: str) -> list[str]:
    """Flag keys unlocked by a plan slug (its own tier and everything below)."""
    tier = plan_tier(slug)
    return [f["key"] for f in FEATURE_FLAGS if plan_tier(f["min_plan"]) <= tier]


# ---------------------------------------------------------------------------
# Role catalogue (SPEC §4) — consumed by deps.require_roles and GET /users/roles
# ---------------------------------------------------------------------------

ROLES: tuple[str, ...] = (
    "master",
    "administrator",
    "editor",
    "viewer",
    "simulator",
    "system",
)

#: capability -> roles that hold it. Mirrors the matrix in SPEC §4 exactly.
ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "manage_plans_flags": ("master",),
    "manage_users": ("master", "administrator"),
    "manage_boards": ("master", "administrator"),
    "manage_cards": ("master", "administrator", "editor", "system"),
    "read_boards_metrics": (
        "master",
        "administrator",
        "editor",
        "viewer",
        "simulator",
        "system",
    ),
    "simulator_boards": ("master", "administrator", "simulator"),
    "api_tokens": ("master", "administrator", "system"),
}

ROLE_LABELS: dict[str, str] = {
    "master": "Master",
    "administrator": "Administrator",
    "editor": "Editor",
    "viewer": "Viewer",
    "simulator": "Simulator",
    "system": "System",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "master": "Owns the workspace: billing plan, feature flags, users and every board.",
    "administrator": "Runs the workspace day to day: users, boards, columns, policies and cards.",
    "editor": "Works the board: creates, edits and moves cards.",
    "viewer": "Read-only access to boards and metrics.",
    "simulator": "Read-only on operational boards, full control of simulator boards.",
    "system": "Non-human integration identity for the REST API and MCP server. Cannot sign in to the UI.",
}

#: Roles that may never obtain a token through the interactive login endpoint.
NON_INTERACTIVE_ROLES: frozenset[str] = frozenset({"system"})

# Enumerations shared with schemas.py / schema.sql so validation stays in sync.
BOARD_KINDS: tuple[str, ...] = ("operational", "simulator")
COLUMN_SEGMENTS: tuple[str, ...] = ("upstream", "downstream")
COLUMN_KINDS: tuple[str, ...] = ("envelope", "queue", "activity", "done")
PROJECT_STATUSES: tuple[str, ...] = (
    "discovery",
    "active",
    "on_hold",
    "done",
    "cancelled",
)
SERVICE_CLASSES: tuple[str, ...] = ("expedite", "standard", "fixed_date", "intangible")
DEMAND_TYPES: tuple[str, ...] = ("value", "failure", "maintenance")
VALUE_AREAS: tuple[str, ...] = ("feature", "defect", "risk", "debt", "compliance")
ENVELOPE_STATES: tuple[str, ...] = ("sealed", "opened")
CARD_SIZES: tuple[str, ...] = ("xs", "s", "m", "l", "xl")
MOVEMENT_SOURCES: tuple[str, ...] = ("ui", "api", "mcp", "seed", "simulation")
EVENT_TYPES: tuple[str, ...] = (
    "created",
    "moved",
    "updated",
    "blocked",
    "unblocked",
    "opened_envelope",
    "archived",
    "commented",
    "task_added",
    "task_completed",
)
WIP_STATES: tuple[str, ...] = ("under", "ok", "at_limit", "over")
SIMULATION_KINDS: tuple[str, ...] = (
    "monte_carlo_when",
    "monte_carlo_how_many",
    "wip_experiment",
    "class_of_service_mix",
    "replay",
)
