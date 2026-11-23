"""O quadro se atualiza sozinho (23/11/2026): ``GET /boards/{id}/version``.

As telas conferem a versao a cada ~10 s e so recarregam o quadro (e o cartao aberto)
quando ela muda. A versao precisa mudar com tudo o que a tela mostra, inclusive o que nao
grava evento no cartao (apagar tarefa, editar ou apagar comentario), e ficar igual quando
nada mudou.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from test_cards_movement import _make_board, col_id, make_card, move

API = "/api/v1"


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


@pytest.fixture()
def quadro(client, master) -> Iterator[dict[str, Any]]:
    board = _make_board(client, master, template="simple_delivery", confirm_moves=False)
    yield board
    client.delete(f"{API}/boards/{board['id']}?hard=true", headers=master)


def versao(client, headers: dict[str, str], board_id: str) -> str:
    resposta = client.get(f"{API}/boards/{board_id}/version", headers=headers)
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()
    assert corpo["checked_at"]
    return corpo["version"]


def test_versao_igual_sem_mudanca(client, master, quadro) -> None:
    make_card(client, master, quadro["id"], col_id(quadro, "Backlog"))
    assert versao(client, master, quadro["id"]) == versao(client, master, quadro["id"])


def test_versao_muda_com_cada_coisa_que_a_tela_mostra(client, master, quadro) -> None:
    backlog, em_andamento = col_id(quadro, "Backlog"), col_id(quadro, "In Progress")
    vistas: list[str] = [versao(client, master, quadro["id"])]

    def mudou(descricao: str) -> None:
        nova = versao(client, master, quadro["id"])
        assert nova not in vistas, f"a versao nao mudou: {descricao}"
        vistas.append(nova)

    cartao = make_card(client, master, quadro["id"], backlog, title="Primeiro")
    mudou("cartao criado")

    r = move(client, master, cartao["id"], em_andamento)
    assert r.status_code == 200, r.text
    mudou("cartao movido")

    r = client.patch(
        f"{API}/cards/{cartao['id']}", json={"title": "Primeiro, renomeado"}, headers=master
    )
    assert r.status_code == 200, r.text
    mudou("titulo alterado")

    r = client.post(f"{API}/cards/{cartao['id']}/comments", json={"body": "Oi"}, headers=master)
    assert r.status_code == 201, r.text
    comentario = r.json()
    mudou("comentario novo")

    r = client.patch(
        f"{API}/comments/{comentario['id']}", json={"body": "Oi, editado"}, headers=master
    )
    assert r.status_code == 200, r.text
    mudou("comentario editado (nao grava evento)")

    r = client.delete(f"{API}/comments/{comentario['id']}", headers=master)
    assert r.status_code == 200, r.text
    mudou("comentario apagado (nao grava evento)")

    r = client.post(f"{API}/cards/{cartao['id']}/tasks", json={"title": "Conferir"}, headers=master)
    assert r.status_code == 201, r.text
    tarefa = r.json()
    mudou("tarefa nova")

    r = client.delete(f"{API}/tasks/{tarefa['id']}", headers=master)
    assert r.status_code == 200, r.text
    mudou("tarefa apagada (nao grava evento)")

    r = client.patch(f"{API}/columns/{em_andamento}", json={"wip_max": 7}, headers=master)
    assert r.status_code == 200, r.text
    mudou("WIP da coluna")


def test_versao_de_um_quadro_nao_muda_com_o_outro(client, master, quadro) -> None:
    outro = _make_board(client, master, template="simple_delivery")
    try:
        antes = versao(client, master, quadro["id"])
        make_card(client, master, outro["id"], col_id(outro, "Backlog"))
        assert versao(client, master, quadro["id"]) == antes
    finally:
        client.delete(f"{API}/boards/{outro['id']}?hard=true", headers=master)


def test_quem_le_o_quadro_le_a_versao(client, auth, quadro) -> None:
    assert versao(client, auth("viewer"), quadro["id"])


def test_quadro_inexistente(client, master) -> None:
    r = client.get(f"{API}/boards/nao-existe/version", headers=master)
    assert r.status_code == 404
