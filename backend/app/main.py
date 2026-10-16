"""FastAPI application factory.

One origin serves everything: the JSON API under ``/api/v1`` and the static
frontend from ``/``. That keeps the browser free of CORS and lets the frontend
use plain relative fetches.

Routers are written concurrently by different agents, so each ``include_router``
is guarded: a module that is not ready yet logs a warning instead of stopping the
whole app from booting.
"""

from __future__ import annotations

import importlib
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import func, select

from . import __version__
from .config import settings
from .db import Database, migrate
from .errors import error_payload, install_error_handlers
from .models import User
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
    try:
        yield
    finally:
        await database.dispose()


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


def _safe_frontend_path(relative: str) -> Path | None:
    """Resolve a request path inside ``frontend/``, refusing traversal attempts."""
    root = settings.frontend_dir.resolve()
    candidate = (root / relative.lstrip("/")).resolve()
    if candidate == root or root in candidate.parents:
        return candidate
    return None


def _file_response(path: Path) -> FileResponse:
    media_type, _ = mimetypes.guess_type(path.name)
    headers = dict(_NO_CACHE) if settings.dev else {}
    return FileResponse(path, media_type=media_type or "application/octet-stream", headers=headers)


def create_app(database_url: str | None = None) -> FastAPI:
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
    app.state.database = Database(database_url)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    install_error_handlers(app)

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

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def spa(full_path: str, request: Request) -> Any:
        """Serve a static asset, or fall back to the SPA shell.

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
            return JSONResponse(
                status_code=404,
                content=error_payload(
                    "not_found", f"No API route for {request.method} /{full_path}."
                ),
            )
        relative = full_path[len("static/") :] if full_path.startswith("static/") else full_path
        candidate = _safe_frontend_path(relative)
        if candidate and candidate.is_file():
            return _file_response(candidate)
        return _index_response()

    return app


def _index_response() -> Any:
    """The SPA shell, or a helpful placeholder if the frontend is not built yet."""
    index_file = settings.frontend_dir / "index.html"
    if index_file.is_file():
        return _file_response(index_file)
    return PlainTextResponse(
        "Nagare Kanban API is running.\n\n"
        f"The frontend shell was not found at {index_file}.\n"
        f"The API is available at {API_PREFIX} and the docs at /docs.\n",
        status_code=200,
        headers=dict(_NO_CACHE),
    )


app = create_app()
