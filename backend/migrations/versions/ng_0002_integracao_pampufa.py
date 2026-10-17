"""Integracao com a Pampufa: vinculo produto-projeto, referencia da providencia, webhook (NG8-NG14).

Revision ID: ng_0002
Revises: ng_0001
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Id, Instant, Json

revision: str = "ng_0002"
down_revision: str | None = "ng_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_outbox",
        sa.Column("id", Id(), nullable=False),
        sa.Column("event", sa.String(length=60), nullable=False),
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("payload", Json, nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", Instant(), nullable=False),
        sa.Column("delivered_at", Instant(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_outbox")),
    )
    op.create_index(
        "idx_webhook_pending", "webhook_outbox", ["delivered_at", "next_attempt_at"], unique=False
    )

    op.create_table(
        "product_link_history",
        sa.Column("id", Id(), nullable=False),
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("moved_cards", sa.Integer(), nullable=False),
        sa.Column("at", Instant(), nullable=False),
        sa.Column("by", Id(), nullable=True),
        sa.CheckConstraint(
            "action IN ('linked','unlinked')", name=op.f("ck_product_link_history_action")
        ),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_product_link_history_board_id_boards"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["by"], ["users.id"], name=op.f("fk_product_link_history_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_product_link_history_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_link_history")),
    )
    op.create_index(
        "idx_link_history_product", "product_link_history", ["product_id", "at"], unique=False
    )

    op.create_table(
        "product_links",
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("linked_at", Instant(), nullable=False),
        sa.Column("linked_by", Id(), nullable=True),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_product_links_board_id_boards"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["users.id"],
            name=op.f("fk_product_links_linked_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_product_links_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_product_links")),
    )
    op.create_table(
        "external_refs",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("service", sa.String(length=40), nullable=True),
        sa.Column("kind", sa.String(length=80), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("resolved_at_origin", Instant(), nullable=True),
        sa.Column("closed_at", Instant(), nullable=True),
        sa.CheckConstraint(
            "state IN ('open','resolved_at_origin','delivered','discarded')",
            name=op.f("ck_external_refs_state"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name=op.f("fk_external_refs_card_id_cards"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_refs")),
        sa.UniqueConstraint(
            "source", "external_id", name=op.f("uq_external_refs_source_external_id")
        ),
    )
    op.create_index("idx_external_refs_card", "external_refs", ["card_id"], unique=False)

    with op.batch_alter_table("boards", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("read_only", sa.Boolean(), server_default=sa.false(), nullable=False)
        )

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("external_subject", sa.String(length=80), nullable=True))
        batch_op.create_unique_constraint(
            batch_op.f("uq_users_external_subject"), ["external_subject"]
        )

    # Grupo SR no plano enterprise, com tudo ligado (NG15; uma instalacao, um plano).
    op.execute(
        "UPDATE workspace SET active_plan_id = "
        "(SELECT id FROM plans WHERE slug = 'enterprise') "
        "WHERE EXISTS (SELECT 1 FROM plans WHERE slug = 'enterprise')"
    )


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("uq_users_external_subject"), type_="unique")
        batch_op.drop_column("external_subject")

    with op.batch_alter_table("boards", schema=None) as batch_op:
        batch_op.drop_column("read_only")

    op.drop_index("idx_external_refs_card", table_name="external_refs")

    op.drop_table("external_refs")
    op.drop_table("product_links")
    op.drop_index("idx_link_history_product", table_name="product_link_history")

    op.drop_table("product_link_history")
    op.drop_index("idx_webhook_pending", table_name="webhook_outbox")

    op.drop_table("webhook_outbox")
