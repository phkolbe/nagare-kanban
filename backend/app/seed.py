"""Deterministic demo workspace generator (SPEC §11).

Every chart in this product is computed from ``card_movements``; a board seeded
with "20 cards in tidy columns" produces empty CFDs, flat throughput and a
meaningless Monte Carlo. So this module builds a full **twelve months of
back-dated movement history** for ~260 cards, with the statistical shape a real
Kanban system has:

* queues hold work far longer than activities, so flow efficiency lands at 25-40%
* weekly throughput wobbles but trends mildly upward
* about 35 cards are in flight, spread across the downstream columns, with a
  long tail of aged work so the aging chart has real outliers
* 12% of demand is failure demand; the class-of-service mix is 8/70/12/10
* fixed-date work carries real due dates and hits them about 80% of the time
* ``committed_at`` / ``delivered_at`` are read off the movement rows themselves,
  never invented separately, so every metric agrees with the history

Randomness comes from a single ``random.Random(20260816)`` — including the UUIDs
— so the structure of two fresh seeds is identical. Timestamps are anchored to
the moment of seeding so the board always looks current.

Seeding is idempotent: :func:`seed_if_empty` does nothing once ``users`` is
non-empty, while the flag and plan catalogues are re-synced on every boot so a
newly introduced flag appears without a database reset.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import FEATURE_FLAGS, PLANS, flags_for_plan, settings
from .db import Database, iso, migrate, new_id
from .models import (
    Blocker,
    Board,
    BoardColumn,
    Card,
    CardEvent,
    CardMovement,
    CardTask,
    ColumnGroup,
    Comment,
    FeatureFlag,
    Plan,
    Project,
    User,
    Workspace,
    as_dict,
)
from .security import hash_password

__all__ = ["SeedSummary", "is_empty", "seed", "seed_if_empty"]


# ---------------------------------------------------------------------------
# static catalogues
# ---------------------------------------------------------------------------

USERS: list[dict[str, str]] = [
    {"email": "nagare.master@grupo-sr.com.br", "name": "Yuki Tanaka", "role": "master"},
    {"email": "nagare.admin@grupo-sr.com.br", "name": "Marina Duarte", "role": "administrator"},
    {"email": "nagare.editor@grupo-sr.com.br", "name": "Caio Ribeiro", "role": "editor"},
    {"email": "nagare.leitor@grupo-sr.com.br", "name": "Alice Whitfield", "role": "viewer"},
    {"email": "nagare.simulador@grupo-sr.com.br", "name": "Sofia Almeida", "role": "simulator"},
    {"email": "system@nagare.app", "name": "Nagare Integration", "role": "system"},
]

PROJECTS: list[dict[str, Any]] = [
    {
        "key": "NGR",
        "name": "Nagare Platform",
        "color": "#3b7ea1",
        "status": "active",
        "owner": "nagare.master@grupo-sr.com.br",
        "weight": 0.34,
        "description": "The flow-management product itself: board, metrics and policies.",
    },
    {
        "key": "PAY",
        "name": "Payments Revamp",
        "color": "#a1683b",
        "status": "active",
        "owner": "nagare.admin@grupo-sr.com.br",
        "weight": 0.26,
        "description": "Rebuild of the charge, refund and settlement pipeline.",
    },
    {
        "key": "MOB",
        "name": "Mobile App",
        "color": "#6b4fa1",
        "status": "active",
        "owner": "nagare.editor@grupo-sr.com.br",
        "weight": 0.22,
        "description": "iOS and Android clients for field teams.",
    },
    {
        "key": "INF",
        "name": "Infra Reliability",
        "color": "#3f8f6f",
        "status": "discovery",
        "owner": "nagare.admin@grupo-sr.com.br",
        "weight": 0.18,
        "description": "Availability, backup, observability and cost of the platform.",
    },
]

BOARD_SLUG = "nagare-flow"
SIM_BOARD_SLUG = "scenario-lab"

#: name, segment, kind, wip_min, wip_max, group, description
BOARD_COLUMNS: list[tuple[str, str, str, int | None, int | None, str | None, str]] = [
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
]

#: name, position, wip_min, wip_max, color
BOARD_GROUPS: list[tuple[str, int, int | None, int | None, str]] = [
    ("Discovery", 0, 6, 24, "#7d8ba1"),
    ("Delivery", 1, 12, 40, "#3b7ea1"),
]

COMMITMENT_COLUMN = "Analysis"
DELIVERY_COLUMN = "Delivered"

#: column -> (min days, max days) dwell. Queues are deliberately long and
#: activities short: that ratio is what produces a believable flow efficiency.
DWELL: dict[str, tuple[float, float]] = {
    "Envelope": (2.0, 26.0),
    "Ideas": (1.0, 10.0),
    "Discovery": (0.6, 5.0),
    "Options": (1.0, 8.0),
    "Ready to Commit": (0.5, 5.0),
    "Analysis": (0.5, 3.0),
    "Analysis Done": (0.5, 6.0),
    "Build": (1.5, 6.5),
    "Build Done": (0.5, 6.0),
    "Verify": (0.5, 2.4),
    "Ready to Deploy": (1.0, 9.5),
}

UPSTREAM_FLOW = ["Ideas", "Discovery", "Options", "Ready to Commit"]
DOWNSTREAM_FLOW = [
    "Analysis",
    "Analysis Done",
    "Build",
    "Build Done",
    "Verify",
    "Ready to Deploy",
]

#: Current occupancy targets, chosen against the WIP maxima above so the board
#: shows every WIP state at once: Build 8/8 is `at_limit`, Verify 6/5 is `over`,
#: Ready to Commit 4 against a min of 5 is `under` (starving).
IN_FLIGHT_TARGET: dict[str, int] = {
    "Analysis": 5,
    "Analysis Done": 4,
    "Build": 8,
    "Build Done": 5,
    "Verify": 6,
    "Ready to Deploy": 7,
}
UPSTREAM_TARGET: dict[str, int] = {
    "Ideas": 7,
    "Discovery": 4,
    "Options": 5,
    "Ready to Commit": 4,
}
SEALED_TARGET = 25

SERVICE_CLASS_MIX: list[tuple[str, float]] = [
    ("expedite", 0.08),
    ("standard", 0.70),
    ("fixed_date", 0.12),
    ("intangible", 0.10),
]
DEMAND_TYPE_MIX: list[tuple[str, float]] = [
    ("value", 0.75),
    ("failure", 0.12),
    ("maintenance", 0.13),
]
SIZE_MIX: list[tuple[str, float]] = [
    ("xs", 0.10),
    ("s", 0.30),
    ("m", 0.35),
    ("l", 0.20),
    ("xl", 0.05),
]

BLOCKER_CATEGORIES: list[tuple[str, float]] = [
    ("dependency", 0.26),
    ("external_vendor", 0.16),
    ("waiting_on_customer", 0.18),
    ("technical", 0.16),
    ("people", 0.10),
    ("compliance", 0.06),
    ("environment", 0.08),
]

BLOCKER_REASONS: dict[str, list[str]] = {
    "dependency": [
        "Waiting on the identity service to expose the new scope",
        "Blocked by the schema migration owned by the platform team",
        "Needs the shared component released first",
    ],
    "external_vendor": [
        "Vendor sandbox returns 502 on every request",
        "Waiting for the acquirer to enable the test merchant",
        "Third-party SDK release slipped a week",
    ],
    "waiting_on_customer": [
        "Awaiting sign-off from the requesting business unit",
        "Customer has not supplied the sample data set",
        "Pending answer on the rounding rule from finance",
    ],
    "technical": [
        "Flaky integration suite hides the real failure",
        "Race condition only reproduces under load",
        "Legacy adapter cannot express the new state machine",
    ],
    "people": [
        "Only reviewer with context is on leave",
        "Pairing partner pulled onto an expedite item",
        "Handover pending after the on-call rotation",
    ],
    "compliance": [
        "Waiting on the privacy review of the new field",
        "Audit evidence must be attached before release",
    ],
    "environment": [
        "Staging cluster is down for maintenance",
        "Release train frozen for the quarter close",
    ],
}

REQUESTERS = [
    "Finance Ops",
    "Customer Success",
    "Growth",
    "Risk & Compliance",
    "Field Sales",
    "Support Tier 2",
    "Data Platform",
    "Security",
    "Partner Integrations",
    "Executive Team",
]

TASK_TITLES = [
    "Write the acceptance criteria",
    "Spike the API contract",
    "Update the migration script",
    "Add integration test coverage",
    "Review with the requesting team",
    "Instrument the new code path",
    "Document the policy change",
    "Pair on the tricky edge case",
    "Update the runbook",
    "Check the dashboard after release",
    "Confirm the rollback plan",
    "Sanity-check the query plan",
]

COMMENT_BODIES = [
    "Splitting this: the reporting half can ship independently and keeps the card small.",
    "Pulled forward because the requester is blocked on it downstream.",
    "Flow efficiency on this one is poor — it sat in the queue for most of its life.",
    "Agreed with the requester to defer the export until after the release train.",
    "Verified in staging against a production-shaped data set.",
    "This is failure demand from last month's release; adding a regression test.",
    "Sized down after discovery: the risky part turned out to be already handled.",
    "Blocked twice on the same vendor. Raising it at the next replenishment.",
    "Moving to Ready to Deploy — the release window is Thursday.",
    "Kept the class of service as standard; expediting this would starve Build.",
    "Discussed at the daily: we are over WIP in Verify, so nothing new starts today.",
    "Rebased on the new schema, the adapter is much simpler now.",
]

MOVE_NOTES = [
    None,
    None,
    None,
    None,
    None,
    "Pulled at the daily standup",
    "Replenishment meeting decision",
    "Capacity freed in the next column",
    "Expedited by request",
    "Handed over after review",
]

TITLES: dict[str, dict[str, list[str]]] = {
    "NGR": {
        "feature": [
            "Commitment point rail on the unified board",
            "Per-group WIP badges in the column header",
            "Aging dot with percentile bands on compact cards",
            "Board template picker for new boards",
            "Column reorder with drag handles",
            "Sealed envelope unsealing animation",
            "CSV export for the lead-time scatter",
            "Saved views in the filter bar",
            "Inline editing of column WIP limits",
            "Bulk archive for delivered cards",
            "Cross-project board search",
            "Percentile lines on the scatter chart",
            "Keyboard navigation across the board grid",
            "Move confirmation modal with a remember-me toggle",
            "Service-class stripe on the compact card",
            "Blocked-time roll-up in the card drawer",
            "Movement history pagination",
            "Flow-efficiency tooltip with the wait/touch split",
            "Segment ribbon for upstream and downstream",
            "Empty state for a brand-new board",
            "Rolling average overlay on the throughput chart",
            "Group headers spanning multiple columns",
            "Card task progress indicator",
            "Board-level whole-flow WIP policy",
            "Due-date performance tile on the dashboard",
            "Little's Law coherence panel",
            "KMM ladder with readiness rings",
            "Monte Carlo forecast chart",
        ],
        "defect": [
            "Aging dot uses created_at instead of committed_at",
            "Drawer loses its focus trap after closing a modal",
            "Backward drop target stays highlighted while dragging",
            "Board slug collision returns a 500",
            "CFD area ordering flips when a column is empty",
            "WIP badge shows a stale count after a move",
            "Dark theme contrast fails on the muted chips",
            "Scatter tooltip is clipped at the right edge",
            "Filter bar drops the tag filter on reload",
        ],
        "maintenance": [
            "Upgrade the SVG chart primitives to shared scales",
            "Consolidate the duplicated column-state helpers",
            "Backfill missing seconds_in_from_column values",
            "Reindex card_movements for the CFD query",
            "Prune archived cards older than two years",
            "Refresh the API reference for the metrics routes",
        ],
    },
    "PAY": {
        "feature": [
            "3-D Secure step-up flow",
            "Retry ladder for soft declines",
            "Instant refunds over the real-time rail",
            "Idempotency keys on the charge endpoint",
            "Settlement reconciliation report",
            "Card vault tokenisation",
            "Chargeback evidence upload",
            "Multi-currency rounding rules",
            "Payout schedule editor",
            "Webhook signature rotation",
            "Partial capture on delayed shipment",
            "Merchant-level fee configuration",
            "Dispute timeline in the back office",
            "Network token lifecycle handling",
            "Split payments for marketplace sellers",
            "Statement descriptor per merchant",
            "Payment method fallback ordering",
            "Recurring mandate management",
            "Refund approval workflow above a threshold",
            "Ledger export for the finance team",
            "Fraud score surfacing in the review queue",
            "Payment link expiry policy",
        ],
        "defect": [
            "Duplicate charge when the client retries a timeout",
            "Refund rounds the wrong way in JPY",
            "Webhook replay marks the order paid twice",
            "Settlement file misses the last batch of the day",
            "Chargeback status stuck pending after the vendor callback",
            "Expired token still accepted on recurring charges",
            "Fee calculation ignores the merchant override",
        ],
        "maintenance": [
            "Rotate the acquirer API credentials",
            "Upgrade the payment gateway SDK",
            "Archive settlement files older than seven years",
            "Split the monolithic charge module",
            "Add contract tests against the acquirer sandbox",
        ],
    },
    "MOB": {
        "feature": [
            "Offline board cache with conflict resolution",
            "Biometric unlock on launch",
            "Push notification opt-in flow",
            "Pull-to-refresh on the board view",
            "Edge-to-edge layout for Android 15",
            "Card drawer as a bottom sheet",
            "Deep links into a single card",
            "Home-screen widget with WIP and aging",
            "Barcode scanning for field intake",
            "Photo attachments on cards",
            "Background sync with exponential backoff",
            "Accessibility pass on the board grid",
            "Haptic feedback on a completed move",
            "Dark mode following the system setting",
            "Localised number and date formats",
            "Crash-free session dashboard",
            "In-app changelog on first launch",
            "Reduced-motion support for the unseal animation",
        ],
        "defect": [
            "Board scroll jumps after a background refresh",
            "Push token is not refreshed after reinstall",
            "Card drawer keyboard covers the comment field",
            "Offline queue replays moves out of order",
            "Avatar images leak memory on the board list",
            "Crash when rotating during a drag",
        ],
        "maintenance": [
            "Bump the minimum supported iOS version",
            "Replace the deprecated storage API",
            "Trim the asset catalogue to cut install size",
            "Migrate to the new navigation component",
        ],
    },
    "INF": {
        "feature": [
            "Blue-green deploy for the API tier",
            "Automated restore drill for the database",
            "Alert routing by severity and ownership",
            "Read replica for the metrics queries",
            "Structured request logging with trace ids",
            "Per-tenant rate limiting",
            "Cost dashboard by service",
            "Canary analysis on deploy",
            "Secrets rotation without downtime",
            "Synthetic checks for the login path",
            "WAL checkpoint tuning for write bursts",
            "Backup verification report",
            "Graceful shutdown on rolling restart",
            "Error budget policy dashboard",
        ],
        "defect": [
            "Health check passes while the database is unreachable",
            "Log shipper drops lines under load",
            "Alert storm on a single node restart",
            "Certificate renewal missed the staging domain",
            "Metrics query times out on the 90-day window",
        ],
        "maintenance": [
            "Patch the base image for the CVE feed",
            "Retire the unused staging cluster",
            "Consolidate the two alerting pipelines",
            "Upgrade the runtime to the current LTS",
            "Document the on-call escalation path",
        ],
    },
}

DESCRIPTION_TEMPLATES = [
    "Requested by {requester}. {title} — agreed at the replenishment meeting; the "
    "acceptance criteria are captured in the tasks below.",
    "{title}. Scope was trimmed during discovery so the risky part ships first and the "
    "remainder becomes a separate option.",
    "Raised by {requester} after the last release. {title} — the policy and the runbook "
    "both need updating alongside the change.",
    "{title}. Blocked twice historically on the same dependency, so the plan is to land "
    "the interface first and integrate behind a flag.",
    "Pulled from the {project} option pool. {title} — sized {size}, with no external "
    "dependency expected.",
]


class SeedSummary(dict):
    """Row counts written by :func:`seed` (also handy in tests)."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc


