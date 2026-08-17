"""sqlite3 access layer.

There is no ORM in this product on purpose: the domain is a handful of tables and
the metrics are hand-written SQL, so a thin, explicit helper beats a mapper.

Usage from a router::

    from backend.app.deps import get_db

    @router.get("/things")
    def list_things(db: Database = Depends(get_db)):
        return db.q("SELECT * FROM cards WHERE board_id = ?", board_id)

Usage from a script (seed, run.py, MCP server)::

    from backend.app import db
    db.migrate()
    rows = db.q("SELECT * FROM users")

Connections are per-thread: FastAPI runs sync endpoints in a worker thread pool,
so a single shared connection would be unsafe. ``Database`` instances wrap one
connection each; the module-level helpers use a thread-local connection.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import settings

__all__ = [
    "Database",
    "connect",
    "get_database",
    "close_thread_connection",
    "migrate",
    "reset_database",
    "is_seeded",
    "q",
    "q1",
    "ex",
    "exmany",
    "tx",
    "new_id",
    "utcnow",
    "iso",
    "parse_iso",
    "add_days",
    "seconds_between",
]

_local = threading.local()


# ---------------------------------------------------------------------------
# time & id helpers
# ---------------------------------------------------------------------------


def new_id() -> str:
    """A fresh uuid4 string — the id format for every table."""
    return str(uuid.uuid4())


def iso(dt: datetime) -> str:
    """Render a datetime as an ISO-8601 UTC string ending in ``Z``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> str:
    """Current UTC instant as an ISO-8601 ``Z`` string."""
    return iso(datetime.now(timezone.utc))


def parse_iso(value: str | datetime | None) -> datetime | None:
    """Parse any timestamp this app writes back into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def add_days(value: str | datetime, days: float) -> str:
    """Shift an ISO timestamp by ``days`` (may be fractional/negative)."""
    base = parse_iso(value) or datetime.now(timezone.utc)
    return iso(base + timedelta(days=days))


def seconds_between(start: str | datetime | None, end: str | datetime | None) -> float | None:
    """Elapsed seconds between two ISO timestamps, or ``None`` if either is missing."""
    a, b = parse_iso(start), parse_iso(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------


def _dict_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """Open a configured sqlite3 connection.

    ``isolation_level=None`` puts the driver in autocommit mode; transactions are
    explicit through :meth:`Database.tx`, which keeps write boundaries visible in
    the code that owns them rather than hidden in the driver.
    """
    db_path = Path(path) if path is not None else settings.db_path
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        timeout=settings.sqlite_timeout,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


class Database:
    """A connection plus the four query helpers every router uses."""

    __slots__ = ("conn", "_tx_depth", "_owned")

    def __init__(self, conn: sqlite3.Connection, owned: bool = True) -> None:
        self.conn = conn
        self._tx_depth = 0
        self._owned = owned

    # -- reads --------------------------------------------------------------

    def q(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Run a SELECT and return every row as a dict."""
        cur = self.conn.execute(sql, _flatten(args))
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def q1(self, sql: str, *args: Any) -> dict[str, Any] | None:
        """Run a SELECT and return the first row as a dict, or ``None``."""
        cur = self.conn.execute(sql, _flatten(args))
        try:
            return cur.fetchone()
        finally:
            cur.close()

    def scalar(self, sql: str, *args: Any) -> Any:
        """Return the first column of the first row (``None`` when there is none)."""
        row = self.q1(sql, *args)
        if not row:
            return None
        return next(iter(row.values()))

    # -- writes -------------------------------------------------------------

    def ex(self, sql: str, *args: Any) -> int:
        """Execute a write. Returns ``lastrowid`` for INSERTs, else ``rowcount``."""
        cur = self.conn.execute(sql, _flatten(args))
        try:
            if cur.lastrowid and sql.lstrip()[:6].upper() == "INSERT":
                return int(cur.lastrowid)
            return int(cur.rowcount)
        finally:
            cur.close()

    def exmany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        """Execute a write for every row in ``rows``; returns affected row count."""
        cur = self.conn.executemany(sql, rows)
        try:
            return int(cur.rowcount)
        finally:
            cur.close()

    def executescript(self, script: str) -> None:
        """Run a multi-statement SQL script (used by :func:`migrate`)."""
        self.conn.executescript(script)

    # -- transactions -------------------------------------------------------

    @contextmanager
    def tx(self) -> Iterator["Database"]:
        """Explicit transaction; nests through SAVEPOINTs.

        Commits on clean exit, rolls back on any exception, and re-raises.
        """
        if self._tx_depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield self
            except BaseException:
                self.conn.execute("ROLLBACK")
                self._tx_depth = 0
                raise
            else:
                self.conn.execute("COMMIT")
                self._tx_depth = 0
        else:
            name = f"sp_{self._tx_depth}"
            self.conn.execute(f"SAVEPOINT {name}")
            self._tx_depth += 1
            try:
                yield self
            except BaseException:
                self.conn.execute(f"ROLLBACK TO {name}")
                self.conn.execute(f"RELEASE {name}")
                self._tx_depth -= 1
                raise
            else:
                self.conn.execute(f"RELEASE {name}")
                self._tx_depth -= 1

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection (no-op for borrowed connections)."""
        if self._owned:
            try:
                self.conn.close()
            except sqlite3.Error:  # pragma: no cover - closing twice is harmless
                pass

    # Convenience passthroughs so callers can drop to raw sqlite3 when needed.
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Database {settings.db_path}>"


def _flatten(args: tuple[Any, ...]) -> Sequence[Any]:
    """Allow both ``q(sql, a, b)`` and ``q(sql, [a, b])`` / ``q(sql, {...})``."""
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        return list(args[0])
    if len(args) == 1 and isinstance(args[0], dict):
        return args[0]  # type: ignore[return-value]
    return list(args)


# ---------------------------------------------------------------------------
# thread-local module-level connection (scripts, seed, MCP)
# ---------------------------------------------------------------------------


def get_database() -> Database:
    """Return this thread's shared :class:`Database`, creating it on first use."""
    existing: Database | None = getattr(_local, "database", None)
    if existing is None:
        existing = Database(connect())
        _local.database = existing
    return existing


