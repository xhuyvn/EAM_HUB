"""Test Task and Run API endpoints.

Covers:
- POST /v1/tasks returns HTTP 202 Accepted.
- Re-submitting same tenant + client key returns same task reference (C01).
- Request body carrying tenant_id / principal -> HTTP 422.
- Query parameters carrying tenant_id / principal -> HTTP 422.
- Headers carrying X-Tenant-Id, X-Principal-Id, or any tenant/principal header -> HTTP 422 (P2-1).
- get_principal resolves principal server-side only; tests simulate multiple tenants via
  app.dependency_overrides[get_principal], never with headers (P2-1).
- Tenant-B principal GET tenant-A task or runs -> HTTP 404 (no existence oracle).
- Grep/AST check: no request.headers access in eap.api except the forbidden-field check.
- SC-04 canary verification: canary never appears in logs or error responses.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal


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
        app.dependency_overrides.pop(get_principal, None)


@pytest.mark.asyncio
async def test_api_create_task_and_c01_idempotency(client: AsyncClient) -> None:
    """POST /v1/tasks returns 202; second submission returns identical task reference."""
    tenant_id = "tenant-c01-simulated"
    client_key = "idemp-c01-unique-key-123"

    # Simulate principal via dependency override, never with headers (P2-1)
    app.dependency_overrides[get_principal] = lambda: Principal(
        tenant_id=tenant_id,
        principal_id="principal-test",
    )

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "API idempotency test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }

    # First submission (no client tenant headers)
    resp1 = await client.post("/v1/tasks", json=payload)
    assert resp1.status_code == 202
    data1 = resp1.json()
    assert "task_id" in data1
    assert "task_intent_key" in data1
    assert data1["agent_id"] == "agent-ppm"
    assert data1["state"] == "ready"

    # Second submission with same tenant and client key
    resp2 = await client.post("/v1/tasks", json=payload)
    assert resp2.status_code == 202
    data2 = resp2.json()
    assert data2["task_id"] == data1["task_id"]
    assert data2["task_intent_key"] == data1["task_intent_key"]


@pytest.mark.asyncio
async def test_api_forged_tenant_headers_rejected_422(client: AsyncClient) -> None:
    """P2-1: Any request carrying X-Tenant-Id, X-Principal-Id, or tenant/principal headers returns 422."""
    forged_headers = [
        {"X-Tenant-Id": "tenant-attacker"},
        {"x-tenant-id": "tenant-attacker"},
        {"X-Principal-Id": "principal-attacker"},
        {"x-principal-id": "principal-attacker"},
        {"Tenant-Id": "tenant-attacker"},
        {"X-Custom-Tenant": "tenant-attacker"},
        {"Principal": "admin"},
    ]

    for headers in forged_headers:
        # POST /v1/tasks
        resp_post = await client.post(
            "/v1/tasks",
            json={
                "contract_version": "1.0",
                "agent_id": "agent-ppm",
                "task_type": "code.review",
                "objective": "Forged header test",
                "input_refs": ["in-1"],
                "resource_refs": ["res-1"],
                "idempotency_key": "idemp-forged-header-key",
            },
            headers=headers,
        )
        assert resp_post.status_code == 422, f"Expected 422 for header {headers}, got {resp_post.status_code}"
        assert "forbidden" in resp_post.text.lower()
        assert "task_id" not in resp_post.json()

        # GET /v1/tasks/task-123
        resp_get = await client.get("/v1/tasks/task-123", headers=headers)
        assert resp_get.status_code == 422, f"Expected 422 for header {headers}, got {resp_get.status_code}"
        assert "forbidden" in resp_get.text.lower()

        # GET /v1/tasks/task-123/runs
        resp_runs = await client.get("/v1/tasks/task-123/runs", headers=headers)
        assert resp_runs.status_code == 422, f"Expected 422 for header {headers}, got {resp_runs.status_code}"
        assert "forbidden" in resp_runs.text.lower()


@pytest.mark.asyncio
async def test_api_forbidden_body_fields_return_422(client: AsyncClient) -> None:
    """Body declaring tenant_id, tenant, or principal must return 422."""
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
            "idempotency_key": "idemp-body-forbidden-key",
            **forbidden,
        }
        resp = await client.post("/v1/tasks", json=payload)
        assert resp.status_code == 422, f"Expected 422 for forbidden payload {forbidden}, got {resp.status_code}"
        assert "forbidden" in resp.text.lower()


@pytest.mark.asyncio
async def test_api_forbidden_query_fields_return_422(client: AsyncClient) -> None:
    """Query parameter carrying tenant_id or principal must return 422."""
    # Query param on POST /v1/tasks
    resp1 = await client.post("/v1/tasks?tenant_id=injected", json={})
    assert resp1.status_code == 422
    assert "forbidden" in resp1.text.lower()

    # Query param on GET /v1/tasks/{id}
    resp2 = await client.get("/v1/tasks/task-test?tenant=injected")
    assert resp2.status_code == 422

    # Query param on GET /v1/tasks/{id}/runs
    resp3 = await client.get("/v1/tasks/task-test/runs?principal_id=injected")
    assert resp3.status_code == 422


@pytest.mark.asyncio
async def test_api_tenant_b_principal_override_get_tenant_a_task_returns_404(client: AsyncClient) -> None:
    """P2-1: Tenant-B principal (via override) GET tenant-A task returns 404 (no existence oracle)."""
    tenant_a = "tenant-alpha-isolation"
    tenant_b = "tenant-beta-isolation"
    client_key = "idemp-isolation-test-key-99"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": "Cross-tenant isolation test",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": client_key,
    }

    # 1. Create task under tenant_a using dependency override
    app.dependency_overrides[get_principal] = lambda: Principal(tenant_id=tenant_a, principal_id="p-alpha")
    resp_create = await client.post("/v1/tasks", json=payload)
    assert resp_create.status_code == 202
    task_id = resp_create.json()["task_id"]

    # 2. Own tenant (tenant_a) GET returns 200
    resp_get_a = await client.get(f"/v1/tasks/{task_id}")
    assert resp_get_a.status_code == 200
    assert resp_get_a.json()["task_id"] == task_id

    # Own tenant runs GET returns 200
    resp_runs_a = await client.get(f"/v1/tasks/{task_id}/runs")
    assert resp_runs_a.status_code == 200
    assert len(resp_runs_a.json()["runs"]) >= 1

    # 3. Switch principal to tenant_b using dependency override
    app.dependency_overrides[get_principal] = lambda: Principal(tenant_id=tenant_b, principal_id="p-beta")

    # Cross-tenant GET returns 404 (not 403, preventing existence discovery)
    resp_get_b = await client.get(f"/v1/tasks/{task_id}")
    assert resp_get_b.status_code == 404
    assert f"'{task_id}' not found" in resp_get_b.json()["detail"]

    # Cross-tenant GET /runs returns 404
    resp_runs_b = await client.get(f"/v1/tasks/{task_id}/runs")
    assert resp_runs_b.status_code == 404
    assert f"'{task_id}' not found" in resp_runs_b.json()["detail"]


def test_api_no_request_headers_access_in_api() -> None:
    """P2-1: Verify static invariant that no request.headers access exists in eap.api except forbidden-field check."""
    api_dir = Path(__file__).resolve().parents[1] / "src" / "eap" / "api"
    py_files = sorted(api_dir.glob("*.py"))
    assert len(py_files) > 0

    allowed_functions = {"check_forbidden_headers_and_query", "check_forbidden_body_fields"}

    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            # Check attribute access of .headers
            if isinstance(node, ast.Attribute) and node.attr == "headers":
                # Must be located in dependencies.py inside allowed functions
                assert py_file.name == "dependencies.py", (
                    f"Forbidden headers access in {py_file.name}:{node.lineno}"
                )
                # Find enclosing function
                enclosing_func = None
                for parent in ast.walk(tree):
                    if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        for child in ast.walk(parent):
                            if child is node:
                                enclosing_func = parent.name
                                break
                        if enclosing_func:
                            break
                assert enclosing_func in allowed_functions, (
                    f"request.headers accessed in unauthorized function {enclosing_func} in {py_file.name}:{node.lineno}"
                )


@pytest.mark.asyncio
async def test_sc04_secret_canary_never_in_logs_or_responses(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SC-04: Canary string in request or body never leaks into server logs or error responses."""
    canary = "EAP-CANARY-SEC-04-TEST-TOKEN"

    payload = {
        "contract_version": "1.0",
        "agent_id": "agent-ppm",
        "task_type": "code.review",
        "objective": f"Safe task with canary token {canary}",
        "input_refs": ["in-1"],
        "resource_refs": ["res-1"],
        "idempotency_key": "idemp-canary-key-12345",
        "tenant_id": f"forbidden-tenant-{canary}",  # trigger 422 error
    }

    with caplog.at_level(logging.DEBUG):
        resp = await client.post("/v1/tasks", json=payload)

    assert resp.status_code == 422
    # Ensure canary is NOT in the error message
    assert canary not in resp.text
    # Ensure canary is NOT logged
    assert canary not in caplog.text
