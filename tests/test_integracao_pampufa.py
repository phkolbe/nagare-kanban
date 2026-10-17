"""Pampufa integration (NG1-NG17): login, product isolation, providências as cards.

The Pampufa is replaced by an in-memory double: a real Ed25519 key signs the
tokens and the ``pampufa-sdk`` validator checks them against a static JWKS, the
``/auth/eu`` profiles come from a dict and the webhook endpoint records what it
receives (and can be told to fail). Webhooks are delivered by hand with
``deliver_pending`` so every assertion is deterministic.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from backend.app.pampufa import PampufaProfile, deliver_pending
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pampufa_sdk.auth.token import ValidadorDeToken, jwk_publico

from sqlhelper import Database

API = "/api/v1"
PRODUCT = "sr-adv"
OTHER = "sr-gj"
KID = "chave-de-teste"


class FakePampufa:
    """What the Nagare needs from the Pampufa, without a network."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.validator = ValidadorDeToken(
            servico="nagare", emissor="pampufa", jwks={"keys": [jwk_publico(self.key, KID)]}
        )
        self.profiles: dict[str, PampufaProfile] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.fail_webhooks = False
        #: product -> the people with ``nagare.*`` there, as the Pampufa lists them
        self.directory: dict[str, list[dict[str, Any]]] = {}

    def token(self, sub: str, papeis: dict[str, list[str]], **extra: Any) -> str:
        now = int(time.time())
        claims = {
            "sub": sub,
            "sid": None if sub.startswith("servico:") else str(uuid.uuid4()),
            "iss": "pampufa",
            "aud": ["pampufa", "nagare"],
            "iat": now,
            "exp": now + 900,
            "typ": "access",
            "papeis": papeis,
            **extra,
        }
        return jwt.encode(claims, self.key, algorithm="EdDSA", headers={"kid": KID})

    def person(self, papeis: dict[str, list[str]], *, email: str | None = None) -> str:
        sub = str(uuid.uuid4())
        address = email or f"pessoa-{sub[:8]}@grupo-sr.test"
        self.profiles[sub] = PampufaProfile(sub=sub, email=address, name=f"Pessoa {sub[:4]}")
        return self.token(sub, papeis)

    def service(self) -> str:
        return self.token("servico:pampufa", {PRODUCT: ["system"], OTHER: ["system"]})

    # -- the gateway interface -------------------------------------------------

    async def validate(self, token: str):
        return await self.validator.validar(token)

    async def profile(self, token: str) -> PampufaProfile:
        claims = jwt.decode(token, options={"verify_signature": False})
        return self.profiles[claims["sub"]]

    async def people(self, product_id: str) -> list[dict[str, Any]]:
        return list(self.directory.get(product_id, []))

    async def post_event(self, event: str, payload: dict[str, Any]) -> None:
        if self.fail_webhooks:
            import httpx

            raise httpx.ConnectError("pampufa fora do ar")
        self.events.append((event, payload))


@pytest.fixture(scope="module")
def pampufa(app, database_url: str) -> Iterator[FakePampufa]:
    """The double plugged into the session app; product data removed afterwards."""
    fake = FakePampufa()
    previous = app.state.pampufa
    app.state.pampufa = fake
    yield fake
    app.state.pampufa = previous
    _remove_products(database_url, (PRODUCT, OTHER))


def _remove_products(url: str, products: tuple[str, ...]) -> None:
    """Leave the shared session database as the seed made it."""
    from sqlhelper import connect

    db = connect(url)
    try:
        for product in products:
            db.ex("DELETE FROM product_links WHERE product_id = ?", product)
            db.ex("DELETE FROM product_link_history WHERE product_id = ?", product)
            db.ex("DELETE FROM webhook_outbox WHERE product_id = ?", product)
            db.ex("DELETE FROM boards WHERE product_id = ?", product)
            db.ex("DELETE FROM projects WHERE product_id = ?", product)
    finally:
        db.close()


def headers(token: str, product: str | None = PRODUCT) -> dict[str, str]:
    out = {"Authorization": f"Bearer {token}"}
    if product:
        out["X-Produto"] = product
    return out


