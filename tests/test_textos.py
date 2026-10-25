"""Texts in the caller's language (decision of 17/10/2026).

Three layers are checked here:

1. **The catalog, statically.** Every literal handed to ``_``/``N_``/``_n`` in the
   modules that write human-readable texts has a pt-BR entry with exactly the same
   ``{placeholders}``, and the catalog has no entry nobody uses.
2. **The helpers.** English formatting is byte-for-byte what the code always
   produced; pt-BR uses ``,`` decimals, ``.`` thousands and ``12 de outubro``.
3. **The API.** A local user whose ``locale`` is ``pt-BR`` reads metrics, the KMM
   ladder and Monte Carlo / replay sentences in Portuguese; reset to ``null`` the
   same endpoints answer in the original English.
"""

from __future__ import annotations

import ast
import asyncio
import string
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from backend.app import texts
from backend.app.db import Database as AppDatabase
from backend.app.texts import PT, date_label, delta_pct, num

API = "/api/v1"
ROOT = Path(__file__).resolve().parents[1] / "backend" / "app"

#: The modules whose human-readable output goes through the catalog.
TRANSLATED_MODULES: tuple[str, ...] = (
    "flow/metrics.py",
    "flow/kmm.py",
    "flow/forecast.py",
    "routers/metrics.py",
    "routers/simulator.py",
)
MARKERS = {"_": 1, "N_": 1, "_n": 2}  # function -> how many leading args are catalog keys

MC_SAMPLES = [3, 5, 0, 7, 4, 6, 2, 8, 5, 4]


# ---------------------------------------------------------------------------
# 1. the catalog, statically
# ---------------------------------------------------------------------------


def _catalog_calls() -> list[tuple[str, int, ast.expr]]:
    """``(module, line, argument)`` for every catalog-key argument in the modules."""
    found: list[tuple[str, int, ast.expr]] = []
    for name in TRANSLATED_MODULES:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            keys = MARKERS.get(node.func.id)
            if keys:
                found.extend((name, node.lineno, arg) for arg in node.args[:keys])
    return found


def _literals() -> dict[str, str]:
    """Every literal catalog key -> where it was first seen."""
    out: dict[str, str] = {}
    for name, line, arg in _catalog_calls():
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.setdefault(arg.value, f"{name}:{line}")
    return out


def _placeholders(text: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(text) if field is not None}


def test_every_translated_literal_has_a_portuguese_entry() -> None:
    missing = {text: where for text, where in _literals().items() if text not in PT}
    assert not missing, f"strings without a pt-BR translation: {missing}"


def test_portuguese_entries_keep_the_same_placeholders() -> None:
    mismatched = {
        english: (sorted(_placeholders(english)), sorted(_placeholders(portuguese)))
        for english, portuguese in PT.items()
        if _placeholders(english) != _placeholders(portuguese)
    }
    assert not mismatched, f"placeholders differ: {mismatched}"


def test_the_catalog_has_no_unused_entry() -> None:
    unused = sorted(set(PT) - set(_literals()))
    assert not unused, f"catalog entries no module uses: {unused}"


def test_no_f_string_reaches_the_catalog() -> None:
    """An f-string (or a concatenation) inside ``_()`` could never match a catalog key."""
    offenders = [
        f"{name}:{line}"
        for name, line, arg in _catalog_calls()
        if isinstance(arg, (ast.JoinedStr, ast.BinOp))
    ]
    assert not offenders, f"build the text with named placeholders instead: {offenders}"


def test_portuguese_entries_are_actually_portuguese() -> None:
    """Sentences must not be left in English by accident (identical text is fine for codes)."""
    untouched = [
        english
        for english, portuguese in PT.items()
        if english == portuguese and " " in english and not english.startswith("{")
    ]
    assert not untouched, f"pt-BR entries identical to the English: {untouched}"


# ---------------------------------------------------------------------------
# 2. the helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def pt_br() -> Iterator[None]:
    token = texts.set_locale(texts.PT_BR)
    try:
        yield
    finally:
        texts.current_locale.reset(token)


