"""Esquema inicial do Nagare em SQLAlchemy (tabelas do antigo schema.sql com product_id).

Revision ID: ng_0001
Revises:
Create Date: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Id, Instant, Json

revision: str = "ng_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "boards",
        sa.Column("id", Id(), nullable=False),
        sa.Column("product_id", sa.String(length=40), server_default="nagare", nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_board_id", Id(), nullable=True),
        sa.Column("count_envelope_in_metrics", sa.Boolean(), nullable=False),
        sa.Column("confirm_moves", sa.Boolean(), nullable=False),
        sa.Column("wip_min", sa.Integer(), nullable=True),
        sa.Column("wip_max", sa.Integer(), nullable=True),
        sa.Column("sle_days", sa.Integer(), nullable=True),
        sa.Column("sle_percentile", sa.Integer(), nullable=True),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('operational','simulator')", name=op.f("ck_boards_kind")),
        sa.CheckConstraint("sle_days IS NULL OR sle_days > 0", name=op.f("ck_boards_sle_days")),
        sa.CheckConstraint(
            "sle_percentile IS NULL OR (sle_percentile > 0 AND sle_percentile <= 100)",
            name=op.f("ck_boards_sle_percentile"),
        ),
        sa.CheckConstraint("wip_max IS NULL OR wip_max >= 0", name=op.f("ck_boards_wip_max")),
        sa.CheckConstraint("wip_min IS NULL OR wip_min >= 0", name=op.f("ck_boards_wip_min")),
        sa.ForeignKeyConstraint(
            ["source_board_id"],
            ["boards.id"],
            name=op.f("fk_boards_source_board_id_boards"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boards")),
        sa.UniqueConstraint("product_id", "slug", name=op.f("uq_boards_product_id_slug")),
    )
    op.create_index("idx_boards_kind", "boards", ["kind", "archived"], unique=False)
    op.create_index("idx_boards_product", "boards", ["product_id", "archived"], unique=False)
    op.create_index("idx_boards_source", "boards", ["source_board_id"], unique=False)

    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("min_plan", sa.String(length=20), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "min_plan IN ('free','team','business','enterprise')",
            name=op.f("ck_feature_flags_min_plan"),
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_feature_flags")),
    )
    op.create_index("idx_flags_category", "feature_flags", ["category"], unique=False)

    op.create_table(
        "plans",
        sa.Column("id", Id(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("price_month", sa.Float(), nullable=False),
        sa.Column("price_year", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("tier_order", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("flags", Json, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("slug", name=op.f("uq_plans_slug")),
    )
    op.create_index("idx_plans_tier", "plans", ["tier_order"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", Id(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=200), nullable=True),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("last_login_at", Instant(), nullable=True),
        sa.CheckConstraint(
            "role IN ('master','administrator','editor','viewer','simulator','system')",
            name=op.f("ck_users_role"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index("idx_users_active", "users", ["is_active"], unique=False)
    op.create_index("idx_users_role", "users", ["role"], unique=False)

    op.create_table(
        "column_groups",
        sa.Column("id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("wip_min", sa.Integer(), nullable=True),
        sa.Column("wip_max", sa.Integer(), nullable=True),
        sa.Column("color", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "wip_max IS NULL OR wip_max >= 0", name=op.f("ck_column_groups_wip_max")
        ),
        sa.CheckConstraint(
            "wip_min IS NULL OR wip_min >= 0", name=op.f("ck_column_groups_wip_min")
        ),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_column_groups_board_id_boards"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_column_groups")),
    )
    op.create_index("idx_groups_board", "column_groups", ["board_id", "position"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", Id(), nullable=False),
        sa.Column("product_id", sa.String(length=40), server_default="nagare", nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("color", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("owner_id", Id(), nullable=True),
        sa.Column("started_at", Instant(), nullable=True),
        sa.Column("target_date", Instant(), nullable=True),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "status IN ('discovery','active','on_hold','done','cancelled')",
            name=op.f("ck_projects_status"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_projects_owner_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("product_id", "key", name=op.f("uq_projects_product_id_key")),
    )
    op.create_index("idx_projects_product", "projects", ["product_id", "archived"], unique=False)
    op.create_index("idx_projects_status", "projects", ["status", "archived"], unique=False)

    op.create_table(
        "simulation_runs",
        sa.Column("id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("params", Json, nullable=False),
        sa.Column("results", Json, nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("created_by", Id(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('monte_carlo_when','monte_carlo_how_many','wip_experiment','class_of_service_mix','replay')",
            name=op.f("ck_simulation_runs_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_simulation_runs_board_id_boards"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_simulation_runs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
    )
    op.create_index("idx_runs_board", "simulation_runs", ["board_id", "created_at"], unique=False)
    op.create_index("idx_runs_kind", "simulation_runs", ["kind"], unique=False)

    op.create_table(
        "workspace",
        sa.Column("id", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("active_plan_id", Id(), nullable=True),
        sa.Column("timezone", sa.String(length=60), nullable=False),
        sa.Column("updated_at", Instant(), nullable=False),
        sa.CheckConstraint("id = 'workspace'", name=op.f("ck_workspace_singleton")),
        sa.ForeignKeyConstraint(
            ["active_plan_id"],
            ["plans.id"],
            name=op.f("fk_workspace_active_plan_id_plans"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace")),
    )
    op.create_table(
        "columns",
        sa.Column("id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("group_id", Id(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("segment", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("wip_min", sa.Integer(), nullable=True),
        sa.Column("wip_max", sa.Integer(), nullable=True),
        sa.Column("is_commitment_point", sa.Boolean(), nullable=False),
        sa.Column("is_delivery_point", sa.Boolean(), nullable=False),
        sa.Column("color", sa.String(length=40), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('envelope','queue','activity','done')", name=op.f("ck_columns_kind")
        ),
        sa.CheckConstraint("segment IN ('upstream','downstream')", name=op.f("ck_columns_segment")),
        sa.CheckConstraint("wip_max IS NULL OR wip_max >= 0", name=op.f("ck_columns_wip_max")),
        sa.CheckConstraint("wip_min IS NULL OR wip_min >= 0", name=op.f("ck_columns_wip_min")),
        sa.ForeignKeyConstraint(
            ["board_id"], ["boards.id"], name=op.f("fk_columns_board_id_boards"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["column_groups.id"],
            name=op.f("fk_columns_group_id_column_groups"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_columns")),
    )
    op.create_index("idx_columns_board", "columns", ["board_id", "position"], unique=False)
    op.create_index("idx_columns_group", "columns", ["group_id"], unique=False)
    op.create_index(
        "idx_columns_segment", "columns", ["board_id", "segment", "position"], unique=False
    )

    op.create_table(
        "cards",
        sa.Column("id", Id(), nullable=False),
        sa.Column("product_id", sa.String(length=40), server_default="nagare", nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("column_id", Id(), nullable=False),
        sa.Column("project_id", Id(), nullable=True),
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("service_class", sa.String(length=20), nullable=False),
        sa.Column("assignee_id", Id(), nullable=True),
        sa.Column("requester", sa.String(length=200), nullable=True),
        sa.Column("size", sa.String(length=4), nullable=True),
        sa.Column("value_area", sa.String(length=20), nullable=False),
        sa.Column("demand_type", sa.String(length=20), nullable=False),
        sa.Column("due_date", Instant(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("envelope_state", sa.String(length=10), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("tags", Json, nullable=False),
        sa.Column("custom_fields", Json, nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("opened_at", Instant(), nullable=True),
        sa.Column("committed_at", Instant(), nullable=True),
        sa.Column("delivered_at", Instant(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_by", Id(), nullable=True),
        sa.CheckConstraint(
            "demand_type IN ('value','failure','maintenance')", name=op.f("ck_cards_demand_type")
        ),
        sa.CheckConstraint(
            "envelope_state IN ('sealed','opened')", name=op.f("ck_cards_envelope_state")
        ),
        sa.CheckConstraint(
            "service_class IN ('expedite','standard','fixed_date','intangible')",
            name=op.f("ck_cards_service_class"),
        ),
        sa.CheckConstraint(
            "size IS NULL OR size IN ('xs','s','m','l','xl')", name=op.f("ck_cards_size")
        ),
        sa.CheckConstraint(
            "value_area IN ('feature','defect','risk','debt','compliance')",
            name=op.f("ck_cards_value_area"),
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_cards_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["board_id"], ["boards.id"], name=op.f("fk_cards_board_id_boards"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["columns.id"],
            name=op.f("fk_cards_column_id_columns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_cards_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_cards_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cards")),
        sa.UniqueConstraint("product_id", "key", name=op.f("uq_cards_product_id_key")),
    )
    op.create_index("idx_cards_assignee", "cards", ["assignee_id"], unique=False)
    op.create_index("idx_cards_board_archived", "cards", ["board_id", "archived"], unique=False)
    op.create_index("idx_cards_board_column", "cards", ["board_id", "column_id"], unique=False)
    op.create_index("idx_cards_committed", "cards", ["board_id", "committed_at"], unique=False)
    op.create_index("idx_cards_delivered", "cards", ["board_id", "delivered_at"], unique=False)
    op.create_index("idx_cards_envelope", "cards", ["board_id", "envelope_state"], unique=False)
    op.create_index("idx_cards_project", "cards", ["project_id"], unique=False)
    op.create_index("idx_cards_service_class", "cards", ["board_id", "service_class"], unique=False)

    op.create_table(
        "blockers",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("started_at", Instant(), nullable=False),
        sa.Column("ended_at", Instant(), nullable=True),
        sa.Column("created_by", Id(), nullable=True),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_blockers_card_id_cards"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_blockers_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blockers")),
    )
    op.create_index("idx_blockers_card", "blockers", ["card_id", "started_at"], unique=False)
    op.create_index("idx_blockers_open", "blockers", ["ended_at"], unique=False)

    op.create_table(
        "card_events",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("actor_id", Id(), nullable=True),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("field", sa.String(length=80), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("created_at", Instant(), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('created','moved','updated','blocked','unblocked','opened_envelope','archived','commented','task_added','task_completed')",
            name=op.f("ck_card_events_event_type"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_card_events_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_card_events_board_id_boards"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_card_events_card_id_cards"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_events")),
    )
    op.create_index(
        "idx_events_board_time", "card_events", ["board_id", "created_at"], unique=False
    )
    op.create_index("idx_events_card", "card_events", ["card_id", "created_at"], unique=False)
    op.create_index("idx_events_type", "card_events", ["event_type"], unique=False)

    op.create_table(
        "card_movements",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("board_id", Id(), nullable=False),
        sa.Column("from_column_id", Id(), nullable=True),
        sa.Column("to_column_id", Id(), nullable=False),
        sa.Column("from_segment", sa.String(length=20), nullable=True),
        sa.Column("to_segment", sa.String(length=20), nullable=False),
        sa.Column("moved_at", Instant(), nullable=False),
        sa.Column("moved_by", Id(), nullable=True),
        sa.Column("seconds_in_from_column", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_backward", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "from_segment IS NULL OR from_segment IN ('upstream','downstream')",
            name=op.f("ck_card_movements_from_segment"),
        ),
        sa.CheckConstraint(
            "source IN ('ui','api','mcp','seed','simulation')",
            name=op.f("ck_card_movements_source"),
        ),
        sa.CheckConstraint(
            "to_segment IN ('upstream','downstream')", name=op.f("ck_card_movements_to_segment")
        ),
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_card_movements_board_id_boards"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name=op.f("fk_card_movements_card_id_cards"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_column_id"],
            ["columns.id"],
            name=op.f("fk_card_movements_from_column_id_columns"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["moved_by"],
            ["users.id"],
            name=op.f("fk_card_movements_moved_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["to_column_id"],
            ["columns.id"],
            name=op.f("fk_card_movements_to_column_id_columns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_movements")),
    )
    op.create_index(
        "idx_movements_board_time", "card_movements", ["board_id", "moved_at"], unique=False
    )
    op.create_index("idx_movements_card", "card_movements", ["card_id", "moved_at"], unique=False)
    op.create_index("idx_movements_from_column", "card_movements", ["from_column_id"], unique=False)
    op.create_index(
        "idx_movements_to_column", "card_movements", ["to_column_id", "moved_at"], unique=False
    )

    op.create_table(
        "card_tasks",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False),
        sa.Column("assignee_id", Id(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.Column("completed_at", Instant(), nullable=True),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_card_tasks_assignee_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_card_tasks_card_id_cards"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_card_tasks")),
    )
    op.create_index("idx_tasks_card", "card_tasks", ["card_id", "position"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", Id(), nullable=False),
        sa.Column("card_id", Id(), nullable=False),
        sa.Column("author_id", Id(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", Instant(), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name=op.f("fk_comments_author_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_comments_card_id_cards"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index("idx_comments_card", "comments", ["card_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_comments_card", table_name="comments")

    op.drop_table("comments")
    op.drop_index("idx_tasks_card", table_name="card_tasks")

    op.drop_table("card_tasks")
    op.drop_index("idx_movements_to_column", table_name="card_movements")
    op.drop_index("idx_movements_from_column", table_name="card_movements")
    op.drop_index("idx_movements_card", table_name="card_movements")
    op.drop_index("idx_movements_board_time", table_name="card_movements")

    op.drop_table("card_movements")
    op.drop_index("idx_events_type", table_name="card_events")
    op.drop_index("idx_events_card", table_name="card_events")
    op.drop_index("idx_events_board_time", table_name="card_events")

    op.drop_table("card_events")
    op.drop_index("idx_blockers_open", table_name="blockers")
    op.drop_index("idx_blockers_card", table_name="blockers")

    op.drop_table("blockers")
    op.drop_index("idx_cards_service_class", table_name="cards")
    op.drop_index("idx_cards_project", table_name="cards")
    op.drop_index("idx_cards_envelope", table_name="cards")
    op.drop_index("idx_cards_delivered", table_name="cards")
    op.drop_index("idx_cards_committed", table_name="cards")
    op.drop_index("idx_cards_board_column", table_name="cards")
    op.drop_index("idx_cards_board_archived", table_name="cards")
    op.drop_index("idx_cards_assignee", table_name="cards")

    op.drop_table("cards")
    op.drop_index("idx_columns_segment", table_name="columns")
    op.drop_index("idx_columns_group", table_name="columns")
    op.drop_index("idx_columns_board", table_name="columns")

    op.drop_table("columns")
    op.drop_table("workspace")
    op.drop_index("idx_runs_kind", table_name="simulation_runs")
    op.drop_index("idx_runs_board", table_name="simulation_runs")

    op.drop_table("simulation_runs")
    op.drop_index("idx_projects_status", table_name="projects")
    op.drop_index("idx_projects_product", table_name="projects")

    op.drop_table("projects")
    op.drop_index("idx_groups_board", table_name="column_groups")

    op.drop_table("column_groups")
    op.drop_index("idx_users_role", table_name="users")
    op.drop_index("idx_users_active", table_name="users")

    op.drop_table("users")
    op.drop_index("idx_plans_tier", table_name="plans")

    op.drop_table("plans")
    op.drop_index("idx_flags_category", table_name="feature_flags")

    op.drop_table("feature_flags")
    op.drop_index("idx_boards_source", table_name="boards")
    op.drop_index("idx_boards_product", table_name="boards")
    op.drop_index("idx_boards_kind", table_name="boards")

    op.drop_table("boards")
