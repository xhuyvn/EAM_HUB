"""Test Task and Run API endpoints.

Covers:
- POST /v1/tasks returns HTTP 202 Accepted.
- Re-submitting same tenant + client key returns same task reference (C01).
- Request body carrying tenant_id / principal -> HTTP 422.
- Query parameters carrying tenant_id / principal -> HTTP 422.
- GET /v1/tasks/{id} returns task details for own tenant.
- Cross-tenant GET /v1/tasks/{id} -> HTTP 404 (no existence oracle).
- GET /v1/tasks/{id}/runs returns run list for own tenant.
- Cross-tenant GET /v1/tasks/{id}/runs -> HTTP 404 (no existence oracle).
- SC-04 canary verification: canary never appears in logs or error responses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.api.app import app
from eap.api.dependencies import get_db


@pytest.fixture
def client(db_engine: Engine):
    """Async test client with get_db dependency overridden to use db_engine."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        yield AsyncClient(transport=transport, base_url="http://testserver")
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_api_create_task_and_c01_idempotency(client: AsyncClient) -> None:
    """POST /v1/tasks returns 202; second submission returns identical task reference."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "API idempotency test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }
    headers = {"X-Tenant-Id": tenant_id}

    # First submission
    resp1 = await client.post("/v1/tasks", json=payload, headers=headers)
    assert resp1.status_code == 202
    data1 = resp1.json()
    assert "task_id" in data1
    assert "task_intent_key" in data1
    assert data1["agent_id"] == "agent-ppm"
    assert data1["state"] == "ready"

    # Second submission with same tenant and client key
    resp2 = await client.post("/v1/tasks", json=payload, headers=headers)
    assert resp2.status_code == 202
    data2 = resp2.json()
    assert data2["task_id"] == data1["task_id"]
    assert data2["task_intent_key"] == data1["task_intent_key"]


@pytest.mark.asyncio
async def test_api_forbidden_body_fields_return_422(client: AsyncClient) -> None:
    """Body declaring tenant_id, tenant, or principal must return 422."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    headers = {"X-Tenant-Id": tenant_id}

    forbidden_variants = [
        {"tenant_id": "attacker-tenant"},
        {"tenant": "attacker-tenant"},
        {"principal": "attacker-user"},
        {"principal_id": "attacker-user"},
    ]

    for forbidden in forbidden_variants:
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "agent_id": "agent-ppm",
            "task_type": "code.review",
            "objective": "Testing forbidden field",
            "input_refs": ["in-1"],
            "resource_refs": ["res-1"],
            "idempotency_key": f"key-{uuid.uuid4().hex[:12]}",
            **forbidden,
        }
        resp = await client.post("/v1/tasks", json=payload, headers=headers)
        assert resp.status_code == 422, f"Expected 422 for forbidden payload {forbidden}, got {resp.status_code}"
        assert "forbidden" in resp.text.lower()


@pytest.mark.asyncio
async def test_api_forbidden_query_fields_return_422(client: AsyncClient) -> None:
    """Query parameter carrying tenant_id or principal must return 422."""
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    headers = {"X-Tenant-Id": tenant_id}

    # Query param on POST /v1/tasks
    resp1 = await client.post("/v1/tasks?tenant_id=injected", json={}, headers=headers)
    assert resp1.status_code == 422
    assert "forbidden" in resp1.text.lower()

    # Query param on GET /v1/tasks/{id}
    resp2 = await client.get("/v1/tasks/task-test?tenant=injected", headers=headers)
    assert resp2.status_code == 422

    # Query param on GET /v1/tasks/{id}/runs
    resp3 = await client.get("/v1/tasks/task-test/runs?principal_id=injected", headers=headers)
    assert resp3.status_code == 422


@pytest.mark.asyncio
async def test_api_get_task_and_cross_tenant_isolation(client: AsyncClient) -> None:
    """GET /v1/tasks/{id} returns 200 for own tenant, 404 for different tenant (no existence oracle)."""
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "Cross-tenant isolation test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }

    # Create under tenant_a
    resp_create = await client.post("/v1/tasks", json=payload, headers={"X-Tenant-Id": tenant_a})
    assert resp_create.status_code == 202
    task_id = resp_create.json()["task_id"]

    # Own tenant GET returns 200
    resp_get_a = await client.get(f"/v1/tasks/{task_id}", headers={"X-Tenant-Id": tenant_a})
    assert resp_get_a.status_code == 200
    assert resp_get_a.json()["task_id"] == task_id

    # Cross-tenant GET returns 404 (not 403, preventing existence discovery)
    resp_get_b = await client.get(f"/v1/tasks/{task_id}", headers={"X-Tenant-Id": tenant_b})
    assert resp_get_b.status_code == 404
    assert f"'{task_id}' not found" in resp_get_b.json()["detail"]


@pytest.mark.asyncio
async def test_api_get_task_runs_and_cross_tenant_isolation(client: AsyncClient) -> None:
    """GET /v1/tasks/{id}/runs returns 200 for own tenant, 404 for different tenant."""
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "Cross-tenant runs test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }

    resp_create = await client.post("/v1/tasks", json=payload, headers={"X-Tenant-Id": tenant_a})
    task_id = resp_create.json()["task_id"]

    # Own tenant returns 200 and run list
    resp_runs_a = await client.get(f"/v1/tasks/{task_id}/runs", headers={"X-Tenant-Id": tenant_a})
    assert resp_runs_a.status_code == 200
    runs_data = resp_runs_a.json()
    assert runs_data["task_id"] == task_id
    assert len(runs_data["runs"]) >= 1
    assert runs_data["runs"][0]["attempt"] == 1
    assert runs_data["runs"][0]["state"] == "queued"

    # Cross tenant returns 404
    resp_runs_b = await client.get(f"/v1/tasks/{task_id}/runs", headers={"X-Tenant-Id": tenant_b})
    assert resp_runs_b.status_code == 404


@pytest.mark.asyncio
async def test_sc04_secret_canary_never_in_logs_or_responses(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-04: Canary string in request or headers never leaks into server logs or error responses."""
    canary = f"EAP-CANARY-{uuid.uuid4()}"
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": f"Safe task with canary token {canary}",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": f"key-{uuid.uuid4().hex[:12]}",
        "tenant_id": f"forbidden-tenant-{canary}",  # trigger 422 error
    }

    with caplog.at_level(logging.DEBUG):
        resp = await client.post(
            "/v1/tasks",
            json=payload,
            headers={"X-Tenant-Id": tenant_id, "X-Canary-Token": canary},
        )

    assert resp.status_code == 422
    # Ensure canary is NOT in the error message
    assert canary not in resp.text
    # Ensure canary is NOT logged
    assert canary not in caplog.text
