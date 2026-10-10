"""Low-cardinality metrics, optional OTLP traces, and fixed-field JSON events."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "automiq_http_requests_total", "HTTP requests", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "automiq_http_request_duration_seconds", "HTTP request duration", ["method", "route"]
)
WORKFLOW_RUNS = Counter("automiq_workflow_runs_total", "Terminal workflow runs", ["status"])
WORKFLOW_DURATION = Histogram("automiq_workflow_run_duration_seconds", "Run duration")
STEP_DURATION = Histogram("automiq_step_duration_seconds", "Step duration", ["status"])
ACTIVITY_RETRIES = Counter("automiq_activity_retry_total", "Activity retries", ["category"])
WEBHOOK_DEDUPLICATED = Counter("automiq_webhook_deduplicated_total", "Duplicate webhooks")
AGENT_TOKENS = Counter("automiq_agent_tokens_total", "Agent tokens", ["profile", "direction"])
AGENT_COST = Counter("automiq_agent_cost_estimate_microusd_total", "Estimated agent cost")
APPROVAL_WAIT = Histogram("automiq_approval_wait_seconds", "Approval wait duration")

_provider: TracerProvider | None = None


def configure_tracing(service_name: str, endpoint: str) -> None:
    global _provider
    if not endpoint or _provider is not None:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _provider = provider


def close_tracing() -> None:
    if _provider is not None:
        _provider.shutdown()


def event(logger: logging.Logger, name: str, **fields: str | int | float | None) -> None:
    """Call sites supply identifiers and categories, never payloads or credentials."""
    logger.info(
        json.dumps(
            {"time": datetime.now(UTC).isoformat(), "event": name, **fields},
            separators=(",", ":"),
        )
    )


def set_span_ids(span: Any, **fields: str) -> None:
    for key, value in fields.items():
        span.set_attribute(key, value)
