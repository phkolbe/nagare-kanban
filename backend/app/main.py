"""FastAPI application factory.

One origin serves everything: the JSON API under ``/api/v1`` and the static
frontend from ``/``. That keeps the browser free of CORS and lets the frontend
use plain relative fetches.

Routers are written concurrently by different agents, so each ``include_router``
is guarded: a module that is not ready yet logs a warning instead of stopping the
whole app from booting.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import mimetypes
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import func, select

from . import __version__, observability
from .config import settings
from .db import Database, migrate
from .errors import ApiError, install_error_handlers
from .models import User
from .pampufa import PampufaGateway, webhook_loop
from .routers import ROUTER_MODULES

log = logging.getLogger("nagare")

API_PREFIX = "/api/v1"

# Windows reads MIME types from the registry, where ".js" is frequently mapped to
# "text/plain". A native ES module served as text/plain is refused by every
# browser, so the correct types are registered explicitly before any response.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")

_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the engine and apply migrations on boot, so a cold clone is usable."""
    database: Database = app.state.database
    if settings.auto_migrate:
        await migrate(database.url)
    log.info("Nagare Kanban API ready — database at %r", database)

    # Pampufa integration: the gateway may already be there (tests inject a double).
    if getattr(app.state, "pampufa", None) is None and settings.pampufa_enabled:
        app.state.pampufa = PampufaGateway(settings)
    sender: asyncio.Task[None] | None = None
    if app.state.pampufa is not None and app.state.webhooks_enabled:
        sender = asyncio.create_task(
            webhook_loop(database.sessions, app.state.pampufa, settings.webhook_interval_s)
        )
        log.info("Pampufa integration on — %s", settings.pampufa_url or "test double")
    try:
        yield
    finally:
        if sender is not None:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
        await database.dispose()
        observability.shutdown()


def _mount_routers(app: FastAPI) -> list[str]:
    """Include every router listed in SPEC §1, tolerating missing modules."""
    mounted: list[str] = []
    for name in ROUTER_MODULES:
        try:
            module = importlib.import_module(f"{__package__}.routers.{name}")
        except ImportError as exc:
            log.warning("Router '%s' is not available yet (%s)", name, exc)
            continue
        except Exception as exc:  # pragma: no cover - a broken router must not kill boot
            log.error("Router '%s' failed to import: %r", name, exc)
            continue

        router: APIRouter | None = getattr(module, "router", None)
        if router is None:
            log.warning("Router module '%s' exposes no 'router' attribute", name)
            continue

        # A router may already carry the full public prefix; only add ours when
        # it does not, so both conventions work while agents build in parallel.
        already_prefixed = router.prefix.startswith(API_PREFIX) or any(
            getattr(route, "path", "").startswith(API_PREFIX) for route in router.routes
        )
        app.include_router(router, prefix="" if already_prefixed else API_PREFIX)
        mounted.append(name)
    return mounted


def _file_response(path: Path) -> FileResponse:
    media_type, _ = mimetypes.guess_type(path.name)
    headers = dict(_NO_CACHE) if settings.dev else {}
    return FileResponse(path, media_type=media_type or "application/octet-stream", headers=headers)