def providencia(**overrides: Any) -> dict[str, Any]:
    body = {
        "providencia_id": str(uuid.uuid4()),
        "servico": "financeiro",
        "tipo": "extrato_atrasado",
        "titulo": "Extrato do Nubank atrasado",
        "motivo": "O último extrato importado tem mais de 7 dias.",
        "url": "http://127.0.0.1:8080/grupo-sr/sr-adv/financeiro/conciliacao",
        "produto_nome": "SR Adv",
        "prazo": "2026-11-30",
        "classe_servico": "fixed_date",
    }
    body.update(overrides)
    return body


def deliver(app) -> int:
    """One delivery round, on an engine of its own.

    The app's pool lives on the TestClient's event loop; asyncpg connections cannot
    be shared with ``asyncio.run``'s loop, so the round opens and closes its own
    engine — exactly what the background task does inside the app's loop.
    """
    from backend.app.db import Database as AppDatabase

    async def run() -> int:
        database = AppDatabase(app.state.database.url)
        try:
            return await deliver_pending(database.sessions, app.state.pampufa)
        finally:
            await database.dispose()

    return asyncio.run(run())


def columns_of(client, token: str, board_id: str) -> dict[str, dict[str, Any]]:
    board = client.get(f"{API}/boards/{board_id}", headers=headers(token)).json()
    return {column["name"]: column for column in board["columns"]}


# ---------------------------------------------------------------------------
# identity and product
# ---------------------------------------------------------------------------


def test_a_pampufa_token_needs_the_product_header(client, pampufa) -> None:
    token = pampufa.person({PRODUCT: ["nagare.editor"]})
    response = client.get(f"{API}/boards", headers=headers(token, product=None))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "product_required"


def test_no_nagare_role_in_the_product_is_forbidden(client, pampufa) -> None:
    token = pampufa.person({PRODUCT: ["financeiro.admin"]})
    response = client.get(f"{API}/boards", headers=headers(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "product_forbidden"


def test_first_sign_in_creates_the_user_and_maps_the_role(client, pampufa, db: Database) -> None:
    token = pampufa.person({PRODUCT: ["nagare.editor"]})
    me = client.get(f"{API}/auth/me", headers=headers(token))
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "editor"
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]
    row = db.q1("SELECT email, password_hash FROM users WHERE external_subject = ?", sub)
    assert row is not None and row["password_hash"] is None
    assert row["email"] == pampufa.profiles[sub].email


def test_same_email_links_the_local_user(client, auth, pampufa, db: Database) -> None:
    email = f"vinculo-{uuid.uuid4().hex[:6]}@grupo-sr.test"
    created = client.post(
        f"{API}/users",
        json={
            "email": email,
            "name": "Pessoa local",
            "password": "senha-local-123",
            "role": "editor",
        },
        headers=auth("master"),
    )
    assert created.status_code == 201, created.text
    token = pampufa.person({PRODUCT: ["nagare.leitor"]}, email=email)
    me = client.get(f"{API}/auth/me", headers=headers(token))
    assert me.status_code == 200, me.text
    assert me.json()["id"] == created.json()["id"], "same person, same Nagare user"
    assert me.json()["role"] == "viewer", "inside the product the Pampufa role rules"
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]
    assert db.scalar("SELECT external_subject FROM users WHERE id = ?", created.json()["id"]) == sub


def test_local_login_stays_in_nagare_unless_master(client, auth) -> None:
    editor = auth("editor")
    assert client.get(f"{API}/boards", headers=editor).status_code == 200
    denied = client.get(f"{API}/boards", headers={**editor, "X-Produto": PRODUCT})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "product_forbidden"
    master = client.get(f"{API}/boards", headers={**auth("master"), "X-Produto": PRODUCT})
    assert master.status_code == 200, master.text


def test_products_do_not_see_each_other(client, auth, pampufa) -> None:
    standalone = client.get(f"{API}/boards", headers=auth("master")).json()["items"]
    assert any(board["slug"] == "nagare-flow" for board in standalone)
    token = pampufa.person({PRODUCT: ["nagare.admin"]})
    inside = client.get(f"{API}/boards", headers=headers(token)).json()["items"]
    assert all(board["product_id"] == PRODUCT for board in inside)
    nagare_flow = next(board for board in standalone if board["slug"] == "nagare-flow")
    hidden = client.get(f"{API}/boards/{nagare_flow['id']}", headers=headers(token))
    assert hidden.status_code == 404


# ---------------------------------------------------------------------------
# providências become cards
# ---------------------------------------------------------------------------


