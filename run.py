#!/usr/bin/env python
"""Nagare Kanban — single entry point.

    uv sync
    uv run python run.py

Migrates the database (Alembic), seeds the demo workspace on first run, then
serves the API *and* the React screens (web/dist) from one origin at http://127.0.0.1:8770.
The database is ``NAGARE_DATABASE_URL`` (PostgreSQL in the platform's dev setup);
without it, a SQLite file in ``data/`` keeps a cold clone runnable as a demo.

    python run.py --reset          delete the database and reseed from scratch
    python run.py --port 9000      serve on another port
    python run.py --seed-only      migrate + seed, then exit (no server)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Run the Nagare Kanban server (API + screens on one origin).",
    )
    parser.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="bind port (default 8770)")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the existing database and regenerate the demo workspace",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="migrate and seed, print a summary, and exit without serving",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="enable uvicorn auto-reload (development only)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL (default NAGARE_DATABASE_URL, else data/nagare.db)",
    )
    return parser.parse_args(argv)


BANNER = r"""
   _  __
  / |/ /__ ____ ____ _____ ___
 /    / _ `/ _ `/ _ `/ __// -_)
/_/|_/\_,_/\_, /\_,_/_/   \__/     流れ
          /___/
"""


def main(argv: list[str] | None = None) -> int:
    # Windows consoles and piped stdout default to cp1252, which cannot encode
    # the 流れ in the banner. Switch to UTF-8 rather than dumbing the output down.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):  # pragma: no cover - exotic shells
            pass

    args = parse_args(argv)

    # Settings read the environment at import time, so apply overrides first.
    if args.port is not None:
        os.environ["NAGARE_PORT"] = str(args.port)
    if args.host is not None:
        os.environ["NAGARE_HOST"] = args.host
    if args.database_url is not None:
        os.environ["NAGARE_DATABASE_URL"] = args.database_url

    import asyncio

    from backend.app import db as db_module
    from backend.app.config import settings
    from backend.app.seed import migrate_and_seed
    from sqlalchemy.engine import make_url

    settings.ensure_dirs()
    where = make_url(settings.database_url).render_as_string(hide_password=True)

    if args.reset:
        db_module.reset_database()
        print(f"Reset the database at {where}")

    summary = asyncio.run(migrate_and_seed())

    if summary.get("skipped"):
        print(
            f"Database ready at {where} "
            f"({summary.get('users', 0)} users, {summary.get('cards', 0)} cards) — "
            "seeding skipped."
        )
    else:
        print(
            f"Seeded {summary.get('cards', 0)} cards "
            f"({summary.get('delivered', 0)} delivered, "
            f"{summary.get('in_flight', 0)} in flight, "
            f"{summary.get('upstream', 0)} upstream, "
            f"{summary.get('sealed', 0)} sealed) with "
            f"{summary.get('movements', 0)} movements and "
            f"{summary.get('events', 0)} audit events."
        )

    if args.seed_only:
        return 0

    url = f"http://{settings.host}:{settings.port}"
    print(BANNER)
    print(f"  Nagare Kanban  ·  {url}")
    print(f"  API docs       ·  {url}/docs")
    print(f"  Health         ·  {url}/api/v1/health")
    print()
    print("  Sign in with any of these (all share the same demo password):")
    print(f"    nagare.master@grupo-sr.com.br     master          {settings.demo_password}")
    print(f"    nagare.admin@grupo-sr.com.br      administrator   {settings.demo_password}")
    print(f"    nagare.editor@grupo-sr.com.br     editor          {settings.demo_password}")
    print(f"    nagare.leitor@grupo-sr.com.br     viewer          {settings.demo_password}")
    print(f"    nagare.simulador@grupo-sr.com.br  simulator       {settings.demo_password}")
    print("    (system@nagare.app is an API-only identity and cannot sign in)")
    print()
    print("  Press Ctrl+C to stop.")
    print()

    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=args.reload,
        log_level="info",
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
