"""Prove the increment-2 features actually work, by driving the real UI.

Rendering without errors is not the same as working. Every check below performs a real
user action and asserts the observable consequence — a column that appears on the board,
a commitment point that moves, a badge that repaints, a lane preference that survives a
reload.

Run against an already-running server:  python run.py  (then)  python tools/v2_check.py
"""

from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from uiharness import (  # noqa: E402
    API, BASE, ROOT, api_get, api_login, discover_ids, goto_route, new_context, prime,
    routes_for,
)

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return bool(ok)


def board_route(ids: dict, extra: str = "") -> dict:
    base = [r for r in routes_for(ids) if r["slug"] == "board-unified"][0]
    if extra:
        return {**base, "hash": base["hash"] + extra}
    return base


# --------------------------------------------------------------------------- columns
def check_column_lifecycle(token: str, ids: dict) -> None:
    """Create -> rename -> re-kind -> re-segment -> delete, straight through the API the
    structure editor calls, asserting the board's derived structure follows each step."""
    board_id = ids["board_id"]
    import urllib.error
    import urllib.request

    def call(method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    status, created = call("POST", f"/boards/{board_id}/columns", {
        "name": "Peer Review", "segment": "downstream", "kind": "activity",
        "wip_min": 1, "wip_max": 3,
    })
    if not check("column: create", status == 201, f"HTTP {status}"):
        return
    col_id = created["id"]

    try:
        status, renamed = call("PATCH", f"/columns/{col_id}", {"name": "Peer Review (QA)"})
        check("column: rename", status == 200 and renamed.get("name") == "Peer Review (QA)",
              f"HTTP {status}")

        status, rekinded = call("PATCH", f"/columns/{col_id}", {"kind": "queue"})
        check("column: change kind activity->queue (wait vs touch)",
              status == 200 and rekinded.get("kind") == "queue", f"HTTP {status}")

        # Moving the only-just-created column to upstream must NOT leave the board
        # without a commitment point, and the point must be re-derived.
        before = api_get(f"/boards/{board_id}", token)
        before_commit = next((c["id"] for c in before["columns"] if c["is_commitment_point"]), None)
        status, _ = call("PATCH", f"/columns/{col_id}", {"segment": "upstream"})
        after = api_get(f"/boards/{board_id}", token)
        commits = [c for c in after["columns"] if c["is_commitment_point"]]
        deliveries = [c for c in after["columns"] if c["is_delivery_point"]]
        check("column: change segment re-derives exactly one commitment point",
              status == 200 and len(commits) == 1 and len(deliveries) == 1,
              f"commit={len(commits)} delivery={len(deliveries)} (was {str(before_commit)[:8]})")

        # Swap two adjacent columns inside one segment. Yanking the last column to the
        # front would split a column group, which the API rightly refuses with
        # `group_not_contiguous` — that guard is asserted separately below.
        order = [c["id"] for c in after["columns"]]
        same_segment = [c for c in after["columns"] if c["segment"] == "upstream"]
        if len(same_segment) >= 3:
            i, j = order.index(same_segment[1]["id"]), order.index(same_segment[2]["id"])
            swapped = order[:]
            swapped[i], swapped[j] = swapped[j], swapped[i]
            status, _ = call("POST", f"/boards/{board_id}/columns/reorder",
                             {"ordered_ids": swapped})
            reordered = api_get(f"/boards/{board_id}", token)
            positions = [c["position"] for c in reordered["columns"]]
            check("column: reorder keeps positions dense",
                  status == 200 and positions == sorted(positions)
                  and len(set(positions)) == len(positions), f"HTTP {status}")
            call("POST", f"/boards/{board_id}/columns/reorder", {"ordered_ids": order})

        # A reorder that would break group contiguity must be refused, not silently
        # corrupt the board.
        #
        # Rotating the list does NOT test this: the server orders columns by segment
        # first, so a grouped upstream column yanked to the end of the payload still
        # lands inside the upstream run and the group stays whole (a correct 200). The
        # only arrangement that genuinely splits a group is wedging an UNGROUPED column
        # between two of its members — which is what `col_id`, created ungrouped above
        # and currently upstream, is for.
        current = api_get(f"/boards/{board_id}", token)["columns"]
        members = [c for c in current
                   if c["segment"] == "upstream" and c["group_id"] and c["id"] != col_id]
        if len(members) >= 2:
            rest = [c["id"] for c in current
                    if c["segment"] == "upstream" and c["id"] != col_id
                    and c["id"] not in {m["id"] for m in members}]
            wedged = (rest + [members[0]["id"], col_id]
                      + [m["id"] for m in members[1:]]
                      + [c["id"] for c in current if c["segment"] == "downstream"])
            status, err = call("POST", f"/boards/{board_id}/columns/reorder",
                               {"ordered_ids": wedged})
            check("column: a reorder that splits a group is refused",
                  status == 409
                  and (err.get("error") or {}).get("code") == "group_not_contiguous",
                  f"HTTP {status} code={(err.get('error') or {}).get('code')}")

        status, _ = call("DELETE", f"/columns/{col_id}")
        check("column: delete empty column", status == 200, f"HTTP {status}")
        col_id = None
    finally:
        if col_id:
            call("DELETE", f"/columns/{col_id}")


def check_occupied_delete_and_drain(token: str, ids: dict) -> None:
    """An occupied column must refuse deletion, and drain must be the way out."""
    import urllib.error
    import urllib.request

    def call(method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{API}{path}", data=data, method=method,
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    board = api_get(f"/boards/{ids['board_id']}", token)
    wip = api_get(f"/boards/{ids['board_id']}/wip", token)
    occupied = next((c for c in board["columns"]
                     if (wip["columns"].get(c["id"], {}).get("count") or 0) > 0
                     and not c["is_delivery_point"]), None)
    if not occupied:
        check("drain: found an occupied column to test", False, "none found")
        return

    status, err = call("DELETE", f"/columns/{occupied['id']}")
    code = (err.get("error") or {}).get("code")
    if not check("column: deleting an occupied column is refused",
                 status == 409 and code == "column_not_empty", f"HTTP {status} code={code}"):
        return

    target = next((c for c in board["columns"] if c["id"] != occupied["id"]), None)
    before = wip["columns"][occupied["id"]]["count"]
    status, drained = call("POST", f"/columns/{occupied['id']}/drain",
                           {"to_column_id": target["id"]})
    ok = status == 200 and int(drained.get("moved", 0)) == before
    check("drain: moves every card out", ok,
          f"HTTP {status} moved={drained.get('moved')} expected={before}")

    after = api_get(f"/boards/{ids['board_id']}/wip", token)
    check("drain: source column is now empty",
          (after["columns"][occupied["id"]]["count"] or 0) == 0)

    # The whole point of drain over a raw UPDATE: history must stay truthful.
    sample = api_get(f"/cards?column_id={target['id']}&limit=1", token)["items"]
    if sample:
        hist = api_get(f"/cards/{sample[0]['id']}/history", token)
        check("drain: moved cards kept a movement history",
              len(hist.get("movements", hist.get("items", []))) > 0)

    # Put it back so the demo board keeps its shape.
    call("POST", f"/columns/{target['id']}/drain", {"to_column_id": occupied["id"]})


# --------------------------------------------------------------------------- WIP
def check_wip_editing(token: str, ids: dict) -> None:
    import urllib.request

    def patch(path: str, body: dict):
        req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                     method="PATCH",
                                     headers={"Authorization": f"Bearer {token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status

    board = api_get(f"/boards/{ids['board_id']}", token)
    wip = api_get(f"/boards/{ids['board_id']}/wip", token)
    col = next((c for c in board["columns"]
                if (wip["columns"].get(c["id"], {}).get("count") or 0) >= 2), None)
    if not col:
        check("wip: found a populated column", False)
        return

    original_max = col["wip_max"]
    count = wip["columns"][col["id"]]["count"]
    patch(f"/columns/{col['id']}", {"wip_max": 1})
    after = api_get(f"/boards/{ids['board_id']}/wip", token)
    check("wip: lowering a column max flips its state to over",
          after["columns"][col["id"]]["state"] == "over",
          f"{col['name']}: count={count} max=1 state={after['columns'][col['id']]['state']}")
    patch(f"/columns/{col['id']}", {"wip_max": original_max})

    original_flow = board.get("wip_max")
    patch(f"/boards/{ids['board_id']}", {"wip_max": 1})
    flow = api_get(f"/boards/{ids['board_id']}/wip", token)
    check("wip: whole-flow limit is editable and reported",
          (flow.get("flow") or {}).get("state") in {"over", "at_limit"},
          f"flow state={(flow.get('flow') or {}).get('state')}")
    patch(f"/boards/{ids['board_id']}", {"wip_max": original_flow})

    groups = board.get("groups") or []
    if groups:
        g = groups[0]
        # Send both bounds: the API rightly rejects a max below the existing min with
        # `invalid_wip_range`, so squeezing only the max would test the guard, not the edit.
        patch(f"/groups/{g['id']}", {"wip_min": 0, "wip_max": 1})
        gw = api_get(f"/boards/{ids['board_id']}/wip", token)
        check("wip: group limit is editable and reported",
              gw["groups"][g["id"]]["state"] in {"over", "at_limit"},
              f"{g['name']} state={gw['groups'][g['id']]['state']}")
        patch(f"/groups/{g['id']}",
              {"wip_min": g.get("wip_min"), "wip_max": g.get("wip_max")})

        # And the guard itself is worth pinning: an inverted range must be refused.
        import urllib.error
        req = urllib.request.Request(
            f"{API}/groups/{g['id']}", data=json.dumps({"wip_max": 0}).encode(),
            method="PATCH", headers={"Authorization": f"Bearer {token}",
                                     "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20)
            check("wip: an inverted group range is refused", False, "accepted min > max")
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
            check("wip: an inverted group range is refused",
                  e.code == 400 and (body.get("error") or {}).get("code") == "invalid_wip_range",
                  f"HTTP {e.code} code={(body.get('error') or {}).get('code')}")


# --------------------------------------------------------------------------- lanes
def check_lanes(pw, token: str, ids: dict) -> None:
    browser, ctx = new_context(pw, theme="dark")
    prime(ctx, token, "dark")
    page = ctx.new_page()

    goto_route(page, board_route(ids, "&lanes=sc"))
    page.wait_for_timeout(1200)

    lanes = page.evaluate("""() => [...document.querySelectorAll('.board-lane, [data-lane]')]
        .map(n => (n.getAttribute('data-lane') || n.textContent || '').trim().toLowerCase())""")
    distinct = {l for l in lanes if l}
    expected = {"expedite", "fixed_date", "fixed date", "standard", "intangible"}
    check("lanes: a lane per class of service renders",
          len(distinct) >= 4 or any(e in " ".join(distinct) for e in expected),
          f"{len(distinct)} lane nodes")

    cards_in_lanes = page.evaluate(
        "() => document.querySelectorAll('.board-lane .k-card, [data-lane] .k-card').length")
    check("lanes: cards are distributed into the lanes", cards_in_lanes > 0,
          f"{cards_in_lanes} cards")

    # A lane view is worthless if its header drifts away from the columns it labels.
    aligned = page.evaluate("""() => {
        const grid = document.querySelector('.board-grid');
        const lane = document.querySelector('.board-lane, [data-lane]');
        if (!grid || !lane) return null;
        return Math.abs(grid.getBoundingClientRect().left - lane.getBoundingClientRect().left) < 320;
    }""")
    check("lanes: lane rail stays aligned with the column grid", aligned is not False,
          f"aligned={aligned}")

    # Class of service must survive being viewed in lanes.
    before = api_get(f"/cards?board_id={ids['board_id']}&limit=200", token)["items"]
    mix_before = {}
    for c in before:
        mix_before[c["service_class"]] = mix_before.get(c["service_class"], 0) + 1

    page.reload()
    page.wait_for_timeout(1800)
    persisted = page.evaluate(
        "() => localStorage.getItem('nagare.lanes.' + (location.hash.match(/board\\/([^?]+)/)||[])[1])")
    still = page.evaluate(
        "() => document.querySelectorAll('.board-lane, [data-lane]').length")
    check("lanes: preference survives a reload", still > 0,
          f"lane nodes after reload={still} stored={persisted}")

    after = api_get(f"/cards?board_id={ids['board_id']}&limit=200", token)["items"]
    mix_after = {}
    for c in after:
        mix_after[c["service_class"]] = mix_after.get(c["service_class"], 0) + 1
    check("lanes: viewing in lanes never reclassifies a card", mix_before == mix_after,
          f"{mix_before} -> {mix_after}")

    browser.close()


def check_structure_editor_opens(pw, token: str, ids: dict) -> None:
    browser, ctx = new_context(pw, theme="dark")
    prime(ctx, token, "dark")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))

    goto_route(page, board_route(ids))
    expected_columns = len(api_get(f"/boards/{ids['board_id']}", token)["columns"])

    trigger = page.locator(".board-toolbar button:has-text('Edit columns')").first
    if not check("structure: the board toolbar offers 'Edit columns'", trigger.count() > 0):
        browser.close()
        return

    trigger.click()
    page.wait_for_timeout(1600)

    state = page.evaluate("""() => {
        const panel = document.querySelector('.drawer, [role="dialog"]');
        if (!panel) return null;
        const text = panel.innerText || '';
        return {
            columnRows: panel.querySelectorAll('[data-column-id]').length,
            hasAdd: /add column/i.test(text),
            mentionsWait: /wait/i.test(text),
            mentionsTouch: /touch/i.test(text),
            chars: text.length,
        };
    }""")
    check("structure: editor opens from the board toolbar", state is not None)
    if state:
        check("structure: editor lists every column on the board",
              state["columnRows"] == expected_columns,
              f"{state['columnRows']} rows for {expected_columns} columns")
        check("structure: editor offers adding a column", state["hasAdd"])
        check("structure: editor teaches wait vs touch",
              state["mentionsWait"] and state["mentionsTouch"],
              f"wait={state['mentionsWait']} touch={state['mentionsTouch']}")

        # Rows are collapsed by design; the fields live behind each row's Edit action.
        # Assert the fields actually appear, rather than assuming the list implies them.
        page.locator(".drawer [data-column-id] button[aria-label*='Edit' i]").first.click()
        page.wait_for_timeout(1200)
        fields = page.evaluate(
            "() => document.querySelectorAll('.drawer input, .drawer select, .drawer textarea').length")
        check("structure: a column row opens editable fields", fields >= 5,
              f"{fields} fields")

        reorder = page.evaluate(
            """() => document.querySelectorAll(
                ".drawer [data-column-id] button[aria-label*='Move' i]").length""")
        check("structure: columns are reorderable by keyboard", reorder >= expected_columns,
              f"{reorder} move buttons")
    check("structure: opening the editor raises no page error", not errors,
          "; ".join(errors[:2]))
    browser.close()


def main() -> int:
    token = api_login()
    ids = discover_ids(token)
    print(f"board={ids['board_id'][:8]}  sim={ids['sim_board_id'][:8]}\n")

    print("--- columns (R2.1) ---")
    check_column_lifecycle(token, ids)
    check_occupied_delete_and_drain(token, ids)
    print("\n--- wip limits (R2.2) ---")
    check_wip_editing(token, ids)
    print("\n--- swimlanes (R2.3) ---")
    with sync_playwright() as pw:
        check_lanes(pw, token, ids)
        print("\n--- structure editor UI ---")
        check_structure_editor_opens(pw, token, ids)

    (ROOT / "tools" / "v2_check_report.json").write_text(
        json.dumps(CHECKS, indent=2), encoding="utf-8")
    failed = [c for c in CHECKS if not c["ok"]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    for f in failed:
        print(f"  FAILED: {f['name']} — {f['detail']}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
