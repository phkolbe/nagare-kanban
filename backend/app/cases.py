"""The SR Adv case a card is about (PPLAT-310, 12/11/2026).

The case lives in the ``sr-adv`` service; the card keeps its id, the label and the client
the case picker showed, and the link to the case page
(``/grupo-sr/<produto>/processos/<id>``). A providência whose link is a case page is
linked to that case when its card is created.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from .config import settings
from .errors import BadRequest
from .models import PRODUCT_STANDALONE

__all__ = ["CASE_FIELDS", "case_from_url", "case_page_url", "case_values"]

#: The card columns that hold the case, all set (or cleared) together.
CASE_FIELDS: tuple[str, ...] = ("case_id", "case_label", "case_url", "client_id", "client_name")

_CASE_PAGE = re.compile(
    r"/grupo-sr/(?P<product>[a-z0-9-]+)/processos/(?P<case>[0-9a-fA-F-]{36})(?:[/?#]|$)"
)


def case_page_url(product_id: str, case_id: str) -> str:
    """Where a person opens the case: the SR Adv screen of the product."""
    base = settings.screens_url.rstrip("/") if settings.screens_url else "/grupo-sr"
    return f"{base}/{product_id}/processos/{case_id}"


def case_from_url(url: str | None, product_id: str) -> str | None:
    """The case id of a case page link of ``product_id`` (``None`` for anything else)."""
    if not url:
        return None
    found = _CASE_PAGE.search(str(url))
    if found is None or found["product"] != product_id:
        return None
    try:
        return str(uuid.UUID(found["case"]))
    except ValueError:
        return None


def _text(value: Any, size: int) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:size] or None


def case_values(product_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The five case columns from what the client sent (``case_id`` empty clears them)."""
    case_id = fields.get("case_id")
    if not case_id:
        return dict.fromkeys(CASE_FIELDS)
    if product_id == PRODUCT_STANDALONE:
        raise BadRequest(
            "Cards link to SR Adv cases only inside a Pampufa product.",
            code="case_link_unavailable",
            details={"product": product_id},
        )
    client_id = fields.get("client_id") or None
    return {
        "case_id": str(case_id),
        "case_label": _text(fields.get("case_label"), 200),
        "case_url": case_page_url(product_id, str(case_id)),
        "client_id": str(client_id) if client_id else None,
        "client_name": _text(fields.get("client_name"), 200),
    }