# ---------------------------------------------------------------------------
# deterministic helpers
# ---------------------------------------------------------------------------


def _uid(rnd: random.Random) -> str:
    """A uuid4-shaped id drawn from the seeded RNG, so a seed is reproducible."""
    return str(uuid.UUID(int=rnd.getrandbits(128), version=4))


#: Namespace of the catalogue ids, so a plan keeps its id across reseeds.
_CATALOGUE_NS = uuid.UUID("6b1f2a4e-9c3d-4f5a-8e7b-0d2c4a6e8f10")


def _plan_id(slug: str) -> str:
    return str(uuid.uuid5(_CATALOGUE_NS, f"plan:{slug}"))


async def _count(s: AsyncSession, model: Any) -> int:
    return int(await s.scalar(select(func.count()).select_from(model)) or 0)


def _pick(rnd: random.Random, mix: Sequence[tuple[str, float]]) -> str:
    """Weighted choice over ``[(value, weight), ...]``."""
    roll = rnd.random()
    cumulative = 0.0
    for value, weight in mix:
        cumulative += weight
        if roll <= cumulative:
            return value
    return mix[-1][0]


def _dwell_days(rnd: random.Random, column: str, difficulty: float = 1.0) -> float:
    """Sample a right-skewed dwell time for a column, in days."""
    low, high = DWELL[column]
    mode = low + 0.35 * (high - low)
    return max(0.05, rnd.triangular(low, high, mode) * difficulty)