def test_a_providencia_becomes_one_card_on_the_product_board(client, pampufa, db: Database) -> None:
    service = pampufa.service()
    person = {"sub": str(uuid.uuid4()), "email": "responsavel@grupo-sr.test", "nome": "Responsável"}
    body = providencia(responsavel=person)
    created = client.post(f"{API}/integration/providencias", json=body, headers=headers(service))
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    assert card["key"].startswith("SRADV-")
    assert card["column"] == "Entrada"
    assert {"providencia", "financeiro", "extrato_atrasado"} <= set(card["tags"])
    assert card["assignee"]["email"] == "responsavel@grupo-sr.test"
    assert card["assignee"]["sub"] == person["sub"]

    again = client.post(f"{API}/integration/providencias", json=body, headers=headers(service))
    assert again.status_code == 200, "a retry returns the same card"
    assert again.json()["card"]["id"] == card["id"]

    link = client.get(f"{API}/integration/product", headers=headers(service)).json()["link"]
    assert link["project"]["key"] == "SRADV"
    names = [
        row["name"]
        for row in db.q(
            "SELECT name FROM columns WHERE board_id = ? ORDER BY position", link["board"]["id"]
        )
    ]
    assert names == ["Entrada", "A fazer", "Fazendo", "Aguardando terceiro", "Entregue"]
    stored = db.q1("SELECT due_date, service_class FROM cards WHERE id = ?", card["id"])
    assert stored["service_class"] == "fixed_date"
    assert stored["due_date"].startswith("2026-11-30")


def test_only_the_service_identity_creates_providencia_cards(client, pampufa) -> None:
    token = pampufa.person({PRODUCT: ["nagare.admin"]})
    response = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(token)
    )
    assert response.status_code == 403


def test_delivering_the_card_concludes_the_providencia(client, app, pampufa, db: Database) -> None:
    service = pampufa.service()
    body = providencia()
    card = client.post(
        f"{API}/integration/providencias", json=body, headers=headers(service)
    ).json()["card"]
    editor = pampufa.person({PRODUCT: ["nagare.editor"]})
    columns = columns_of(client, editor, card["board_id"])
    for name in ("A fazer", "Entregue"):
        moved = client.post(
            f"{API}/cards/{card['id']}/move",
            json={"to_column_id": columns[name]["id"]},
            headers=headers(editor),
        )
        assert moved.status_code == 200, moved.text

    pending = db.q1(
        "SELECT event, payload FROM webhook_outbox WHERE delivered_at IS NULL AND product_id = ? "
        "ORDER BY created_at DESC",
        PRODUCT,
    )
    assert pending is not None and pending["event"] == "cartao.entregue"

    pampufa.events.clear()
    assert deliver(app) >= 1
    sent = [payload for event, payload in pampufa.events if event == "cartao.entregue"]
    assert any(payload["providencia_id"] == body["providencia_id"] for payload in sent)
    state = client.get(
        f"{API}/integration/providencias/{body['providencia_id']}", headers=headers(service)
    ).json()["state"]
    assert state == "delivered"


def test_cause_resolved_at_origin_comments_and_tags_but_never_moves(client, pampufa) -> None:
    service = pampufa.service()
    body = providencia()
    card = client.post(
        f"{API}/integration/providencias", json=body, headers=headers(service)
    ).json()["card"]
    resolved = client.post(
        f"{API}/integration/providencias/{body['providencia_id']}/resolved-at-origin",
        json={"em": "2026-10-16T10:00:00Z", "origem": "Financeiro"},
        headers=headers(service),
    )
    assert resolved.status_code == 200, resolved.text
    out = resolved.json()
    assert out["changed"] is True and out["state"] == "resolved_at_origin"
    assert "resolvido-na-origem" in out["card"]["tags"]
    assert out["card"]["column"] == "Entrada", "the card never moves by itself (NG11)"
    comments = client.get(f"{API}/cards/{card['id']}/comments", headers=headers(service)).json()
    assert any("Resolvido na origem em 2026-10-16" in c["body"] for c in comments["items"])

    again = client.post(
        f"{API}/integration/providencias/{body['providencia_id']}/resolved-at-origin",
        json={},
        headers=headers(service),
    )
    assert again.json()["changed"] is False


