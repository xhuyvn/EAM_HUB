"""SC-04 secret canary and credential redaction verification (DG1-C4, SC-04).

Flows driven:
1. Create task with a valid bearer token (POST /v1/tasks -> 202).
2. Failed auth with invalid/canary token (GET /v1/tasks/... -> 401).
3. DB error with the DSN and database password in the exception (500).
4. An unhandled exception carrying a token and canary (500).

Asserted across all flows:
- The canary (EAP_SECRET_CANARY=EAP-CANARY-<uuid>) appears in NO log record.
- The bearer token appears in NO log record.
- The token hash appears in NO log record.
- The DB password appears in NO log record.
- Verified in BOTH caplog AND a physical FileHandler on disk.
- Verified in NO HTTP response body.
- Verified in NO outbox table payload in the database.

Mutation test:
- Proves that temporarily disabling the redaction filter causes the SC-04 test to fail.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import logging.config
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
import uvicorn.config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from eap.api.app import create_app
from eap.api.dependencies import get_db, get_settings
from eap.config import Settings
from eap.db import outbox
from eap.observability.logging import (
    JsonFormatter,
    apply_redaction_filter,
    configure_logging,
    disabled_redaction,
    register_secret,
)


@pytest.mark.asyncio
async def test_sc04_canary_and_credential_redaction_across_all_flows(
    db_engine: Engine,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-04: Drive real flows and assert no secret leaks into logs, file handler, responses, or outbox."""
    # 1. Generate unique secret canary, tokens, and credentials
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    valid_token = f"eap-token-valid-{uuid.uuid4().hex}"
    invalid_token = f"EAP-CANARY-FAIL-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(valid_token.encode("utf-8")).hexdigest()
    db_password = f"secret-pg-pass-{uuid.uuid4().hex[:12]}"
    test_dsn = f"postgresql+psycopg://eap:{db_password}@127.0.0.1:5432/eap_test"

    # Register secrets in filter
    register_secret(canary)
    register_secret(valid_token)
    register_secret(token_hash)
    register_secret(db_password)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_ENV="test",
        EAP_BIND="127.0.0.1:8000",
        EAP_TELEMETRY="local_only",
        EAP_SECRET_CANARY=SecretStr(canary),
        POSTGRES_PASSWORD=SecretStr(db_password),
        DATABASE_URL=SecretStr(test_dsn),
        EAP_OWNER_TENANT_ID="tenant-sc04",
        EAP_OWNER_PRINCIPAL_ID="principal-sc04",
        EAP_OWNER_TOKEN_HASH=SecretStr(token_hash),
    )

    # 2. Configure a physical FileHandler on disk
    log_file = tmp_path / "sc04_audit.log"
    file_handler = logging.FileHandler(str(log_file), mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    apply_redaction_filter(file_handler)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    apply_redaction_filter(caplog.handler)

    # 3. Create test app with real DB session and mock error routes
    test_app = create_app(settings)

    # Add real exception endpoints for flow 3 and 4
    @test_app.get("/_test/db-error")
    async def route_db_error():
        raise OperationalError(
            f"connection to server at '127.0.0.1', port 5432 failed: FATAL: password authentication failed for user 'eap' in {test_dsn}",
            params=None,
            orig=Exception(f"DSN error with password: {db_password}"),
        )

    @test_app.get("/_test/token-error")
    async def route_token_error():
        raise RuntimeError(
            f"External service failed when using token: {valid_token} and canary: {canary}"
        )

    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=test_app)
    response_bodies: list[str] = []

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        with caplog.at_level(logging.DEBUG):
            # Flow 1: Create task with valid bearer token
            task_payload = {
                "contract_version": "1.0",
                "agent_id": "agent-sc04",
                "task_type": "ppm.scan",
                "objective": "SC-04 real task flow",
                "input_refs": ["input-1"],
                "resource_refs": ["res-1"],
                "idempotency_key": f"idemp-sc04-{uuid.uuid4()}",
            }
            resp1 = await client.post(
                "/v1/tasks",
                headers={"Authorization": f"Bearer {valid_token}"},
                json=task_payload,
            )
            assert resp1.status_code == 202
            response_bodies.append(resp1.text)

            # Flow 2: Failed auth with invalid/canary token
            resp2 = await client.get(
                "/v1/tasks/task-nonexistent",
                headers={"Authorization": f"Bearer {invalid_token}"},
            )
            assert resp2.status_code == 401
            response_bodies.append(resp2.text)

            # Flow 3: DB error with DSN in the exception
            resp3 = await client.get(
                "/_test/db-error",
                headers={"Authorization": f"Bearer {valid_token}"},
            )
            assert resp3.status_code == 500
            response_bodies.append(resp3.text)

            # Flow 4: Exception carrying a token and canary
            resp4 = await client.get(
                "/_test/token-error",
                headers={"Authorization": f"Bearer {valid_token}"},
            )
            assert resp4.status_code == 500
            response_bodies.append(resp4.text)

    # Flush and close file handler
    file_handler.flush()
    file_handler.close()
    root_logger.removeHandler(file_handler)

    # 4. Assertions across all secrets
    secrets_to_check = [canary, valid_token, token_hash, db_password, invalid_token]
    file_content = log_file.read_text(encoding="utf-8")
    caplog_content = caplog.text

    # (a) No secret in caplog
    for s in secrets_to_check:
        assert s not in caplog_content, f"SC-04 VIOLATION: Secret '{s}' leaked into caplog!"

    # (b) No secret in file handler log
    for s in secrets_to_check:
        assert s not in file_content, f"SC-04 VIOLATION: Secret '{s}' leaked into file handler log!"

    # (c) No secret in any response body
    for resp_body in response_bodies:
        for s in secrets_to_check:
            assert s not in resp_body, f"SC-04 VIOLATION: Secret '{s}' leaked into response body: {resp_body}"

    # (d) No secret in outbox payloads in the database
    with Session(db_engine) as session:
        outbox_rows = session.execute(sa.select(outbox.c.payload)).scalars().all()
        assert len(outbox_rows) >= 1, "Expected at least one outbox record from Flow 1"
        for payload in outbox_rows:
            payload_str = json.dumps(payload) if isinstance(payload, dict) else str(payload)
            for s in secrets_to_check:
                assert s not in payload_str, f"SC-04 VIOLATION: Secret '{s}' leaked into outbox payload: {payload_str}"


