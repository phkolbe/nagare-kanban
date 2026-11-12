"""Tipos de cartao por produto e cartao ligado ao caso (PPLAT-312 e PPLAT-310, 12/11/2026).

- `card_types`: a lista de tipos de cada produto (a chave vai em `cards.value_area`). Produto
  sem linhas segue com a lista de software de hoje. O SR Adv e o SR GJ nascem com a lista da
  Sarah (Prazo, Peticao, Audiencia, Diligencia, Administrativo); o tipo que um cartao desses
  produtos ja usa e fica fora dela entra arquivado, para o cartao nao perder o tipo.
- `cards.value_area` perde a checagem fixa (`ck_cards_value_area`).
- `cards.case_*` e `client_*`: o caso do SR Adv a que o cartao se refere. Os cartoes de
  providencia cujo link ja e a pagina de um caso (`/grupo-sr/<produto>/processos/<id>`) sao
  ligados a ele aqui.

O Nagare nao usa seguranca por linha (o produto e filtrado na aplicacao): os passos de dados
leem todas as linhas, produto a produto pela coluna.

Revision ID: ng_0007
Revises: ng_0006
Create Date: 2026-11-12
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.app.db import new_id as _new_id
from backend.app.db import utcnow as _now
from backend.app.sqltypes import Id, Instant

revision: str = "ng_0007"
down_revision: str | None = "ng_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOFTWARE = ("feature", "defect", "risk", "debt", "compliance")
_LEGAL = (
    ("prazo", "Prazo"),
    ("peticao", "Petição"),
    ("audiencia", "Audiência"),
    ("diligencia", "Diligência"),
    ("administrativo", "Administrativo"),
)
_LEGAL_PRODUCTS = ("sr-adv", "sr-gj")
_CASE_PAGE = re.compile(
    r"/grupo-sr/(?P<product>[a-z0-9-]+)/processos/(?P<case>[0-9a-fA-F-]{36})(?:[/?#]|$)"
)

_card_types = sa.table(
    "card_types",
    sa.column("id", Id),
    sa.column("product_id", sa.String),
    sa.column("key", sa.String),
    sa.column("name", sa.String),
    sa.column("position", sa.Integer),
    sa.column("archived", sa.Boolean),
    sa.column("created_at", Instant),
)
_cards = sa.table(
    "cards",
    sa.column("id", Id),
    sa.column("product_id", sa.String),
    sa.column("value_area", sa.String),
    sa.column("case_id", sa.String),
    sa.column("case_url", sa.String),
)
_refs = sa.table(
    "external_refs",
    sa.column("card_id", Id),
    sa.column("url", sa.Text),
)


def upgrade() -> None:
    with op.batch_alter_table("cards") as batch:
        batch.drop_constraint(op.f("ck_cards_value_area"), type_="check")
        batch.add_column(sa.Column("case_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("case_label", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("case_url", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("client_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("client_name", sa.String(length=200), nullable=True))
        batch.create_index("idx_cards_case", ["product_id", "case_id"])
    op.create_table(
        "card_types",
        sa.Column("id", Id, primary_key=True),
        sa.Column("product_id", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", Instant, nullable=False),
        sa.UniqueConstraint("product_id", "key", name="uq_card_types_product_id_key"),
    )
    op.create_index("idx_card_types_product", "card_types", ["product_id", "position"])

    bind = op.get_bind()
    now = _now()
    rows: list[dict[str, object]] = []
    for product in _LEGAL_PRODUCTS:
        for position, (key, name) in enumerate(_LEGAL):
            rows.append(
                {
                    "id": _new_id(),
                    "product_id": product,
                    "key": key,
                    "name": name,
                    "position": position,
                    "archived": False,
                    "created_at": now,
                }
            )
        # The types these products' cards already have stay, archived (named by the
        # screens when they are the software ones): no card loses its type.
        in_use = bind.execute(
            sa.select(_cards.c.value_area)
            .where(_cards.c.product_id == product)
            .group_by(_cards.c.value_area)
            .order_by(_cards.c.value_area)
        ).scalars()
        legal_keys = {key for key, _ in _LEGAL}
        position = len(_LEGAL)
        for key in in_use:
            if not key or key in legal_keys:
                continue
            rows.append(
                {
                    "id": _new_id(),
                    "product_id": product,
                    "key": key,
                    "name": None,
                    "position": position,
                    "archived": True,
                    "created_at": now,
                }
            )
            position += 1
    if rows:
        op.bulk_insert(_card_types, rows)

    # Providência cards whose link is a case page are about that case.
    refs = bind.execute(
        sa.select(_refs.c.card_id, _refs.c.url, _cards.c.product_id)
        .join(_cards, _cards.c.id == _refs.c.card_id)
        .where(_refs.c.url.is_not(None), _cards.c.case_id.is_(None))
    ).all()
    for card_id, url, product in refs:
        found = _CASE_PAGE.search(str(url))
        if found is None or found["product"] != product:
            continue
        try:
            case_id = str(uuid.UUID(found["case"]))
        except ValueError:
            continue
        bind.execute(
            sa.update(_cards)
            .where(_cards.c.id == card_id)
            .values(case_id=case_id, case_url=str(url).split("?", 1)[0].split("#", 1)[0])
        )


def downgrade() -> None:
    op.drop_index("idx_card_types_product", table_name="card_types")
    op.drop_table("card_types")
    # The fixed check comes back: a card with a type of a product's own list falls back to
    # the default one (the list itself is gone with its table).
    op.execute(
        sa.update(_cards).where(_cards.c.value_area.not_in(_SOFTWARE)).values(value_area="feature")
    )
    with op.batch_alter_table("cards") as batch:
        batch.drop_index("idx_cards_case")
        batch.drop_column("client_name")
        batch.drop_column("client_id")
        batch.drop_column("case_url")
        batch.drop_column("case_label")
        batch.drop_column("case_id")
        batch.create_check_constraint(
            op.f("ck_cards_value_area"),
            "value_area IN ('feature','defect','risk','debt','compliance')",
        )
