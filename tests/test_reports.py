"""The Reports area, wave 1 (24/11/2026): catalogue, the seven templates, the spreadsheet,
the WIP limit history read by REP-13 and the standalone bell.

The seeded "Nagare Flow" board is realistic, not exact, so the numbers are checked against
the Metrics endpoints for the same period: a report must say what the Metrics screen says.
"""

from __future__ import annotations

import io
import uuid
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from backend.app.reports.documento import resolve_period
from openpyxl import load_workbook

API = "/api/v1"
TEMPLATES = (
    "delivery_review",
    "project_status",
    "hours",
    "portfolio",
    "risks",
    "waiting",
    "policy_exceptions",
)


@pytest.fixture()
def master(auth) -> dict[str, str]:
    return auth("master")


def _params(template: str, board_id: str, project_id: str) -> dict[str, Any]:
    if template == "project_status":
        return {"period": "month", "project_id": project_id}
    if template in ("hours", "portfolio"):
        return {"period": "month"}
    return {"period": "month", "board_id": board_id}


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------


def test_periods_are_monday_weeks_calendar_months_and_free_intervals() -> None:
    utc = ZoneInfo("UTC")
    today = date(2026, 11, 25)  # a Wednesday
    week = resolve_period("week", today=today, zone=utc)
    assert (week.start, week.end) == (date(2026, 11, 16), date(2026, 11, 22))
    assert (week.previous_start, week.previous_end) == (date(2026, 11, 9), date(2026, 11, 15))
    assert resolve_period("week", today=today, zone=utc, ref=today).start == date(2026, 11, 23)

    month = resolve_period("month", today=today, zone=utc)
    assert (month.start, month.end) == (date(2026, 10, 1), date(2026, 10, 31))
    assert (month.previous_start, month.previous_end) == (date(2026, 9, 1), date(2026, 9, 30))

    free = resolve_period(
        "custom", today=today, zone=utc, frm=date(2026, 11, 1), to=date(2026, 11, 10)
    )
    assert (free.previous_start, free.previous_end) == (date(2026, 10, 22), date(2026, 10, 31))
    with pytest.raises(ValueError):
        resolve_period("custom", today=today, zone=utc, frm=today - timedelta(days=500), to=today)


def test_a_period_reads_whole_local_days() -> None:
    period = resolve_period(
        "custom",
        today=date(2026, 11, 25),
        zone=ZoneInfo("America/Sao_Paulo"),
        frm=date(2026, 11, 2),
        to=date(2026, 11, 2),
    )
    start, end = period.window
    assert start.isoformat().startswith("2026-11-02T03:00:00")
    assert end.isoformat().startswith("2026-11-03T02:59:59")


# ---------------------------------------------------------------------------
# the catalogue and the templates
# ---------------------------------------------------------------------------


def test_every_reader_of_metrics_sees_the_catalogue(client, auth) -> None:
    for role in ("master", "administrator", "editor", "viewer", "simulator"):
        response = client.get(f"{API}/reports/templates", headers=auth(role))
        assert response.status_code == 200, (role, response.text)
        items = response.json()["items"]
        assert [i["id"] for i in items] == list(TEMPLATES)
        assert {i["code"] for i in items} == {
            "REP-01",
            "REP-02",
            "REP-03",
            "REP-04",
            "REP-05",
            "REP-11",
            "REP-13",
        }


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_template_builds_a_document(client, master, board, projects, template) -> None:
    params = _params(template, board["id"], projects["NGR"]["id"])
    response = client.get(f"{API}/reports/{template}", params=params, headers=master)
    assert response.status_code == 200, response.text
    doc = response.json()
    assert doc["template"] == template
    assert doc["format_version"] == 1
    assert doc["period"]["kind"] == "month"
    assert doc["previous"]["to"] < doc["period"]["from"]
    assert doc["sections"], "a report has at least one section"
    for sec in doc["sections"]:
        assert sec["title"]
        for block in sec["blocks"]:
            assert block["type"] in {"kpis", "table", "bars", "note"}
            if block["type"] == "table":
                keys = {c["key"] for c in block["columns"]}
                for row in block["rows"]:
                    assert keys <= set(row), (template, sec["id"], keys - set(row))


def test_the_delivery_review_says_what_the_metrics_say(client, master, board) -> None:
    doc = client.get(
        f"{API}/reports/delivery_review",
        params={"period": "month", "board_id": board["id"]},
        headers=master,
    ).json()
    summary = client.get(
        f"{API}/boards/{board['id']}/metrics/summary",
        params={"from": doc["period"]["from"], "to": doc["period"]["to"]},
        headers=master,
    ).json()
    pace = {k["key"]: k for k in doc["sections"][0]["blocks"][0]["items"]}
    assert pace["delivered"]["value"] == summary["metrics"]["delivered"]["value"]
    assert pace["throughput"]["value"] == summary["metrics"]["throughput"]["value"]
    delivered_rows = doc["sections"][0]["blocks"][2]["rows"]
    assert len(delivered_rows) == summary["metrics"]["delivered"]["value"]


def test_reports_need_a_scope_and_a_known_period(client, master, board) -> None:
    assert client.get(f"{API}/reports/delivery_review", headers=master).status_code == 400
    assert client.get(f"{API}/reports/project_status", headers=master).status_code == 400
    bad = client.get(
        f"{API}/reports/delivery_review",
        params={"period": "year", "board_id": board["id"]},
        headers=master,
    )
    assert bad.status_code == 400
    assert bad.json()["codigo"] == "report_bad_period" or "report_bad_period" in bad.text
    assert client.get(f"{API}/reports/nothing", headers=master).status_code == 400


