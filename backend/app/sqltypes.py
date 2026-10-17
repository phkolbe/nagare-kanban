"""Column types that keep the API contract while the database gets real types.

The API (and the MCP server, and the vanilla frontend) speaks in strings: ids are
dashed UUID text and every instant is ISO-8601 UTC ending in ``Z``. The database
should not: on PostgreSQL ids are native ``uuid`` and instants are
``timestamptz``. These decorators translate at the boundary, so Python code keeps
handling the exact values it always handled and the schema is typed properly.

On SQLite (tests and the demo) the values are stored as that same canonical text,
which keeps raw SQL in the tests readable and byte-for-byte comparable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

__all__ = ["NIL_ID", "Id", "Instant", "Json", "from_iso", "to_iso"]

#: Bound in place of a value that is not a UUID; no row ever has this id.
NIL_ID = uuid.UUID(int=0)


def to_iso(moment: datetime) -> str:
    """``2026-10-15T12:00:00Z`` — second precision, always UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: Any) -> datetime | None:
    """Parse what the API accepts for an instant: ISO timestamp, ``Z`` or offset, or a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Not an ISO-8601 instant: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Id(TypeDecorator[str]):
    """UUID: native ``uuid`` on PostgreSQL, dashed text elsewhere; ``str`` in Python."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        try:
            parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except ValueError:
            # The API accepts human handles (a card key, a board slug) wherever an id
            # goes and falls back to them when the id lookup finds nothing. A value
            # that is not a UUID can never be an id: compare against the nil UUID,
            # which matches no row, instead of failing the query on PostgreSQL.
            parsed = NIL_ID
        return parsed if dialect.name == "postgresql" else str(parsed)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else str(value)


class Instant(TypeDecorator[str]):
    """A moment in time: ``timestamptz`` on PostgreSQL, ISO ``Z`` text elsewhere.

    Python always sees the ISO ``Z`` string. Bind values may be an ISO string (with
    ``Z`` or an offset), a bare date (taken as midnight UTC) or a ``datetime``.
    """

    impl = String(20)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.TIMESTAMP(timezone=True))
        return dialect.type_descriptor(String(20))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        moment = from_iso(value)
        if moment is None:
            return None
        if dialect.name == "postgresql":
            return moment.replace(microsecond=0)
        return to_iso(moment)

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return to_iso(value)
        return to_iso(from_iso(value))  # type: ignore[arg-type]


#: JSON documents: ``jsonb`` on PostgreSQL, JSON text elsewhere; ``list``/``dict`` in Python.
Json = JSON().with_variant(postgresql.JSONB(), "postgresql")
