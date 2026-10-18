"""Homologation and production never get the demo accounts (platform A-IMP-8).

``nagare migrar`` on an empty database creates the catalogues and the workspace only;
the first account comes from ``nagare criar-admin``. Outside dev/teste the CLI refuses
the development JWT secret and the demo seed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from backend.app.config import _DEV_JWT_SECRET, Settings
from backend.app.db import Database
from backend.app.models import Board, Plan, User
from backend.app.security import verify_password
from backend.app.seed import create_admin, migrate_and_seed
from sqlalchemy import func, select


def _url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{(tmp_path / 'prod.db').as_posix()}"


async def _counts(url: str) -> dict[str, int]:
    database = Database(url)
    try:
        async with database.sessions() as s:
            return {
                model.__tablename__: await s.scalar(select(func.count()).select_from(model))
                for model in (User, Board, Plan)
            }
    finally:
        await database.dispose()


def test_migrar_sem_demo_nao_cria_contas_nem_quadros(tmp_path: Path) -> None:
    url = _url(tmp_path)
    summary = asyncio.run(migrate_and_seed(url, demo=False))
    counts = asyncio.run(_counts(url))
    assert summary["skipped"] is True
    assert counts["users"] == 0
    assert counts["boards"] == 0
    assert counts["plans"] > 0  # catalogues still synced


def test_criar_admin_cria_master_e_recusa_repetido(tmp_path: Path) -> None:
    url = _url(tmp_path)
    asyncio.run(migrate_and_seed(url, demo=False))
    asyncio.run(create_admin("Paulo@Exemplo.com", "Paulo", "senha-forte-123", url))

    async def _admin() -> User:
        database = Database(url)
        try:
            async with database.sessions() as s:
                return (await s.scalars(select(User))).one()
        finally:
            await database.dispose()

    user = asyncio.run(_admin())
    assert (user.email, user.role, user.is_active) == ("paulo@exemplo.com", "master", True)
    assert verify_password("senha-forte-123", user.password_hash)
    with pytest.raises(ValueError, match="already exists"):
        asyncio.run(create_admin("paulo@exemplo.com", "Outro", "senha-forte-456", url))
    with pytest.raises(ValueError, match="12 characters"):
        asyncio.run(create_admin("curta@exemplo.com", "Curta", "curta", url))


def test_semente_demo_so_em_dev_e_teste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NAGARE_SEMENTE_DEMO", raising=False)
    for ambiente, esperado in (("dev", True), ("teste", True), ("prod", False), ("hml", False)):
        monkeypatch.setenv("NAGARE_AMBIENTE", ambiente)
        assert Settings().demo_seed is esperado, ambiente


def test_producao_recusa_segredo_de_dev_e_semente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAGARE_AMBIENTE", "prod")
    monkeypatch.delenv("NAGARE_SEMENTE_DEMO", raising=False)

    monkeypatch.setenv("NAGARE_JWT_SECRET", _DEV_JWT_SECRET)
    with pytest.raises(SystemExit, match="NAGARE_JWT_SECRET"):
        Settings().check_production()

    monkeypatch.setenv("NAGARE_JWT_SECRET", "um-segredo-de-producao-bem-comprido")
    Settings().check_production()

    monkeypatch.setenv("NAGARE_SEMENTE_DEMO", "true")
    with pytest.raises(SystemExit, match="NAGARE_SEMENTE_DEMO"):
        Settings().check_production()
