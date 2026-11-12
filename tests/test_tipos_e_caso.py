"""Rodada 5, R5-D (12/11/2026): o cartao a mao e o quadro dos produtos juridicos.

- PPLAT-315: cartao a mao sem projeto usa a sigla do produto (SRADV-), como a integracao.
- PPLAT-312: tipos de cartao por produto, editaveis pelo administrador; os juridicos nascem
  com a lista da Sarah e nenhum cartao perde o tipo.
- PPLAT-310: o cartao escolhe o caso do SR Adv (o cliente vem dele), mostra o link e o quadro
  filtra por caso; a providencia de um caso cai no mesmo filtro.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from backend.app.cases import case_from_url
from backend.app.config import LEGAL_CARD_TYPES, settings

from sqlhelper import Database, connect
from test_integracao_pampufa import FakePampufa, _remove_products, headers, providencia

API = "/api/v1"
LEGAL = [key for key, _ in LEGAL_CARD_TYPES]
SOFTWARE = ["feature", "defect", "risk", "debt", "compliance"]


@pytest.fixture(scope="module")
def pampufa(app, database_url: str) -> Iterator[FakePampufa]:
    fake = FakePampufa()
    previous = app.state.pampufa
    app.state.pampufa = fake
    yield fake
    app.state.pampufa = previous
    _remove_products(database_url, ("sr-adv", "sr-gj", "batoom-r5"))
    _restore_types(database_url)


def _restore_types(url: str) -> None:
    """The card types as ng_0007 left them (the tests edit them)."""
    from backend.app.db import new_id, utcnow

    db = connect(url)
    try:
        db.ex("DELETE FROM card_types WHERE product_id IN ('sr-adv', 'sr-gj', 'batoom-r5')")
        for product in ("sr-adv", "sr-gj"):
            for position, (key, name) in enumerate(LEGAL_CARD_TYPES):
                db.ex(
                    "INSERT INTO card_types (id, product_id, key, name, position, archived, "
                    "created_at) VALUES (?, ?, ?, ?, ?, false, ?)",
                    new_id(),
                    product,
                    key,
                    name,
                    position,
                    utcnow(),
                )
    finally:
        db.close()


def _product_board(client, token: str, product: str, nome: str) -> dict[str, Any]:
    created = client.post(
        f"{API}/integration/product/board",
        json={"produto_nome": nome},
        headers=headers(token, product),
    )
    assert created.status_code in (200, 201), created.text
    link = created.json()["link"]
    board = client.get(f"{API}/boards/{link['board_id']}", headers=headers(token, product))
    return board.json()


def _card(client, token: str, product: str, board: dict[str, Any], **extra: Any):
    return client.post(
        f"{API}/cards",
        json={
            "board_id": board["id"],
            "column_id": board["columns"][0]["id"],
            "title": "Cartão à mão",
            **extra,
        },
        headers=headers(token, product),
    )


# ---------------------------------------------------------------------------
# PPLAT-315: a sigla do produto
# ---------------------------------------------------------------------------


def test_cartao_a_mao_sem_projeto_usa_a_sigla_do_produto(client, pampufa, db: Database) -> None:
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    board = _product_board(client, editor, "sr-adv", "SR Adv")
    # The board was named "Providências" on the hml: its three letters were the old prefix.
    db.ex(
        "UPDATE boards SET name = 'Providências', slug = 'providencias' WHERE id = ?", board["id"]
    )
    card = _card(client, editor, "sr-adv", board)
    assert card.status_code == 201, card.text
    assert card.json()["key"].startswith("SRADV-")
    assert card.json()["project_id"] is None

    service = pampufa.service()
    sistema = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    # One counter for both: the next number after the hand-made card.
    assert int(sistema["key"].split("-")[1]) == int(card.json()["key"].split("-")[1]) + 1


def test_cartao_a_mao_sem_vinculo_usa_as_letras_do_produto(client, auth) -> None:
    master = auth("master")
    product = {**master, "X-Produto": "batoom-r5"}
    board = client.post(f"{API}/boards", json={"name": "Loja"}, headers=product)
    assert board.status_code == 201, board.text
    card = client.post(
        f"{API}/cards",
        json={"board_id": board.json()["id"], "title": "Sem projeto"},
        headers=product,
    )
    assert card.status_code == 201, card.text
    assert card.json()["key"].startswith("BATOOMR5-")


def test_o_nagare_sozinho_segue_com_as_letras_do_quadro(client, auth, board_id, columns) -> None:
    card = client.post(
        f"{API}/cards",
        json={"board_id": board_id, "column_id": columns[1]["id"], "title": "Sem projeto"},
        headers=auth("editor"),
    )
    assert card.status_code == 201, card.text
    assert card.json()["key"].startswith("NAG-")


# ---------------------------------------------------------------------------
# PPLAT-312: tipos por produto
# ---------------------------------------------------------------------------


def test_o_nagare_sozinho_segue_com_os_tipos_de_software(client, auth) -> None:
    listed = client.get(f"{API}/card-types", headers=auth("viewer"))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [t["key"] for t in body["items"]] == SOFTWARE
    assert all(t["builtin"] and t["name"] is None for t in body["items"])
    assert body["can_edit"] is False
    assert client.get(f"{API}/card-types", headers=auth("administrator")).json()["can_edit"]


def test_os_produtos_juridicos_nascem_com_a_lista_da_sarah(client, pampufa) -> None:
    for product in ("sr-adv", "sr-gj"):
        leitor = pampufa.person({product: ["nagare.leitor"]})
        items = client.get(f"{API}/card-types", headers=headers(leitor, product)).json()["items"]
        assert [(t["key"], t["name"]) for t in items] == list(LEGAL_CARD_TYPES)


def test_cartao_juridico_escolhe_o_tipo_e_nasce_com_o_primeiro(client, pampufa) -> None:
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    board = _product_board(client, editor, "sr-adv", "SR Adv")
    plain = _card(client, editor, "sr-adv", board)
    assert plain.json()["value_area"] == "prazo"
    peticao = _card(client, editor, "sr-adv", board, value_area="peticao")
    assert peticao.status_code == 201, peticao.text
    assert peticao.json()["value_area"] == "peticao"

    software = _card(client, editor, "sr-adv", board, value_area="feature")
    assert software.status_code == 400
    assert software.json()["error"]["code"] == "invalid_card_type"

    changed = client.patch(
        f"{API}/cards/{plain.json()['id']}",
        json={"value_area": "audiencia"},
        headers=headers(editor, "sr-adv"),
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["value_area"] == "audiencia"
    filtered = client.get(
        f"{API}/cards?board_id={board['id']}&value_area=audiencia",
        headers=headers(editor, "sr-adv"),
    )
    assert [c["id"] for c in filtered.json()["items"]] == [plain.json()["id"]]


def test_a_providencia_juridica_e_administrativa(client, pampufa) -> None:
    service = pampufa.service()
    card = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    full = client.get(f"{API}/cards/{card['id']}", headers=headers(editor, "sr-adv")).json()
    assert full["value_area"] == "administrativo"


def test_so_o_administrador_edita_a_lista(client, pampufa) -> None:
    editor = pampufa.person({"sr-gj": ["nagare.editor"]})
    refused = client.post(
        f"{API}/card-types", json={"name": "Reunião"}, headers=headers(editor, "sr-gj")
    )
    assert refused.status_code == 403

    admin = pampufa.person({"sr-gj": ["nagare.admin"]})
    added = client.post(
        f"{API}/card-types", json={"name": "  Reunião  "}, headers=headers(admin, "sr-gj")
    )
    assert added.status_code == 201, added.text
    assert added.json()["items"][-1] == {
        "key": "reuniao",
        "name": "Reunião",
        "position": 5,
        "archived": False,
        "builtin": False,
    }
    again = client.post(
        f"{API}/card-types", json={"name": "reunião"}, headers=headers(admin, "sr-gj")
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "card_type_name_taken"

    renamed = client.patch(
        f"{API}/card-types/reuniao",
        json={"name": "Reunião com cliente"},
        headers=headers(admin, "sr-gj"),
    )
    assert renamed.json()["items"][-1]["name"] == "Reunião com cliente"
    assert renamed.json()["items"][-1]["key"] == "reuniao", "the key never changes"

    order = ["reuniao", *LEGAL]
    reordered = client.post(
        f"{API}/card-types/order", json={"keys": order}, headers=headers(admin, "sr-gj")
    )
    assert [t["key"] for t in reordered.json()["items"]] == order
    wrong = client.post(
        f"{API}/card-types/order", json={"keys": LEGAL}, headers=headers(admin, "sr-gj")
    )
    assert wrong.status_code == 400

    removed = client.delete(f"{API}/card-types/reuniao", headers=headers(admin, "sr-gj"))
    assert removed.status_code == 200, removed.text
    assert [t["key"] for t in removed.json()["items"]] == LEGAL


def test_tipo_em_uso_e_arquivado_e_o_cartao_nao_perde(client, pampufa) -> None:
    admin = pampufa.person({"sr-gj": ["nagare.admin"]})
    board = _product_board(client, admin, "sr-gj", "SR GJ")
    card = _card(client, admin, "sr-gj", board, value_area="diligencia").json()

    in_use = client.delete(f"{API}/card-types/diligencia", headers=headers(admin, "sr-gj"))
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "card_type_in_use"

    archived = client.patch(
        f"{API}/card-types/diligencia", json={"archived": True}, headers=headers(admin, "sr-gj")
    )
    assert archived.status_code == 200
    assert {t["key"]: t["archived"] for t in archived.json()["items"]}["diligencia"] is True

    # The card keeps it and still edits its other fields; a new card cannot take it.
    kept = client.patch(
        f"{API}/cards/{card['id']}",
        json={"title": "Diligência no fórum", "value_area": "diligencia"},
        headers=headers(admin, "sr-gj"),
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["value_area"] == "diligencia"
    novo = _card(client, admin, "sr-gj", board, value_area="diligencia")
    assert novo.status_code == 400

    back = client.patch(
        f"{API}/card-types/diligencia", json={"archived": False}, headers=headers(admin, "sr-gj")
    )
    assert {t["key"]: t["archived"] for t in back.json()["items"]}["diligencia"] is False


def test_a_lista_nunca_fica_sem_tipo_ativo(client, auth) -> None:
    master = {**auth("master"), "X-Produto": "batoom-r5"}
    items = client.get(f"{API}/card-types", headers=master).json()["items"]
    assert [t["key"] for t in items] == SOFTWARE
    for key in SOFTWARE[1:]:
        archived = client.patch(f"{API}/card-types/{key}", json={"archived": True}, headers=master)
        assert archived.status_code == 200, archived.text
    last = client.patch(f"{API}/card-types/feature", json={"archived": True}, headers=master)
    assert last.status_code == 409
    assert last.json()["error"]["code"] == "card_type_last_active"
    # The copy of the default list became the product's own: the labels stay the screens'.
    items = client.get(f"{API}/card-types", headers=master).json()["items"]
    assert all(not t["builtin"] and t["name"] is None for t in items)


def test_a_migracao_arquiva_o_tipo_que_um_cartao_juridico_ja_tem() -> None:
    """ng_0007 on a copy: a SR Adv card with a software type keeps it, archived."""
    import tempfile
    from pathlib import Path

    from backend.app.db import migrate_sync

    with tempfile.TemporaryDirectory() as folder:
        url = f"sqlite+aiosqlite:///{(Path(folder) / 'copia.db').as_posix()}"
        migrate_sync(url, "ng_0006")
        db = connect(url)
        try:
            board, column, card = (str(uuid.uuid4()) for _ in range(3))
            now = "2026-11-12T10:00:00.000Z"
            db.ex(
                "INSERT INTO boards (id, product_id, name, slug, kind, count_envelope_in_metrics,"
                " confirm_moves, created_at, archived, read_only) VALUES (?, 'sr-adv', 'P', 'p',"
                " 'operational', 0, 1, ?, 0, 0)",
                board,
                now,
            )
            db.ex(
                "INSERT INTO columns (id, board_id, name, position, segment, kind,"
                " is_commitment_point, is_delivery_point) VALUES (?, ?, 'E', 0, 'downstream',"
                " 'queue', 0, 0)",
                column,
                board,
            )
            db.ex(
                "INSERT INTO cards (id, product_id, board_id, column_id, key, title, service_class,"
                " value_area, demand_type, position, envelope_state, is_blocked, tags,"
                " custom_fields, created_at, archived) VALUES (?, 'sr-adv', ?, ?, 'SRADV-101',"
                " 'T', 'standard', 'feature', 'value', 0, 'opened', 0, '[]', '{}', ?, 0)",
                card,
                board,
                column,
                now,
            )
            caso = str(uuid.uuid4())
            db.ex(
                "INSERT INTO external_refs (id, card_id, product_id, source, external_id, url,"
                " state, created_at) VALUES (?, ?, 'sr-adv', 'pampufa', 'p1', ?, 'open', ?)",
                str(uuid.uuid4()),
                card,
                f"https://sr03:8443/grupo-sr/sr-adv/processos/{caso}",
                now,
            )
            db.ex(
                "INSERT INTO card_movements (id, card_id, board_id, to_column_id, to_segment,"
                " moved_at, is_backward, source) VALUES (?, ?, ?, ?, 'downstream', ?, 0, 'api')",
                str(uuid.uuid4()),
                card,
                board,
                column,
                now,
            )
        finally:
            db.close()

        migrate_sync(url, "head")
        db = connect(url)
        try:
            types = db.q(
                "SELECT key, archived FROM card_types WHERE product_id = 'sr-adv' ORDER BY position"
            )
            assert [(t["key"], bool(t["archived"])) for t in types] == [
                *((key, False) for key in LEGAL),
                ("feature", True),
            ]
            row = db.q1("SELECT value_area, case_id FROM cards WHERE id = ?", card)
            assert row["value_area"] == "feature"
            assert row["case_id"] == caso
            # Rebuilding `cards` on SQLite must not cascade into the tables pointing at it.
            assert db.scalar("SELECT count(*) FROM external_refs") == 1
            assert db.scalar("SELECT count(*) FROM card_movements") == 1
        finally:
            db.close()


# ---------------------------------------------------------------------------
# PPLAT-310: o caso do SR Adv
# ---------------------------------------------------------------------------


def test_cartao_ligado_ao_caso_mostra_o_link_e_o_cliente(client, pampufa, db: Database) -> None:
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    board = _product_board(client, editor, "sr-adv", "SR Adv")
    caso, cliente = str(uuid.uuid4()), str(uuid.uuid4())
    created = _card(
        client,
        editor,
        "sr-adv",
        board,
        case_id=caso,
        case_label="Caso 07 · Revisional · Maria Silva",
        client_id=cliente,
        client_name="Maria Silva",
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["case_id"] == caso
    assert card["client_name"] == "Maria Silva"
    base = settings.screens_url.rstrip("/") if settings.screens_url else "/grupo-sr"
    assert card["case_url"] == f"{base}/sr-adv/processos/{caso}"

    outro = _card(client, editor, "sr-adv", board).json()
    view = client.get(
        f"{API}/boards/{board['id']}/view?case_id={caso}", headers=headers(editor, "sr-adv")
    )
    assert view.status_code == 200, view.text
    assert [c["id"] for c in view.json()["cards"]] == [card["id"]]
    assert view.json()["cards"][0]["case_label"] == "Caso 07 · Revisional · Maria Silva"
    listed = client.get(f"{API}/cards?case_id={caso}", headers=headers(editor, "sr-adv"))
    assert [c["id"] for c in listed.json()["items"]] == [card["id"]]

    # Linking another card later: one "case" event, and the filter finds it too.
    linked = client.patch(
        f"{API}/cards/{outro['id']}",
        json={
            "case_id": caso,
            "case_label": "Caso 07",
            "client_id": cliente,
            "client_name": "Maria",
        },
        headers=headers(editor, "sr-adv"),
    )
    assert linked.status_code == 200, linked.text
    assert linked.json()["changed_fields"] == ["case"]
    event = db.q1(
        "SELECT old_value, new_value FROM card_events WHERE card_id = ? AND field = 'case'",
        outro["id"],
    )
    assert event == {"old_value": None, "new_value": "Caso 07"}
    view = client.get(
        f"{API}/boards/{board['id']}/view?case_id={caso}", headers=headers(editor, "sr-adv")
    )
    assert {c["id"] for c in view.json()["cards"]} == {card["id"], outro["id"]}

    cleared = client.patch(
        f"{API}/cards/{outro['id']}", json={"case_id": None}, headers=headers(editor, "sr-adv")
    )
    assert cleared.status_code == 200
    assert {k: cleared.json()[k] for k in ("case_id", "case_url", "client_name")} == {
        "case_id": None,
        "case_url": None,
        "client_name": None,
    }


def test_caso_invalido_e_nagare_sozinho_recusam(client, pampufa, auth, board_id, columns) -> None:
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    board = _product_board(client, editor, "sr-adv", "SR Adv")
    bad = _card(client, editor, "sr-adv", board, case_id="../../x")
    assert bad.status_code == 422
    alone = client.post(
        f"{API}/cards",
        json={
            "board_id": board_id,
            "column_id": columns[1]["id"],
            "title": "Sem caso",
            "case_id": str(uuid.uuid4()),
        },
        headers=auth("editor"),
    )
    assert alone.status_code == 400
    assert alone.json()["error"]["code"] == "case_link_unavailable"


def test_providencia_de_um_caso_cai_no_filtro_do_caso(client, pampufa) -> None:
    service = pampufa.service()
    caso, outro = str(uuid.uuid4()), str(uuid.uuid4())
    by_url = client.post(
        f"{API}/integration/providencias",
        json=providencia(
            servico="sr-adv",
            tipo="caso_sem_cobranca_definida",
            url=f"http://127.0.0.1:8080/grupo-sr/sr-adv/processos/{caso}",
        ),
        headers=headers(service),
    ).json()["card"]
    by_entity = client.post(
        f"{API}/integration/providencias",
        json=providencia(servico="sr-adv", entidade="sa_caso", entidade_id=caso, url=None),
        headers=headers(service),
    ).json()["card"]
    other_product = client.post(
        f"{API}/integration/providencias",
        json=providencia(url=f"http://127.0.0.1:8080/grupo-sr/sr-gj/processos/{outro}"),
        headers=headers(service),
    ).json()["card"]
    editor = pampufa.person({"sr-adv": ["nagare.editor"]})
    listed = client.get(f"{API}/cards?case_id={caso}", headers=headers(editor, "sr-adv"))
    assert {c["id"] for c in listed.json()["items"]} == {by_url["id"], by_entity["id"]}
    full = client.get(f"{API}/cards/{other_product['id']}", headers=headers(editor, "sr-adv"))
    assert full.json()["case_id"] is None, "a case page of another product is not this case"


@pytest.mark.parametrize(
    ("url", "esperado"),
    [
        ("https://sr03:8443/grupo-sr/sr-adv/processos/{c}", True),
        ("https://sr03:8443/grupo-sr/sr-adv/processos/{c}?aba=valores", True),
        ("/grupo-sr/sr-adv/processos/{c}/", True),
        ("https://sr03:8443/grupo-sr/sr-adv/processos?filtro=sem_contrato", False),
        ("https://sr03:8443/grupo-sr/sr-gj/processos/{c}", False),
        ("https://sr03:8443/grupo-sr/sr-adv/projetos/{c}", False),
        ("https://sr03:8443/grupo-sr/sr-adv/processos/{c}x", False),
        (None, False),
    ],
)
def test_link_de_pagina_de_caso(url: str | None, esperado: bool) -> None:
    caso = str(uuid.uuid4())
    found = case_from_url(url.format(c=caso) if url else None, "sr-adv")
    assert found == (caso if esperado else None)
