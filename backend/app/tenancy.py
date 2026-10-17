"""Which product a request works in, and what role the caller holds there.

The Nagare serves several products at once (decision NG4): ``nagare`` for the
standalone use and one product per Pampufa product (``pampufa``, ``sr-adv``,
``sr-gj``, ``batoom``). The product travels in the ``X-Produto`` header:

* local login (HS256 token): no header means ``nagare``; only the ``master`` may
  work in another product (support, flow swaps);
* Pampufa login (EdDSA token): the header is mandatory, and the role comes from the
  token's roles **in that product** (NG6):

  ====================  =====================================
  Pampufa role          Nagare role in the product
  ====================  =====================================
  ``nagare.admin``      administrator
  ``nagare.editor``     editor
  ``nagare.leitor``     viewer
  ``pampufa.gestor``    viewer in every product (consolidated)
  ``system`` (service)  system
  ====================  =====================================

The resolved product is kept in a context variable for the rest of the request,
so every query can scope itself without threading a parameter through every
helper.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from .errors import ApiError, Forbidden
from .models import PRODUCT_STANDALONE

__all__ = [
    "PRODUCT_HEADER",
    "current_product",
    "in_product",
    "product_for_new_rows",
    "project_key_for",
    "read_product_header",
    "role_from_pampufa",
    "same_product",
    "set_current_product",
]

PRODUCT_HEADER = "X-Produto"
_PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

#: Pampufa role -> Nagare role, strongest first.
PAMPUFA_ROLES: tuple[tuple[str, str], ...] = (
    ("system", "system"),
    ("nagare.admin", "administrator"),
    ("nagare.editor", "editor"),
    ("nagare.leitor", "viewer"),
)
GESTOR_ROLE = "pampufa.gestor"
CONSOLIDATED_SCOPE = "pampufa"

_current: ContextVar[str | None] = ContextVar("nagare_product", default=None)


def current_product() -> str | None:
    """The product of the request being served (``None`` outside a request)."""
    return _current.get()


def set_current_product(product_id: str | None) -> None:
    _current.set(product_id)


def read_product_header(headers: Any) -> str | None:
    """The ``X-Produto`` value, validated; ``None`` when absent."""
    raw = headers.get(PRODUCT_HEADER) or headers.get(PRODUCT_HEADER.lower())
    if raw is None or not str(raw).strip():
        return None
    value = str(raw).strip().lower()
    if not _PRODUCT_ID.match(value):
        raise ApiError(
            "invalid_product",
            "The X-Produto header is not a product id.",
            status=400,
            details={"header": PRODUCT_HEADER},
        )
    return value


def local_product(role: str, requested: str | None) -> str:
    """The product a local-login user works in."""
    product = requested or PRODUCT_STANDALONE
    if product != PRODUCT_STANDALONE and role != "master":
        raise Forbidden(
            "Only the master account can work in a Pampufa product with the local login.",
            code="product_forbidden",
            details={"product": product},
        )
    return product


def role_from_pampufa(roles: dict[str, tuple[str, ...]], product: str) -> str | None:
    """The Nagare role a Pampufa identity holds in ``product`` (``None``: no access)."""
    held = set(roles.get(product, ()))
    for pampufa_role, nagare_role in PAMPUFA_ROLES:
        if pampufa_role in held:
            return nagare_role
    if GESTOR_ROLE in roles.get(CONSOLIDATED_SCOPE, ()):
        return "viewer"
    return None


def in_product(model: Any) -> list[Any]:
    """``[model.product_id == <current product>]``, or nothing outside a request."""
    product = current_product()
    return [] if product is None else [model.product_id == product]


def same_product(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """``row`` when it belongs to the current product (or there is none); else ``None``."""
    product = current_product()
    if row is None or product is None or row.get("product_id") == product:
        return row
    return None


def product_for_new_rows() -> str:
    """The product a row created in this request belongs to."""
    return current_product() or PRODUCT_STANDALONE


def project_key_for(product_id: str) -> str:
    """The key of a product's Nagare project: ``sr-adv`` -> ``SRADV``."""
    key = re.sub(r"[^A-Z0-9]", "", product_id.upper())
    return (key or "PRODUTO")[:20]
