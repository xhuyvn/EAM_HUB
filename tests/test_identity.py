"""Tests for P1a-WP5 Bootstrap Identity.

Covers:
- valid token A -> principal A; valid token B -> principal B (different tenant);
  B cannot read A's task (404) through the real dependency (end-to-end with TestClient + DB).
- missing / malformed / wrong token -> 401; response identical for "unknown token" and "wrong token".
- forged tenant headers still 422 even with a valid token.
- token values and hashes never appear in logs, exceptions, repr(settings) or responses (caplog + SC-04 canary).
- settings token hashes loaded from EAP_OWNER_*, EAP_SECOND_*, and EAP_PRINCIPALS_JSON.
- no implicit default principal fallback (401 when no token is supplied).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.api.app import create_app
from eap.api.dependencies import get_db
from eap.config import Settings, get_settings


def _sha256(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()


# Fixed test tokens and hashes
TOKEN_A = "pilot-bearer-token-owner-alpha-secret-123"
HASH_A = _sha256(TOKEN_A)

TOKEN_B = "pilot-bearer-token-second-beta-secret-456"
HASH_B = _sha256(TOKEN_B)

TENANT_A = "tenant-pilot-alpha"
PRINCIPAL_A = "principal-pilot-alpha"

TENANT_B = "tenant-pilot-beta"
PRINCIPAL_B = "principal-pilot-beta"


@pytest.fixture
def identity_settings() -> Settings:
    """Settings configured with owner and second principal token hashes."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_BIND="127.0.0.1:8000",
        REDIS_URL="",
        QDRANT_URL="",
        EAP_OWNER_TENANT_ID=TENANT_A,
        EAP_OWNER_PRINCIPAL_ID=PRINCIPAL_A,
        EAP_OWNER_TOKEN_HASH=SecretStr(HASH_A),
        EAP_SECOND_TENANT_ID=TENANT_B,
        EAP_SECOND_PRINCIPAL_ID=PRINCIPAL_B,
        EAP_SECOND_TOKEN_HASH=SecretStr(f"sha256:{HASH_B}"),
    )