def test_archiving_the_card_discards_the_providencia(client, app, pampufa) -> None:
    service = pampufa.service()
    body = providencia()
    card = client.post(
        f"{API}/integration/providencias", json=body, headers=headers(service)
    ).json()["card"]
    admin = pampufa.person({PRODUCT: ["nagare.admin"]})
    archived = client.delete(f"{API}/cards/{card['id']}", headers=headers(admin))
    assert archived.status_code == 200, archived.text
    pampufa.events.clear()
    deliver(app)
    assert any(
        event == "cartao.arquivado" and payload["providencia_id"] == body["providencia_id"]
        for event, payload in pampufa.events
    )


def test_a_failed_webhook_is_retried_later(client, app, pampufa, db: Database) -> None:
    service = pampufa.service()
    card = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    admin = pampufa.person({PRODUCT: ["nagare.admin"]})
    client.delete(f"{API}/cards/{card['id']}", headers=headers(admin))

    pampufa.fail_webhooks = True
    try:
        assert deliver(app) == 0
    finally:
        pampufa.fail_webhooks = False
    row = db.q1(
        "SELECT id, attempts, last_error FROM webhook_outbox WHERE delivered_at IS NULL "
        "AND product_id = ? ORDER BY created_at DESC",
        PRODUCT,
    )
    assert row["attempts"] == 1 and "fora do ar" in row["last_error"]
    assert deliver(app) == 0, "not due yet: the backoff holds it"
    db.ex(
        "UPDATE webhook_outbox SET next_attempt_at = ? WHERE id = ?",
        "2020-01-01T00:00:00Z",
        row["id"],
    )
    assert deliver(app) >= 1
    assert db.scalar("SELECT delivered_at FROM webhook_outbox WHERE id = ?", row["id"]) is not None


# ---------------------------------------------------------------------------
# the flow lock (NG13) and the swap (NG14)
# ---------------------------------------------------------------------------


def test_product_board_flow_is_editable_until_the_first_card(client, pampufa) -> None:
    admin = pampufa.person({PRODUCT: ["nagare.admin"]})
    board = client.post(
        f"{API}/boards",
        json={"name": f"Fluxo {uuid.uuid4().hex[:6]}", "template": "providencias"},
        headers=headers(admin),
    )
    assert board.status_code == 201, board.text
    board_id = board.json()["id"]
    added = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Revisão", "segment": "downstream", "kind": "queue"},
        headers=headers(admin),
    )
    assert added.status_code == 201, added.text

    columns = columns_of(client, admin, board_id)
    card = client.post(
        f"{API}/cards",
        json={"board_id": board_id, "title": "Primeira demanda"},
        headers=headers(admin),
    )
    assert card.status_code == 201, card.text

    locked = client.post(
        f"{API}/boards/{board_id}/columns",
        json={"name": "Outra", "segment": "downstream", "kind": "queue"},
        headers=headers(admin),
    )
    assert locked.status_code == 409
    assert locked.json()["error"]["code"] == "flow_locked"
    rekind = client.patch(
        f"{API}/columns/{columns['Revisão']['id']}",
        json={"kind": "activity"},
        headers=headers(admin),
    )
    assert rekind.status_code == 409 and rekind.json()["error"]["code"] == "flow_locked"
    delete = client.delete(f"{API}/columns/{columns['Revisão']['id']}", headers=headers(admin))
    assert delete.status_code == 409

    policy = client.patch(
        f"{API}/columns/{columns['Fazendo']['id']}",
        json={"wip_max": 4, "name": "Em andamento", "color": "#3b7ea1"},
        headers=headers(admin),
    )
    assert policy.status_code == 200, policy.text
    assert policy.json()["wip_max"] == 4 and policy.json()["name"] == "Em andamento"
    sle = client.patch(f"{API}/boards/{board_id}", json={"sle_days": 10}, headers=headers(admin))
    assert sle.status_code == 200, sle.text


def test_standalone_boards_keep_free_structure(client, auth, board_id: str) -> None:
    master = auth("master")
    added = client.post(
        f"{API}/boards",
        json={"name": f"Livre {uuid.uuid4().hex[:6]}", "template": "simple_delivery"},
        headers=master,
    ).json()
    client.post(f"{API}/cards", json={"board_id": added["id"], "title": "Um"}, headers=master)
    column = client.post(
        f"{API}/boards/{added['id']}/columns",
        json={"name": "Extra", "segment": "downstream", "kind": "queue"},
        headers=master,
    )
    assert column.status_code == 201, "the lock is for product boards only"
    client.delete(f"{API}/boards/{added['id']}?hard=true", headers=master)


