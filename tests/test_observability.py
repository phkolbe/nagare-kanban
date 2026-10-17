"""Observability (OBS-I): the /metrics the platform's collector scrapes, and the
tracing hooks that must stay harmless when no collector is configured."""

from __future__ import annotations

import logging

from backend.app import observability
from backend.app.config import settings


def test_metrics_answer_to_the_loopback_in_the_prometheus_format(client) -> None:
    client.get("/api/v1/health")
    client.get("/api/v1/boards")  # 401, counted all the same, by route template
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "nagare_http_requests_total{" in body
    assert 'route="/api/v1/boards"' in body
    assert "nagare_http_latency_seconds_bucket" in body
    assert "nagare_webhook_outbox_pending" in body
    # Probes do not pollute the series and the real path never appears.
    assert 'route="/api/v1/health"' not in body
    assert 'route="/metrics"' not in body


def test_metrics_require_the_token_when_one_is_set(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "metrics_token", "segredo-do-coletor")
    refused = client.get("/metrics")
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "metrics_token"
    accepted = client.get("/metrics", headers={"Authorization": "Bearer segredo-do-coletor"})
    assert accepted.status_code == 200


def test_route_template_falls_back_when_unrouted() -> None:
    assert observability.route_template({}) == "unrouted"


def test_every_record_gets_trace_ids_for_the_console_format() -> None:
    observability.configure_logging()
    logger = logging.getLogger("nagare")
    assert logger.propagate is False
    [handler] = [h for h in logger.handlers if getattr(h, "_nagare_console", False)]
    record = logging.LogRecord("nagare.test", logging.INFO, "", 0, "hello", (), None)
    assert all(f.filter(record) for f in handler.filters)
    assert record.trace_id == "-" and record.span_id == "-"  # type: ignore[attr-defined]
    assert handler.format(record).endswith("trace_id=- hello")


def test_without_a_collector_nothing_is_installed(app) -> None:
    # The test apps run without OTEL_EXPORTER_OTLP_ENDPOINT: no provider, no-op tracer.
    assert observability._provider is None
    span = observability.tracer().start_span("x")
    assert not span.get_span_context().is_valid
