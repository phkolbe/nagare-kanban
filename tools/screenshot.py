"""Capture every Nagare route in both themes, for the blind comparison harness.

Writes shots/<theme>/<slug>.png and verifies each image is actually a rendered page
rather than an empty shell (a blank screenshot silently poisons the A/B rounds).

Run against an already-running server:  python run.py  (then)  python tools/screenshot.py
"""

from __future__ import annotations

import json
import pathlib
import sys

from PIL import Image, ImageStat
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from uiharness import (
    ROOT,
    api_login,
    discover_ids,
    goto_route,
    new_context,
    prime,
    routes_for,
)

SHOTS = ROOT / "shots"
MIN_BYTES = 30_000
MIN_STDDEV = 12.0  # a near-uniform image is a blank/failed render


def verdict(path: pathlib.Path) -> dict:
    size = path.stat().st_size
    with Image.open(path) as im:
        grey = im.convert("L")
        stat = ImageStat.Stat(grey)
        stddev = stat.stddev[0]
        w, h = im.size
    return {
        "bytes": size,
        "stddev": round(stddev, 1),
        "w": w,
        "h": h,
        "ok": size >= MIN_BYTES and stddev >= MIN_STDDEV,
    }


def main() -> int:
    token = api_login()
    ids = discover_ids(token)
    routes = routes_for(ids)
    results = []

    with sync_playwright() as pw:
        for theme in ("dark", "light"):
            dest = SHOTS / theme
            dest.mkdir(parents=True, exist_ok=True)
            browser, ctx = new_context(pw, theme=theme)
            prime(ctx, token, theme)
            page = ctx.new_page()

            for route in routes:
                goto_route(page, route)
                path = dest / f"{route['slug']}.png"
                # full_page would stitch a very tall canvas for the board (which scrolls
                # horizontally, not vertically) and make the panels incomparable; the
                # viewport shot is what a reviewer would actually see.
                page.screenshot(path=str(path))
                v = verdict(path)
                results.append(
                    {
                        "theme": theme,
                        "route": route["slug"],
                        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                        **v,
                    }
                )
                print(
                    f"[{'OK  ' if v['ok'] else 'THIN'}] {theme:5} {route['slug']:18} "
                    f"{v['bytes']:>9,}b  sd={v['stddev']:>5}  {v['w']}x{v['h']}"
                )
            browser.close()

    (ROOT / "tools" / "screenshot_report.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    thin = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(thin)}/{len(results)} screenshots look real")
    if thin:
        print("SUSPECT (blank or near-blank):")
        for r in thin:
            print(f"  {r['theme']:5} {r['route']:18} {r['bytes']:,}b sd={r['stddev']}")
    return 0 if not thin else 1


if __name__ == "__main__":
    sys.exit(main())
