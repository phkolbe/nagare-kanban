"""Preferencias da tela escolhidas pela pessoa (decisao de 18/10/2026).

Hoje so o formato das ferramentas do quadro (`board_toolbar`: barra recolhivel ou painel
flutuante). Vazio: os padroes das telas.

Revision ID: ng_0004
Revises: ng_0003
Create Date: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Json

revision: str = "ng_0004"
down_revision: str | None = "ng_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("preferences", Json, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("preferences")
