"""Idioma escolhido pela pessoa (N2, decisao de 17/10/2026).

Vazio: o padrao do acesso (portugues pela Pampufa, ingles no login local).

Revision ID: ng_0003
Revises: ng_0002
Create Date: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ng_0003"
down_revision: str | None = "ng_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("locale", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("locale")
