"""The Reports area, wave 2 (25/11/2026): issued reports, client links, the public page,
registered forecasts and REP-09 (forecast against actual).

Ready when (plan, §5): an issued report reopened later shows the same numbers; the client
opens the link without an account and sees only the link's projects; an expired or revoked
link does not open; a registered forecast appears in REP-09.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from backend.app.reports.emitidos import public_limit
from backend.app.reports.previsoes import evaluate
from openpyxl import load_workbook

from sqlhelper import connect

API = "/api/v1"


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


@pytest.fixture(autouse=True)
def _fresh_public_limit() -> Iterator[None]:
    public_limit.reset()
    yield
    public_limit.reset()


def _issue(client, headers, **body: Any) -> dict[str, Any]:
    response = client.post(f"{API}/reports/issued", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _link(client, headers, issue_id: str, **body: Any) -> Any:
    return client.post(f"{API}/reports/issued/{issue_id}/links", json=body, headers=headers)


# ---------------------------------------------------------------------------
# issuing
# ---------------------------------------------------------------------------


def test_issuing_needs_whoever_manages_boards(client, auth, board) -> None:
    body = {"template": "risks", "period": "month", "board_id": board["id"]}
    for role in ("editor", "viewer", "simulator"):
        response = client.post(f"{API}/reports/issued", json=body, headers=auth(role))
        assert response.status_code == 403, (role, response.text)
    for role in ("master", "administrator"):
        assert _issue(client, auth(role), **body)["code"] == "REP-05"


def test_an_issued_report_keeps_its_numbers_after_the_board_changes(
    client, auth, master, board
) -> None:
    params = {"period": "month", "board_id": board["id"]}
    live = client.get(f"{API}/reports/delivery_review", params=params, headers=master).json()
    issued = _issue(client, master, template="delivery_review", **params)
    assert issued["issued_by"]["name"]
    assert issued["period"]["from"] == live["period"]["from"]
    assert issued["format_version"] == 1
    frozen = issued["document"]
    # The same numbers as the live report of the same instant.
    assert frozen["sections"] == live["sections"]

    original = board["name"]
    renamed = f"{original} (renamed)"
    try:
        assert (
            client.patch(
                f"{API}/boards/{board['id']}", json={"name": renamed}, headers=master
            ).status_code
            == 200
        )
        again = client.get(f"{API}/reports/delivery_review", params=params, headers=master)
        assert again.json()["subtitle"] == renamed
        reopened = client.get(f"{API}/reports/issued/{issued['id']}", headers=master).json()
        assert reopened["document"] == frozen
        assert reopened["document"]["subtitle"] == original
    finally:
        client.patch(f"{API}/boards/{board['id']}", json={"name": original}, headers=master)

    listing = client.get(f"{API}/reports/issued", headers=auth("viewer")).json()
    assert any(item["id"] == issued["id"] for item in listing["items"])
    assert "document" not in listing["items"][0]


def test_an_issued_report_belongs_to_its_product(client, master, board) -> None:
    issued = _issue(client, master, template="risks", period="month", board_id=board["id"])
    other = client.get(
        f"{API}/reports/issued/{issued['id']}", headers={**master, "X-Produto": "sr-adv"}
    )
    assert other.status_code == 404
    assert other.json()["error"]["code"] == "report_issue_not_found"
    listing = client.get(f"{API}/reports/issued", headers={**master, "X-Produto": "sr-adv"})
    assert all(item["id"] != issued["id"] for item in listing.json()["items"])
    assert client.get(f"{API}/reports/issued/{uuid.uuid4()}", headers=master).status_code == 404


def test_an_issued_report_exports_its_frozen_copy(client, master, board) -> None:
    issued = _issue(client, master, template="waiting", period="month", board_id=board["id"])
    response = client.get(
        f"{API}/reports/issued/{issued['id']}/export", params={"format": "xlsx"}, headers=master
    )
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content))
    assert len(book.sheetnames) == len(issued["document"]["sections"])


def test_issuing_checks_the_parameters_like_the_live_report(client, master) -> None:
    bad = client.post(f"{API}/reports/issued", json={"template": "delivery_review"}, headers=master)
    assert bad.status_code == 400
    missing = client.post(
        f"{API}/reports/issued",
        json={"template": "project_status", "project_id": str(uuid.uuid4())},
        headers=master,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "project_not_found"


# ---------------------------------------------------------------------------
# client links and the public page
# ---------------------------------------------------------------------------


def test_only_project_status_and_hours_by_project_can_be_shared(
    client, master, board, projects
) -> None:
    review = _issue(
        client, master, template="delivery_review", period="month", board_id=board["id"]
    )
    refused = _link(client, master, review["id"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "report_link_not_allowed"
    assert review["shareable"] is False

    every = _issue(client, master, template="hours", period="month")
    refused = _link(client, master, every["id"])
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "report_link_needs_projects"

    ngr = projects["NGR"]["id"]
    chosen = _issue(client, master, template="hours", period="month", project_ids=[ngr])
    assert chosen["shareable"] is True
    created = _link(client, master, chosen["id"])
    assert created.status_code == 201, created.text
    assert [p["id"] for p in created.json()["projects"]] == [ngr]


def test_a_link_needs_whoever_shares(client, auth, projects) -> None:
    ngr = projects["NGR"]["id"]
    issued = _issue(
        client, auth("master"), template="project_status", period="month", project_id=ngr
    )
    for role in ("editor", "viewer"):
        assert _link(client, auth(role), issued["id"]).status_code == 403
    # Readers see the issued copy, but not its links.
    seen = client.get(f"{API}/reports/issued/{issued['id']}", headers=auth("viewer")).json()
    assert seen["links"] == []


def test_the_client_opens_the_link_without_an_account(client, master, projects, db) -> None:
    ngr = projects["NGR"]["id"]
    issued = _issue(client, master, template="project_status", period="month", project_id=ngr)
    created = _link(client, master, issued["id"]).json()
    token = created["token"]
    assert created["path"] == f"/r/{token}"
    assert created["state"] == "active"
    # 7 days by default (R19).
    expires = datetime.fromisoformat(created["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)
    # Only the hash is kept.
    row = db.q1("SELECT * FROM report_links WHERE id = ?", created["id"])
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in {str(v) for v in row.values()}

    page = client.get(f"{API}/public/reports/{token}")
    assert page.status_code == 200, page.text
    assert page.headers["x-robots-tag"].startswith("noindex")
    assert page.headers["cache-control"] == "no-store"
    body = page.json()
    assert body["document"] == issued["document"]
    assert body["document"]["scope"]["project"]["id"] == ngr
    assert body["issued_by_name"]
    assert body["expires_at"] == created["expires_at"]
    client.get(f"{API}/public/reports/{token}")

    links = client.get(f"{API}/reports/issued/{issued['id']}", headers=master).json()["links"]
    mine = next(x for x in links if x["id"] == created["id"])
    assert mine["access_count"] == 2
    assert mine["last_access_at"]
    assert "token" not in mine


def test_the_client_sees_only_the_projects_of_the_link(client, master, projects) -> None:
    ngr, pay = projects["NGR"]["id"], projects["PAY"]["id"]
    issued = _issue(client, master, template="hours", period="month", project_ids=f"{ngr},{pay}")
    token = _link(client, master, issued["id"], days=3).json()["token"]
    document = client.get(f"{API}/public/reports/{token}").json()["document"]
    shown = {p["id"] for p in document["scope"]["projects"]}
    assert shown == {ngr, pay}
    keys = {r["key"] for r in document["sections"][2]["blocks"][0]["rows"]}
    assert keys <= {"NGR", "PAY"}


def test_a_revoked_or_expired_link_does_not_open(client, master, projects, db) -> None:
    ngr = projects["NGR"]["id"]
    issued = _issue(client, master, template="project_status", period="month", project_id=ngr)

    revoked = _link(client, master, issued["id"]).json()
    gone = client.delete(
        f"{API}/reports/issued/{issued['id']}/links/{revoked['id']}", headers=master
    )
    assert gone.status_code == 200
    assert gone.json()["state"] == "revoked"
    answer = client.get(f"{API}/public/reports/{revoked['token']}")
    assert answer.status_code == 410
    assert answer.json()["error"]["code"] == "report_link_revoked"

    expired = _link(client, master, issued["id"], days=1).json()
    db.ex(
        "UPDATE report_links SET expires_at = ? WHERE id = ?",
        "2026-01-01T00:00:00Z",
        expired["id"],
    )
    answer = client.get(f"{API}/public/reports/{expired['token']}")
    assert answer.status_code == 410
    assert answer.json()["error"]["code"] == "report_link_expired"

    unknown = client.get(f"{API}/public/reports/not-a-real-token")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "report_link_not_found"

    links = client.get(f"{API}/reports/issued/{issued['id']}", headers=master).json()["links"]
    assert {x["id"]: x["state"] for x in links}[expired["id"]] == "expired"
    assert (
        client.post(
            f"{API}/reports/issued/{issued['id']}/links", json={"days": 91}, headers=master
        ).status_code
        == 422
    )


def test_the_public_page_limits_requests_per_ip(client) -> None:
    old = public_limit.limit
    public_limit.limit = 3
    try:
        codes = [client.get(f"{API}/public/reports/nothing").status_code for _ in range(4)]
    finally:
        public_limit.limit = old
    assert codes == [404, 404, 404, 429]


def test_the_client_page_is_not_indexed(client) -> None:
    page = client.get("/nagare/r/some-token")
    assert page.status_code == 200
    assert page.headers["x-robots-tag"].startswith("noindex")
    assert "x-robots-tag" not in client.get("/nagare/board").headers


# ---------------------------------------------------------------------------
# registered forecasts and REP-09
# ---------------------------------------------------------------------------


def test_a_registered_forecast_keeps_what_the_panel_showed(client, auth, board, projects) -> None:
    master = auth("master")
    panel = client.get(
        f"{API}/boards/{board['id']}/metrics/forecast",
        params={"question": "when", "backlog_size": 6},
        headers=master,
    ).json()
    body = {"board_id": board["id"], "question": "when", "backlog_size": 6}
    for role in ("editor", "viewer"):
        assert (
            client.post(f"{API}/forecasts/records", json=body, headers=auth(role)).status_code
            == 403
        )
    created = client.post(
        f"{API}/forecasts/records",
        json={**body, "project_id": projects["NGR"]["id"]},
        headers=master,
    )
    assert created.status_code == 201, created.text
    record = created.json()
    assert record["percentiles"] == panel["percentiles"]
    assert record["dates"] == panel["dates"]
    assert record["params"]["backlog_size"] == 6
    assert record["created_by"]["name"]

    listing = client.get(
        f"{API}/forecasts/records", params={"board_id": board["id"]}, headers=auth("viewer")
    ).json()
    assert listing["items"][0]["id"] == record["id"]

    missing = client.post(
        f"{API}/forecasts/records",
        json={**body, "project_id": str(uuid.uuid4())},
        headers=master,
    )
    assert missing.status_code == 404


def test_a_registered_forecast_appears_in_rep_09(client, master, board) -> None:
    created = client.post(
        f"{API}/forecasts/records",
        json={"board_id": board["id"], "question": "how_many", "days": 30},
        headers=master,
    )
    assert created.status_code == 201, created.text
    # A free interval without dates: the 30 days ending today, in the product's time zone.
    doc = client.get(
        f"{API}/reports/forecast_review",
        params={"period": "custom", "board_id": board["id"]},
        headers=master,
    ).json()
    assert doc["code"] == "REP-09"
    pace = {k["key"]: k for k in doc["sections"][0]["blocks"][0]["items"]}
    assert pace["registered"]["value"] >= 1
    rows = doc["sections"][1]["blocks"][0]["rows"]
    mine = [r for r in rows if r["question"].startswith("How many items by")]
    assert mine, rows
    assert all(r["result"] for r in mine)


# ---------------------------------------------------------------------------
# forecast evaluation, by hand
# ---------------------------------------------------------------------------

UTC_ZONE = ZoneInfo("UTC")


def _at(day: str, hour: int = 12) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00")


def _when(backlog: int) -> dict[str, Any]:
    return {
        "question": "when",
        "created_at": _at("2026-11-02"),
        "params": {"backlog_size": backlog},
        "percentiles": {"p50": 1, "p85": 2, "p95": 3},
        "dates": {"p50": "2026-11-09", "p85": "2026-11-16", "p95": "2026-11-23"},
        "target_date": None,
    }


def test_a_when_forecast_is_met_missed_or_open() -> None:
    record = _when(3)
    before = [_at("2026-11-01")]  # before the record: not counted
    fast = [*before, _at("2026-11-03"), _at("2026-11-05"), _at("2026-11-12")]
    outcome = evaluate(record, fast, _at("2026-11-20"), UTC_ZONE)
    assert outcome.status == "met"
    assert outcome.band == 85
    assert outcome.levels == {50: "missed", 85: "met", 95: "met"}
    assert outcome.realized_on == date(2026, 11, 12)

    slow = [_at("2026-11-03"), _at("2026-11-20")]
    still = evaluate(record, slow, _at("2026-11-15"), UTC_ZONE)
    assert still.status == "open"
    assert still.delivered == 1
    late = evaluate(record, slow, _at("2026-11-17"), UTC_ZONE)
    assert late.status == "missed"
    assert late.decided == datetime(2026, 11, 16, 23, 59, 59, 999999, tzinfo=UTC)
    assert late.levels[95] == "open"


def test_a_how_many_forecast_is_met_early_missed_or_open() -> None:
    record = {
        "question": "how_many",
        "created_at": _at("2026-11-02"),
        "params": {"days": 14},
        "percentiles": {"p50": 4, "p85": 2, "p95": 1},
        "dates": {},
        "target_date": "2026-11-16",
    }
    two = [_at("2026-11-03"), _at("2026-11-04")]
    early = evaluate(record, two, _at("2026-11-05"), UTC_ZONE)
    assert early.status == "met"
    assert early.decided == _at("2026-11-04")
    assert early.levels[50] == "open"

    one = [_at("2026-11-03"), _at("2026-11-18")]  # the second one comes too late
    assert evaluate(record, one, _at("2026-11-10"), UTC_ZONE).status == "open"
    missed = evaluate(record, one, _at("2026-11-20"), UTC_ZONE)
    assert missed.status == "missed"
    assert missed.delivered == 1
    assert missed.band == 95


def test_the_day_of_a_forecast_is_the_products_day() -> None:
    record = _when(1)
    # 01:00 UTC on the 17th is still the 16th in São Paulo: within the 85% date there.
    delivery = [datetime(2026, 11, 17, 1, 0, tzinfo=UTC)]
    as_of = _at("2026-11-30")
    assert evaluate(record, delivery, as_of, ZoneInfo("America/Sao_Paulo")).status == "met"
    assert evaluate(record, delivery, as_of, UTC_ZONE).status == "missed"


# ---------------------------------------------------------------------------
# the migration, up, down and up again
# ---------------------------------------------------------------------------


def test_ng_0009_goes_up_down_and_up_again() -> None:
    from backend.app.db import downgrade_sync, migrate_sync

    with tempfile.TemporaryDirectory() as folder:
        url = f"sqlite+aiosqlite:///{(Path(folder) / 'ida-e-volta.db').as_posix()}"
        migrate_sync(url, "ng_0009")
        db = connect(url)
        try:
            tables = {
                r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            db.close()
        assert {"report_issues", "report_links", "forecast_records"} <= tables
        downgrade_sync(url, "ng_0008")
        db = connect(url)
        try:
            tables = {
                r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            db.close()
        assert not {"report_issues", "report_links", "forecast_records"} & tables
        migrate_sync(url, "head")