@pytest.fixture
def real_auth_client(db_engine: Engine, identity_settings: Settings):
    """Async test client using REAL get_principal dependency (no principal override)."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def _override_get_settings():
        return identity_settings

    test_app = create_app(identity_settings)
    test_app.dependency_overrides[get_db] = _override_get_db
    test_app.dependency_overrides[get_settings] = _override_get_settings

    transport = ASGITransport(app=test_app)
    yield AsyncClient(transport=transport, base_url="http://testserver")

    test_app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_end_to_end_real_tokens_and_cross_tenant_isolation_404(
    real_auth_client: AsyncClient,
) -> None:
    """End-to-end verification with DB and real dependency resolution.

    - valid token A -> principal A (tenant A)
    - valid token B -> principal B (tenant B)
    - principal B cannot read principal A's task or runs -> 404 Not Found (no existence oracle).
    """
    client_key = f"idemp-real-token-{uuid.uuid4()}"
    task_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "Real token identity test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }

    # 1. Principal A creates a task using real Bearer token A
    resp_create = await real_auth_client.post(
        "/v1/tasks",
        json=task_payload,
        headers={"Authorization": f"Bearer {TOKEN_A}"},
    )
    assert resp_create.status_code == 202, f"Expected 202, got {resp_create.status_code}: {resp_create.text}"
    created_data = resp_create.json()
    task_id = created_data["task_id"]
    assert "task_intent_key" in created_data

    # 2. Principal A can read its own task
    resp_get_a = await real_auth_client.get(
        f"/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {TOKEN_A}"},
    )
    assert resp_get_a.status_code == 200
    assert resp_get_a.json()["task_id"] == task_id

    # Principal A can read its own runs
    resp_runs_a = await real_auth_client.get(
        f"/v1/tasks/{task_id}/runs",
        headers={"Authorization": f"Bearer {TOKEN_A}"},
    )
    assert resp_runs_a.status_code == 200
    assert len(resp_runs_a.json()["runs"]) >= 1

    # 3. Principal B (different tenant) attempts to read Principal A's task -> 404
    resp_get_b = await real_auth_client.get(
        f"/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {TOKEN_B}"},
    )
    assert resp_get_b.status_code == 404, f"Expected 404, got {resp_get_b.status_code}"
    assert f"'{task_id}' not found" in resp_get_b.json()["detail"]

    # Principal B attempts to read Principal A's task runs -> 404
    resp_runs_b = await real_auth_client.get(
        f"/v1/tasks/{task_id}/runs",
        headers={"Authorization": f"Bearer {TOKEN_B}"},
    )
    assert resp_runs_b.status_code == 404, f"Expected 404, got {resp_runs_b.status_code}"
    assert f"'{task_id}' not found" in resp_runs_b.json()["detail"]


@pytest.mark.asyncio
async def test_missing_malformed_and_wrong_tokens_return_identical_401(
    real_auth_client: AsyncClient,
) -> None:
    """Missing, malformed, unknown, and wrong tokens return 401 with identical responses."""
    test_cases: list[tuple[str, dict[str, str]]] = [
        ("missing_header", {}),
        ("empty_bearer", {"Authorization": "Bearer"}),
        ("whitespace_bearer", {"Authorization": "Bearer    "}),
        ("basic_auth", {"Authorization": "Basic dXNlcjpwYXNz"}),
        ("custom_scheme", {"Authorization": "Token secret-123"}),
        ("lowercase_scheme", {"Authorization": f"bearer {TOKEN_A}"}),
        ("unknown_token", {"Authorization": "Bearer unknown-token-random-xyz-999"}),
        ("wrong_token_tampered", {"Authorization": f"Bearer {TOKEN_A[:-1]}X"}),
        ("wrong_token_arbitrary", {"Authorization": "Bearer invalid-pilot-token"}),
    ]

    expected_body = {"detail": "Unauthorized"}
    expected_status = 401

    for label, headers in test_cases:
        resp = await real_auth_client.get("/v1/tasks/task-arbitrary-123", headers=headers)
        assert resp.status_code == expected_status, (
            f"Case '{label}' expected {expected_status}, got {resp.status_code}: {resp.text}"
        )
        assert resp.json() == expected_body, (
            f"Case '{label}' response body differs: got {resp.json()!r}, expected {expected_body!r}"
        )
        assert resp.headers.get("www-authenticate") == "Bearer", (
            f"Case '{label}' missing WWW-Authenticate: Bearer header"
        )


@pytest.mark.asyncio
async def test_forged_tenant_headers_rejected_422_even_with_valid_token(
    real_auth_client: AsyncClient,
) -> None:
    """Forged tenant or principal headers/queries/body return 422 even when a valid token is provided."""
    valid_auth = {"Authorization": f"Bearer {TOKEN_A}"}

    forged_headers_list = [
        {"X-Tenant-Id": "attacker-tenant"},
        {"x-tenant-id": "attacker-tenant"},
        {"X-Principal-Id": "attacker-principal"},
        {"Tenant-Id": "attacker-tenant"},
        {"X-Custom-Tenant": "attacker-tenant"},
        {"Principal": "attacker-user"},
    ]

    for forged_header in forged_headers_list:
        combined_headers = {**valid_auth, **forged_header}

        # GET /v1/tasks/task-123 with valid token + forged header -> 422
        resp_get = await real_auth_client.get(
            "/v1/tasks/task-123",
            headers=combined_headers,
        )
        assert resp_get.status_code == 422, (
            f"Expected 422 for header {forged_header}, got {resp_get.status_code}"
        )
        assert "forbidden" in resp_get.text.lower()

        # POST /v1/tasks with valid token + forged header -> 422
        resp_post = await real_auth_client.post(
            "/v1/tasks",
            json={
                "contract_version": "1.0",
                "agent_id": "agent-ppm",
                "task_type": "code.review",
                "objective": "Forged header test",
                "input_refs": ["in-1"],
                "resource_refs": ["res-1"],
                "idempotency_key": "idemp-forged-test",
            },
            headers=combined_headers,
        )
        assert resp_post.status_code == 422
        assert "forbidden" in resp_post.text.lower()

    # Query param with valid token -> 422
    resp_query = await real_auth_client.get(
        "/v1/tasks/task-123?tenant_id=attacker",
        headers=valid_auth,
    )
    assert resp_query.status_code == 422
    assert "forbidden" in resp_query.text.lower()

    # Forbidden body field with valid token -> 422
    resp_body = await real_auth_client.post(
        "/v1/tasks",
        json={
            "contract_version": "1.0",
            "agent_id": "agent-ppm",
            "task_type": "code.review",
            "objective": "Forged body test",
            "input_refs": ["in-1"],
            "resource_refs": ["res-1"],
            "idempotency_key": "idemp-body-test",
            "tenant_id": "attacker-tenant",
        },
        headers=valid_auth,
    )
    assert resp_body.status_code == 422
    assert "forbidden" in resp_body.text.lower()


def test_token_values_and_hashes_never_leaked_in_settings_or_repr() -> None:
    """SC-04 canary verification: token values and hashes never appear in repr or str of Settings."""
    canary_token = f"EAP-CANARY-TOKEN-{uuid.uuid4()}"
    canary_hash = _sha256(canary_token)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_OWNER_TOKEN_HASH=SecretStr(canary_hash),
        EAP_SECOND_TOKEN_HASH=SecretStr(f"sha256:{canary_hash}"),
        EAP_SECRET_CANARY=SecretStr(canary_token),
    )

    s_repr = repr(settings)
    s_str = str(settings)

    assert canary_token not in s_repr, "Canary token leaked in settings repr"
    assert canary_hash not in s_repr, "Canary token hash leaked in settings repr"
    assert canary_token not in s_str, "Canary token leaked in settings str"
    assert canary_hash not in s_str, "Canary token hash leaked in settings str"


def test_token_values_and_hashes_never_leaked_in_caplog(caplog: pytest.LogCaptureFixture) -> None:
    """SC-04: logging Settings at INFO/DEBUG never logs canary token or token hash."""
    canary_token = f"EAP-CANARY-TOKEN-{uuid.uuid4()}"
    canary_hash = _sha256(canary_token)

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_OWNER_TOKEN_HASH=SecretStr(canary_hash),
        EAP_SECOND_TOKEN_HASH=SecretStr(canary_hash),
    )

    with caplog.at_level(logging.DEBUG):
        logging.info("Settings str: %s", settings)  # noqa: LOG015
        logging.info("Settings repr: %r", settings)  # noqa: LOG015

    assert canary_token not in caplog.text, "Canary token leaked in caplog"
    assert canary_hash not in caplog.text, "Canary token hash leaked in caplog"


@pytest.mark.asyncio
async def test_canary_token_in_auth_header_never_leaked_in_response_or_logs(
    real_auth_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-04: Unauthorized request carrying canary token never echoes token or hash in logs or response."""
    canary_token = f"EAP-CANARY-UNAUTHORIZED-{uuid.uuid4()}"
    canary_hash = _sha256(canary_token)

    with caplog.at_level(logging.DEBUG):
        resp = await real_auth_client.get(
            "/v1/tasks/task-random-123",
            headers={"Authorization": f"Bearer {canary_token}"},
        )

    assert resp.status_code == 401
    assert canary_token not in resp.text, "Canary token leaked in response body"
    assert canary_hash not in resp.text, "Canary token hash leaked in response body"
    assert canary_token not in caplog.text, "Canary token leaked in logs"
    assert canary_hash not in caplog.text, "Canary token hash leaked in logs"


def test_no_implicit_default_principal_fallback() -> None:
    """When no principals are configured, get_principal raises 401."""
    empty_settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_OWNER_TOKEN_HASH=None,
        EAP_SECOND_TOKEN_HASH=None,
    )
    assert len(empty_settings.get_configured_principals()) == 0


def test_configured_principals_from_json() -> None:
    """Principals can be configured via EAP_PRINCIPALS_JSON with SecretStr protection."""
    token_c = "token-pilot-charlie"
    hash_c = _sha256(token_c)

    json_val = f'[{{"tenant_id": "tenant-c", "principal_id": "p-c", "token_hash": "{hash_c}", "roles": ["viewer"]}}]'
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        EAP_OWNER_TOKEN_HASH=None,
        EAP_SECOND_TOKEN_HASH=None,
        EAP_PRINCIPALS_JSON=SecretStr(json_val),
    )

    principals = settings.get_configured_principals()
    assert len(principals) == 1
    p = principals[0]
    assert p.tenant_id == "tenant-c"
    assert p.principal_id == "p-c"
    assert p.token_hash.get_secret_value() == hash_c
    assert p.roles == ["viewer"]

    # Verify secret is not in repr(settings)
    assert hash_c not in repr(settings)
    assert json_val not in repr(settings)
