"""Visit every Nagare route and fail loudly on console errors or bad network calls.

Run against an already-running server:  python run.py  (then)  python tools/ui_check.py
"""

from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from uiharness import (  # noqa: E402
    ROOT, api_login, discover_ids, goto_route, new_context, prime, routes_for,
)

# Noise that says nothing about our code quality.
IGNORE_SUBSTRINGS = (
    "favicon",
    "Download the React DevTools",
    "StarletteDeprecationWarning",
)


def interesting(text: str) -> bool:
    return not any(s in text for s in IGNORE_SUBSTRINGS)


def check_theme(pw, token: str, theme: str, ids: dict) -> list[dict]:
    browser, ctx = new_context(pw, theme=theme)
    prime(ctx, token, theme)
    page = ctx.new_page()

    console_errors: list[str] = []
    page_errors: list[str] = []
    bad_requests: list[str] = []

    page.on("console", lambda m: console_errors.append(f"{m.type}: {m.text}")
            if m.type == "error" and interesting(m.text) else None)
    page.on("pageerror", lambda e: page_errors.append(str(e)))

    def on_response(resp):
        if resp.status >= 400 and interesting(resp.url):
            bad_requests.append(f"{resp.status} {resp.request.method} {resp.url}")

    page.on("response", on_response)

    results = []
    for route in routes_for(ids):
        console_errors.clear()
        page_errors.clear()
        bad_requests.clear()
        goto_route(page, route)

        counts = page.evaluate(
            """() => ({
                columns: document.querySelectorAll('.board-column, [data-column-id]').length,
                cards: document.querySelectorAll('.board-card, [data-card-id]').length,
                commitment: document.querySelectorAll(
                    '.commitment-point, .commitment-rail, [data-commitment]').length,
                charts: document.querySelectorAll('main svg').length,
                mainText: (document.querySelector('main')?.innerText || '').trim().length,
            })"""
        )
        results.append({
            "route": route["slug"], "theme": theme,
            "console_errors": list(console_errors), "page_errors": list(page_errors),
            "bad_requests": list(bad_requests), **counts,
        })
        total = len(console_errors) + len(page_errors) + len(bad_requests)
        flag = "OK  " if total == 0 else "FAIL"
        print(f"[{flag}] {theme:5} {route['slug']:18} cols={counts['columns']:>3} "
              f"cards={counts['cards']:>4} svg={counts['charts']:>3} "
              f"text={counts['mainText']:>6} errs={total}")
        for e in list(console_errors)[:4] + list(page_errors)[:4] + list(bad_requests)[:4]:
            print(f"         - {e[:190]}")

    browser.close()
    return results


def main() -> int:
    token = api_login()
    ids = discover_ids(token)
    print(f"board={ids['board_id'][:8]}  sim={ids['sim_board_id'][:8]}  "
          f"project={ids['project_id'][:8]}\n")

    all_results = []
    with sync_playwright() as pw:
        for theme in ("dark", "light"):
            all_results += check_theme(pw, token, theme, ids)
            print()

    out = ROOT / "tools" / "ui_check_report.json"
    out.write_text(json.dumps(all_results, indent=2), encoding="utf-8")

    errs = sum(len(r["console_errors"]) + len(r["page_errors"]) + len(r["bad_requests"])
               for r in all_results)
    empty = [r for r in all_results if r["mainText"] < 200]
    print(f"total problems: {errs}")
    if empty:
        print("routes that rendered almost nothing:")
        for r in empty:
            print(f"  {r['theme']:5} {r['route']} (text={r['mainText']})")
    print(f"report -> {out}")
    return 0 if errs == 0 and not empty else 1


if __name__ == "__main__":
    sys.exit(main())
