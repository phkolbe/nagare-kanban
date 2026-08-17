"""Capture reference screenshots of best-in-class Kanban / flow-analytics products.

These become the opponent side of the blind A/B comparison in ``blind_compare.py``.
We only visit publicly reachable pages -- live public demo boards where they exist,
otherwise the product pages that show the real application UI.

Usage:
    python tools/fetch_references.py            # capture everything that responds
    python tools/fetch_references.py --list     # show the catalogue
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "refs"

VIEWPORT = {"width": 1440, "height": 900}

# category: what part of Nagare this reference is the yardstick for.
REFERENCES: list[dict] = [
    # ---- board UI ----
    {"id": "kanbanflow_demo", "category": "board", "url": "https://kanbanflow.com/", "note": "KanbanFlow live board"},
    {"id": "wekan", "category": "board", "url": "https://wekan.github.io/", "note": "Wekan open-source kanban"},
    {"id": "vikunja", "category": "board", "url": "https://vikunja.io/features", "note": "Vikunja kanban view"},
    {"id": "plane", "category": "board", "url": "https://plane.so/", "note": "Plane project boards"},
    {"id": "linear", "category": "board", "url": "https://linear.app/", "note": "Linear -- the visual craft bar"},
    {"id": "height", "category": "board", "url": "https://height.app/", "note": "Height boards"},
    {"id": "taiga", "category": "board", "url": "https://taiga.io/", "note": "Taiga kanban"},
    {"id": "jira_kanban", "category": "board",
     "url": "https://www.atlassian.com/software/jira/features/kanban-boards", "note": "Jira kanban board"},
    {"id": "kanbantool", "category": "board", "url": "https://kanbantool.com/", "note": "Kanban Tool"},

    # ---- flow metrics / analytics ----
    {"id": "businessmap_analytics", "category": "metrics",
     "url": "https://businessmap.io/features/analytics", "note": "Businessmap (Kanbanize) flow analytics"},
    {"id": "actionableagile", "category": "metrics", "url": "https://actionableagile.com/",
     "note": "ActionableAgile -- the flow-metrics reference"},
    {"id": "nave", "category": "metrics", "url": "https://getnave.com/", "note": "Nave flow analytics"},
    {"id": "swiftkanban_metrics", "category": "metrics",
     "url": "https://www.nimblework.com/kanban/kanban-metrics/", "note": "SwiftKanban metrics"},

    # ---- method documentation ----
    {"id": "kanbanguides", "category": "docs", "url": "https://kanbanguides.org/english/",
     "note": "The Kanban Guide"},
    {"id": "businessmap_docs", "category": "docs",
     "url": "https://businessmap.io/kanban-resources/getting-started/what-is-kanban",
     "note": "Businessmap Kanban resources"},
    {"id": "atlassian_kanban", "category": "docs", "url": "https://www.atlassian.com/agile/kanban",
     "note": "Atlassian Kanban guide"},
    {"id": "kanban_university", "category": "docs", "url": "https://kanban.university/kanban-guide/",
     "note": "Kanban University (David J. Anderson)"},
    {"id": "kmm", "category": "docs", "url": "https://kanban.university/kanban-maturity-model/",
     "note": "Kanban Maturity Model"},
]

# Consent / cookie banners: pick the most privacy-preserving option so the shot is clean.
DISMISS_SELECTORS = [
    "button:has-text('Reject all')",
    "button:has-text('Reject All')",
    "button:has-text('Decline')",
    "button:has-text('Only necessary')",
    "button:has-text('Necessary only')",
    "#onetrust-reject-all-handler",
    "button:has-text('Accept')",  # last resort so the banner stops covering the page
    "[aria-label='Close']",
]


def dismiss_banners(page) -> None:
    for sel in DISMISS_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=1500)
                page.wait_for_timeout(400)
                return
        except (PWError, PWTimeout):
            continue


# A reference that is really an error page is worse than no reference at all: it still
# renders large enough to pass a byte-size check, so it silently becomes an opponent that
# any real interface beats, and the comparison reports a meaningless win.
ERROR_MARKERS = (
    "error 404", "404 error", "page not found", "page you are looking for does not exist",
    "page doesn't exist", "page does not exist", "410 gone", "403 forbidden",
    "500 internal server error", "service unavailable", "access denied",
    "are you a robot", "verify you are human", "enable javascript",
)


def looks_like_error_page(page) -> str | None:
    """Return the marker that identifies this as an error/interstitial page, if any."""
    try:
        text = (page.inner_text("body") or "")[:4000].lower()
    except (PWError, PWTimeout):
        return None
    for marker in ERROR_MARKERS:
        if marker in text:
            return marker
    return None


def _capture_once(ctx, ref: dict, path: pathlib.Path) -> dict:
    # A fresh page per reference: a site that redirects slowly would otherwise leave a
    # pending navigation that aborts the *next* reference's goto and cascade the run.
    page = ctx.new_page()
    try:
        page.goto(ref["url"], wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(2500)
        dismiss_banners(page)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except PWTimeout:
            pass
        page.mouse.wheel(0, 250)          # nudge lazy-loaded hero imagery into view
        page.wait_for_timeout(1200)
        page.mouse.wheel(0, -250)
        page.wait_for_timeout(600)

        marker = looks_like_error_page(page)
        if marker:
            return {**ref, "path": None, "bytes": 0, "ok": False,
                    "error": f"error page (matched {marker!r}) at {page.url}"}

        page.screenshot(path=str(path))
        size = path.stat().st_size
        return {**ref, "path": str(path.relative_to(ROOT)), "bytes": size, "ok": size > 40_000}
    finally:
        page.close()


def capture(ctx, ref: dict, attempts: int = 2) -> dict:
    dest = OUT / ref["category"]
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{ref['id']}.png"
    last = ""
    for attempt in range(attempts):
        try:
            res = _capture_once(ctx, ref, path)
            if res["ok"]:
                return res
            # Keep the specific reason (error page, interstitial) rather than flattening
            # every rejection to "thin render" — that is what hid the 404 last time.
            last = res.get("error") or f"thin render ({res['bytes']} bytes)"
            path.unlink(missing_ok=True)
        except (PWError, PWTimeout) as exc:
            last = str(exc).splitlines()[0][:180]
    return {**ref, "path": None, "bytes": 0, "ok": False, "error": last}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="comma-separated reference ids")
    args = ap.parse_args()

    if args.list:
        for r in REFERENCES:
            print(f"{r['category']:8} {r['id']:24} {r['url']}")
        return 0

    wanted = REFERENCES
    if args.only:
        keep = {s.strip() for s in args.only.split(",")}
        wanted = [r for r in REFERENCES if r["id"] in keep]

    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,
            reduced_motion="reduce",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        )
        for ref in wanted:
            res = capture(ctx, ref)
            results.append(res)
            flag = "OK  " if res["ok"] else "FAIL"
            print(f"[{flag}] {res['category']:8} {res['id']:24} {res['bytes']:>9,} bytes"
                  f"{'  ' + res.get('error', '') if not res['ok'] else ''}")
        browser.close()

    manifest = OUT / "manifest.json"
    manifest.write_text(json.dumps(results, indent=2), encoding="utf-8")
    good = [r for r in results if r["ok"]]
    print(f"\n{len(good)}/{len(results)} references captured -> {manifest}")
    by_cat: dict[str, int] = {}
    for r in good:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    print("by category:", by_cat)
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
