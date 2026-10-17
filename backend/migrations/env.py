"""Alembic environment for the Nagare schema (async engine, PostgreSQL or SQLite).

Run through ``backend.app.db.migrate`` (the app on boot, ``run.py``, the CLI and
the tests) or straight with ``alembic`` using the ``sqlalchemy.url`` option. A
connection handed in through ``config.attributes["connection"]`` is used as is.
"""

from __future__ import annotations

import asyncio

from alembic import context
from backend.app.config import settings
from backend.app.db import create_engine
from backend.app.models import metadata
from sqlalchemy.engine import Connection

config = context.config


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
    else:
        asyncio.run(_run_async())