def _difficulty(rnd: random.Random) -> float:
    """A per-card multiplier applied to *every* column it passes through.

    Sampling each column independently makes lead times converge on their mean
    (the central limit theorem flattens the tail), which produces a suspiciously
    tidy distribution. Real work items are correlated: a hard card is slow
    everywhere. This multiplier restores the fat right tail that percentile
    charts, SLE attainment and Monte Carlo forecasts exist to expose.
    """
    return min(3.6, max(0.35, rnd.lognormvariate(0.0, 0.45)))


def _business_hour(rnd: random.Random, moment: datetime) -> datetime:
    """Nudge a timestamp into working hours and, usually, off the weekend."""
    moment = moment.replace(
        hour=rnd.randint(9, 18),
        minute=rnd.choice([3, 11, 17, 24, 38, 46, 52]),
        second=0,
        microsecond=0,
    )
    # Always draw: the random stream must not depend on the calendar, or the demo mix
    # (and the tests that check it) would change with the day the seed runs.
    off_the_weekend = rnd.random() < 0.78
    if moment.weekday() >= 5 and off_the_weekend:
        moment -= timedelta(days=moment.weekday() - 4)
    return moment


class TitlePool:
    """Hands out non-repeating titles, then recycles them with a phase suffix."""

    def __init__(self, rnd: random.Random, titles: list[str]) -> None:
        self._rnd = rnd
        self._source = list(titles)
        self._remaining: list[str] = []
        self._pass = 0

    def take(self) -> str:
        if not self._remaining:
            self._remaining = list(self._source)
            self._rnd.shuffle(self._remaining)
            self._pass += 1
        title = self._remaining.pop()
        return title if self._pass < 2 else f"{title} (phase {self._pass})"