def test_mutation_sc04_fails_without_redaction(caplog: pytest.LogCaptureFixture) -> None:
    """Mutation check: temporarily disable the filter and demonstrate that secrets leak and SC-04 fails."""
    canary = f"EAP-CANARY-MUTATION-{uuid.uuid4()}"
    secret_token = f"eap-token-mutation-{uuid.uuid4().hex}"
    secret_db_pass = "mutation-db-pass-999"
    dsn = f"postgresql://eap:{secret_db_pass}@127.0.0.1:5432/eap_dev"

    register_secret(canary)
    register_secret(secret_token)
    register_secret(secret_db_pass)

    logger = logging.getLogger("eap.test.mutation")

    # 1. With redaction enabled (normal mode): secrets are redacted
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        logger.error(
            "Normal error log with token %s, canary %s, dsn %s",
            secret_token,
            canary,
            dsn,
        )
        assert secret_token not in caplog.text, "Secret token should be redacted"
        assert canary not in caplog.text, "Canary should be redacted"
        assert secret_db_pass not in caplog.text, "DB password should be redacted"

    # 2. With redaction disabled (mutation): secrets leak and verification fails
    with disabled_redaction(), caplog.at_level(logging.DEBUG):
        caplog.clear()
        logger.error(
            "Mutated unredacted log with token %s, canary %s, dsn %s",
            secret_token,
            canary,
            dsn,
        )
        # When redaction is disabled, secrets leak directly into caplog!
        assert secret_token in caplog.text, "Mutation check: secret token leaked without redaction"
        assert canary in caplog.text, "Mutation check: canary leaked without redaction"
        assert secret_db_pass in caplog.text, "Mutation check: db password leaked without redaction"


