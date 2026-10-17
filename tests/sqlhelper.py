"""Raw-SQL access for test assertions, on SQLite or PostgreSQL.

The application goes through SQLAlchemy; the tests keep the old, explicit style
of checking the database with a line of SQL (``db.q1("SELECT ... WHERE id = ?",
card_id)``) because that is what proves what was actually written. This module
gives them the old helper API (``q``, ``q1``, ``scalar``, ``ex``, ``exmany``) on
both databases:

* SQLite: the stdlib driver, rows as dicts, exactly as before;
* PostgreSQL: asyncpg on a private event loop. ``?`` placeholders become ``$n``,
  each parameter is coerced to the type the server reports for it (an ISO string
  for a ``timestamptz``, ``0/1`` for a ``boolean``) and results come back in the
  API's canonical form (dashed UUID text, ISO ``Z`` instants).
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from backend.app.sqltypes import from_iso, to_iso

__all__ = ["Database", "connect"]


def _flatten(args: tuple[Any, ...]) -> list[Any]:
    """Allow both ``q(sql, a, b)`` and ``q(sql, [a, b])``."""
    if len(args) == 1 and isinstance(args[0], list | tuple):
        return list(args[0])
    return list(args)


class Database:
    """Common interface; :func:`connect` returns the right implementation."""

    #: The SQLAlchemy URL this handle was opened on.
    url: str = ""

    def q(self, sql: str, *args: Any) -> list[dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError

    def q1(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def scalar(self, sql: str, *args: Any) -> Any:
        row = self.q1(sql, *args)
        if not row:
            return None
        return next(iter(row.values()))

    def ex(self, sql: str, *args: Any) -> int:  # pragma: no cover
        raise NotImplementedError

    def exmany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


class SqliteDatabase(Database):
    def __init__(self, path: str) -> None:
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = lambda cursor, row: {
            col[0]: row[index] for index, col in enumerate(cursor.description)
        }
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")

    def q(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, _flatten(args))
        try:
            return cursor.fetchall()
        finally:
            cursor.close()

    def ex(self, sql: str, *args: Any) -> int:
        cursor = self.conn.execute(sql, _flatten(args))
        try:
            return int(cursor.rowcount)
        finally:
            cursor.close()

    def exmany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        cursor = self.conn.executemany(sql, list(rows))
        try:
            return int(cursor.rowcount)
        finally:
            cursor.close()

    def close(self) -> None:
        self.conn.close()


_PLACEHOLDER = re.compile(r"\?(?=(?:[^']*'[^']*')*[^']*$)")


def _numbered(sql: str) -> str:
    """``?`` placeholders (outside string literals) to asyncpg's ``$1, $2, ...``."""
    counter = iter(range(1, 10_000))
    return _PLACEHOLDER.sub(lambda _m: f"${next(counter)}", sql)


def _coerce(value: Any, type_name: str) -> Any:
    if value is None:
        return None
    if type_name in {"timestamptz", "timestamp", "date"}:
        return from_iso(value) if not isinstance(value, datetime) else value
    if type_name == "bool":
        return bool(value)
    if type_name in {"int2", "int4", "int8"}:
        return int(value)
    if type_name in {"float4", "float8", "numeric"}:
        return float(value)
    if type_name in {"json", "jsonb"}:
        return value if isinstance(value, str) else json.dumps(value)
    if type_name == "uuid":
        return str(value)
    if type_name in {"text", "varchar", "bpchar"}:
        return value if isinstance(value, str) else str(value)
    return value


def _canonical(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return to_iso(value)
    return value


class PostgresDatabase(Database):
    def __init__(self, url: str) -> None:
        import asyncpg

        self._loop = asyncio.new_event_loop()
        dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._conn = self._loop.run_until_complete(asyncpg.connect(dsn))

    def _run(self, coroutine: Any) -> Any:
        return self._loop.run_until_complete(coroutine)

    async def _prepared(self, sql: str, args: list[Any]) -> tuple[Any, list[Any]]:
        statement = await self._conn.prepare(_numbered(sql))
        types = [parameter.name for parameter in statement.get_parameters()]
        return statement, [_coerce(value, name) for value, name in zip(args, types, strict=True)]

    def q(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        async def run() -> list[dict[str, Any]]:
            statement, values = await self._prepared(sql, _flatten(args))
            records = await statement.fetch(*values)
            return [{key: _canonical(value) for key, value in record.items()} for record in records]

        return self._run(run())

    def ex(self, sql: str, *args: Any) -> int:
        async def run() -> int:
            statement, values = await self._prepared(sql, _flatten(args))
            await statement.fetch(*values)
            status = statement.get_statusmsg() or ""
            tail = status.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0

        return self._run(run())

    def exmany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        materialised = [list(row) for row in rows]

        async def run() -> int:
            statement = await self._conn.prepare(_numbered(sql))
            types = [parameter.name for parameter in statement.get_parameters()]
            await statement.executemany(
                [
                    [_coerce(value, name) for value, name in zip(row, types, strict=True)]
                    for row in materialised
                ]
            )
            return len(materialised)

        return self._run(run())

    def close(self) -> None:
        self._run(self._conn.close())
        self._loop.close()


def connect(url: str) -> Database:
    """A raw-SQL handle on the database behind ``url`` (a SQLAlchemy URL)."""
    database: Database
    if url.startswith("sqlite"):
        database = SqliteDatabase(url.split("///", 1)[-1])
    else:
        database = PostgresDatabase(url)
    database.url = url
    return database
