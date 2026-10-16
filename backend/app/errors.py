"""The single error envelope for the whole API.

Every failure — raised by us, by FastAPI's validator, or by an unhandled bug —
leaves the process as::

    {"error": {"code": "wip_limit_exceeded", "message": "...", "details": {...}}}

Routers raise :class:`ApiError` with a stable machine-readable ``code``; the
frontend switches on that code (``api.js`` surfaces it through ``ui.toast``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("nagare.errors")

__all__ = [
    "ApiError",
    "BadRequest",
    "Conflict",
    "Forbidden",
    "NotFound",
    "Unauthorized",
    "error_payload",
    "install_error_handlers",
]


class ApiError(Exception):
    """A failure that already knows how it should be rendered to the client."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def payload(self) -> dict[str, Any]:
        """The response body for this error."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<ApiError {self.status} {self.code}: {self.message}>"


class BadRequest(ApiError):
    """400 — the request is understood but unusable."""

    def __init__(
        self,
        message: str,
        code: str = "bad_request",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, 400, details)


class Unauthorized(ApiError):
    """401 — no usable credentials were presented."""

    def __init__(
        self,
        message: str = "Authentication required.",
        code: str = "unauthorized",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, 401, details)


class Forbidden(ApiError):
    """403 — authenticated, but the role or plan does not allow this."""

    def __init__(
        self,
        message: str = "You do not have permission to do that.",
        code: str = "forbidden",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, 403, details)


class NotFound(ApiError):
    """404 — the addressed resource does not exist (or is not visible)."""

    def __init__(
        self,
        resource: str = "Resource",
        resource_id: str | None = None,
        code: str = "not_found",
    ) -> None:
        message = f"{resource} not found."
        details = {"resource": resource}
        if resource_id:
            details["id"] = resource_id
        super().__init__(code, message, 404, details)


class Conflict(ApiError):
    """409 — the request collides with a policy or with current state."""

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, 409, details)


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the error envelope without raising (useful in partial responses)."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


# Status codes that map onto a nicer machine code than the generic default.
_HTTP_CODE_MAP: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
    503: "service_unavailable",
}


def install_error_handlers(app: FastAPI) -> None:
    """Register every exception handler on ``app``."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content=exc.payload(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for err in exc.errors():
            location = [str(part) for part in err.get("loc", []) if part != "body"]
            fields.append(
                {
                    "field": ".".join(location) or "body",
                    "message": err.get("msg", "Invalid value."),
                    "type": err.get("type", "value_error"),
                }
            )
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "validation_error",
                "The request body or query string failed validation.",
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODE_MAP.get(exc.status_code, "http_error")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        headers = dict(exc.headers or {})
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, detail),
            headers=headers or None,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Log with the traceback: the client only ever gets the opaque message.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "internal_error",
                "An unexpected error occurred. The incident was logged.",
                {"type": type(exc).__name__},
            ),
        )
