"""Observability package: structured JSON logging, credential redaction, and local telemetry."""

from eap.observability.logging import (
    JsonFormatter,
    RedactedLastResortHandler,
    RedactionFilter,
    apply_redaction_filter,
    clear_log_context,
    configure_logging,
    disabled_redaction,
    log_context,
    principal_id_var,
    redact_structure,
    redact_text,
    register_secret,
    request_id_var,
    run_id_var,
    set_log_context,
    task_id_var,
    tenant_id_var,
    unregister_secret,
)
from eap.observability.middleware import RequestLoggingMiddleware
from eap.observability.telemetry import (
    InsecureTelemetryError,
    validate_telemetry_config,
)

__all__ = [
    "InsecureTelemetryError",
    "JsonFormatter",
    "RedactedLastResortHandler",
    "RedactionFilter",
    "RequestLoggingMiddleware",
    "apply_redaction_filter",
    "clear_log_context",
    "configure_logging",
    "disabled_redaction",
    "log_context",
    "principal_id_var",
    "redact_structure",
    "redact_text",
    "register_secret",
    "request_id_var",
    "run_id_var",
    "set_log_context",
    "task_id_var",
    "tenant_id_var",
    "unregister_secret",
    "validate_telemetry_config",
]
