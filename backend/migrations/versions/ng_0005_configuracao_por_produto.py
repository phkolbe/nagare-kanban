"""Configuracao por produto (D46, 06/11/2026): fuso, plano e flags de cada produto.

O que o produto deixa vazio vale o da conta (`workspace`), que segue como o padrao e como a
configuracao do Nagare sozinho. A flag por produto so liga ou desliga; o plano minimo e global.

Revision ID: ng_0005
Revises: ng_0004
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Id, Instant, Json

revision: str = "ng_0005"
down_revision: str | None = "ng_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_settings",
        sa.Column("product_id", sa.String(length=40), primary_key=True),
        sa.Column(
            "active_plan_id", Id, sa.ForeignKey("plans.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("timezone", sa.String(length=60), nullable=True),
        sa.Column("flags", Json, nullable=False),
        sa.Column("updated_at", Instant, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("product_settings")
