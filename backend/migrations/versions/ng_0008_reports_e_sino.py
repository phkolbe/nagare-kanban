"""Area de Reports, onda 1 (24/11/2026).

- `card_movements.wip_override`: o movimento que passou por cima do limite de WIP (classe
  urgente). Antes so voltava na resposta do movimento; o REP-13 (Excecoes de politica) le daqui.
- `wip_limit_changes`: cada mudanca de limite de WIP (quadro, grupo ou coluna), com o antes e o
  depois, quem e quando. Tambem para o REP-13.
- `notifications`: os avisos do sino do Nagare sozinho (geral; o primeiro tipo, o relatorio
  agendado, chega na onda 3).

O REP-13 so mostra o que acontecer depois desta migracao (decisao R14 do plano).

Revision ID: ng_0008
Revises: ng_0007
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Id, Instant

revision: str = "ng_0008"
down_revision: str | None = "ng_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD COLUMN direto (sem batch): no SQLite nao recria a tabela.
    op.add_column(
        "card_movements",
        sa.Column("wip_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "wip_limit_changes",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False, server_default="nagare"),
        sa.Column("board_id", Id, sa.ForeignKey("boards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=10), nullable=False),
        sa.Column("target_id", Id, nullable=False),
        sa.Column("target_name", sa.String(length=200), nullable=False),
        sa.Column("field", sa.String(length=10), nullable=False),
        sa.Column("old_value", sa.Integer(), nullable=True),
        sa.Column("new_value", sa.Integer(), nullable=True),
        sa.Column("changed_by", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("changed_at", Instant, nullable=False),
        sa.CheckConstraint(
            "scope IN ('board','group','column')", name="ck_wip_limit_changes_scope"
        ),
        sa.CheckConstraint("field IN ('wip_min','wip_max')", name="ck_wip_limit_changes_field"),
    )
    op.create_index("idx_wip_changes_board_time", "wip_limit_changes", ["board_id", "changed_at"])
    op.create_table(
        "notifications",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False, server_default="nagare"),
        sa.Column("user_id", Id, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.String(length=300), nullable=True),
        sa.Column("created_at", Instant, nullable=False),
        sa.Column("read_at", Instant, nullable=True),
    )
    op.create_index("idx_notifications_user", "notifications", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_notifications_user", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("idx_wip_changes_board_time", table_name="wip_limit_changes")
    op.drop_table("wip_limit_changes")
    with op.batch_alter_table("card_movements") as batch:
        batch.drop_column("wip_override")
