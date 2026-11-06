"""Settings by product (D46): the product's row over the workspace defaults.

``current_product()`` says whose settings apply. The standalone Nagare (and a request with
no product) reads the workspace row only; a Pampufa product reads its ``product_settings``
row first and falls back to the workspace for whatever it leaves empty.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from .models import PRODUCT_STANDALONE, ProductSettings, Workspace, as_dict
from .tenancy import current_product


def own_product() -> str | None:
    """The product whose own settings apply, or ``None`` for the workspace defaults."""
    product = current_product()
    return None if product in (None, PRODUCT_STANDALONE) else product


async def product_row(s: AsyncSession) -> dict[str, Any] | None:
    product = own_product()
    return as_dict(await s.get(ProductSettings, product)) if product else None


async def flag_overrides(s: AsyncSession) -> dict[str, bool]:
    row = await product_row(s)
    return {str(k): bool(v) for k, v in ((row or {}).get("flags") or {}).items()}


def zone_name(db: Session) -> str:
    """The time zone in force (sync, for the metrics built inside ``run_sync``)."""
    product = own_product()
    if product:
        own = db.scalar(
            select(ProductSettings.timezone).where(ProductSettings.product_id == product)
        )
        if own:
            return str(own)
    return str(db.scalar(select(Workspace.timezone)) or "UTC")


def zone(db: Session) -> ZoneInfo:
    try:
        return ZoneInfo(zone_name(db))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")
