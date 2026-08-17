"""HTTP routers.

Every module here exposes a module-level ``router`` (``fastapi.APIRouter``) that
``backend.app.main`` mounts under ``/api/v1``. Routers are written by different
agents and are imported defensively so a missing module never stops the app from
booting.
"""

# The canonical mount order used by main.create_app(). Keeping it here means the
# app factory and the docs agree on one list.
ROUTER_MODULES: tuple[str, ...] = (
    "auth",
    "users",
    "flags",
    "boards",
    "projects",
    "cards",
    "movements",
    "metrics",
    "simulator",
)

__all__ = ["ROUTER_MODULES"]