def test_w6_1_uvicorn_dictconfig_preexisting_handlers_and_access_redaction() -> None:
    """W6-1: dictConfig(uvicorn LOGGING_CONFIG) BEFORE create_app().

    Asserts that:
    1. Pre-existing handlers on uvicorn and uvicorn.access receive RedactionFilter.
    2. Logs emitted through uvicorn.error (including exceptions) have secrets redacted.
    3. Logs emitted through uvicorn.access have query strings dropped and secrets redacted.
    4. None of canary, bearer token, or DSN password appears in captured output.
    """
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    bearer_token = f"eap-test-bearer-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(bearer_token.encode("utf-8")).hexdigest()
    dsn_password = f"secret-pg-{uuid.uuid4().hex[:8]}"
    test_dsn = f"postgresql://eap:{dsn_password}@127.0.0.1:5432/eap_test"

    # Step 1: dictConfig BEFORE create_app (simulates real uvicorn entrypoint)
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)

    stream_err = io.StringIO()
    stream_out = io.StringIO()
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_error = logging.getLogger("uvicorn.error")

    assert uvicorn_logger.handlers, "Expected uvicorn logger to have handler from LOGGING_CONFIG"
    assert uvicorn_access.handlers, "Expected uvicorn.access logger to have handler from LOGGING_CONFIG"
    assert isinstance(uvicorn_logger.handlers[0], logging.StreamHandler)
    assert isinstance(uvicorn_access.handlers[0], logging.StreamHandler)
    uvicorn_logger.handlers[0].stream = stream_err
    uvicorn_access.handlers[0].stream = stream_out

    # Step 2: create_app() initializes settings and calls configure_logging()
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_ENV="test",
        EAP_BIND="127.0.0.1:8000",
        EAP_TELEMETRY="local_only",
        EAP_SECRET_CANARY=SecretStr(canary),
        POSTGRES_PASSWORD=SecretStr(dsn_password),
        DATABASE_URL=SecretStr(test_dsn),
        EAP_OWNER_TENANT_ID="tenant-w6",
        EAP_OWNER_PRINCIPAL_ID="principal-w6",
        EAP_OWNER_TOKEN_HASH=SecretStr(token_hash),
    )
    register_secret(canary)
    register_secret(bearer_token)
    register_secret(token_hash)
    register_secret(dsn_password)

    _ = create_app(settings)

    # Step 3: Log secrets and canary through uvicorn.error (including exception)
    uvicorn_error.error(
        "Direct error message with bearer=%s, canary=%s, dsn=%s",
        bearer_token,
        canary,
        test_dsn,
    )
    try:
        raise RuntimeError(
            f"Fatal error with canary {canary}, token {bearer_token}, and dsn {test_dsn}"
        )
    except Exception:  # noqa: BLE001
        uvicorn_error.exception("Unhandled worker exception")

    # Step 4: Log through uvicorn.access with query parameters
    uvicorn_access.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:51234",
        "GET",
        f"/v1/tasks?token={canary}&bearer={bearer_token}&pw={dsn_password}",
        "1.1",
        200,
    )

    err_captured = stream_err.getvalue()
    out_captured = stream_out.getvalue()
    all_output = err_captured + "\n" + out_captured

    # Step 5: Assert secrets are absent and query string was dropped
    assert canary not in all_output, f"Canary leaked in uvicorn output:\n{all_output}"
    assert bearer_token not in all_output, f"Bearer token leaked in uvicorn output:\n{all_output}"
    assert token_hash not in all_output, f"Token hash leaked in uvicorn output:\n{all_output}"
    assert dsn_password not in all_output, f"DSN password leaked in uvicorn output:\n{all_output}"
    assert "?token=" not in all_output, f"Query string leaked in access log:\n{all_output}"

    # Verify messages were indeed outputted (just redacted)
    assert "Direct error message" in err_captured
    assert "Unhandled worker exception" in err_captured
    assert "GET /v1/tasks HTTP/1.1" in out_captured


def test_w6_1_preexisting_handler_on_child_logger_with_propagate_false() -> None:
    """W6-1: Pre-existing handler on a propagate=False child logger is filtered after configure_logging."""
    child_name = f"child.isolated.{uuid.uuid4().hex}"
    child = logging.getLogger(child_name)
    child.propagate = False
    child.handlers.clear()

    # Pre-existing handler attached before configure_logging
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    child.addHandler(handler)

    canary = f"EAP-CANARY-{uuid.uuid4()}"
    secret = "pre-existing-handler-secret-777"
    dsn = "postgresql://user:pass123@localhost/db"

    register_secret(canary)
    register_secret(secret)
    register_secret("pass123")

    configure_logging()

    child.info("Child log with canary=%s secret=%s dsn=%s", canary, secret, dsn)

    output = buffer.getvalue()
    assert canary not in output, f"Canary leaked into pre-existing handler:\n{output}"
    assert secret not in output, f"Secret leaked into pre-existing handler:\n{output}"
    assert "pass123" not in output, f"Password leaked into pre-existing handler:\n{output}"
    assert "canary=***" in output
    assert "secret=***" in output
    assert "postgresql://user:***@localhost/db" in output


def test_w6_2_orphan_logger_fallback_to_last_resort_redacted() -> None:
    """W6-2: orphan logger (no handlers, propagate=False) warning -> stderr contains no secret."""
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    secret_token = f"eap-orphan-secret-{uuid.uuid4().hex}"
    db_pass = "orphan-db-secret-456"

    register_secret(canary)
    register_secret(secret_token)
    register_secret(db_pass)

    configure_logging()

    orphan = logging.getLogger(f"test.orphan.{uuid.uuid4().hex}")
    orphan.propagate = False
    orphan.handlers.clear()

    # Intercept sys.stderr to check what logging.lastResort emits
    stderr_buf = io.StringIO()
    orig_stderr = sys.stderr
    sys.stderr = stderr_buf
    try:
        orphan.warning(
            "Orphan warning: token=%s canary=%s dsn=postgresql://u:%s@host/db",
            secret_token,
            canary,
            db_pass,
        )
    finally:
        sys.stderr = orig_stderr

    output = stderr_buf.getvalue()
    assert canary not in output, f"Canary leaked into stderr via lastResort:\n{output}"
    assert secret_token not in output, f"Secret token leaked into stderr:\n{output}"
    assert db_pass not in output, f"DB pass leaked into stderr:\n{output}"
    assert "token=***" in output
    assert "canary=***" in output
    assert "postgresql://u:***@host/db" in output