def close_thread_connection() -> None:
    """Close and forget this thread's shared connection."""
    existing: Database | None = getattr(_local, "database", None)
    if existing is not None:
        existing.close()
        _local.database = None


def q(sql: str, *args: Any) -> list[dict[str, Any]]:
    """Module-level :meth:`Database.q` on the thread's shared connection."""
    return get_database().q(sql, *args)


def q1(sql: str, *args: Any) -> dict[str, Any] | None:
    """Module-level :meth:`Database.q1` on the thread's shared connection."""
    return get_database().q1(sql, *args)


def ex(sql: str, *args: Any) -> int:
    """Module-level :meth:`Database.ex` on the thread's shared connection."""
    return get_database().ex(sql, *args)


def exmany(sql: str, rows: Iterable[Sequence[Any]]) -> int:
    """Module-level :meth:`Database.exmany` on the thread's shared connection."""
    return get_database().exmany(sql, rows)


@contextmanager
def tx() -> Iterator[Database]:
    """Module-level transaction on the thread's shared connection."""
    with get_database().tx() as database:
        yield database


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------


def migrate(database: Database | None = None) -> Database:
    """Apply ``schema.sql`` idempotently and return the database used."""
    database = database or get_database()
    script = settings.schema_path.read_text(encoding="utf-8")
    database.executescript(script)
    # executescript() implicitly commits and can drop the pragma state on some
    # builds, so reassert the invariants the app relies on.
    database.conn.execute("PRAGMA foreign_keys = ON")
    return database


def is_seeded(database: Database | None = None) -> bool:
    """True when the workspace already has users (SPEC §11 idempotency rule)."""
    database = database or get_database()
    row = database.q1(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table' AND name='users'"
    )
    if not row or not row["n"]:
        return False
    return bool(database.scalar("SELECT COUNT(*) FROM users"))


def reset_database(path: str | os.PathLike[str] | None = None) -> None:
    """Delete the sqlite files (db + WAL sidecars). Used by ``run.py --reset``."""
    close_thread_connection()
    target = Path(path) if path is not None else settings.db_path
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()
