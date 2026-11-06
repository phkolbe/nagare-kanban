"""Projeto ligado ao SR GJ e horas no cartao (GJ-1 e GJ-4, 06/11/2026).

- `projects`: cliente e a referencia do projeto cadastrado em outro servico (o Projeto do
  SR GJ), que guarda nome, chave, situacao, cliente e datas.
- `product_settings.projects_new_url`: para onde "Novo projeto" leva nesse produto.
- `time_entries`: as horas lancadas no cartao.

Revision ID: ng_0006
Revises: ng_0005
Create Date: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Day, Id, Instant

revision: str = "ng_0006"
down_revision: str | None = "ng_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("client_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("external_source", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("external_url", sa.String(length=300), nullable=True))
        batch.create_index(
            "idx_projects_external", ["product_id", "external_source", "external_id"], unique=True
        )
    with op.batch_alter_table("product_settings") as batch:
        batch.add_column(sa.Column("projects_new_url", sa.String(length=300), nullable=True))
    op.create_table(
        "time_entries",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("card_id", Id, sa.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id", Id, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("user_id", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=False),
        sa.Column("worked_on", Day, nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", Instant, nullable=False),
        sa.CheckConstraint("minutes > 0 AND minutes <= 1440", name="ck_time_entries_minutes"),
    )
    op.create_index("idx_time_card", "time_entries", ["card_id"])
    op.create_index("idx_time_project_day", "time_entries", ["project_id", "worked_on"])
    op.create_index("idx_time_product_day", "time_entries", ["product_id", "worked_on"])


def downgrade() -> None:
    op.drop_table("time_entries")
    with op.batch_alter_table("product_settings") as batch:
        batch.drop_column("projects_new_url")
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("idx_projects_external")
        batch.drop_column("external_url")
        batch.drop_column("external_id")
        batch.drop_column("external_source")
        batch.drop_column("client_name")