class Cycler:
    """Shuffled, repeating iterator over a small pool (tasks, comments…)."""

    def __init__(self, rnd: random.Random, items: Iterable[str]) -> None:
        self._rnd = rnd
        self._source = list(items)
        self._remaining: list[str] = []

    def take(self) -> str:
        if not self._remaining:
            self._remaining = list(self._source)
            self._rnd.shuffle(self._remaining)
        return self._remaining.pop()


# ---------------------------------------------------------------------------
# catalogue seeding (re-synced even on an already-seeded database)
# ---------------------------------------------------------------------------


async def _sync_plans_and_flags(s: AsyncSession) -> tuple[dict[str, str], int, int]:
    """Insert missing plan/flag rows. Returns (plan ids by slug, n_plans, n_flags)."""
    plan_ids: dict[str, str] = {}
    written_plans = 0
    for spec in PLANS:
        existing = await s.scalar(select(Plan.id).where(Plan.slug == spec["slug"]))
        if existing:
            plan_ids[spec["slug"]] = existing
            # Keep the unlocked flag set in step with config.py without ever
            # overwriting pricing an operator may have edited.
            await s.execute(
                update(Plan).where(Plan.id == existing).values(flags=flags_for_plan(spec["slug"]))
            )
            continue
        plan_id = _plan_id(spec["slug"])
        s.add(
            Plan(
                id=plan_id,
                name=spec["name"],
                slug=spec["slug"],
                price_month=float(spec["price_month"]),
                price_year=float(spec["price_year"]),
                currency=spec["currency"],
                tier_order=int(spec["tier_order"]),
                description=spec["description"],
                flags=flags_for_plan(spec["slug"]),
                is_active=True,
            )
        )
        plan_ids[spec["slug"]] = plan_id
        written_plans += 1

    written_flags = 0
    for flag in FEATURE_FLAGS:
        if await s.get(FeatureFlag, flag["key"]):
            continue
        s.add(
            FeatureFlag(
                key=flag["key"],
                name=flag["name"],
                description=flag["description"],
                is_enabled=True,
                min_plan=flag["min_plan"],
                category=flag["category"],
            )
        )
        written_flags += 1

    await s.flush()
    return plan_ids, written_plans, written_flags


async def _ensure_workspace(s: AsyncSession, plan_ids: dict[str, str], now_iso: str) -> None:
    """Create the singleton workspace row on the Enterprise plan (NG15: everything on)."""
    if await s.get(Workspace, "workspace"):
        return
    s.add(
        Workspace(
            id="workspace",
            name="Nagare",
            active_plan_id=plan_ids.get("enterprise"),
            timezone="UTC",
            updated_at=now_iso,
        )
    )
    await s.flush()


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


async def is_empty(s: AsyncSession) -> bool:
    """True when the workspace has no users yet."""
    return not await _count(s, User)


async def seed_if_empty(s: AsyncSession, demo: bool | None = None) -> SeedSummary:
    """Seed only when ``users`` is empty; always re-sync flags and plans.

    ``demo`` (default ``settings.demo_seed``) false: an empty database gets only the
    catalogues and the workspace, never the demo accounts — homologation and production
    create their first account with ``nagare criar-admin``.

    The schema must already be migrated (``db.migrate``).
    """
    if demo is None:
        demo = settings.demo_seed
    if not demo or not await is_empty(s):
        plan_ids, n_plans, n_flags = await _sync_plans_and_flags(s)
        await _ensure_workspace(s, plan_ids, iso(datetime.now(UTC)))
        await s.commit()
        return SeedSummary(
            skipped=True,
            plans_added=n_plans,
            flags_added=n_flags,
            users=await _count(s, User),
            cards=await _count(s, Card),
        )
    return await seed(s)


async def seed(s: AsyncSession) -> SeedSummary:
    """Build the whole demo workspace in one transaction. Assumes an empty schema."""
    rnd = random.Random(settings.seed_random_seed)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)

    plan_ids, _, _ = await _sync_plans_and_flags(s)
    await _ensure_workspace(s, plan_ids, iso(now))
    users = await _seed_users(s, rnd, now)
    projects = await _seed_projects(s, rnd, users, now)
    board, columns = await _seed_board(s, rnd, now)
    await _seed_simulator_board(s, rnd, board, now)
    summary = await _seed_cards(s, rnd, now, board, columns, projects, users)
    await s.commit()

    summary.update(
        {
            "skipped": False,
            "users": len(users),
            "projects": len(projects),
            "boards": await _count(s, Board),
            "columns": len(columns),
        }
    )
    return summary


# ---------------------------------------------------------------------------
# users, projects, boards
# ---------------------------------------------------------------------------


async def _seed_users(
    s: AsyncSession, rnd: random.Random, now: datetime
) -> dict[str, dict[str, Any]]:
    """Create the six demo accounts; every account shares the demo password."""
    created_at = iso(now - timedelta(days=400))
    password_hash = hash_password(settings.demo_password)
    out: dict[str, dict[str, Any]] = {}
    for index, spec in enumerate(USERS):
        user_id = _uid(rnd)
        s.add(
            User(
                id=user_id,
                email=spec["email"],
                name=spec["name"],
                password_hash=password_hash,
                role=spec["role"],
                is_active=True,
                created_at=created_at,
                last_login_at=iso(now - timedelta(hours=index * 5 + 2)),
            )
        )
        out[spec["email"]] = {"id": user_id, **spec}
    await s.flush()
    return out