def create_app(
    database_url: str | None = None,
    *,
    pampufa: Any = None,
    webhooks_enabled: bool = True,
) -> FastAPI:
    """Build the application. ``database_url`` lets tests point at their own database."""
    app = FastAPI(
        title="Nagare Kanban API",
        version=__version__,
        description=(
            "Flow-management API for the Nagare Kanban board: upstream/downstream "
            "visualisation, WIP policies, movement history, flow metrics, "
            "probabilistic forecasting and a scenario simulator."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    # Observability (OBS-I) before the engine exists: the SQLAlchemy instrumentation
    # wraps the ``create_async_engine`` the ``Database`` below calls; the FastAPI span is
    # the outermost middleware and continues the Caddy's ``traceparent``.
    observability.install(app)
    app.state.database = Database(database_url)
    #: The Pampufa gateway (``None``: standalone). Tests pass an in-memory double.
    app.state.pampufa = pampufa
    #: Tests deliver webhooks by hand (``pampufa.deliver_pending``) to stay deterministic.
    app.state.webhooks_enabled = webhooks_enabled

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    app.add_middleware(observability.RequestMetrics)
    # Outermost: the standalone screens live under /nagare (decision of 19/10/2026).
    app.add_middleware(StandalonePrefix)
    install_error_handlers(app)

    # ---- observability: /metrics for the platform's collector (before the SPA catch-all)
    app.include_router(observability.metrics_router)

    # ---- meta ------------------------------------------------------------

    meta = APIRouter(tags=["meta"])

    @meta.get("/health", summary="Liveness and readiness probe")
    async def health(request: Request) -> dict[str, Any]:
        """Report process health plus whether the database is migrated and seeded."""
        database_ok = True
        seeded = False
        try:
            async with request.app.state.database.sessions() as s:
                seeded = bool(await s.scalar(select(func.count()).select_from(User)))
        except Exception:  # pragma: no cover - only when the database is unreachable
            database_ok = False
        return {
            "status": "ok" if database_ok else "degraded",
            "app": "Nagare Kanban API",
            "version": __version__,
            "database": database_ok,
            "seeded": seeded,
            "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    app.include_router(meta, prefix=API_PREFIX)

    mounted = _mount_routers(app)
    app.state.mounted_routers = mounted
    log.info("Mounted routers: %s", ", ".join(mounted) or "none")

    # ---- frontend --------------------------------------------------------

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        return _index_response()

    @app.get("/grupo-sr/{full_path:path}", include_in_schema=False)
    def platform_screens(full_path: str) -> Any:
        """The React screens of the platform's Tarefas module (NG7, N2).

        The gateway sends ``/grupo-sr/_nagare/*`` (the build) and
        ``/grupo-sr/<produto>/tarefas*`` (the screens) here, prefix kept.
        """
        return _screens_response(full_path)

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def spa(full_path: str, request: Request) -> Any:
        """The standalone Nagare's screens: every other GET is the React shell (N3).

        Every verb is claimed so that a request to an API route whose router is
        not mounted yet answers with the standard JSON 404 rather than a 405 from
        the router table — the client cannot tell those apart usefully.
        """
        if (
            full_path.startswith("api/")
            or full_path == "api"
            or request.method
            not in {
                "GET",
                "HEAD",
            }
        ):
            raise ApiError("not_found", f"No API route for {request.method} /{full_path}.", 404)
        return _index_response()

    return app


_SCREEN_ROUTE = re.compile(r"^[a-z0-9-]+/tarefas(/.*)?$")

#: Where the standalone Nagare lives, directly on :8770 and behind the gateway (19/10/2026).
STANDALONE_PREFIX = "/nagare"
#: Paths that are not screens: never redirected to /nagare.
_NOT_SCREENS = ("/api", "/docs", "/redoc", "/openapi.json", "/grupo-sr", "/metrics", "/health")
#: The Portuguese route names used before 19/10/2026, and their English names in the URL.
_OLD_NAMES = {
    "quadro": "board",
    "projetos": "projects",
    "metricas": "metrics",
    "simulador": "simulator",
    "entrar": "sign-in",
    "usuarios": "users",
    "papeis": "roles",
    "planos": "plans",
}


def standalone_location(path: str) -> str:
    """``/quadro/1`` -> ``/nagare/board/1``; ``/`` -> ``/nagare/``."""
    parts = [_OLD_NAMES.get(part, part) for part in path.split("/")]
    return STANDALONE_PREFIX + ("/".join(parts) if path != "/" else "/")


class StandalonePrefix:
    """The standalone Nagare under ``/nagare`` (decision of 19/10/2026).

    * ``/nagare/...`` (straight to :8770) is served as if the prefix were not there: the
      screens, the API at ``/nagare/api/v1`` and the build all work;
    * a screen asked for without the prefix and not through a proxy (``:8770/``, an old
      ``:8770/quadro/1`` link) is redirected to ``/nagare/...``, with the English name;
    * behind the gateway (``X-Forwarded-For``) nothing changes: Caddy already publishes the
      Nagare at ``/nagare/`` and strips the prefix before forwarding.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path: str = scope["path"]
            if path == STANDALONE_PREFIX or path.startswith(STANDALONE_PREFIX + "/"):
                inner = path[len(STANDALONE_PREFIX) :] or "/"
                scope = {**scope, "path": inner, "raw_path": inner.encode()}
            elif (
                scope["method"] in ("GET", "HEAD")
                and not path.startswith(_NOT_SCREENS)
                and not any(name == b"x-forwarded-for" for name, _ in scope.get("headers", []))
            ):
                query = scope.get("query_string", b"").decode("latin-1")
                location = standalone_location(path) + (f"?{query}" if query else "")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 308,
                        "headers": [
                            (b"location", location.encode("latin-1")),
                            (b"content-length", b"0"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)


def _screens_response(full_path: str) -> Any:
    root = settings.screens_dir.resolve()
    if full_path.startswith("_nagare/"):
        candidate = (root / full_path[len("_nagare/") :]).resolve()
        if root in candidate.parents and candidate.is_file():
            return _file_response(candidate)
        raise ApiError("not_found", "No such file.", 404)
    if not _SCREEN_ROUTE.match(full_path):
        raise ApiError("not_found", "Not a Nagare screen.", 404)
    index_file = root / "index.html"
    if index_file.is_file():
        return _file_response(index_file)
    return PlainTextResponse(
        f"The Tarefas screens are not built yet: run `npm run build` in web/ ({index_file}).\n",
        status_code=503,
        headers=dict(_NO_CACHE),
    )


def _index_response() -> Any:
    """The React shell (``web/dist``), or a placeholder while it is not built yet."""
    index_file = settings.screens_dir / "index.html"
    if index_file.is_file():
        return _file_response(index_file)
    return PlainTextResponse(
        "Nagare Kanban API is running.\n\n"
        f"The screens are not built yet: run `npm run build` in web/ ({index_file}).\n"
        f"The API is available at {API_PREFIX} and the docs at /docs.\n",
        status_code=200,
        headers=dict(_NO_CACHE),
    )


app = create_app()
