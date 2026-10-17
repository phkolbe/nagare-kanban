"""Database access: async SQLAlchemy engine, sessions, migrations and time helpers.

PostgreSQL is the database of record (``postgresql+asyncpg://...``); SQLite
(``sqlite+aiosqlite://...``) runs the tests and the zero-setup demo, the same rule
the Pampufa platform follows. The schema comes from Alembic
(``backend/migrations``), never from ``create_all`` in production code.

Usage from a router::

    from backend.app.deps import get_session

    @router.get("/things")
    async def list_things(s: AsyncSession = Depends(get_session)):
        return (await s.scalars(select(Card).where(Card.board_id == board_id))).all()

Writes are committed explicitly by the endpoint that owns them (``await
s.commit()``); the dependency only rolls back when something escapes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session

from .config import BASE_DIR, settings
from .models import User, new_uuid
from .sqltypes import from_iso, to_iso


@event.listens_for(Session, "do_orm_execute")
def _always_fresh(state: ORMExecuteState) -> None:
    """Every ORM ``SELECT`` refreshes the instances it returns.

    The routers mix ORM reads with bulk ``UPDATE`` statements (``COALESCE`` stamps,
    position shuffles). Without this, a read after such an update could hand back
    the instance already in the identity map with its old values.
    """
    if state.is_select:
        state.update_execution_options(populate_existing=True)


__all__ = [
    "Database",
    "add_days",
    "create_engine",
    "is_seeded",
    "iso",
    "make_sessionmaker",
    "migrate",
    "new_id",
    "parse_iso",
    "reset_database",
    "seconds_between",
    "utcnow",
]

MIGRATIONS_DIR: Path = BASE_DIR / "backend" / "migrations"


# ---------------------------------------------------------------------------
# time & id helpers
# ---------------------------------------------------------------------------


def new_id() -> str:
    """A fresh UUID v7 string — the id format for every table."""
    return new_uuid()


def iso(dt: datetime) -> str:
    """Render a datetime as an ISO-8601 UTC string ending in ``Z``."""
    return to_iso(dt)


def utcnow() -> str:
    """Current UTC instant as an ISO-8601 ``Z`` string."""
    return iso(datetime.now(UTC))


def parse_iso(value: str | datetime | None) -> datetime | None:
    """Parse any timestamp this app writes back into an aware UTC datetime."""
    try:
        return from_iso(value)
    except ValueError:
        return None


def add_days(value: str | datetime, days: float) -> str:
    """Shift an ISO timestamp by ``days`` (may be fractional/negative)."""
    base = parse_iso(value) or datetime.now(UTC)
    return iso(base + timedelta(days=days))


def seconds_between(start: str | datetime | None, end: str | datetime | None) -> float | None:
    """Elapsed seconds between two ISO timestamps, or ``None`` if either is missing."""
    a, b = parse_iso(start), parse_iso(end)
    if a is None or b is None:
        return None
    return (b - a).total_seconds()


# ---------------------------------------------------------------------------
# engine and sessions
# ---------------------------------------------------------------------------


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


def create_engine(url: str | None = None) -> AsyncEngine:
    """An async engine for ``url`` (default: ``settings.database_url``)."""
    url = url or settings.database_url
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(url, connect_args={"timeout": 30})
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
        return engine
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions that keep attribute values after commit (routers read them back)."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


class Database:
    """One engine plus its session factory — what the app and the scripts hold."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.database_url
        self.engine = create_engine(self.url)
        self.sessions = make_sessionmaker(self.engine)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    async def dispose(self) -> None:
        await self.engine.dispose()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Database {self.engine.url.render_as_string(hide_password=True)}>"


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------


def _alembic_config(url: str) -> Any:
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url)
    return config


def migrate_sync(url: str | None = None, revision: str = "head") -> None:
    """Upgrade the schema to ``revision`` (blocking; scripts and tests)."""
    from alembic import command

    command.upgrade(_alembic_config(url or settings.database_url), revision)


async def migrate(url: str | None = None, revision: str = "head") -> None:
    """Upgrade the schema from inside a running event loop (Alembic runs its own)."""
    await asyncio.to_thread(migrate_sync, url, revision)


def downgrade_sync(url: str | None = None, revision: str = "base") -> None:
    """Downgrade the schema (``reset`` on PostgreSQL)."""
    from alembic import command

    command.downgrade(_alembic_config(url or settings.database_url), revision)


async def is_seeded(session: AsyncSession) -> bool:
    """True when the workspace already has users (SPEC §11 idempotency rule)."""
    return bool(await session.scalar(select(func.count()).select_from(User)))


def reset_database(url: str | None = None) -> None:
    """Start from an empty schema: delete the SQLite files, or downgrade PostgreSQL."""
    url = url or settings.database_url
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(path + suffix)
            if candidate.exists():
                candidate.unlink()
        return
    downgrade_sync(url)


async def session_scope(database: Database) -> AsyncIterator[AsyncSession]:
    """A session that rolls back anything left uncommitted (scripts)."""
    async with database.sessions() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
