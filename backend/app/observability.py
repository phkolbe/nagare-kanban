"""Observability of the Nagare, on its own (decision OBS-I of the Pampufa platform).

The Nagare is not a Pampufa service (NG1), so it does not use the SDK's app factory;
it speaks the same OpenTelemetry anyway, with the same variables, so that the
platform's collector sees it as one more node of the picture:

* **Tracing** — with ``OTEL_EXPORTER_OTLP_ENDPOINT`` (the collector, OTLP/HTTP), a
  ``TracerProvider`` with ``service.name=nagare`` exports the spans of FastAPI
  (continuing the Caddy's ``traceparent``), SQLAlchemy (``db.statement`` without
  parameters) and httpx (the webhooks to the Pampufa carry the ``traceparent``).
* **Logs** — every line of the ``nagare`` loggers also goes to the collector by OTLP,
  with ``trace_id``/``span_id`` attached; on the console, ``trace_id`` is printed too.
* **Metrics** — ``/metrics`` in the Prometheus format for the collector to scrape:
  HTTP requests and latency by route template, plus the webhook outbox backlog.
  Protected by ``NAGARE_METRICS_TOKEN`` (the platform's ``METRICS_TOKEN``) or, without
  one, answered only to the loopback.

Without the variable nothing is installed and the tracer is the API's no-op.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from opentelemetry import trace
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .config import settings
from .errors import ApiError

SERVICE_NAME = "nagare"
INSTRUMENTATION = "nagare"

#: Probe routes: no span, no access metric noise in the logs.
EXCLUDED_URLS = "/api/v1/health,/metrics"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}

log = logging.getLogger("nagare.observability")

_provider: Any = None
_log_provider: Any = None

# ---------------------------------------------------------------------------
# metrics (Prometheus, scraped by the collector)
# ---------------------------------------------------------------------------

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "nagare_http_requests_total",
    "HTTP requests by route template, method and status",
    ["route", "method", "status"],
    registry=REGISTRY,
)
HTTP_LATENCY = Histogram(
    "nagare_http_latency_seconds",
    "HTTP request latency by route template and method",
    ["route", "method"],
    registry=REGISTRY,
)
WEBHOOK_PENDING = Gauge(
    "nagare_webhook_outbox_pending",
    "Webhooks to the Pampufa waiting for delivery",
    registry=REGISTRY,
)
WEBHOOKS = Counter(
    "nagare_webhooks_total",
    "Webhook deliveries to the Pampufa by event and outcome",
    ["event", "outcome"],
    registry=REGISTRY,
)


def tracer() -> trace.Tracer:
    return trace.get_tracer(INSTRUMENTATION)


def route_template(scope: dict[str, Any]) -> str:
    """The route TEMPLATE (``/api/v1/boards/{board_id}``), never the real path.

    FastAPI 0.14x includes routers lazily: ``scope["route"]`` is the original route,
    without prefixes; the full path sits in the effective route context.
    """
    effective = (scope.get("fastapi") or {}).get("effective_route_context")
    path = getattr(effective, "path", None) or getattr(scope.get("route"), "path", None)
    return path or "unrouted"


class RequestMetrics(BaseHTTPMiddleware):
    """Counts and times every request; sets route, product and status on the span."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = route_template(request.scope)
            if request.url.path not in EXCLUDED_URLS.split(","):
                HTTP_REQUESTS.labels(route, request.method, str(status)).inc()
                HTTP_LATENCY.labels(route, request.method).observe(time.perf_counter() - started)
            span = trace.get_current_span()
            if span.get_span_context().is_valid:
                span.set_attribute("http.route", route)
                product = request.headers.get("x-produto")
                if product and re.fullmatch(r"[a-z0-9-]{2,40}", product):
                    span.set_attribute("pampufa.product_id", product)


metrics_router = APIRouter(tags=["observability"])


@metrics_router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    token = settings.metrics_token
    if token:
        if request.headers.get("authorization") != f"Bearer {token}":
            raise ApiError("metrics_token", "Invalid metrics token.", 401)
    elif (request.client.host if request.client else "") not in LOOPBACK:
        raise ApiError("metrics_local_only", "Metrics answer only to the local machine.", 403)
    return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------------------
# tracing and logs (OpenTelemetry, exported to the collector)
# ---------------------------------------------------------------------------


class _TraceIds(logging.Filter):
    """Puts ``trace_id``/``span_id`` on every stdlib record (console format uses them)."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = trace.get_current_span().get_span_context()
        record.trace_id = format(context.trace_id, "032x") if context.is_valid else "-"
        record.span_id = format(context.span_id, "016x") if context.is_valid else "-"
        return True


def configure_logging() -> None:
    """Console handler for the ``nagare`` loggers with the trace ids; idempotent."""
    logger = logging.getLogger("nagare")
    if any(getattr(h, "_nagare_console", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler._nagare_console = True  # type: ignore[attr-defined]
    handler.addFilter(_TraceIds())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s %(message)s"
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def install(app: Any) -> None:
    """Install tracing and OTLP logs when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.

    Must run BEFORE the engine is created: the SQLAlchemy instrumentation wraps
    ``create_async_engine``. Idempotent across ``create_app`` calls (the tests build
    several apps in one process): the providers are created once, the FastAPI
    instrumentation is applied to every app.
    """
    global _provider, _log_provider
    configure_logging()
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    if not endpoint:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if _provider is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {"service.name": SERVICE_NAME, "deployment.environment": settings.environment}
        )
        _provider = TracerProvider(resource=resource)
        _provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(_provider)

        _log_provider = LoggerProvider(resource=resource)
        _log_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
        )
        logging.getLogger("nagare").addHandler(
            LoggingHandler(level=logging.INFO, logger_provider=_log_provider)
        )
        # A collector that is down must not become noise on every batch.
        logging.getLogger("opentelemetry").setLevel(logging.ERROR)

        for instrumentor in (HTTPXClientInstrumentor(), SQLAlchemyInstrumentor()):
            if not instrumentor.is_instrumented_by_opentelemetry:
                # skip_dep_check: the instrumentation declares ``sqlalchemy < 2.1`` and
                # would refuse the 2.1 in use; the engine events it wraps are the same.
                instrumentor.instrument(tracer_provider=_provider, skip_dep_check=True)
        log.info("OpenTelemetry on — exporting to %s", endpoint)

    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=_provider, excluded_urls=EXCLUDED_URLS
    )


def shutdown() -> None:
    """Flush what is pending to the collector before the process exits."""
    if _provider is not None:
        _provider.force_flush()
    if _log_provider is not None:
        _log_provider.force_flush()
