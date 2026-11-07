"""Telemetry configuration and egress validation (ARCH-15, SC-04)."""

from __future__ import annotations

import os
import urllib.parse
from typing import TYPE_CHECKING

from eap.config import is_loopback_host

if TYPE_CHECKING:
    from eap.config import Settings


class InsecureTelemetryError(ValueError):
    """Raised when a remote exporter is configured while EAP_TELEMETRY=local_only."""


def _is_local_endpoint(endpoint: str) -> bool:
    """Check if an endpoint URL or host:port points to a local loopback interface."""
    endpoint = endpoint.strip()
    if not endpoint:
        return True

    # Parse URL scheme and netloc if present
    if "://" in endpoint:
        parsed = urllib.parse.urlparse(endpoint)
        host = parsed.hostname or ""
    elif ":" in endpoint:
        host = endpoint.split(":", 1)[0]
    else:
        host = endpoint

    return is_loopback_host(host)


def validate_telemetry_config(settings: Settings | None = None) -> None:
    """Verify telemetry configuration compliance with platform egress policy.

    When EAP_TELEMETRY=local_only:
    - No exporter may send data off-host.
    - Any configured remote/off-host exporter raises InsecureTelemetryError.
    - Traces and metrics (if configured) must point to local loopback only.
    """
    telemetry_mode = "local_only"
    if settings is not None:
        telemetry_mode = getattr(settings, "eap_telemetry", "local_only")
    else:
        telemetry_mode = os.environ.get("EAP_TELEMETRY", "local_only")

    if telemetry_mode != "local_only":
        return

    # Check common OpenTelemetry and platform telemetry environment variables
    checked_endpoint_vars = [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "EAP_TELEMETRY_ENDPOINT",
    ]

    for var in checked_endpoint_vars:
        val = os.environ.get(var)
        if val and not _is_local_endpoint(val):
            raise InsecureTelemetryError(
                f"EAP_TELEMETRY=local_only forbids off-host telemetry exporter "
                f"({var}='{val}'). Traces must stay local (loopback only)."
            )

    # Check exporter type variables
    checked_exporter_vars = [
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "EAP_TELEMETRY_EXPORTER",
    ]

    for var in checked_exporter_vars:
        val = (os.environ.get(var) or "").strip().lower()
        if not val or val in ("none", "console", "in_memory", "memory"):
            continue
        if val in ("remote", "http", "zipkin", "jaeger"):
            raise InsecureTelemetryError(
                f"EAP_TELEMETRY=local_only forbids remote exporter '{val}' "
                f"configured in {var}. Traces must stay local."
            )
        if val == "otlp":
            # For OTLP exporter, ensure default or configured endpoint is strictly local
            endpoint = (
                os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
                or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
                or "http://localhost:4318"
            )
            if not _is_local_endpoint(endpoint):
                raise InsecureTelemetryError(
                    f"EAP_TELEMETRY=local_only forbids remote OTLP exporter endpoint '{endpoint}'. "
                    f"Traces must stay local (loopback only)."
                )
