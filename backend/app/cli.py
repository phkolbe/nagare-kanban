"""Command line: ``nagare <command>`` (``uv run nagare ...``).

    migrar              upgrade the schema (Alembic) and seed the demo workspace when empty
    servir [--reload]   serve the API and the frontend (port 8770 by default)

The command names follow the Pampufa services (``uv run <service> migrar|servir``),
so the platform's ``dev-completo`` runs the Nagare the same way it runs the
others. ``python run.py`` remains the friendly all-in-one entry point.
"""

from __future__ import annotations

import argparse
import asyncio

from .config import settings


def _migrar() -> None:
    from .seed import migrate_and_seed

    summary = asyncio.run(migrate_and_seed())
    if summary.get("skipped"):
        print(
            f"Nagare: schema up to date ({summary.get('users', 0)} users, "
            f"{summary.get('cards', 0)} cards)."
        )
    else:
        print(
            f"Nagare: schema created and demo workspace seeded ({summary.get('cards', 0)} cards)."
        )


def _servir(reload: bool) -> None:
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
        proxy_headers=True,
        log_level="info",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nagare", description="Nagare Kanban")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("migrar", help="upgrade the schema and seed the demo workspace when empty")
    servir = sub.add_parser("servir", help="serve the API and the frontend")
    servir.add_argument("--reload", action="store_true", help="uvicorn auto-reload")
    args = parser.parse_args(argv)
    if args.comando == "migrar":
        _migrar()
    elif args.comando == "servir":
        _servir(args.reload)


if __name__ == "__main__":  # pragma: no cover
    main()
