"""Shared pytest fixtures for the whole suite.

The database is built **once per session** into a temp file, seeded with the real
:mod:`backend.app.seed` generator, and every test in every module reads from it.
Seeding takes a couple of seconds; doing it per test would make the suite
unusable.

Fixtures other test modules rely on:

``client``      FastAPI ``TestClient`` bound to the seeded database
``auth``        ``auth("editor")`` -> ``{"Authorization": "Bearer …"}``
``db``          a :class:`backend.app.db.Database` on the same file
``board``       the seeded "Nagare Flow" operational board row
``columns``     its columns, ordered, as a list of dicts
``sim_board``   the seeded "Scenario Lab" simulator board row
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import db as db_module  # noqa: E402
from backend.app.config import settings  # noqa: E402
from backend.app.db import Database, connect  # noqa: E402
from backend.app.security import encode_token  # noqa: E402

ROLE_EMAILS: dict[str, str] = {
    "master": "master@nagare.app",
    "administrator": "admin@nagare.app",
    "editor": "editor@nagare.app",
    "viewer": "viewer@nagare.app",
    "simulator": "simulator@nagare.app",
    "system": "system@nagare.app",
}


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A temp sqlite file, migrated and seeded once for the whole session."""
    path = tmp_path_factory.mktemp("nagare-db") / "test.db"

    # Point the process-wide settings at the temp file so module-level helpers
    # (db.q, seed, MCP-style scripts) hit the same database as the dependency.
    original = settings.db_path
    settings.db_path = path
    db_module.close_thread_connection()

    from backend.app.seed import seed  # imported late: it reads settings.db_path

    database = Database(connect(path))
    try:
        db_module.migrate(database)
        seed(database)
    finally:
        database.close()

    yield path

    db_module.close_thread_connection()
    settings.db_path = original


@pytest.fixture(scope="session")
def app(db_path: Path):
    """The FastAPI application wired to the seeded temp database."""
    from backend.app.main import create_app

    return create_app(db_path=db_path)


@pytest.fixture(scope="session")
def client(app) -> Iterator[Any]:
    """A ``TestClient`` with the app's lifespan started."""
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db(db_path: Path) -> Iterator[Database]:
    """A fresh connection to the seeded database, closed after the test."""
    database = Database(connect(db_path))
    try:
        yield database
    finally:
        database.close()


@pytest.fixture(scope="session")
def _user_rows(db_path: Path) -> dict[str, dict[str, Any]]:
    database = Database(connect(db_path))
    try:
        return {row["role"]: row for row in database.q("SELECT * FROM users")}
    finally:
        database.close()


@pytest.fixture(scope="session")
def auth(client, _user_rows) -> Callable[[str], dict[str, str]]:
    """Return ``auth(role)`` producing an ``Authorization`` header for that role.

    Prefers a real ``POST /auth/login`` round trip so the header exercises the
    same code path the browser does; falls back to minting the token directly
    while the auth router is still being written.
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
            try:
                response = client.post(
                    "/api/v1/auth/login",
                    json={"email": email, "password": settings.demo_password},
                )
                if response.status_code == 200:
                    token = response.json().get("access_token")
            except Exception:  # pragma: no cover - router not ready yet
                token = None

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
    return db.q(
        "SELECT * FROM columns WHERE board_id = ? ORDER BY position", board["id"]
    )


@pytest.fixture()
def columns_by_name(columns: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {column["name"]: column for column in columns}


@pytest.fixture()
def projects(db: Database) -> dict[str, dict[str, Any]]:
    """Seeded projects keyed by their short key (NGR, PAY, MOB, INF)."""
    return {row["key"]: row for row in db.q("SELECT * FROM projects")}
