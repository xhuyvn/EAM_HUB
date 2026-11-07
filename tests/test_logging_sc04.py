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
import json
import logging
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
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