def test_w6_1_uvicorn_subprocess_e2e_redaction(tmp_path: Path) -> None:
    """W6-1 E2E: Subprocess running uvicorn on 127.0.0.1 handles request whose handler raises with canary.

    Asserts that:
    1. Canary appears in neither stdout nor stderr of the uvicorn subprocess.
    2. Query string (?token=<canary>) is dropped from access output.
    3. Password in exception / DSN is redacted.
    """
    def find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    port = find_free_port()
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    secret_pw = "supersecret-db-pass-42"

    server_script = tmp_path / "uvicorn_runner.py"
    server_script.write_text(
        f"""
import logging
import sys
import uvicorn
from fastapi import HTTPException
from pydantic import SecretStr
from eap.config import Settings
from eap.api.app import create_app

port = {port}
canary = {canary!r}
secret_pw = {secret_pw!r}

settings = Settings(
    _env_file=None,
    EAP_ENV="test",
    EAP_BIND=f"127.0.0.1:{{port}}",
    EAP_TELEMETRY="local_only",
    EAP_SECRET_CANARY=SecretStr(canary),
    POSTGRES_PASSWORD=SecretStr(secret_pw),
    DATABASE_URL=SecretStr(f"postgresql://eap:{{secret_pw}}@127.0.0.1:5432/eap_test"),
    EAP_OWNER_TENANT_ID="tenant-subp",
    EAP_OWNER_PRINCIPAL_ID="principal-subp",
    EAP_OWNER_TOKEN_HASH=SecretStr("abc123hash"),
)

app = create_app(settings)

@app.get("/trigger-error")
async def trigger_error():
    logging.getLogger("uvicorn.error").error(f"Handler error with canary {{canary}} and secret password {{secret_pw}}")
    try:
        raise RuntimeError(f"Underlying exception with canary {{canary}} and {{secret_pw}}")
    except Exception:
        logging.getLogger("uvicorn.error").exception("Exception in handler")
    raise HTTPException(status_code=500, detail="Redacted failure")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
""",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [sys.executable, "-u", str(server_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Wait for server readiness
        ready = False
        for _ in range(60):
            time.sleep(0.1)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:  # noqa: BLE001, S110
                pass

        assert ready, "Uvicorn subprocess failed to start and respond to /healthz within 6s"

        # Issue request that triggers exception with query string
        url = f"http://127.0.0.1:{port}/trigger-error?token={canary}"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                pass
        except urllib.error.HTTPError as exc:
            # 500 Internal Server Error expected
            assert exc.code == 500

        time.sleep(0.5)
    finally:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()

    all_output = stdout + "\n" + stderr

    # Assertions
    assert canary not in all_output, f"Canary leaked in uvicorn subprocess output:\n{all_output}"
    assert "?token=" not in all_output, f"Query string leaked in uvicorn subprocess output:\n{all_output}"
    assert secret_pw not in all_output, f"Password leaked in uvicorn subprocess output:\n{all_output}"
    assert "trigger-error" in all_output, f"Expected /trigger-error path in output:\n{all_output}"


@pytest.mark.asyncio
async def test_l4_request_logs_carry_tenant_and_principal_after_auth(
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """L4: Request logs must carry tenant_id and principal_id after authentication (not null)."""
    valid_token = f"eap-token-l4-{uuid.uuid4().hex}"
    token_hash = hashlib.sha256(valid_token.encode("utf-8")).hexdigest()

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_ENV="test",
        EAP_BIND="127.0.0.1:8000",
        EAP_OWNER_TENANT_ID="tenant-l4-audit",
        EAP_OWNER_PRINCIPAL_ID="principal-l4-audit",
        EAP_OWNER_TOKEN_HASH=SecretStr(token_hash),
    )

    test_app = create_app(settings)
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_settings] = lambda: settings
    caplog.set_level(logging.INFO, logger="eap.api.request")

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/v1/ppm/sources",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert resp.status_code == 200

    # Inspect records from eap.api.request logger
    req_records = [r for r in caplog.records if r.name == "eap.api.request"]
    assert len(req_records) >= 1
    record = req_records[-1]

    # Verify attributes on LogRecord directly (L4)
    assert getattr(record, "tenant_id", None) == "tenant-l4-audit"
    assert getattr(record, "principal_id", None) == "principal-l4-audit"

    # Verify serialized JSON formatted output
    formatter = JsonFormatter()
    formatted = formatter.format(record)
    entry = json.loads(formatted)
    assert entry.get("tenant_id") == "tenant-l4-audit"
    assert entry.get("principal_id") == "principal-l4-audit"