async def _seed_projects(
    s: AsyncSession, rnd: random.Random, users: dict[str, dict[str, Any]], now: datetime
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for index, spec in enumerate(PROJECTS):
        project_id = _uid(rnd)
        s.add(
            Project(
                id=project_id,
                name=spec["name"],
                key=spec["key"],
                color=spec["color"],
                description=spec["description"],
                status=spec["status"],
                owner_id=users[spec["owner"]]["id"],
                started_at=iso(now - timedelta(days=380 - index * 22)),
                target_date=iso(now + timedelta(days=60 + index * 45)),
                created_at=iso(now - timedelta(days=390 - index * 20)),
                archived=False,
            )
        )
        out[spec["key"]] = {"id": project_id, "counter": 100, **spec}
    await s.flush()
    return out


async def _insert_columns(
    s: AsyncSession, rnd: random.Random, board_id: str
) -> dict[str, dict[str, Any]]:
    """Write the SPEC §11 group and column structure onto a board."""
    group_ids: dict[str, str] = {}
    for name, position, wip_min, wip_max, color in BOARD_GROUPS:
        group_id = _uid(rnd)
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
    # Groups first: without relationship() the unit of work does not order the
    # inserts by foreign key, and the columns point at these rows.
    await s.flush()

    columns: dict[str, dict[str, Any]] = {}
    for position, (name, segment, kind, wip_min, wip_max, group, description) in enumerate(
        BOARD_COLUMNS
    ):
        column_id = _uid(rnd)
        s.add(
            BoardColumn(
                id=column_id,
                board_id=board_id,
                group_id=group_ids.get(group) if group else None,
                name=name,
                position=position,
                segment=segment,
                kind=kind,
                wip_min=wip_min,
                wip_max=wip_max,
                is_commitment_point=name == COMMITMENT_COLUMN,
                is_delivery_point=name == DELIVERY_COLUMN,
                color=None,
                description=description,
            )
        )
        columns[name] = {
            "id": column_id,
            "name": name,
            "segment": segment,
            "kind": kind,
            "position": position,
        }
    await s.flush()
    return columns


async def _seed_board(
    s: AsyncSession, rnd: random.Random, now: datetime
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Create the "Nagare Flow" operational board with its groups and columns."""
    board = Board(
        id=_uid(rnd),
        name="Nagare Flow",
        slug=BOARD_SLUG,
        description=(
            "The end-to-end value stream: discovery upstream, delivery downstream, with an "
            "explicit commitment point at Analysis."
        ),
        kind="operational",
        source_board_id=None,
        count_envelope_in_metrics=False,
        confirm_moves=True,
        wip_min=10,
        wip_max=45,
        sle_days=25,
        sle_percentile=85,
        created_at=iso(now - timedelta(days=395)),
        archived=False,
    )
    s.add(board)
    await s.flush()
    columns = await _insert_columns(s, rnd, board.id)
    return as_dict(board) or {"id": board.id}, columns


async def _seed_simulator_board(
    s: AsyncSession, rnd: random.Random, source_board: dict[str, Any], now: datetime
) -> str:
    """Clone the operational column structure into an empty simulator board."""
    board = Board(
        id=_uid(rnd),
        name="Scenario Lab",
        slug=SIM_BOARD_SLUG,
        description=(
            "An isolated copy of Nagare Flow for WIP experiments, class-of-service mixes and "
            "Monte Carlo replays. Nothing here touches operational metrics."
        ),
        kind="simulator",
        source_board_id=source_board["id"],
        count_envelope_in_metrics=False,
        confirm_moves=False,
        wip_min=6,
        wip_max=24,
        sle_days=25,
        sle_percentile=85,
        created_at=iso(now - timedelta(days=120)),
        archived=False,
    )
    s.add(board)
    await s.flush()
    await _insert_columns(s, rnd, board.id)
    return board.id


# ---------------------------------------------------------------------------
# cards + twelve months of movement history
# ---------------------------------------------------------------------------


class _Builder:
    """Accumulates rows for one seeding pass, then bulk-inserts them."""

    def __init__(self) -> None:
        self.cards: list[tuple] = []
        self.movements: list[tuple] = []
        self.events: list[tuple] = []
        self.tasks: list[tuple] = []
        self.blockers: list[tuple] = []
        self.comments: list[tuple] = []

    #: Column order of each accumulated tuple, per table.
    COLUMNS: ClassVar[dict[str, tuple[str, ...]]] = {
        "cards": (
            "id",
            "board_id",
            "column_id",
            "project_id",
            "key",
            "title",
            "description",
            "service_class",
            "assignee_id",
            "requester",
            "size",
            "value_area",
            "demand_type",
            "due_date",
            "position",
            "envelope_state",
            "is_blocked",
            "tags",
            "custom_fields",
            "created_at",
            "opened_at",
            "committed_at",
            "delivered_at",
            "archived",
            "created_by",
        ),
        "movements": (
            "id",
            "card_id",
            "board_id",
            "from_column_id",
            "to_column_id",
            "from_segment",
            "to_segment",
            "moved_at",
            "moved_by",
            "seconds_in_from_column",
            "note",
            "is_backward",
            "source",
        ),
        "events": (
            "id",
            "card_id",
            "board_id",
            "actor_id",
            "event_type",
            "field",
            "old_value",
            "new_value",
            "created_at",
        ),
        "tasks": (
            "id",
            "card_id",
            "title",
            "is_done",
            "assignee_id",
            "position",
            "created_at",
            "completed_at",
        ),
        "blockers": ("id", "card_id", "reason", "category", "started_at", "ended_at", "created_by"),
        "comments": ("id", "card_id", "author_id", "body", "created_at"),
    }
    MODELS: ClassVar[dict[str, Any]] = {
        "cards": Card,
        "movements": CardMovement,
        "events": CardEvent,
        "tasks": CardTask,
        "blockers": Blocker,
        "comments": Comment,
    }
    BOOLEANS: ClassVar[frozenset[str]] = frozenset(
        {"is_blocked", "archived", "is_backward", "is_done"}
    )

    async def flush(self, s: AsyncSession) -> None:
        for name, model in self.MODELS.items():
            columns = self.COLUMNS[name]
            rows = [
                {
                    column: bool(value) if column in self.BOOLEANS else value
                    for column, value in zip(columns, row, strict=True)
                }
                for row in getattr(self, name)
            ]
            for start in range(0, len(rows), 1000):
                await s.execute(insert(model), rows[start : start + 1000])


async def _seed_cards(
    s: AsyncSession,
    rnd: random.Random,
    now: datetime,
    board: dict[str, Any],
    columns: dict[str, dict[str, Any]],
    projects: dict[str, dict[str, Any]],
    users: dict[str, dict[str, Any]],
) -> SeedSummary:
    """Generate every card together with its full movement and audit history."""
    board_id = board["id"]
    builder = _Builder()

    workers = [
        users["nagare.master@grupo-sr.com.br"]["id"],
        users["nagare.admin@grupo-sr.com.br"]["id"],
        users["nagare.editor@grupo-sr.com.br"]["id"],
    ]
    movers = [*workers, users["system@nagare.app"]["id"]]

    title_pools = {
        (project_key, bucket): TitlePool(rnd, titles)
        for project_key, buckets in TITLES.items()
        for bucket, titles in buckets.items()
    }
    task_cycler = Cycler(rnd, TASK_TITLES)
    comment_cycler = Cycler(rnd, COMMENT_BODIES)
    requester_cycler = Cycler(rnd, REQUESTERS)

    project_weights = [(p["key"], p["weight"]) for p in PROJECTS]
    occupancy: dict[str, list[tuple[datetime, str]]] = {name: [] for name in columns}
    counters = {"blockers": 0}

    # -- per-card helpers ---------------------------------------------------

    def next_key(project_key: str) -> str:
        projects[project_key]["counter"] += 1
        return f"{project_key}-{projects[project_key]['counter']}"

    def make_identity() -> dict[str, Any]:
        """Everything about a card that does not depend on its timeline."""
        project_key = _pick(rnd, project_weights)
        demand_type = _pick(rnd, DEMAND_TYPE_MIX)
        bucket = {"value": "feature", "failure": "defect", "maintenance": "maintenance"}[
            demand_type
        ]
        title = title_pools[(project_key, bucket)].take()
        if demand_type == "failure":
            value_area = "defect"
        elif demand_type == "maintenance":
            value_area = _pick(rnd, [("debt", 0.62), ("risk", 0.20), ("compliance", 0.18)])
        else:
            value_area = _pick(
                rnd,
                [("feature", 0.74), ("risk", 0.10), ("debt", 0.08), ("compliance", 0.08)],
            )
        size = _pick(rnd, SIZE_MIX)
        requester = requester_cycler.take()
        tags: list[str] = []
        if demand_type == "failure":
            tags.append("regression")
        if value_area == "compliance":
            tags.append("compliance")
        if rnd.random() < 0.28:
            tags.append(rnd.choice(["quick-win", "customer-request", "tech-risk", "revenue"]))
        return {
            "project_id": projects[project_key]["id"],
            "key": next_key(project_key),
            "title": title,
            "description": rnd.choice(DESCRIPTION_TEMPLATES).format(
                requester=requester,
                title=title,
                project=projects[project_key]["name"],
                size=size,
            ),
            "service_class": _pick(rnd, SERVICE_CLASS_MIX),
            "demand_type": demand_type,
            "value_area": value_area,
            "size": size,
            "requester": requester,
            "tags": tags,
            "assignee_id": rnd.choice(workers) if rnd.random() < 0.86 else None,
            "created_by": rnd.choice(workers),
        }

    def plan_timeline(path: Sequence[str], dwells: Sequence[float]) -> list[datetime]:
        """Entry moments for each column, anchored arbitrarily at ``now``.

        The caller shifts the whole list so the last entry lands where it wants;
        computing the shape first and translating afterwards is what guarantees a
        delivered card is never stamped in the future.
        """
        moments = [_business_hour(rnd, now)]
        for index in range(1, len(path)):
            candidate = _business_hour(rnd, moments[-1] + timedelta(days=dwells[index - 1]))
            if candidate <= moments[-1]:
                candidate = moments[-1] + timedelta(hours=6)
            moments.append(candidate)
        return moments

    def shift(moments: list[datetime], last_at: datetime) -> list[datetime]:
        """Translate a timeline so its final entry sits exactly at ``last_at``."""
        delta = last_at - moments[-1]
        return [moment + delta for moment in moments]

    def emit_moves(card_id: str, path: Sequence[str], moments: Sequence[datetime]) -> None:
        """One ``card_movements`` row per transition, plus its audit event."""
        previous: datetime | None = None
        for index, column_name in enumerate(path):
            from_col = columns[path[index - 1]] if index else None
            to_col = columns[column_name]
            moment = moments[index]
            builder.movements.append(
                (
                    _uid(rnd),
                    card_id,
                    board_id,
                    from_col["id"] if from_col else None,
                    to_col["id"],
                    from_col["segment"] if from_col else None,
                    to_col["segment"],
                    iso(moment),
                    rnd.choice(movers),
                    int((moment - previous).total_seconds()) if previous else None,
                    rnd.choice(MOVE_NOTES) if from_col else None,
                    0,
                    "seed",
                )
            )
            builder.events.append(
                (
                    _uid(rnd),
                    card_id,
                    board_id,
                    rnd.choice(workers),
                    "created" if from_col is None else "moved",
                    None if from_col is None else "column",
                    from_col["name"] if from_col else None,
                    to_col["name"],
                    iso(moment),
                )
            )
            previous = moment

    def add_card(
        identity: dict[str, Any],
        column_name: str,
        entered_at: datetime,
        created_at: datetime,
        opened_at: datetime | None,
        committed_at: datetime | None,
        delivered_at: datetime | None,
        envelope_state: str,
        is_blocked: bool,
        due_date: str | None,
    ) -> str:
        card_id = _uid(rnd)
        occupancy[column_name].append((entered_at, card_id))
        builder.cards.append(
            (
                card_id,
                board_id,
                columns[column_name]["id"],
                identity["project_id"],
                identity["key"],
                identity["title"],
                identity["description"],
                identity["service_class"],
                identity["assignee_id"],
                identity["requester"],
                identity["size"],
                identity["value_area"],
                identity["demand_type"],
                due_date,
                0,  # rewritten once every card exists
                envelope_state,
                1 if is_blocked else 0,
                list(identity["tags"]),
                {},
                iso(created_at),
                iso(opened_at) if opened_at else None,
                iso(committed_at) if committed_at else None,
                iso(delivered_at) if delivered_at else None,
                0,
                identity["created_by"],
            )
        )
        return card_id

    def add_unseal_event(card_id: str, moment: datetime) -> None:
        builder.events.append(
            (
                _uid(rnd),
                card_id,
                board_id,
                rnd.choice(workers),
                "opened_envelope",
                "envelope_state",
                "sealed",
                "opened",
                iso(moment),
            )
        )

    def add_tasks(card_id: str, created_at: datetime, done_ratio: float) -> None:
        for position in range(rnd.choice([0, 0, 2, 3, 3, 4, 5])):
            done = rnd.random() < done_ratio
            completed = (
                iso(_business_hour(rnd, created_at + timedelta(days=rnd.uniform(0.5, 9))))
                if done
                else None
            )
            builder.tasks.append(
                (
                    _uid(rnd),
                    card_id,
                    task_cycler.take(),
                    1 if done else 0,
                    rnd.choice(workers) if rnd.random() < 0.6 else None,
                    position,
                    iso(created_at),
                    completed,
                )
            )
            builder.events.append(
                (
                    _uid(rnd),
                    card_id,
                    board_id,
                    rnd.choice(workers),
                    "task_completed" if done else "task_added",
                    "task",
                    None,
                    "done" if done else "added",
                    completed or iso(created_at),
                )
            )

    def add_comments(card_id: str, start: datetime, end: datetime) -> None:
        span = max((end - start).total_seconds(), 3600.0)
        for _ in range(rnd.choice([0, 0, 0, 1, 1, 2, 3])):
            moment = start + timedelta(seconds=rnd.uniform(0, span))
            author = rnd.choice(workers)
            builder.comments.append(
                (_uid(rnd), card_id, author, comment_cycler.take(), iso(moment))
            )
            builder.events.append(
                (_uid(rnd), card_id, board_id, author, "commented", None, None, None, iso(moment))
            )

    def add_blocker(card_id: str, start: datetime, duration_days: float | None) -> None:
        category = _pick(rnd, BLOCKER_CATEGORIES)
        actor = rnd.choice(workers)
        ended = (
            iso(_business_hour(rnd, start + timedelta(days=duration_days)))
            if duration_days is not None
            else None
        )
        builder.blockers.append(
            (
                _uid(rnd),
                card_id,
                rnd.choice(BLOCKER_REASONS[category]),
                category,
                iso(start),
                ended,
                actor,
            )
        )
        counters["blockers"] += 1
        builder.events.append(
            (_uid(rnd), card_id, board_id, actor, "blocked", "is_blocked", "0", "1", iso(start))
        )
        if ended:
            builder.events.append(
                (_uid(rnd), card_id, board_id, actor, "unblocked", "is_blocked", "1", "0", ended)
            )

    # -- 1. delivered cards, week by week across the last twelve months -----

    weeks = 52
    delivered_count = 0
    for week in range(weeks - 1, -1, -1):
        progress = (weeks - 1 - week) / (weeks - 1)
        # Mild upward trend plus week-to-week noise: real throughput is lumpy.
        base = 2.0 + 2.4 * progress
        count = max(0, round(base + rnd.choice([-1, -1, 0, 0, 0, 1, 1, 2])))
        window_end = now - timedelta(days=week * 7)
        for _ in range(count):
            delivered_at = window_end - timedelta(days=rnd.uniform(0.05, 6.95))
            delivered_at = delivered_at.replace(
                hour=rnd.randint(9, 18),
                minute=rnd.choice([5, 20, 35, 50]),
                second=0,
                microsecond=0,
            )
            if delivered_at >= now:
                delivered_at = now - timedelta(hours=rnd.randint(3, 30))

            identity = make_identity()
            from_envelope = rnd.random() < 0.32
            path = (
                (["Envelope"] if from_envelope else [])
                + UPSTREAM_FLOW
                + DOWNSTREAM_FLOW
                + [DELIVERY_COLUMN]
            )
            difficulty = _difficulty(rnd)
            dwells = [_dwell_days(rnd, name, difficulty) for name in path[:-1]]
            # A slow tail: roughly one card in eight lingers somewhere far longer.
            if rnd.random() < 0.12:
                dwells[rnd.randrange(len(dwells))] *= rnd.uniform(2.0, 4.5)

            moments = shift(plan_timeline(path, dwells), delivered_at)
            entries = dict(zip(path, moments, strict=False))
            created_at = moments[0]
            committed_at = entries[COMMITMENT_COLUMN]
            opened_at = entries["Ideas"]

            due_date = None
            if identity["service_class"] == "fixed_date":
                offset = rnd.uniform(0.5, 7.0) if rnd.random() < 0.80 else -rnd.uniform(0.5, 5.0)
                due_date = iso(delivered_at + timedelta(days=offset))

            card_id = add_card(
                identity,
                DELIVERY_COLUMN,
                delivered_at,
                created_at,
                opened_at,
                committed_at,
                delivered_at,
                "opened",
                False,
                due_date,
            )
            emit_moves(card_id, path, moments)
            if from_envelope:
                add_unseal_event(card_id, entries["Ideas"])
            add_tasks(card_id, created_at, done_ratio=0.92)
            add_comments(card_id, committed_at, delivered_at)
            if rnd.random() < 0.12:
                block_start = committed_at + timedelta(days=rnd.uniform(0.5, 5.0))
                if block_start < delivered_at:
                    room = (delivered_at - block_start).total_seconds() / 86400.0
                    add_blocker(card_id, block_start, min(rnd.uniform(0.5, 6.0), room * 0.7))
            delivered_count += 1

    # -- 2. in-flight cards, spread across the downstream columns -----------

    in_flight_count = 0
    for column_name, target in IN_FLIGHT_TARGET.items():
        stop = DOWNSTREAM_FLOW.index(column_name)
        for _ in range(target):
            identity = make_identity()
            from_envelope = rnd.random() < 0.30
            path = (
                (["Envelope"] if from_envelope else [])
                + UPSTREAM_FLOW
                + DOWNSTREAM_FLOW[: stop + 1]
            )
            difficulty = _difficulty(rnd)
            dwells = [_dwell_days(rnd, name, difficulty) for name in path[:-1]]
            current_dwell = _dwell_days(rnd, column_name, difficulty)
            # A long tail of aged work is what makes the aging chart worth having.
            if rnd.random() < 0.16:
                current_dwell *= rnd.uniform(3.0, 6.0)

            entered_current = now - timedelta(days=current_dwell)
            moments = shift(plan_timeline(path, dwells), entered_current)
            entries = dict(zip(path, moments, strict=False))
            created_at = moments[0]
            committed_at = entries[COMMITMENT_COLUMN]

            due_date = None
            if identity["service_class"] == "fixed_date":
                due_date = iso(
                    now + timedelta(days=rnd.uniform(2.0, 21.0))
                    if rnd.random() < 0.80
                    else now - timedelta(days=rnd.uniform(0.5, 6.0))
                )

            blocked_now = rnd.random() < 0.16
            card_id = add_card(
                identity,
                column_name,
                entered_current,
                created_at,
                entries["Ideas"],
                committed_at,
                None,
                "opened",
                blocked_now,
                due_date,
            )
            emit_moves(card_id, path, moments)
            if from_envelope:
                add_unseal_event(card_id, entries["Ideas"])
            add_tasks(card_id, created_at, done_ratio=0.45)
            add_comments(card_id, committed_at, now)
            if blocked_now:
                add_blocker(card_id, now - timedelta(days=rnd.uniform(0.3, 5.0)), None)
            elif rnd.random() < 0.10:
                start = committed_at + timedelta(days=rnd.uniform(0.5, 3.0))
                if start < now:
                    add_blocker(card_id, start, rnd.uniform(0.5, 4.0))
            in_flight_count += 1

    # -- 3. upstream work in progress (not yet committed) -------------------

    upstream_count = 0
    for column_name, target in UPSTREAM_TARGET.items():
        stop = UPSTREAM_FLOW.index(column_name)
        for _ in range(target):
            identity = make_identity()
            from_envelope = rnd.random() < 0.35
            path = (["Envelope"] if from_envelope else []) + UPSTREAM_FLOW[: stop + 1]
            difficulty = _difficulty(rnd)
            dwells = [_dwell_days(rnd, name, difficulty) for name in path[:-1]]
            entered_current = now - timedelta(days=_dwell_days(rnd, column_name, difficulty))

            moments = shift(plan_timeline(path, dwells), entered_current)
            entries = dict(zip(path, moments, strict=False))

            due_date = (
                iso(now + timedelta(days=rnd.uniform(14.0, 60.0)))
                if identity["service_class"] == "fixed_date"
                else None
            )
            card_id = add_card(
                identity,
                column_name,
                entered_current,
                moments[0],
                entries["Ideas"],
                None,
                None,
                "opened",
                False,
                due_date,
            )
            emit_moves(card_id, path, moments)
            if from_envelope:
                add_unseal_event(card_id, entries["Ideas"])
            add_tasks(card_id, moments[0], done_ratio=0.25)
            add_comments(card_id, moments[0], now)
            upstream_count += 1

    # -- 4. sealed envelope demands ----------------------------------------

    for _ in range(SEALED_TARGET):
        identity = make_identity()
        created_at = _business_hour(rnd, now - timedelta(days=rnd.uniform(0.5, 75.0)))
        due_date = (
            iso(now + timedelta(days=rnd.uniform(20.0, 90.0)))
            if identity["service_class"] == "fixed_date"
            else None
        )
        card_id = add_card(
            identity,
            "Envelope",
            created_at,
            created_at,
            None,
            None,
            None,
            "sealed",
            False,
            due_date,
        )
        emit_moves(card_id, ["Envelope"], [created_at])

    # -- 5. stable ordering inside every column ----------------------------

    position_by_card: dict[str, int] = {}
    for entries_list in occupancy.values():
        # Oldest arrival on top: in a pull system the card that has waited
        # longest is the one to look at first.
        for index, (_, card_id) in enumerate(sorted(entries_list, key=lambda item: item[0])):
            position_by_card[card_id] = index
    builder.cards = [
        (*row[:14], position_by_card.get(row[0], 0), *row[15:]) for row in builder.cards
    ]

    await builder.flush(s)

    return SeedSummary(
        cards=len(builder.cards),
        delivered=delivered_count,
        in_flight=in_flight_count,
        upstream=upstream_count,
        sealed=SEALED_TARGET,
        movements=len(builder.movements),
        events=len(builder.events),
        tasks=len(builder.tasks),
        comments=len(builder.comments),
        blockers=len(builder.blockers),
    )


async def migrate_and_seed(url: str | None = None, demo: bool | None = None) -> SeedSummary:
    """Migrate the configured database and seed it when empty (demo only if allowed)."""
    database = Database(url)
    try:
        await migrate(database.url)
        async with database.sessions() as s:
            return await seed_if_empty(s, demo)
    finally:
        await database.dispose()


async def create_admin(email: str, name: str, password: str, url: str | None = None) -> str:
    """Create a ``master`` account (the first one of a real workspace); returns its id.

    Refuses an e-mail already in use. The schema must be migrated (``nagare migrar``).
    """
    if len(password) < 12:
        raise ValueError("password must have at least 12 characters")
    database = Database(url)
    try:
        async with database.sessions() as s:
            email = email.strip().lower()
            if await s.scalar(select(func.count()).select_from(User).where(User.email == email)):
                raise ValueError(f"{email} already exists")
            user_id = new_id()
            s.add(
                User(
                    id=user_id,
                    email=email,
                    name=name,
                    password_hash=hash_password(password),
                    role="master",
                    is_active=True,
                    created_at=iso(datetime.now(UTC)),
                )
            )
            await s.commit()
            return user_id
    finally:
        await database.dispose()


def main() -> None:  # pragma: no cover - convenience entry point
    """``python -m backend.app.seed`` — migrate and seed the configured database."""
    summary = asyncio.run(migrate_and_seed())
    print("Nagare seed: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))


if __name__ == "__main__":  # pragma: no cover
    main()