def test_english_is_the_default_and_formats_as_before() -> None:
    assert texts.locale() == "en"
    assert texts._("Throughput") == "Throughput"
    assert texts._("{n}-week rolling average", n=4) == "4-week rolling average"
    assert num(1.4567, 1) == f"{1.4567:.1f}" == "1.5"
    assert num(12345.678, 2) == "12345.68"
    assert num(-3.25, "+.1f") == f"{-3.25:+.1f}"
    assert num(0.5, "g") == "0.5"
    assert num(4.25) == "4.25"
    assert num(None) == "None"
    assert delta_pct(None) == ""
    assert delta_pct(12.34) == " (+12.3%)"
    assert date_label(date(2026, 11, 12), date(2026, 10, 17)) == "12 Nov"
    assert date_label(date(2027, 1, 3), date(2026, 10, 17)) == "3 Jan 2027"
    assert texts._n("item", "items", 1) == "item"
    assert texts._n("item", "items", 0) == "items"


def test_portuguese_numbers_dates_and_plurals(pt_br: None) -> None:
    assert texts._("Throughput") == "Vazão"
    assert texts._("{n}-week rolling average", n=4) == "Média móvel de 4 semanas"
    assert num(1.4567, 1) == "1,5"
    assert num(12345.678, 2) == "12.345,68"
    assert num(-3.25, "+.1f") == "-3,2"
    assert num(1500) == "1.500"
    assert num(None) == "None"
    assert delta_pct(12.34) == " (+12,3%)"
    assert date_label(date(2026, 11, 12), date(2026, 10, 17)) == "12 de novembro"
    assert date_label(date(2027, 3, 1), date(2026, 10, 17)) == "1º de março de 2027"
    assert texts._n("item", "items", 2) == "itens"


def test_effective_locale_follows_the_choice_then_the_login() -> None:
    assert texts.effective_locale(None, "local") == "en"
    assert texts.effective_locale(None, "pampufa") == "pt-BR"
    assert texts.effective_locale("en", "pampufa") == "en"
    assert texts.effective_locale("pt-BR", "local") == "pt-BR"
    assert texts.effective_locale("fr", "local") == "en"


def test_the_locale_is_visible_inside_run_sync(database_url: str) -> None:
    """The flow engine runs inside ``AsyncSession.run_sync``; the context must follow it."""

    async def inside() -> str:
        database = AppDatabase(database_url)
        try:
            async with database.sessions() as session:
                texts.set_locale(texts.PT_BR)
                return await session.run_sync(lambda _sync: texts.locale())
        finally:
            await database.dispose()

    assert asyncio.run(inside()) == "pt-BR"
    assert texts.locale() == "en"  # asyncio.run works on a copy of the context


# ---------------------------------------------------------------------------
# 3. the API
# ---------------------------------------------------------------------------


def _set_master_locale(client, auth, value: str | None) -> None:
    response = client.patch(f"{API}/auth/me", json={"locale": value}, headers=auth("master"))
    assert response.status_code == 200, response.text
    assert response.json()["locale_chosen"] == value


@pytest.fixture()
def portuguese(client, auth) -> Iterator[dict[str, str]]:
    """The master's own preference set to pt-BR for the test, then back to none."""
    _set_master_locale(client, auth, "pt-BR")
    try:
        yield auth("master")
    finally:
        _set_master_locale(client, auth, None)


@pytest.fixture()
def created_runs(db) -> Iterator[list[str]]:
    """Ids of simulation runs a test creates, deleted afterwards."""
    ids: list[str] = []
    yield ids
    for run_id in ids:
        db.ex("DELETE FROM simulation_runs WHERE id = ?", run_id)


