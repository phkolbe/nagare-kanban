"""Shared pytest fixtures for the whole suite.

The database is built **once per session**, migrated with Alembic and seeded with
the real :mod:`backend.app.seed` generator, and every test in every module reads
from it. Seeding takes a couple of seconds; doing it per test would make the suite
unusable.

It is a temp SQLite file by default. With ``TEST_DATABASE_URL`` set (for example
``postgresql+asyncpg://nagare:nagare@localhost:5432/nagare_teste``) the same suite
runs against PostgreSQL — the database of record — after resetting its schema.

Fixtures other test modules rely on:

``client``      FastAPI ``TestClient`` bound to the seeded database
``auth``        ``auth("editor")`` -> ``{"Authorization": "Bearer …"}``
``db``          a raw-SQL handle (:mod:`sqlhelper`) on the same database
``board``       the seeded "Nagare Flow" operational board row
``columns``     its columns, ordered, as a list of dicts
``sim_board``   the seeded "Scenario Lab" simulator board row
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("NAGARE_IGNORE_DOTENV", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import settings  # noqa: E402
from backend.app.db import Database as AppDatabase  # noqa: E402
from backend.app.db import migrate_sync, reset_database  # noqa: E402
from backend.app.security import encode_token  # noqa: E402

from sqlhelper import Database, connect  # noqa: E402

ROLE_EMAILS: dict[str, str] = {
    "master": "nagare.master@grupo-sr.com.br",
    "administrator": "nagare.admin@grupo-sr.com.br",
    "editor": "nagare.editor@grupo-sr.com.br",
    "viewer": "nagare.leitor@grupo-sr.com.br",
    "simulator": "nagare.simulador@grupo-sr.com.br",
    "system": "system@nagare.app",
}


async def _seed(url: str) -> None:
    from backend.app.seed import seed

    database = AppDatabase(url)
    try:
        async with database.sessions() as session:
            await seed(session)
    finally:
        await database.dispose()


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A migrated and seeded database for the whole session."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        reset_database(url)
    else:
        path = tmp_path_factory.mktemp("nagare-db") / "test.db"
        url = f"sqlite+aiosqlite:///{path.as_posix()}"

    # Point the process-wide settings at the test database so anything that reads
    # them (the app factory, scripts) hits the same place as the fixtures.
    original = settings.database_url
    settings.database_url = url
    migrate_sync(url)
    asyncio.run(_seed(url))

    yield url

    settings.database_url = original


@pytest.fixture(scope="session")
def app(database_url: str):
    """The FastAPI application wired to the seeded test database."""
    from backend.app.main import create_app

    return create_app(database_url=database_url)


@pytest.fixture(scope="session")
def client(app) -> Iterator[Any]:
    """A ``TestClient`` with the app's lifespan started."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db(database_url: str) -> Iterator[Database]:
    """A fresh raw-SQL handle on the seeded database, closed after the test."""
    database = connect(database_url)
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(scope="session")
def _user_rows(database_url: str) -> dict[str, dict[str, Any]]:
    database = connect(database_url)
    try:
        return {row["role"]: row for row in database.q("SELECT * FROM users")}
    finally:
        database.close()


@pytest.fixture(scope="session")
def auth(client, _user_rows) -> Callable[[str], dict[str, str]]:
    """Return ``auth(role)`` producing an ``Authorization`` header for that role.

    Prefers a real ``POST /auth/login`` round trip so the header exercises the
    same code path the browser does; the non-interactive ``system`` identity gets
    its token minted directly.
    """
    cache: dict[str, str] = {}

    def _headers(role: str = "master") -> dict[str, str]:
        if role in cache:
            return {"Authorization": f"Bearer {cache[role]}"}
        email = ROLE_EMAILS.get(role)
        if email is None:
            raise KeyError(f"Unknown demo role: {role}")

        token: str | None = None
        if role not in {"system"}:
            response = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": settings.demo_password},
            )
            if response.status_code == 200:
                token = response.json().get("access_token")

        if not token:
            row = _user_rows[role]
            token = encode_token(sub=row["id"], role=row["role"])

        cache[role] = token
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture(scope="session")
def users(_user_rows) -> dict[str, dict[str, Any]]:
    """Seeded users keyed by role."""
    return _user_rows


@pytest.fixture()
def board(db: Database) -> dict[str, Any]:
    """The seeded operational board, "Nagare Flow"."""
    row = db.q1("SELECT * FROM boards WHERE slug = 'nagare-flow'")
    assert row is not None, "the seed must create the 'nagare-flow' board"
    return row


@pytest.fixture()
def board_id(board: dict[str, Any]) -> str:
    return board["id"]


@pytest.fixture()
def sim_board(db: Database) -> dict[str, Any]:
    """The seeded simulator board, "Scenario Lab"."""
    row = db.q1("SELECT * FROM boards WHERE slug = 'scenario-lab'")
    assert row is not None, "the seed must create the 'scenario-lab' board"
    return row


@pytest.fixture()
def columns(db: Database, board: dict[str, Any]) -> list[dict[str, Any]]:
    """The operational board's columns in flow order."""
    return db.q("SELECT * FROM columns WHERE board_id = ? ORDER BY position", board["id"])


@pytest.fixture()
def columns_by_name(columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {column["name"]: column for column in columns}


@pytest.fixture()
def projects(db: Database) -> dict[str, dict[str, Any]]:
    """Seeded projects keyed by their short key (NGR, PAY, MOB, INF)."""
    return {row["key"]: row for row in db.q("SELECT * FROM projects")}
