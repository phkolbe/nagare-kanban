"""D46 (06/11/2026): fuso, plano e flags por produto; a conta segue como o padrao."""

from __future__ import annotations

from typing import Any

API = "/api/v1"


def test_cada_produto_tem_o_seu_fuso_e_as_suas_flags(client, auth) -> None:  # type: ignore[no-untyped-def]
    master = auth("master")
    adv: dict[str, Any] = {**master, "X-Produto": "sr-adv"}
    gj: dict[str, Any] = {**master, "X-Produto": "sr-gj"}
    padrao = client.get(f"{API}/workspace", headers=master).json()["timezone"]

    r = client.patch(f"{API}/workspace", json={"timezone": "America/Sao_Paulo"}, headers=adv)
    assert r.status_code == 200, r.text
    assert r.json()["timezone"] == "America/Sao_Paulo"
    assert client.get(f"{API}/workspace", headers=gj).json()["timezone"] == padrao
    assert client.get(f"{API}/workspace", headers=master).json()["timezone"] == padrao

    antes = client.get(f"{API}/flags/effective", headers=gj).json()
    chave = next(k for k, v in antes.items() if v)
    r = client.patch(f"{API}/flags/{chave}", json={"is_enabled": False}, headers=adv)
    assert r.status_code == 200 and r.json()["is_enabled"] is False
    assert client.get(f"{API}/flags/effective", headers=adv).json()[chave] is False
    assert client.get(f"{API}/flags/effective", headers=gj).json()[chave] is True
    assert client.get(f"{API}/flags/effective", headers=master).json()[chave] is True

    # Volta: o produto liga de novo e o fuso dele volta ao padrao.
    client.patch(f"{API}/flags/{chave}", json={"is_enabled": True}, headers=adv)
    client.patch(f"{API}/workspace", json={"timezone": padrao}, headers=adv)
