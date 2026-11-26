"""Area de Reports, onda 2 (25/11/2026): emitidos, links de cliente e previsoes registradas.

- `report_issues`: o relatorio emitido, com o Documento inteiro congelado (nao a consulta),
  os parametros, o periodo, quem emitiu, quando, a versao do formato e a lingua dos textos.
- `report_links`: o link de cliente de um emitido (decisoes R5, R18 e R19). Guarda so o hash
  SHA-256 do token (o token aparece uma vez, ao criar), os projetos a que o link se amarra, a
  validade, a revogacao, a contagem de acessos e o ultimo acesso.
- `forecast_records`: as previsoes Monte Carlo registradas pelo botao "Registrar previsao" das
  Metricas (R15), conferidas depois pelo REP-09 (previsto x realizado).

Tabelas novas, sem mexer nas que existem (no SQLite nada e recriado).

Revision ID: ng_0009
Revises: ng_0008
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.sqltypes import Day, Id, Instant, Json

revision: str = "ng_0009"
down_revision: str | None = "ng_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_issues",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False, server_default="nagare"),
        sa.Column("template", sa.String(length=40), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("subtitle", sa.String(length=300), nullable=True),
        sa.Column("params", Json, nullable=False),
        sa.Column("period_kind", sa.String(length=10), nullable=False),
        sa.Column("period_from", Day, nullable=False),
        sa.Column("period_to", Day, nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=10), nullable=False),
        sa.Column("document", Json, nullable=False),
        sa.Column("issued_by", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("issued_at", Instant, nullable=False),
        sa.CheckConstraint(
            "period_kind IN ('week','month','custom')", name="ck_report_issues_period_kind"
        ),
    )
    op.create_index("idx_report_issues_product_time", "report_issues", ["product_id", "issued_at"])
    op.create_table(
        "report_links",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False, server_default="nagare"),
        sa.Column(
            "issue_id",
            Id,
            sa.ForeignKey("report_issues.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("project_ids", Json, nullable=False),
        sa.Column("expires_at", Instant, nullable=False),
        sa.Column("created_by", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", Instant, nullable=False),
        sa.Column("revoked_at", Instant, nullable=True),
        sa.Column("revoked_by", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_access_at", Instant, nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_report_links_token_hash"),
    )
    op.create_index("idx_report_links_issue", "report_links", ["issue_id"])
    op.create_table(
        "forecast_records",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False, server_default="nagare"),
        sa.Column("board_id", Id, sa.ForeignKey("boards.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "project_id", Id, sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("question", sa.String(length=10), nullable=False),
        sa.Column("params", Json, nullable=False),
        sa.Column("percentiles", Json, nullable=False),
        sa.Column("dates", Json, nullable=False),
        sa.Column("start_date", Day, nullable=False),
        sa.Column("target_date", Day, nullable=True),
        sa.Column("low_confidence", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", Id, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", Instant, nullable=False),
        sa.CheckConstraint("question IN ('when','how_many')", name="ck_forecast_records_question"),
    )
    op.create_index(
        "idx_forecast_records_board_time", "forecast_records", ["board_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_forecast_records_board_time", table_name="forecast_records")
    op.drop_table("forecast_records")
    op.drop_index("idx_report_links_issue", table_name="report_links")
    op.drop_table("report_links")
    op.drop_index("idx_report_issues_product_time", table_name="report_issues")
    op.drop_table("report_issues")