def _metrics(client, headers: dict[str, str], board_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("summary", "kmm", "littles-law", "throughput"):
        response = client.get(f"{API}/boards/{board_id}/metrics/{name}", headers=headers)
        assert response.status_code == 200, response.text
        out[name] = response.json()
    return out


def test_metrics_and_kmm_speak_portuguese_then_english_again(
    client, auth, board_id: str, portuguese: dict[str, str]
) -> None:
    body = _metrics(client, portuguese, board_id)
    summary = body["summary"]["metrics"]
    assert summary["throughput"]["label"] == "Vazão"
    assert summary["flow_efficiency"]["label"] == "Eficiência de fluxo"
    assert summary["littles_law_coherence"]["label"] == "Coerência com a Lei de Little"
    assert summary["throughput"]["unit"] == "cards/week"  # units are codes, never translated

    levels = {level["level"]: level for level in body["kmm"]["levels"]}
    assert levels["ML0"]["name"] == "Alheio"
    assert levels["ML4"]["name"] == "Gerenciado quantitativamente"
    assert levels["ML1"]["metrics"][0]["label"] == "Quadro em uso"
    assert body["kmm"]["current_level_name"] == levels[body["kmm"]["current_level"]]["name"]

    littles = body["littles-law"]
    assert littles["x_label"] == "Data"
    assert littles["series"][0]["label"] == "Trabalho em andamento"
    assert littles["meta"]["verdict"].startswith(
        ("Nada foi entregue", "Trabalho comprometido", "O sistema se comporta", "O WIP observado")
    ), littles["meta"]["verdict"]
    assert body["throughput"]["series"][1]["label"] == "Média móvel de 4 semanas"

    # Back to no preference: the local login defaults to the original English.
    _set_master_locale(client, auth, None)
    english = _metrics(client, auth("master"), board_id)
    assert english["summary"]["metrics"]["throughput"]["label"] == "Throughput"
    english_levels = {level["level"]: level for level in english["kmm"]["levels"]}
    assert english_levels["ML0"]["name"] == "Oblivious"
    assert english["littles-law"]["x_label"] == "Date"
    assert english["throughput"]["series"][1]["label"] == "4-week rolling average"


def test_monte_carlo_sentences_follow_the_locale(
    client, auth, sim_board: dict[str, Any], portuguese: dict[str, str], created_runs: list[str]
) -> None:
    params = {
        "backlog_size": 30,
        "throughput_samples": MC_SAMPLES,
        "trials": 500,
        "seed": 7,
        "start_date": "2026-10-17",
    }
    payload = {"board_id": sim_board["id"], "kind": "monte_carlo_when", "params": params}
    response = client.post(f"{API}/simulator/runs", json=payload, headers=portuguese)
    assert response.status_code == 201, response.text
    created_runs.append(response.json()["id"])
    results = response.json()["results"]
    assert results["question"] == "Quanto tempo para entregar 30 itens?"
    assert results["sentence"].startswith("85% de confiança: o backlog de 30 itens")
    assert " de 2026" not in results["sentence"]  # same year as the start: no year
    assert results["verdict"].startswith("A data cara ou coroa é ")
    assert response.json()["name"].startswith("Monte Carlo: quando — ")

    _set_master_locale(client, auth, None)
    response = client.post(f"{API}/simulator/runs", json=payload, headers=auth("master"))
    assert response.status_code == 201, response.text
    created_runs.append(response.json()["id"])
    english = response.json()["results"]
    assert english["question"] == "How long to deliver 30 items?"
    assert english["sentence"].startswith("85% confidence: all 30 items delivered within ")
    assert english["verdict"].startswith("A coin-flip date is ")
    assert english["percentiles"] == results["percentiles"]  # the numbers never change


def test_replay_runs_inside_run_sync_and_still_speaks_portuguese(
    client, board_id: str, portuguese: dict[str, str], created_runs: list[str]
) -> None:
    """``replay`` computes inside ``AsyncSession.run_sync``: the locale must reach it."""
    payload = {"board_id": board_id, "kind": "replay", "params": {"seed": 3, "weeks": 8}}
    response = client.post(f"{API}/simulator/runs", json=payload, headers=portuguese)
    assert response.status_code == 201, response.text
    created_runs.append(response.json()["id"])
    results = response.json()["results"]
    assert results["rows"][0]["name"] == "Sem limites de WIP"
    assert "chegadas reais deste quadro reproduzidas em" in results["verdict"]


def test_board_toolbar_preference_is_the_persons_and_merges(client, auth) -> None:
    """18/10/2026: the board tools (bar or floating panel) follow the person."""
    headers = auth("editor")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["preferences"] == {"board_toolbar": "bar"}, "the collapsible bar by default"
    changed = client.patch(
        "/api/v1/auth/me", json={"preferences": {"board_toolbar": "panel"}}, headers=headers
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["preferences"]["board_toolbar"] == "panel"
    assert changed.json()["locale_chosen"] == me["locale_chosen"], "the language is untouched"
    language = client.patch("/api/v1/auth/me", json={"locale": "pt-BR"}, headers=headers).json()
    assert language["preferences"]["board_toolbar"] == "panel", "a language change keeps it"
    wrong = client.patch(
        "/api/v1/auth/me", json={"preferences": {"board_toolbar": "sideways"}}, headers=headers
    )
    assert wrong.status_code == 422
    back = client.patch(
        "/api/v1/auth/me",
        json={"locale": None, "preferences": {"board_toolbar": None}},
        headers=headers,
    ).json()
    assert back["preferences"] == {"board_toolbar": "bar"} and back["locale_chosen"] is None
