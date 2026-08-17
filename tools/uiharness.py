"""Shared Playwright plumbing for the UI checker and the screenshotter.

Both tools need the same three things: a signed-in browser context, the canonical list
of routes with the slugs the comparison harness expects, and a reliable "the view has
actually finished rendering" wait. Keeping them here stops the two scripts drifting
apart and disagreeing about what a route is called.
"""

from __future__ import annotations

import json
import pathlib
import urllib.request

BASE = "http://127.0.0.1:8770"
API = f"{BASE}/api/v1"
ROOT = pathlib.Path(__file__).resolve().parents[1]

MASTER = {"email": "master@nagare.app", "password": "nagare123"}
TOKEN_KEY = "nagare.token"
THEME_KEY = "nagare.theme"


def api_login(email: str = MASTER["email"], password: str = MASTER["password"]) -> str:
    """Get a bearer token straight from the API.

    Seeding localStorage beats driving the login form: it is faster, and a failure in
    the form would otherwise masquerade as a failure on every downstream route.
    """
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{API}/auth/login", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())["access_token"]


def api_get(path: str, token: str):
    req = urllib.request.Request(f"{API}{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def discover_ids(token: str) -> dict[str, str]:
    """Real ids from the seeded workspace, so routes are never guessed."""
    boards = api_get("/boards", token)["items"]
    operational = next(b for b in boards if b["kind"] == "operational")
    simulator = next((b for b in boards if b["kind"] == "simulator"), operational)
    projects = api_get("/projects", token)["items"]
    return {
        "board_id": operational["id"],
        "sim_board_id": simulator["id"],
        "project_id": projects[0]["id"],
    }


def routes_for(ids: dict[str, str]) -> list[dict]:
    """The route table. ``slug`` doubles as the screenshot filename and the key the
    blind-comparison harness maps to a reference category."""
    b, s, p = ids["board_id"], ids["sim_board_id"], ids["project_id"]
    return [
        {"slug": "board-unified", "hash": f"#/board/{b}?segment=unified", "settle": ".board-column"},
        {"slug": "board-upstream", "hash": f"#/board/{b}?segment=upstream", "settle": ".board-column"},
        {"slug": "board-downstream", "hash": f"#/board/{b}?segment=downstream", "settle": ".board-column"},
        {"slug": "card-drawer", "hash": f"#/board/{b}?segment=unified", "settle": ".board-column",
         "open_card": True},
        {"slug": "projects", "hash": "#/projects", "settle": "main"},
        {"slug": "project-detail", "hash": f"#/projects/{p}", "settle": "main"},
        {"slug": "metrics", "hash": f"#/metrics/{b}", "settle": "main"},
        {"slug": "metrics-kmm", "hash": f"#/metrics/{b}", "settle": "main", "scroll_bottom": True},
        {"slug": "simulator", "hash": f"#/simulator/{s}", "settle": "main"},
        {"slug": "admin-users", "hash": "#/admin/users", "settle": "main"},
        {"slug": "admin-flags", "hash": "#/admin/flags", "settle": "main"},
        {"slug": "admin-plans", "hash": "#/admin/plans", "settle": "main"},
    ]


def new_context(pw, theme: str = "dark", width: int = 1440, height: int = 900, scale: int = 2):
    browser = pw.chromium.launch()
    ctx = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=scale,
        reduced_motion="reduce",
        color_scheme="dark" if theme == "dark" else "light",
    )
    return browser, ctx


def prime(ctx, token: str, theme: str) -> None:
    """Install the token and theme before any script runs, so the first paint is
    already authenticated and in the right palette (no login flash in screenshots)."""
    ctx.add_init_script(
        f"""
        try {{
          localStorage.setItem({json.dumps(TOKEN_KEY)}, {json.dumps(token)});
          localStorage.setItem({json.dumps(THEME_KEY)}, {json.dumps(theme)});
        }} catch (e) {{}}
        """
    )


def goto_route(page, route: dict, timeout: int = 25_000) -> None:
    """Navigate and wait until the view is genuinely painted."""
    page.goto(f"{BASE}/{route['hash']}", wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(400)
    # A hash change on an already-loaded document does not re-fire load events, so
    # force the router to run for routes visited back to back.
    page.evaluate("window.dispatchEvent(new HashChangeEvent('hashchange'))")
    try:
        page.wait_for_selector(route.get("settle", "main"), timeout=timeout, state="attached")
    except Exception:
        pass
    # Skeletons mean the view is still fetching; give them a chance to clear.
    for _ in range(30):
        pending = page.evaluate(
            "document.querySelectorAll('.skeleton, [data-loading=\"true\"]').length"
        )
        if not pending:
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(700)

    if route.get("open_card"):
        card = page.locator(".board-card, .card-tile, [data-card-id]").first
        if card.count():
            card.click(timeout=5000)
            page.wait_for_timeout(1200)
    if route.get("scroll_bottom"):
        # The app scrolls inside a pane, not the document, so window.scrollTo is a no-op.
        # Find the tallest actually-scrollable element and drive that instead.
        page.evaluate(
            """() => {
                const scrollable = [...document.querySelectorAll('main, main *, body')]
                    .filter(n => n.scrollHeight - n.clientHeight > 200)
                    .sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
                if (scrollable) scrollable.scrollTop = scrollable.scrollHeight;
                else window.scrollTo(0, document.body.scrollHeight);
            }"""
        )
        page.wait_for_timeout(1200)