def test_an_unknown_project_is_a_404_like_the_rest_of_the_api(client, master) -> None:
    # RM-13 / NGK-117 (25/11/2026): os Reports respondiam 400 project_not_found.
    missing = str(uuid.uuid4())
    for template, params in (
        ("project_status", {"period": "month", "project_id": missing}),
        ("hours", {"period": "month", "project_ids": missing}),
    ):
        response = client.get(f"{API}/reports/{template}", params=params, headers=master)
        assert response.status_code == 404, (template, response.text)
        assert response.json()["error"]["code"] == "project_not_found"


def test_hours_can_be_limited_to_chosen_projects(client, master, projects) -> None:
    ngr = projects["NGR"]["id"]
    doc = client.get(
        f"{API}/reports/hours", params={"period": "month", "project_ids": ngr}, headers=master
    ).json()
    project_rows = doc["sections"][2]["blocks"][0]["rows"]
    assert {r["key"] for r in project_rows} <= {"NGR"}
    assert doc["scope"]["projects"] == [{"id": ngr, "name": projects["NGR"]["name"]}]


# ---------------------------------------------------------------------------
# the spreadsheet
# ---------------------------------------------------------------------------


def test_the_spreadsheet_has_one_sheet_per_section(client, master, board) -> None:
    params = {"period": "month", "board_id": board["id"], "format": "xlsx"}
    response = client.get(f"{API}/reports/delivery_review/export", params=params, headers=master)
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.xlsx"')
    book = load_workbook(io.BytesIO(response.content))
    doc = client.get(
        f"{API}/reports/delivery_review",
        params={"period": "month", "board_id": board["id"]},
        headers=master,
    ).json()
    assert len(book.sheetnames) == len(doc["sections"])


def test_the_csv_opens_with_accents_in_excel(client, master, board) -> None:
    params = {"period": "month", "board_id": board["id"], "format": "csv"}
    response = client.get(f"{API}/reports/risks/export", params=params, headers=master)
    assert response.status_code == 200, response.text
    assert response.content.startswith("﻿".encode())
    assert response.headers["content-type"].startswith("text/csv")


# ---------------------------------------------------------------------------
# REP-13: WIP limit changes are kept
# ---------------------------------------------------------------------------


def test_a_wip_limit_change_is_kept_and_reported(client, master, board, columns, db) -> None:
    column = next(c for c in columns if c["kind"] in ("queue", "activity"))
    before = column["wip_max"]
    changed = (before or 0) + 7
    try:
        assert (
            client.patch(
                f"{API}/columns/{column['id']}", json={"wip_max": changed}, headers=master
            ).status_code
            == 200
        )
        # The same value again is not a change.
        client.patch(f"{API}/columns/{column['id']}", json={"wip_max": changed}, headers=master)
    finally:
        client.patch(f"{API}/columns/{column['id']}", json={"wip_max": before}, headers=master)
    rows = db.q(
        "SELECT * FROM wip_limit_changes WHERE target_id = ? ORDER BY changed_at", column["id"]
    )
    assert [(r["old_value"], r["new_value"]) for r in rows][-2:] == [
        (before, changed),
        (changed, before),
    ]
    assert len([r for r in rows if r["new_value"] == changed]) == 1

    doc = client.get(
        f"{API}/reports/policy_exceptions",
        params={"period": "week", "date": date.today().isoformat(), "board_id": board["id"]},
        headers=master,
    ).json()
    changes = next(s for s in doc["sections"] if s["id"] == "wip_changes")["blocks"][0]["rows"]
    assert any(column["name"] in r["where"] for r in changes)


# ---------------------------------------------------------------------------
# the bell
# ---------------------------------------------------------------------------


def test_the_bell_lists_and_marks_a_persons_notices(client, auth, users, db) -> None:
    master = auth("master")
    db.ex(
        "INSERT INTO notifications (id, product_id, user_id, kind, title, link, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        str(uuid.uuid4()),
        "nagare",
        users["master"]["id"],
        "test",
        "A test notice",
        "/reports",
        "2026-11-24T12:00:00Z",
    )
    listing = client.get(f"{API}/notifications", headers=master).json()
    assert listing["unread"] >= 1
    notice = next(n for n in listing["items"] if n["title"] == "A test notice")
    assert notice["read_at"] is None
    # Someone else's notice is not found.
    other = client.post(f"{API}/notifications/{notice['id']}/read", headers=auth("viewer"))
    assert other.status_code == 404
    assert client.post(f"{API}/notifications/{notice['id']}/read", headers=master).json()["read_at"]
    assert client.post(f"{API}/notifications/read-all", headers=master).json()["ok"] is True
    assert client.get(f"{API}/notifications", headers=master).json()["unread"] == 0


def test_the_file_name_keeps_the_letters_of_accented_words() -> None:
    from backend.app.reports.planilha import file_name

    doc = {
        "subtitle": "Providências · SR Adv",
        "template": "x",
        "code": "REP-01",
        "period": {"from": "2026-10-01"},
    }
    assert file_name(doc, "xlsx") == "rep-01-providencias-sr-adv-2026-10-01.xlsx"
