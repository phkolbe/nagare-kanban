"""Command line: ``nagare <command>`` (``uv run nagare ...``).

    migrar              upgrade the schema (Alembic); seed the demo workspace when empty,
                        only in dev/teste (NAGARE_AMBIENTE; NAGARE_SEMENTE_DEMO overrides)
    criar-admin         create a master account (--email, --nome; NAGARE_SENHA_ADMIN or prompt)
    servir [--reload]   serve the API and the frontend (port 8770 by default)
    demo                SQLite in data/demo.db, wired to the Pampufa demo (demo-completo)

The command names follow the Pampufa services (``uv run <service> migrar|servir``),
so the platform's ``dev-completo`` runs the Nagare the same way it runs the
others. ``python run.py`` remains the friendly all-in-one entry point.
"""

from __future__ import annotations

import argparse
import asyncio

from .config import BASE_DIR, settings

#: The Pampufa demo (``pampufa demo``): its URL and the demo service credential it
#: accepts. A public demo value, like the other services' — never use it outside the demo.
DEMO_PAMPUFA_URL = "http://127.0.0.1:8030"
DEMO_SEGREDO = "demo-servico-nagare"


def _migrar() -> None:
    from .seed import migrate_and_seed

    settings.check_production()
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

    settings.check_production()
    uvicorn.run(
        "backend.app.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
        proxy_headers=True,
        log_level="info",
    )


def _demo() -> None:
    """Like ``servir``, on its own SQLite file and talking to the Pampufa demo."""
    settings.database_url = f"sqlite+aiosqlite:///{(BASE_DIR / 'data' / 'demo.db').as_posix()}"
    settings.pampufa_url = DEMO_PAMPUFA_URL
    settings.pampufa_client_secret = DEMO_SEGREDO
    settings.public_url = "http://127.0.0.1:8080/nagare"
    settings.ensure_dirs()
    _migrar()
    _servir(reload=False)


def _criar_admin(email: str, nome: str) -> None:
    """First account of a real workspace (homologation, production): role ``master``.

    The password comes from ``NAGARE_SENHA_ADMIN`` (scripts) or is typed twice.
    """
    import getpass
    import os

    from .seed import create_admin

    senha = os.environ.get("NAGARE_SENHA_ADMIN") or getpass.getpass("Senha (12+ caracteres): ")
    if "NAGARE_SENHA_ADMIN" not in os.environ and senha != getpass.getpass("Repita a senha: "):
        raise SystemExit("As senhas não conferem.")
    try:
        user_id = asyncio.run(create_admin(email, nome, senha))
    except ValueError as exc:
        raise SystemExit(f"Nagare: {exc}") from exc
    print(f"Nagare: conta master {email} criada ({user_id}).")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nagare", description="Nagare Kanban")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser(
        "migrar", help="upgrade the schema; seed the demo workspace when empty (dev/teste only)"
    )
    admin = sub.add_parser("criar-admin", help="create a master account (homologation, prod)")
    admin.add_argument("--email", required=True)
    admin.add_argument("--nome", required=True)
    servir = sub.add_parser("servir", help="serve the API and the frontend")
    servir.add_argument("--reload", action="store_true", help="uvicorn auto-reload")
    sub.add_parser("demo", help="SQLite demo wired to the Pampufa demo (pampufa demo-completo)")
    args = parser.parse_args(argv)
    if args.comando == "migrar":
        _migrar()
    elif args.comando == "servir":
        _servir(args.reload)
    elif args.comando == "criar-admin":
        _criar_admin(args.email, args.nome)
    elif args.comando == "demo":
        _demo()


if __name__ == "__main__":  # pragma: no cover
    main()
