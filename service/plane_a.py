"""Plane A observability: Prometheus /metrics + optional OTLP traces.

Sampling rate comes from env TRACING_SAMPLING_PROBABILITY (Vault-injected).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def setup_plane_a(app: FastAPI, *, application: str) -> None:
    """Expose /metrics with application= label and optionally enable OTEL."""
    _setup_metrics(app, application)
    _setup_tracing(app, application)


def _setup_metrics(app: FastAPI, application: str) -> None:
    try:
        from prometheus_client import Gauge
        from prometheus_fastapi_instrumentator import Instrumentator, metrics
    except ImportError:
        logger.warning("prometheus deps missing — /metrics not enabled")
        return

    # Always-present series for Grafana discovery once non-JVM query lands.
    Gauge(
        "am_process_up",
        "1 if the process is up",
        labelnames=("application",),
    ).labels(application=application).set(1)

    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/metrics", "/health", "/router/health"],
    ).add(
        metrics.default(
            custom_labels={"application": application},
        )
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus /metrics enabled application=%s", application)


def _setup_tracing(app: FastAPI, service_name: str) -> None:
    endpoint = (os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") or "").strip()
    if not endpoint:
        logger.info("OTEL endpoint unset — tracing disabled")
        return

    try:
        sample = float(os.getenv("TRACING_SAMPLING_PROBABILITY", "1.0"))
    except ValueError:
        sample = 1.0
    sample = max(0.0, min(1.0, sample))

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError:
        logger.warning("opentelemetry deps missing — tracing not enabled")
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "application": service_name,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(sample)),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="metrics,health,router/health")
    logger.info(
        "OTEL tracing enabled service=%s sample=%s endpoint=%s",
        service_name,
        sample,
        endpoint,
    )
