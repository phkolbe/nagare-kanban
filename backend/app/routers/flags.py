"""Feature flags, plans and the workspace — the commercial surface (SPEC §3).

A flag is **effective** when it is switched on *and* the workspace's active plan
sits at or above the flag's ``min_plan`` tier. That single rule decides three
things at once: what ``GET /flags/effective`` reports, what the frontend renders,
and what :func:`backend.app.deps.require_flag` lets through. It is therefore
computed in exactly one place — :func:`backend.app.deps.effective_flags` — and
this module imports it rather than restating it. Switching the active plan on
``PATCH /workspace`` changes every answer in the same instant, with no cache to
invalidate.

Guards follow SPEC §4: "Manage plans & flags" belongs to ``master`` alone, so
reading the raw switchboard, flipping a flag and editing plans are all master
work. Two things stay open to everyone: ``GET /flags/effective``, because every
signed-in client needs it to gate its own UI, and ``GET /workspace``, because the
top bar prints the workspace name and current plan.

Custom plans: :data:`backend.app.config.PLAN_TIERS` is seeded from the canonical
four tiers, so a plan created at runtime would otherwise be invisible to
``plan_tier()`` and silently behave as *free*. Every entry point here calls
:func:`_sync_plan_tiers`, which reconciles that lookup with the ``plans`` table —
the database stays authoritative and ``require_flag`` in every other router keeps
agreeing with this one.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from ..config import (
    FLAG_BY_KEY,
    FLAG_CATEGORIES,
    FLAG_KEYS,
    PLAN_TIERS,
    flags_for_plan,
    plan_tier,
)
from ..db import Database, new_id, utcnow
from ..deps import (
    CurrentUser,
    effective_flags,
    get_active_plan,
    get_db,
    require_capability,
)
from ..errors import BadRequest, Conflict, Forbidden, NotFound
from ..schemas import (
    ApiErrorBody,
    FlagOut,
    FlagUpdate,
    ListResponse,
    PlanCreate,
    PlanOut,
    PlanUpdate,
    Schema,
    WorkspaceOut,
    WorkspaceUpdate,
)

__all__ = ["router", "FlagListOut", "FlagCategoryOut", "WorkspaceDetailOut"]

router = APIRouter(tags=["flags & plans"])

#: SPEC §4 — only the master manages the commercial surface.
manage_flags = require_capability("manage_plans_flags")
#: Any signed-in identity may read effective flags and the workspace header.
any_user = require_capability("read_boards_metrics")

_FORBIDDEN = {403: {"model": ApiErrorBody, "description": "Master only"}}

#: Human headings for the switchboard, in the order the admin UI renders them.
CATEGORY_LABELS: dict[str, str] = {
    "board": "Board & flow",
    "wip": "WIP policies",
    "work": "Work items",
    "metrics": "Metrics & analytics",
    "forecasting": "Forecasting & simulation",
    "integration": "Integrations",
    "administration": "Administration",
    "general": "General",
}


# ---------------------------------------------------------------------------
# response models
# ---------------------------------------------------------------------------


class FlagCategoryOut(Schema):
    """One section of the feature-flag switchboard."""

    category: str
    label: str
    flags: list[FlagOut]
    enabled_count: int = 0
    effective_count: int = 0


class FlagListOut(Schema):
    """``GET /flags`` — the raw flags, plus the grouping the admin UI renders."""

    items: list[FlagOut]
    total: int
    categories: list[FlagCategoryOut]
    plan: PlanOut | None = None
    effective_count: int = 0


class WorkspaceDetailOut(WorkspaceOut):
    """``GET|PATCH /workspace`` — the workspace with its plan and live flag state."""

    #: ``{flag_key: bool}`` recomputed after the write, so a plan switch is visible
    #: in the same response that performed it
    flags: dict[str, bool] = {}
    plans: list[PlanOut] = []


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sync_plan_tiers(db: Database) -> None:
    """Teach :func:`config.plan_tier` about every plan row in the database."""
    for row in db.q("SELECT slug, tier_order FROM plans"):
        PLAN_TIERS[str(row["slug"])] = int(row["tier_order"] or 0)


def _plan_out(row: dict[str, Any]) -> dict[str, Any]:
    """Map a ``plans`` row to the API shape, decoding its JSON flag list."""
    raw = row.get("flags")
    if isinstance(raw, str):
        try:
            flags = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            flags = []
    else:
        flags = raw or []
    if not isinstance(flags, list):
        flags = []
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "price_month": float(row.get("price_month") or 0),
        "price_year": float(row.get("price_year") or 0),
        "currency": row.get("currency") or "USD",
        "tier_order": int(row.get("tier_order") or 0),
        "description": row.get("description"),
        "flags": [str(key) for key in flags],
        "is_active": bool(row.get("is_active", 1)),
    }


def _flag_out(row: dict[str, Any], tier: int) -> dict[str, Any]:
    """Map a ``feature_flags`` row, annotating why it is (not) effective."""
    enabled = bool(row["is_enabled"])
    unlocked = plan_tier(str(row["min_plan"])) <= tier
    return {
        "key": row["key"],
        "name": row["name"],
        "description": row.get("description"),
        "is_enabled": enabled,
        "min_plan": row["min_plan"],
        "category": row.get("category") or "general",
        "effective": enabled and unlocked,
        "unlocked_by_plan": unlocked,
    }


def _active_tier(db: Database) -> int:
    plan = get_active_plan(db)
    return plan_tier(plan.get("slug") if plan else None)


def _load_flag(db: Database, key: str) -> dict[str, Any]:
    row = db.q1("SELECT * FROM feature_flags WHERE key = ?", key)
    if row is None:
        raise NotFound("Feature flag", key, code="flag_not_found")
    return row


def _load_plan(db: Database, plan_id: str) -> dict[str, Any]:
    row = db.q1("SELECT * FROM plans WHERE id = ?", plan_id)
    if row is None:
        row = db.q1("SELECT * FROM plans WHERE slug = ?", plan_id)
    if row is None:
        raise NotFound("Plan", plan_id, code="plan_not_found")
    return row


def _validate_flag_keys(keys: list[str]) -> list[str]:
    unknown = [key for key in keys if key not in FLAG_KEYS]
    if unknown:
        raise BadRequest(
            "That plan references feature flags that do not exist.",
            code="unknown_flag",
            details={"unknown": unknown, "known": list(FLAG_KEYS)},
        )
    return list(dict.fromkeys(keys))


def _workspace_row(db: Database) -> dict[str, Any]:
    """The singleton workspace row, provisioned on first read if absent.

    A cold database that has been migrated but not seeded would otherwise answer
    every ``/workspace`` read with a 404 and leave the shell with no header to
    draw. Provisioning is idempotent and picks the lowest tier available.
    """
    row = db.q1("SELECT * FROM workspace WHERE id = 'workspace'")
    if row is not None:
        return row
    fallback = db.q1("SELECT id FROM plans WHERE is_active = 1 ORDER BY tier_order LIMIT 1")
    db.ex(
        "INSERT INTO workspace (id, name, active_plan_id, timezone, updated_at) "
        "VALUES ('workspace', ?, ?, 'UTC', ?)",
        "Nagare",
        fallback["id"] if fallback else None,
        utcnow(),
    )
    return db.q1("SELECT * FROM workspace WHERE id = 'workspace'") or {
        "id": "workspace",
        "name": "Nagare",
        "active_plan_id": None,
        "timezone": "UTC",
        "updated_at": utcnow(),
    }


def _workspace_out(db: Database) -> dict[str, Any]:
    row = _workspace_row(db)
    plan_row = db.q1("SELECT * FROM plans WHERE id = ?", row.get("active_plan_id"))
    plans = [_plan_out(item) for item in db.q("SELECT * FROM plans ORDER BY tier_order")]
    return {
        "id": "workspace",
        "name": row["name"],
        "active_plan_id": row.get("active_plan_id"),
        "timezone": row.get("timezone") or "UTC",
        "updated_at": row.get("updated_at"),
        "plan": _plan_out(plan_row) if plan_row else None,
        "flags": effective_flags(db),
        "plans": plans,
    }


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------


@router.get(
    "/flags",
    response_model=FlagListOut,
    responses=_FORBIDDEN,
    summary="Every feature flag, raw and grouped by category",
)
def list_flags(
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(manage_flags),
    category: str | None = Query(None, description="Restrict to one category."),
) -> dict[str, Any]:
    """The switchboard: each flag with its plan gate and whether it is effective."""
    _sync_plan_tiers(db)
    tier = _active_tier(db)
    rows = db.q("SELECT * FROM feature_flags ORDER BY category, key")
    items = [_flag_out(row, tier) for row in rows]
    if category:
        items = [item for item in items if item["category"] == category]

    ordering = {name: index for index, name in enumerate(FLAG_CATEGORIES)}
    present = sorted({item["category"] for item in items}, key=lambda c: ordering.get(c, 99))
    categories = []
    for name in present:
        grouped = [item for item in items if item["category"] == name]
        categories.append(
            {
                "category": name,
                "label": CATEGORY_LABELS.get(name, name.replace("_", " ").title()),
                "flags": grouped,
                "enabled_count": sum(1 for item in grouped if item["is_enabled"]),
                "effective_count": sum(1 for item in grouped if item["effective"]),
            }
        )

    plan = get_active_plan(db)
    return {
        "items": items,
        "total": len(items),
        "categories": categories,
        "plan": _plan_out(plan) if plan else None,
        "effective_count": sum(1 for item in items if item["effective"]),
    }


@router.get(
    "/flags/effective",
    response_model=dict[str, bool],
    summary="{key: bool} — enabled AND unlocked by the active plan",
)
def effective(
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(any_user),
) -> dict[str, bool]:
    """The exact map ``deps.require_flag`` enforces; the UI gates itself with it."""
    _sync_plan_tiers(db)
    return effective_flags(db)


@router.patch(
    "/flags/{key}",
    response_model=FlagOut,
    responses={**_FORBIDDEN, 404: {"model": ApiErrorBody, "description": "Unknown flag"}},
    summary="Switch a flag on or off, or move its minimum plan",
)
def update_flag(
    key: str,
    payload: FlagUpdate,
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(manage_flags),
) -> dict[str, Any]:
    """Flip a flag. The response already reflects the new effective state."""
    _sync_plan_tiers(db)
    row = _load_flag(db, key)

    changes: dict[str, Any] = {}
    if payload.is_enabled is not None and bool(payload.is_enabled) != bool(row["is_enabled"]):
        changes["is_enabled"] = 1 if payload.is_enabled else 0
    if payload.min_plan is not None and payload.min_plan != row["min_plan"]:
        changes["min_plan"] = payload.min_plan

    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.ex(f"UPDATE feature_flags SET {assignments} WHERE key = ?", [*changes.values(), key])
        row = _load_flag(db, key)

    return _flag_out(row, _active_tier(db))


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


@router.get(
    "/plans",
    response_model=ListResponse[PlanOut],
    responses=_FORBIDDEN,
    summary="The plan catalogue, cheapest tier first",
)
def list_plans(
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(manage_flags),
) -> ListResponse[PlanOut]:
    """Every plan with the flag set it advertises."""
    _sync_plan_tiers(db)
    rows = db.q("SELECT * FROM plans ORDER BY tier_order, name")
    return ListResponse[PlanOut](
        items=[PlanOut(**_plan_out(row)) for row in rows], total=len(rows)
    )


@router.post(
    "/plans",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    responses={**_FORBIDDEN, 409: {"model": ApiErrorBody, "description": "Slug taken"}},
    summary="Create a plan",
)
def create_plan(
    payload: PlanCreate,
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(manage_flags),
) -> PlanOut:
    """Add a tier. ``tier_order`` is what gates flags; ``flags`` is the shop window."""
    slug = payload.slug.strip().lower()
    if db.q1("SELECT id FROM plans WHERE slug = ?", slug):
        raise Conflict("plan_slug_taken", "A plan with that slug already exists.", {"slug": slug})

    keys = _validate_flag_keys(list(payload.flags)) or flags_for_plan(slug)
    row = {
        "id": new_id(),
        "name": payload.name.strip(),
        "slug": slug,
        "price_month": float(payload.price_month),
        "price_year": float(payload.price_year),
        "currency": (payload.currency or "USD").upper(),
        "tier_order": int(payload.tier_order),
        "description": payload.description,
        "flags": json.dumps(keys),
        "is_active": 1 if payload.is_active else 0,
    }
    db.ex(
        "INSERT INTO plans (id, name, slug, price_month, price_year, currency, tier_order, "
        "description, flags, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [row[column] for column in
         ("id", "name", "slug", "price_month", "price_year", "currency", "tier_order",
          "description", "flags", "is_active")],
    )
    _sync_plan_tiers(db)
    return PlanOut(**_plan_out(row))


@router.patch(
    "/plans/{plan_id}",
    response_model=PlanOut,
    responses={**_FORBIDDEN, 404: {"model": ApiErrorBody, "description": "Unknown plan"}},
    summary="Update a plan",
)
def update_plan(
    plan_id: str,
    payload: PlanUpdate,
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(manage_flags),
) -> PlanOut:
    """Change pricing, copy, tier order or the advertised flag set."""
    row = _load_plan(db, plan_id)
    changes: dict[str, Any] = {}

    if payload.name is not None:
        changes["name"] = payload.name.strip()
    if payload.price_month is not None:
        changes["price_month"] = float(payload.price_month)
    if payload.price_year is not None:
        changes["price_year"] = float(payload.price_year)
    if payload.currency is not None:
        changes["currency"] = payload.currency.upper()
    if payload.tier_order is not None:
        changes["tier_order"] = int(payload.tier_order)
    if payload.description is not None:
        changes["description"] = payload.description
    if payload.flags is not None:
        changes["flags"] = json.dumps(_validate_flag_keys(list(payload.flags)))
    if payload.is_active is not None:
        active_id = db.scalar("SELECT active_plan_id FROM workspace WHERE id = 'workspace'")
        if not payload.is_active and active_id == row["id"]:
            raise Conflict(
                "active_plan_locked",
                "Switch the workspace to another plan before retiring this one.",
                {"plan_id": row["id"], "slug": row["slug"]},
            )
        changes["is_active"] = 1 if payload.is_active else 0

    if changes:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.ex(f"UPDATE plans SET {assignments} WHERE id = ?", [*changes.values(), row["id"]])
        row = _load_plan(db, row["id"])
        _sync_plan_tiers(db)

    return PlanOut(**_plan_out(row))


# ---------------------------------------------------------------------------
# workspace
# ---------------------------------------------------------------------------


@router.get(
    "/workspace",
    response_model=WorkspaceDetailOut,
    summary="The workspace, its active plan and the flags that plan makes effective",
)
def get_workspace(
    db: Database = Depends(get_db),
    _actor: CurrentUser = Depends(any_user),
) -> dict[str, Any]:
    """Readable by every signed-in identity: the shell prints name and plan."""
    _sync_plan_tiers(db)
    return _workspace_out(db)


@router.patch(
    "/workspace",
    response_model=WorkspaceDetailOut,
    responses={**_FORBIDDEN, 404: {"model": ApiErrorBody, "description": "Unknown plan"}},
    summary="Rename the workspace or switch its active plan",
)
def update_workspace(
    payload: WorkspaceUpdate,
    db: Database = Depends(get_db),
    actor: CurrentUser = Depends(require_capability("manage_users")),
) -> dict[str, Any]:
    """Switching ``active_plan_id`` re-gates every feature in the same request.

    Renaming is administrator work; changing the plan is a commercial decision and
    stays with the master, matching "Manage plans & flags" in SPEC §4.
    """
    _sync_plan_tiers(db)
    row = _workspace_row(db)
    changes: dict[str, Any] = {}

    if payload.active_plan_id is not None and payload.active_plan_id != row.get("active_plan_id"):
        if actor["role"] != "master":
            raise Forbidden(
                "Only a master can change the workspace plan.",
                code="plan_change_forbidden",
                details={"role": actor["role"], "required": ["master"]},
            )
        plan = _load_plan(db, payload.active_plan_id)
        if not plan["is_active"]:
            raise BadRequest(
                "That plan is retired and cannot be activated.",
                code="plan_inactive",
                details={"plan_id": plan["id"], "slug": plan["slug"]},
            )
        changes["active_plan_id"] = plan["id"]

    if payload.name is not None:
        changes["name"] = payload.name.strip()
    if payload.timezone is not None:
        changes["timezone"] = payload.timezone.strip() or "UTC"

    if changes:
        changes["updated_at"] = utcnow()
        assignments = ", ".join(f"{column} = ?" for column in changes)
        db.ex(
            f"UPDATE workspace SET {assignments} WHERE id = 'workspace'",
            list(changes.values()),
        )
        _sync_plan_tiers(db)

    return _workspace_out(db)


@router.get(
    "/flags/catalogue",
    response_model=dict[str, Any],
    summary="Static flag catalogue with the plan each flag requires",
)
def flag_catalogue(_actor: CurrentUser = Depends(any_user)) -> dict[str, Any]:
    """The SPEC §3 table as data — used by the pricing/upgrade copy in the UI.

    It reads from :mod:`backend.app.config` rather than the database on purpose:
    it answers "what does this product offer", not "what is switched on here".
    """
    return {
        "flags": [
            {
                "key": key,
                "name": FLAG_BY_KEY[key]["name"],
                "description": FLAG_BY_KEY[key]["description"],
                "min_plan": FLAG_BY_KEY[key]["min_plan"],
                "category": FLAG_BY_KEY[key]["category"],
                "category_label": CATEGORY_LABELS.get(
                    FLAG_BY_KEY[key]["category"], FLAG_BY_KEY[key]["category"].title()
                ),
            }
            for key in FLAG_KEYS
        ],
        "categories": [
            {"key": name, "label": CATEGORY_LABELS.get(name, name.title())}
            for name in FLAG_CATEGORIES
        ],
        "plan_flags": {slug: flags_for_plan(slug) for slug in PLAN_TIERS},
    }