def _payload(raw: Any) -> dict[str, Any]:
    """JSONB comes back decoded on Postgres, as text on SQLite."""
    return raw if isinstance(raw, dict) else json.loads(raw)


def test_a_new_flow_is_a_new_project_and_open_cards_move(client, pampufa, db: Database) -> None:
    service = pampufa.service()
    body = providencia()
    open_card = client.post(
        f"{API}/integration/providencias", json=body, headers=headers(service)
    ).json()["card"]
    old_link = client.get(f"{API}/integration/product", headers=headers(service)).json()["link"]

    admin = pampufa.person({PRODUCT: ["nagare.admin"]})
    project = client.post(
        f"{API}/projects",
        json={"name": "Providências · SR Adv v2", "key": f"SRA{uuid.uuid4().hex[:3].upper()}"},
        headers=headers(admin),
    )
    assert project.status_code == 201, project.text
    board = client.post(
        f"{API}/boards",
        json={"name": f"Providências v2 {uuid.uuid4().hex[:4]}", "template": "providencias"},
        headers=headers(admin),
    )
    swap = client.put(
        f"{API}/integration/product",
        json={
            "project_id": project.json()["id"],
            "board_id": board.json()["id"],
            "motivo": "Fluxo com etapa de revisão",
        },
        headers=headers(admin),
    )
    assert swap.status_code == 200, swap.text
    assert swap.json()["moved_cards"] >= 1
    assert swap.json()["link"]["board"]["id"] == board.json()["id"]

    moved = client.get(
        f"{API}/integration/providencias/{body['providencia_id']}", headers=headers(service)
    ).json()["card"]
    assert moved["board_id"] == board.json()["id"], "the providência follows the new card"
    assert moved["id"] != open_card["id"]
    assert db.scalar("SELECT archived FROM cards WHERE id = ?", open_card["id"])
    assert db.scalar("SELECT read_only FROM boards WHERE id = ?", old_link["board"]["id"])
    # The Pampufa learns the new key and link; archiving the old card discards nothing.
    rows = db.q(
        "SELECT event, payload FROM webhook_outbox WHERE product_id = ? ORDER BY created_at",
        PRODUCT,
    )
    decoded = [(r["event"], _payload(r["payload"])) for r in rows]
    [(event, payload)] = [
        (e, p) for e, p in decoded if p["providencia_id"] == body["providencia_id"]
    ]
    assert event == "cartao.movido"
    assert payload["cartao_id"] == moved["id"] and payload["cartao_chave"] == moved["key"]
    assert f"/quadro/{board.json()['id']}?cartao=" in payload["cartao_url"]
    assert payload["cartao_anterior_chave"] == open_card["key"]

    old_columns = db.q(
        "SELECT id FROM columns WHERE board_id = ? ORDER BY position", old_link["board"]["id"]
    )
    refused = client.post(
        f"{API}/cards",
        json={"board_id": old_link["board"]["id"], "title": "Tarde demais"},
        headers=headers(admin),
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "board_read_only"
    assert old_columns, "the old board keeps its structure and history"

    next_one = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    assert next_one["board_id"] == board.json()["id"], "new providências go to the new flow"


def test_gestor_reads_every_product_but_does_not_write(client, pampufa) -> None:
    gestor = pampufa.person({"pampufa": ["pampufa.gestor"]})
    boards = client.get(f"{API}/boards", headers=headers(gestor, product=OTHER))
    assert boards.status_code == 200
    me = client.get(f"{API}/auth/me", headers=headers(gestor, product=OTHER)).json()
    assert me["role"] == "viewer"
    write = client.post(
        f"{API}/boards", json={"name": "Não pode", "template": "blank"}, headers=headers(gestor)
    )
    assert write.status_code == 403


def test_another_product_cannot_reach_this_products_card(client, pampufa) -> None:
    service = pampufa.service()
    card = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    other = pampufa.person({OTHER: ["nagare.admin"]})
    assert client.get(f"{API}/cards/{card['id']}", headers=headers(other, OTHER)).status_code == 404
    assert (
        client.get(
            f"{API}/integration/providencias/{card['id']}", headers=headers(service, OTHER)
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# what the React screens need (N2)
# ---------------------------------------------------------------------------


def test_people_of_the_product_come_from_the_pampufa(client, pampufa, db: Database) -> None:
    """17/10/2026: the assignee picker lists the product's nagare.* people, even
    those who never opened the Nagare; each gets a Nagare user to be assigned."""
    from backend.app.integration import forget_people

    newcomer = str(uuid.uuid4())
    pampufa.directory[PRODUCT] = [
        {
            "sub": newcomer,
            "email": "Nova@Grupo-SR.test",
            "nome": "Nova Pessoa",
            "papeis": ["nagare.editor"],
        },
    ]
    forget_people()
    editor = pampufa.person({PRODUCT: ["nagare.editor"]})
    out = client.get(f"{API}/integration/people", headers=headers(editor))
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["source"] == "pampufa"
    [person] = body["people"]
    assert (person["email"], person["sub"], person["roles"]) == (
        "nova@grupo-sr.test",
        newcomer,
        ["nagare.editor"],
    )
    assert db.scalar("SELECT role FROM users WHERE id = ?", person["id"]) == "viewer"

    service = pampufa.service()
    card = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    assigned = client.patch(
        f"{API}/cards/{card['id']}", json={"assignee_id": person["id"]}, headers=headers(editor)
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee_id"] == person["id"]

    pampufa.directory[OTHER] = []
    other = pampufa.person({OTHER: ["nagare.leitor"]})
    assert (
        client.get(f"{API}/integration/people", headers=headers(other, OTHER)).json()["people"]
        == []
    )


def test_the_standalone_nagare_lists_its_own_users(client, auth) -> None:
    out = client.get(f"{API}/integration/people", headers=auth("master")).json()
    assert out["source"] == "local"
    assert out["people"] and all(p["roles"] != ["system"] for p in out["people"])


def test_language_defaults_by_the_way_in_and_follows_the_choice(client, auth, pampufa) -> None:
    """Portuguese through the Pampufa, English on the local login; the person's choice
    wins and is kept on the user (every device)."""
    person = pampufa.person({PRODUCT: ["nagare.leitor"]})
    me = client.get(f"{API}/auth/me", headers=headers(person)).json()
    assert (me["locale"], me["locale_chosen"], me["via"], me["product_id"]) == (
        "pt-BR",
        None,
        "pampufa",
        PRODUCT,
    )
    assert client.get(f"{API}/auth/me", headers=auth("master")).json()["locale"] == "en"

    chosen = client.patch(f"{API}/auth/me", json={"locale": "en"}, headers=headers(person))
    assert chosen.status_code == 200, chosen.text
    assert client.get(f"{API}/auth/me", headers=headers(person)).json()["locale"] == "en"
    back = client.patch(f"{API}/auth/me", json={"locale": None}, headers=headers(person)).json()
    assert (back["locale"], back["locale_chosen"]) == ("pt-BR", None)
    refused = client.patch(f"{API}/auth/me", json={"locale": "fr"}, headers=headers(person))
    assert refused.status_code == 422


def test_errors_are_also_problem_json_for_the_platform(client, pampufa) -> None:
    """``@pampufa/ui`` reads title/status/codigo and renews on auth.token_expirado."""
    person = pampufa.person({PRODUCT: ["nagare.leitor"]})
    missing = client.get(f"{API}/cards/{uuid.uuid4()}", headers=headers(person))
    body = missing.json()
    assert missing.status_code == 404
    assert body["status"] == 404 and body["codigo"] == body["error"]["code"] and body["title"]

    expired = pampufa.token(
        str(uuid.uuid4()), {PRODUCT: ["nagare.leitor"]}, exp=int(time.time()) - 60
    )
    out = client.get(f"{API}/boards", headers=headers(expired))
    assert out.status_code == 401
    assert (out.json()["codigo"], out.json()["error"]["code"]) == (
        "auth.token_expirado",
        "token_expired",
    )


def test_stages_read_any_flow_the_same_way() -> None:
    from backend.app.integration import stages_of

    def col(id_: str, segment: str, kind: str) -> dict[str, Any]:
        return {"id": id_, "segment": segment, "kind": kind}

    flow = [
        col("in", "upstream", "queue"),
        col("todo", "downstream", "queue"),
        col("doing", "downstream", "activity"),
        col("wait", "downstream", "queue"),
        col("review", "downstream", "activity"),
        col("done", "downstream", "done"),
    ]
    assert stages_of(flow) == {
        "in": "entrada",
        "todo": "a_fazer",
        "doing": "fazendo",
        "wait": "aguardando",
        "review": "fazendo",
        "done": "entregue",
    }


def test_the_consolidated_board_shows_each_product_once(client, pampufa) -> None:
    """The gestor sees every linked product by stage; a product person, only theirs."""
    service = pampufa.service()
    mine = client.post(
        f"{API}/integration/providencias", json=providencia(), headers=headers(service)
    ).json()["card"]
    theirs = client.post(
        f"{API}/integration/providencias",
        json=providencia(produto_nome="SR GJ"),
        headers=headers(service, OTHER),
    ).json()["card"]

    gestor = pampufa.person({"pampufa": ["pampufa.gestor"]})
    out = client.get(f"{API}/integration/overview", headers=headers(gestor, "pampufa"))
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["stages"] == ["entrada", "a_fazer", "fazendo", "aguardando", "entregue"]
    lanes = {lane["product_id"]: lane for lane in body["products"]}
    assert {PRODUCT, OTHER} <= set(lanes)
    keys = [c["key"] for lane in body["products"] for c in lane["cards"]]
    assert len(keys) == len(set(keys)), "no card twice"
    card = next(c for c in lanes[PRODUCT]["cards"] if c["id"] == mine["id"])
    assert (card["stage"], card["column"]) == ("entrada", "Entrada")
    assert lanes[PRODUCT]["counts"]["entrada"] >= 1
    assert lanes[PRODUCT]["wip"]["fazendo"] == 3

    leitor = pampufa.person({PRODUCT: ["nagare.leitor"]})
    own = client.get(f"{API}/integration/overview", headers=headers(leitor)).json()
    assert [lane["product_id"] for lane in own["products"]] == [PRODUCT]
    assert theirs["id"] not in {c["id"] for c in own["products"][0]["cards"]}


def test_the_board_says_whether_its_flow_is_locked(client, pampufa) -> None:
    """N2: the screens offer only the edits the lock allows (NG13)."""
    admin = pampufa.person({PRODUCT: ["nagare.admin"]})
    board = client.post(
        f"{API}/boards",
        json={"name": f"Trava {uuid.uuid4().hex[:6]}", "template": "providencias"},
        headers=headers(admin),
    ).json()
    before = client.get(f"{API}/boards/{board['id']}", headers=headers(admin)).json()
    assert (before["product_board"], before["flow_locked"], before["read_only"]) == (
        True,
        False,
        False,
    )
    assert before["product_id"] == PRODUCT
    client.post(
        f"{API}/cards", json={"board_id": board["id"], "title": "Um"}, headers=headers(admin)
    )
    after = client.get(f"{API}/boards/{board['id']}", headers=headers(admin)).json()
    assert after["flow_locked"] is True


def test_platform_cards_open_in_the_tarefas_module(monkeypatch) -> None:
    from backend.app.config import settings
    from backend.app.integration import card_url

    card = {"id": "c1", "board_id": "b1", "product_id": PRODUCT}
    monkeypatch.setattr(settings, "screens_url", "http://gw/grupo-sr")
    assert card_url(card) == "http://gw/grupo-sr/sr-adv/tarefas/quadro/b1?cartao=c1"
    assert card_url({**card, "product_id": "nagare"}).endswith("/quadro/b1?cartao=c1")
    monkeypatch.setattr(settings, "screens_url", "")
    assert card_url(card).endswith("/quadro/b1?cartao=c1")


def test_the_tarefas_screens_are_served_under_the_platform_path(
    client, tmp_path, monkeypatch
) -> None:
    """N2: the gateway keeps /grupo-sr/; the Nagare answers the build and the screens."""
    from backend.app.config import settings

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(settings, "screens_dir", tmp_path)

    for path in (
        "/grupo-sr/sr-adv/tarefas",
        "/grupo-sr/sr-adv/tarefas/quadro/x",
        "/grupo-sr/pampufa/tarefas",
    ):
        page = client.get(path)
        assert page.status_code == 200 and "root" in page.text, path
    asset = client.get("/grupo-sr/_nagare/assets/app.js")
    assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
    assert client.get("/grupo-sr/_nagare/../pyproject.toml").status_code == 404
    assert client.get("/grupo-sr/sr-adv/financeiro").status_code == 404
