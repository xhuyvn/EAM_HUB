"""Tests for structured JSON logging, redaction filter, and telemetry validation."""

from __future__ import annotations

import json
import logging
import os

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from eap.api.app import create_app
from eap.config import Settings
from eap.observability.logging import (
    JsonFormatter,
    RedactionFilter,
    clear_log_context,
    log_context,
    redact_structure,
    redact_text,
    register_secret,
    set_log_context,
    unregister_secret,
)
from eap.observability.telemetry import (
    InsecureTelemetryError,
    validate_telemetry_config,
)


def test_json_formatter_keys_and_correlation() -> None:
    """JSON formatter outputs standard keys: timestamp, level, logger, message, correlation IDs."""
    clear_log_context()
    set_log_context(
        tenant_id="tenant-acme",
        principal_id="principal-agent-1",
        task_id="task-100",
        run_id="run-200",
        request_id="req-300",
    )

    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="eap.worker",
        level=logging.INFO,
        pathname="worker.py",
        lineno=42,
        msg="Processing task execution",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert "timestamp" in data
    assert data["level"] == "INFO"
    assert data["logger"] == "eap.worker"
    assert data["message"] == "Processing task execution"
    assert data["tenant_id"] == "tenant-acme"
    assert data["principal_id"] == "principal-agent-1"
    assert data["task_id"] == "task-100"
    assert data["run_id"] == "run-200"
    assert data["request_id"] == "req-300"
    clear_log_context()


def test_log_context_manager() -> None:
    """log_context context manager isolates context variables."""
    clear_log_context()
    with log_context(tenant_id="tenant-scoped", task_id="task-scoped"):
        formatter = JsonFormatter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
        data = json.loads(formatter.format(record))
        assert data["tenant_id"] == "tenant-scoped"
        assert data["task_id"] == "task-scoped"

    # Context should be reverted
    record_after = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    data_after = json.loads(formatter.format(record_after))
    assert data_after["tenant_id"] is None
    assert data_after["task_id"] is None


def test_redaction_dsn_password() -> None:
    """DSN passwords in any scheme are redacted: postgresql://user:pass@ -> postgresql://user:***@."""
    dsn1 = "postgresql://myuser:secretpassword123@127.0.0.1:5432/eap_dev"
    dsn2 = "postgresql+psycopg://eap:pg_secret_pass@10.0.0.1:5432/eap_test"
    dsn3 = "redis://:redis_secret_pass@localhost:6379/0"

    redacted1 = redact_text(dsn1)
    redacted2 = redact_text(dsn2)
    redacted3 = redact_text(dsn3)

    assert "secretpassword123" not in redacted1
    assert "postgresql://myuser:***@127.0.0.1:5432/eap_dev" == redacted1

    assert "pg_secret_pass" not in redacted2
    assert "postgresql+psycopg://eap:***@10.0.0.1:5432/eap_test" == redacted2

    assert "redis_secret_pass" not in redacted3
    assert "redis://:***@localhost:6379/0" == redacted3


def test_redaction_authorization_and_bearer_tokens() -> None:
    """Authorization headers and Bearer tokens are redacted."""
    raw1 = "Authorization: Bearer secret_bearer_token_xyz_123"
    raw2 = "Calling endpoint with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
    raw3 = "Cookie: session=secret_session_token_999; other=1"

    assert "secret_bearer_token_xyz_123" not in redact_text(raw1)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redact_text(raw2)
    assert "secret_session_token_999" not in redact_text(raw3)


def test_redaction_sensitive_keys_in_json_and_kv() -> None:
    """Keys matching (?i)(password|secret|token|api_key|authorization|cookie) are redacted."""
    json_text = json.dumps({
        "user": "alice",
        "api_key": "secret_api_key_456",
        "nested": {
            "token": "tok_abcdef_789",
            "password": "super_secret_pw",
        },
    })
    redacted = redact_text(json_text)
    assert "secret_api_key_456" not in redacted
    assert "tok_abcdef_789" not in redacted
    assert "super_secret_pw" not in redacted

    url_query = "https://example.com/api?api_key=secret_key_111&token=tok_222&user=bob"
    redacted_url = redact_text(url_query)
    assert "secret_key_111" not in redacted_url
    assert "tok_222" not in redacted_url
    assert "user=bob" in redacted_url


def test_redact_structure_dict_and_secretstr() -> None:
    """Dicts and SecretStr values are recursively redacted."""
    data = {
        "safe": "hello",
        "password": "raw_password",
        "token_hash": "sha256:1234567890abcdef",
        "secret_val": SecretStr("my_secret_str"),
        "nested": {
            "api_key": "deep_key",
        },
    }
    redacted = redact_structure(data)
    assert redacted["safe"] == "hello"
    assert redacted["password"] == "***"
    assert redacted["token_hash"] == "***"
    assert redacted["secret_val"] == "***"
    assert redacted["nested"]["api_key"] == "***"


def test_redaction_exceptions_and_traceback() -> None:
    """Exceptions and tracebacks have secret values redacted."""
    secret = "leak_this_secret_in_exception_12345"
    register_secret(secret)
    try:
        try:
            raise ValueError(f"Connection failed with secret: {secret}")
        except ValueError as exc:
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=10,
                msg="An error occurred: %s",
                args=(exc,),
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            rf = RedactionFilter()
            rf.filter(record)

            assert secret not in str(record.msg)
            assert secret not in (record.exc_text or "")
    finally:
        unregister_secret(secret)


def test_telemetry_validation_local_only() -> None:
    """EAP_TELEMETRY=local_only permits local loopback but forbids remote off-host exporters."""
    # Local loopback succeeds
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://127.0.0.1:4318"
    validate_telemetry_config()

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
    validate_telemetry_config()

    # Remote off-host exporter raises InsecureTelemetryError
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://api.honeycomb.io"
    with pytest.raises(InsecureTelemetryError, match="forbids off-host telemetry exporter"):
        validate_telemetry_config()

    del os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]

    # Remote exporter type raises InsecureTelemetryError
    os.environ["OTEL_TRACES_EXPORTER"] = "zipkin"
    with pytest.raises(InsecureTelemetryError, match="forbids remote exporter 'zipkin'"):
        validate_telemetry_config()

    del os.environ["OTEL_TRACES_EXPORTER"]


@pytest.mark.asyncio
async def test_request_logging_middleware_invariants(caplog: pytest.LogCaptureFixture) -> None:
    """Request logging middleware logs method, path, status, duration, request_id; never bodies or headers."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_ENV="test",
        EAP_BIND="127.0.0.1:8000",
        EAP_TELEMETRY="local_only",
    )
    app = create_app(settings)

    custom_req_id = "req-custom-header-12345"
    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO):
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            resp = await client.get("/healthz", headers={"X-Request-Id": custom_req_id, "X-Sensitive-Header": "do-not-log-me"})

    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == custom_req_id

    # Check request log in caplog
    req_logs = [r for r in caplog.records if r.name == "eap.api.request"]
    assert len(req_logs) >= 1
    record = req_logs[0]

    assert getattr(record, "method", None) == "GET"
    assert getattr(record, "path", None) == "/healthz"
    assert getattr(record, "status", None) == 200
    assert hasattr(record, "duration_ms")
    assert getattr(record, "request_id", None) == custom_req_id

    log_text = caplog.text
    # Invariant: headers and bodies must NEVER be in log
    assert "do-not-log-me" not in log_text
    assert "redis_enabled" not in log_text  # Body content must not be logged
