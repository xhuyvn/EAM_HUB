"""Comprehensive functional, contract, security, and failure tests for Agent Inventory (P3a-WP5, ISS-037)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from eap.adapters.mock_provider import MockProvider
from eap.agents.cli import main as agent_cli_main
from eap.agents.service import (
    AgentValidationError,
    AgentVersionImmutableError,
    InlineSecretError,
    derive_agent_health,
    get_agent_detail,
    list_agents,
    register_agent_and_binding,
    revoke_binding,
)
from eap.api.app import create_app
from eap.api.dependencies import get_db
from eap.config import Settings, get_settings
from eap.contracts.digest import sha256_digest
from eap.contracts.models.task_request import TaskRequest
from eap.db import agent_bindings, agents, runs, usage_ledger
from eap.hud.snapshot import AgentInventoryRowBuilder, RowKindBuilder
from eap.run.dispatcher import Dispatcher
from eap.task.service import TaskService

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_AGENT_PATH = REPO_ROOT / "src" / "eap" / "agents" / "fixtures" / "mock_agent.json"
MOCK_BINDING_PATH = REPO_ROOT / "src" / "eap" / "agents" / "fixtures" / "mock_binding.json"

OWNER_A_TOKEN = "wp5-owner-a-secret-token"
ADMIN_B_TOKEN = "wp5-admin-b-secret-token"


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_settings(
    *,
    bind: str = "127.0.0.1:8080",
    allow_real_roots: bool = False,
    owner_tenant: str = "tenant-a",
    second_tenant: str = "tenant-b",
) -> Settings:
    return Settings.model_validate(
        {
            "eap_bind": bind,
            "eap_collector_allow_real_roots": allow_real_roots,
            "eap_owner_tenant_id": owner_tenant,
            "eap_owner_principal_id": "owner-a",
            "hud_cursor_secret": "wp5-test-hud-cursor-hmac-secret-key",
            "local_principals": [
                {
                    "tenant_id": owner_tenant,
                    "principal_id": "owner-a",
                    "token_hash": _token_hash(OWNER_A_TOKEN),
                    "roles": ["owner"],
                },
                {
                    "tenant_id": second_tenant,
                    "principal_id": "admin-b",
                    "token_hash": _token_hash(ADMIN_B_TOKEN),
                    "roles": ["admin"],
                },
            ],
        }
    )


def _make_client(db_engine: Engine, settings: Settings) -> TestClient:
    app = create_app(settings=settings)

    def _override_db() -> Any:
        session = Session(db_engine)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, raise_server_exceptions=False)


def _load_mock_fixtures() -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(MOCK_AGENT_PATH.read_text(encoding="utf-8"))
    binding = json.loads(MOCK_BINDING_PATH.read_text(encoding="utf-8"))
    return manifest, binding


def _make_manifest(
    *,
    agent_id: str = "agent-custom-worker",
    version: str = "1.0.0",
    domain: str = "engineering",
) -> dict[str, Any]:
    return {
        "contract_version": "1.0",
        "agent_id": agent_id,
        "name": f"Agent {agent_id}",
        "version": version,
        "domain": domain,
        "business_owner_ref": "owner-eng-lead",
        "technical_owner_ref": "owner-platform-eng",
        "runtime_kind": "http",
        "task_types": ["code.review"],
        "input_schema_ref": "contracts/task-envelope.schema.json",
        "output_schema_ref": "contracts/result.schema.json",
        "desired_capabilities": {
            "submit": True,
            "status": True,
            "events": True,
            "collect_result": True,
            "cancel": True,
            "resume": False,
            "approve_tool": False,
        },
        "desired_tools": ["git.read"],
        "harness_version": "1.0.0",
        "eval_profile_ref": "eval-eng-smoke-v1",
    }


def _make_binding(
    manifest: dict[str, Any],
    *,
    binding_id: str = "binding-custom-01",
    secret_ref: str | None = "secret:EXAMPLE-worker-token",
    endpoint_ref: str = "https://worker.internal.example/run",
) -> dict[str, Any]:
    return {
        "contract_version": "1.1",
        "binding_id": binding_id,
        "agent_id": manifest["agent_id"],
        "release_version": manifest["version"],
        "release_sha256": sha256_digest(manifest),
        "mode": "managed",
        "endpoint_ref": endpoint_ref,
        "secret_ref": secret_ref,
        "resource_scope_refs": ["project:proj-custom"],
        "certified_capabilities": {
            "submit": True,
            "status": True,
            "events": True,
            "collect_result": True,
            "cancel": True,
            "resume": False,
            "approve_tool": False,
        },
        "capability_evidence_ref": "artifact:eval-report-custom-01",
        "model_profile_ref": "model-default-scoped",
        "grant_refs": ["grant:custom-read"],
        "execution_boundary": "platform_gateway",
        "policy_version": "policy-1.0",
        "fallback_model_profile_refs": [],
        "embedding_profile_ref": None,
        "egress_policy_ref": "egress:internal-only",
        "telemetry_export": "local_only",
    }


def _count_rows(db_engine: Engine, tenant_id: str) -> tuple[int, int]:
    with db_engine.connect() as conn:
        a_count = conn.scalar(
            sa.select(sa.func.count()).select_from(agents).where(agents.c.tenant_id == tenant_id)
        )
        b_count = conn.scalar(
            sa.select(sa.func.count())
            .select_from(agent_bindings)
            .where(agent_bindings.c.tenant_id == tenant_id)
        )
    return int(a_count or 0), int(b_count or 0)


# ---------------------------------------------------------------------------
# 1. Schema and contract validation failure tests
# ---------------------------------------------------------------------------


def test_invalid_manifest_and_binding_rejected_with_field_path_nothing_stored(
    db_engine: Engine,
) -> None:
    tenant = f"tenant-val-{uuid.uuid4().hex[:8]}"

    bad_manifest = _make_manifest()
    bad_manifest["runtime_kind"] = "unknown_runtime"  # violates contracts/agent.schema.json
    with pytest.raises(AgentValidationError) as exc_info:
        register_agent_and_binding(db_engine, tenant_id=tenant, manifest=bad_manifest)
    assert exc_info.value.field_path == "/runtime_kind"
    assert _count_rows(db_engine, tenant) == (0, 0)

    missing_field_manifest = _make_manifest()
    del missing_field_manifest["task_types"]
    with pytest.raises(AgentValidationError) as exc_info2:
        register_agent_and_binding(db_engine, tenant_id=tenant, manifest=missing_field_manifest)
    assert exc_info2.value.field_path == "/task_types"
    assert _count_rows(db_engine, tenant) == (0, 0)

    good_manifest = _make_manifest()
    bad_binding = _make_binding(good_manifest)
    bad_binding["execution_boundary"] = "not_a_boundary"
    with pytest.raises(AgentValidationError) as exc_info3:
        register_agent_and_binding(
            db_engine, tenant_id=tenant, manifest=good_manifest, binding=bad_binding
        )
    assert exc_info3.value.field_path == "/execution_boundary"
    assert _count_rows(db_engine, tenant) == (0, 0)

    # Observe mode advertising control capabilities must be rejected cleanly with nothing stored.
    observe_bad_binding = _make_binding(good_manifest, binding_id="binding-obs-bad")
    observe_bad_binding["mode"] = "observe"
    with pytest.raises(AgentValidationError) as exc_info4:
        register_agent_and_binding(
            db_engine,
            tenant_id=tenant,
            manifest=good_manifest,
            binding=observe_bad_binding,
        )
    assert exc_info4.value.code == "OBSERVE_ADVERTISES_CONTROL"
    assert exc_info4.value.field_path == "/certified_capabilities"
    assert _count_rows(db_engine, tenant) == (0, 0)


# ---------------------------------------------------------------------------
# 2. Inline secret rejection + log redaction verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    [
        ("secret_ref", "sk-live-canary-secret-999999999"),
        ("secret_ref", "password=canary-inline-password-888"),
        ("endpoint_ref", "https://svcuser:canarypass777@api.provider.example/invoke"),
    ],
)
def test_inline_secret_in_ref_fields_rejected_nothing_stored_and_not_logged(
    db_engine: Engine,
    caplog: pytest.LogCaptureFixture,
    field_name: str,
    bad_value: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tenant = f"tenant-secret-{uuid.uuid4().hex[:8]}"
    manifest = _make_manifest(agent_id="agent-secret-test")
    binding = _make_binding(manifest, binding_id="binding-secret-test")
    binding[field_name] = bad_value

    with caplog.at_level(logging.DEBUG), pytest.raises(InlineSecretError) as exc_info:
        register_agent_and_binding(
            db_engine, tenant_id=tenant, manifest=manifest, binding=binding
        )

    assert exc_info.value.code == "INLINE_SECRET_REJECTED"
    assert exc_info.value.field_path == f"/{field_name}"
    assert _count_rows(db_engine, tenant) == (0, 0)

    # Assert no secret material in caplog text, formatted messages, or args.
    assert bad_value not in caplog.text
    for rec in caplog.records:
        assert bad_value not in rec.getMessage()
        assert bad_value not in repr(rec.args)
    if ":" in bad_value and "@" in bad_value:
        assert "canarypass777" not in caplog.text
    if "=" in bad_value:
        assert "canary-inline-password-888" not in caplog.text

    # Also test CLI stderr never contains the secret.
    m_path = tmp_path / "agent.json"
    b_path = tmp_path / "binding.json"
    m_path.write_text(json.dumps(manifest), encoding="utf-8")
    b_path.write_text(json.dumps(binding), encoding="utf-8")
    rc = agent_cli_main(
        ["register", "--manifest", str(m_path), "--binding", str(b_path)],
        conn_or_session=db_engine,
        settings=_make_settings(owner_tenant=tenant),
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "INLINE_SECRET_REJECTED" in captured.err
    assert bad_value not in captured.out
    assert bad_value not in captured.err
    assert _count_rows(db_engine, tenant) == (0, 0)


# ---------------------------------------------------------------------------
# 3. Idempotent registration and version immutability
# ---------------------------------------------------------------------------


def test_reregister_idempotent_and_changed_manifest_same_version_rejected(
    db_engine: Engine,
) -> None:
    tenant = f"tenant-idem-{uuid.uuid4().hex[:8]}"
    manifest, binding = _load_mock_fixtures()

    first = register_agent_and_binding(
        db_engine, tenant_id=tenant, manifest=manifest, binding=binding
    )
    assert first["agent_created"] is True
    assert first["binding_created"] is True
    assert _count_rows(db_engine, tenant) == (1, 1)

    # Identical re-registration is strictly idempotent.
    second = register_agent_and_binding(
        db_engine, tenant_id=tenant, manifest=manifest, binding=binding
    )
    assert second["agent_created"] is False
    assert second["binding_created"] is False
    assert second["manifest_sha256"] == first["manifest_sha256"]
    assert _count_rows(db_engine, tenant) == (1, 1)

    # Modified manifest with same version must be rejected cleanly.
    mutated = dict(manifest)
    mutated["name"] = "Tampered Mock Agent Same Version"
    with pytest.raises(AgentVersionImmutableError) as exc_info:
        register_agent_and_binding(db_engine, tenant_id=tenant, manifest=mutated)
    assert exc_info.value.code == "AGENT_VERSION_IMMUTABLE"
    assert exc_info.value.field_path == "/version"

    inv = list_agents(db_engine, tenant_id=tenant)
    assert len(inv["agents"]) == 1
    assert inv["agents"][0]["name"] == manifest["name"]
    assert inv["agents"][0]["manifest_sha256"] == first["manifest_sha256"]


# ---------------------------------------------------------------------------
# 4. Derived health, lost ACK, missing usage, unattributed runs, attribution
# ---------------------------------------------------------------------------


def test_health_no_data_lost_ack_missing_usage_and_unattributed_runs(
    db_engine: Engine,
) -> None:
    tenant = f"tenant-health-{uuid.uuid4().hex[:8]}"
    manifest, binding = _load_mock_fixtures()
    register_agent_and_binding(db_engine, tenant_id=tenant, manifest=manifest, binding=binding)

    # 4a. Zero runs -> health status must be 'no_data', never 'healthy'.
    health0 = derive_agent_health(db_engine, tenant_id=tenant, agent_id="agent-mock-provider")
    assert health0["status"] == "no_data"
    assert health0["total_runs"] == 0
    assert health0["last_run_at"] is None
    assert health0["last_observed_at"] is None
    assert health0["unknown_outcome_count"] == 0
    assert health0["cost"]["status"] == "unknown"
    assert health0["cost"]["amount_usd"] is None

    # 4b. Pre-existing / unattributed run referencing an unknown/unregistered binding_id
    # must NOT count as healthy or inflate attributed metrics; it is counted as unattributed.
    req_unattr = TaskRequest(
        contract_version="1.0",
        agent_id="agent-mock-provider",
        task_type="code.patch",
        objective="Legacy run prior to binding registration",
        input_refs=["input-legacy"],
        resource_refs=["res-legacy"],
        idempotency_key="idem-unattr-run-001",
    )
    with Session(db_engine) as db:
        TaskService.create(
            db,
            tenant_id=tenant,
            request=req_unattr,
            binding_id="binding-legacy-unregistered",
        )
    inv_unattr = list_agents(db_engine, tenant_id=tenant)
    assert inv_unattr["unattributed_runs_count"] == 1
    agent_entry = inv_unattr["agents"][0]
    assert agent_entry["health"]["status"] == "no_data"
    assert agent_entry["health"]["unattributed_run_count"] == 1

    # 4c. TaskService.create without explicit binding_id auto-attributes active registered binding.
    req_ack = TaskRequest(
        contract_version="1.0",
        agent_id="agent-mock-provider",
        task_type="code.patch",
        objective="Dispatch losing ACK",
        input_refs=["input-ack-1"],
        resource_refs=["res-ack-1"],
        idempotency_key="idem-lost-ack-001",
    )
    with Session(db_engine) as db:
        task_rec = TaskService.create(db, tenant_id=tenant, request=req_ack)
        run_row = (
            db.execute(
                sa.select(runs).where(runs.c.tenant_id == tenant, runs.c.task_id == task_rec.id)
            )
            .mappings()
            .one()
        )
        assert run_row["binding_id"] == binding["binding_id"]
        ack_run_id = str(run_row["run_id"])

    # Trigger real lost ACK through Dispatcher + MockProvider(drop_ack=True).
    mock_drop = MockProvider(drop_ack=True)
    with Session(db_engine) as db:
        disp_res = Dispatcher().dispatch(
            db,
            tenant_id=tenant,
            run_id=ack_run_id,
            worker_id="worker-health-1",
            provider=mock_drop,
        )
        assert disp_res.status == "unknown"

    health_unknown = derive_agent_health(
        db_engine, tenant_id=tenant, agent_id="agent-mock-provider"
    )
    assert health_unknown["total_runs"] == 1
    assert health_unknown["unknown_outcome_count"] == 1
    assert health_unknown["status"] != "healthy"
    assert health_unknown["status"] == "unknown"
    assert health_unknown["counts_by_state"] == {"unknown": 1}
    assert health_unknown["last_run_at"] is not None
    assert health_unknown["last_observed_at"] is not None
    # Missing usage -> explicit unknown cost.
    assert health_unknown["cost"]["status"] == "unknown"
    assert health_unknown["cost"]["amount_usd"] is None
    assert health_unknown["cost"]["missing_usage_runs"] == 1

    # 4d. Healthy completion + recorded usage_ledger -> cost actual, unknown outcome stays counted
    # until a clean agent gets only completed runs. Test clean second agent for 'healthy' transition.
    clean_manifest = _make_manifest(agent_id="agent-clean-cost")
    clean_binding = _make_binding(clean_manifest, binding_id="binding-clean-cost")
    register_agent_and_binding(
        db_engine, tenant_id=tenant, manifest=clean_manifest, binding=clean_binding
    )

    now = dt.datetime.now(dt.UTC)
    req_clean = TaskRequest(
        contract_version="1.0",
        agent_id="agent-clean-cost",
        task_type="code.review",
        objective="Completed clean run with recorded cost",
        input_refs=["input-clean"],
        resource_refs=["res-clean"],
        idempotency_key="idem-clean-cost-001",
    )
    with Session(db_engine) as db:
        t_clean = TaskService.create(db, tenant_id=tenant, request=req_clean)
        r_clean = (
            db.execute(
                sa.select(runs).where(runs.c.tenant_id == tenant, runs.c.task_id == t_clean.id)
            )
            .mappings()
            .one()
        )
        assert r_clean["binding_id"] == clean_binding["binding_id"]
        clean_run_id = str(r_clean["run_id"])
        db.execute(
            runs.update()
            .where(runs.c.tenant_id == tenant, runs.c.run_id == clean_run_id)
            .values(state="succeeded", remote_outcome="known", updated_at=now)
        )
        db.commit()

    # Completed run without usage_ledger yet -> healthy status, cost still explicit unknown!
    health_no_cost = derive_agent_health(db_engine, tenant_id=tenant, agent_id="agent-clean-cost")
    assert health_no_cost["status"] == "healthy"
    assert health_no_cost["cost"]["status"] == "unknown"
    assert health_no_cost["cost"]["amount_usd"] is None

    with db_engine.begin() as conn:
        conn.execute(
            usage_ledger.insert().values(
                tenant_id=tenant,
                provider_usage_id="puid-clean-001",
                run_id=clean_run_id,
                model_profile="model-default-scoped",
                prompt_tokens=200,
                completion_tokens=50,
                cost_estimate=0.0425,
                reported_at=now,
            )
        )

    health_with_cost = derive_agent_health(db_engine, tenant_id=tenant, agent_id="agent-clean-cost")
    assert health_with_cost["status"] == "healthy"
    assert health_with_cost["cost"]["status"] == "actual"
    assert health_with_cost["cost"]["amount_usd"] == 0.0425
    assert health_with_cost["cost"]["known_runs"] == 1
    assert health_with_cost["cost"]["missing_usage_runs"] == 0


# ---------------------------------------------------------------------------
# 5. Revoked binding + API + cross-tenant isolation + HUD RowKindBuilder
# ---------------------------------------------------------------------------


def test_api_cross_tenant_isolation_revoked_binding_and_hud_builder(
    db_engine: Engine,
) -> None:
    tenant_a = f"tenant-api-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-api-b-{uuid.uuid4().hex[:8]}"
    manifest_a, binding_a = _load_mock_fixtures()
    register_agent_and_binding(
        db_engine, tenant_id=tenant_a, manifest=manifest_a, binding=binding_a
    )

    manifest_b = _make_manifest(agent_id="agent-tenant-b-only")
    binding_b = _make_binding(manifest_b, binding_id="binding-tenant-b-only")
    register_agent_and_binding(
        db_engine, tenant_id=tenant_b, manifest=manifest_b, binding=binding_b
    )

    settings = _make_settings(owner_tenant=tenant_a, second_tenant=tenant_b)
    client = _make_client(db_engine, settings)
    headers_a = {"Authorization": f"Bearer {OWNER_A_TOKEN}"}
    headers_b = {"Authorization": f"Bearer {ADMIN_B_TOKEN}"}

    # Missing/forbidden tenant auth checks.
    assert client.get("/v1/agents").status_code == 401
    assert client.get("/v1/agents", headers={**headers_a, "X-Tenant-Id": tenant_b}).status_code == 422
    assert client.get(f"/v1/agents?tenant_id={tenant_b}", headers=headers_a).status_code == 422

    # Tenant A sees only agent-mock-provider with unverified owner & self-declared capability ref.
    resp_a = client.get("/v1/agents", headers=headers_a)
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    assert body_a["tenant_id"] == tenant_a
    assert [a["agent_id"] for a in body_a["agents"]] == ["agent-mock-provider"]
    ag_a = body_a["agents"][0]
    assert ag_a["owner"]["verification_label"] == "unverified owner"
    assert ag_a["active_binding_count"] == 1
    assert ag_a["revoked_binding_count"] == 0
    assert ag_a["bindings"][0]["capability_evidence_level"] == "self_declared"
    assert ag_a["health"]["status"] == "no_data"

    # Detail route works for Tenant A, returns 404 for Tenant B and non-existent IDs.
    detail_a = client.get("/v1/agents/agent-mock-provider", headers=headers_a)
    assert detail_a.status_code == 200
    detail_body = detail_a.json()
    assert detail_body["manifest_summary"]["manifest_sha256"] == ag_a["manifest_sha256"]
    assert detail_body["capability_evidence_refs"][0]["capability_evidence_ref"] == (
        "artifact:mock-provider-p1a-wp4-capabilities"
    )

    assert client.get("/v1/agents/agent-mock-provider", headers=headers_b).status_code == 404
    assert client.get("/v1/agents/non-existent-agent", headers=headers_b).status_code == 404
    assert client.get("/v1/agents/agent-tenant-b-only", headers=headers_a).status_code == 404

    resp_b = client.get("/v1/agents", headers=headers_b)
    assert resp_b.status_code == 200
    assert [a["agent_id"] for a in resp_b.json()["agents"]] == ["agent-tenant-b-only"]

    # Revoke Tenant A binding -> shown as revoked, active_binding_count=0, excluded from dispatch.
    rev = revoke_binding(
        db_engine, tenant_id=tenant_a, binding_id="binding-mock-provider-local"
    )
    assert rev["status"] == "revoked"
    after_rev = client.get("/v1/agents/agent-mock-provider", headers=headers_a).json()
    assert after_rev["active_binding_count"] == 0
    assert after_rev["revoked_binding_count"] == 1
    assert after_rev["bindings"][0]["status"] == "revoked"
    assert after_rev["bindings"][0]["revoked_at"] is not None

    # TaskService falls back instead of using a revoked binding.
    req_fallback = TaskRequest(
        contract_version="1.0",
        agent_id="agent-mock-provider",
        task_type="code.patch",
        objective="After binding revocation",
        input_refs=["in-1"],
        resource_refs=["res-1"],
        idempotency_key=f"idem-after-revoke-{uuid.uuid4().hex[:8]}",
    )
    with Session(db_engine) as db:
        t_fb = TaskService.create(db, tenant_id=tenant_a, request=req_fallback)
        fb_binding = db.scalar(
            sa.select(runs.c.binding_id).where(runs.c.tenant_id == tenant_a, runs.c.task_id == t_fb.id)
        )
        assert fb_binding != "binding-mock-provider-local"

    # HUD RowKindBuilder isolation + HUD snapshot never leaks cross-tenant rows.
    builder = AgentInventoryRowBuilder()
    assert isinstance(builder, RowKindBuilder)
    with db_engine.connect() as conn:
        rows_b = builder.build_rows(conn, tenant_id=tenant_b)
    assert len(rows_b) == 1
    assert rows_b[0]["kind"] == "agent"
    assert rows_b[0]["agent"]["agent_id"] == "agent-tenant-b-only"

    hud_b = client.get("/v1/hud/snapshot", headers=headers_b)
    assert hud_b.status_code == 200
    serialized_hud = json.dumps(hud_b.json())
    assert "agent-mock-provider" not in serialized_hud

    # Explicit check that read-only API exposes no write verbs on /v1/agents.
    assert client.post("/v1/agents", headers=headers_a, json={}).status_code == 405
    assert client.delete("/v1/agents/agent-mock-provider", headers=headers_a).status_code == 405


# ---------------------------------------------------------------------------
# 6. CLI loader (fixtures mock_agent + mock_binding, loopback & owner guardrails)
# ---------------------------------------------------------------------------


def test_cli_register_mock_fixture_and_security_guardrails(
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_tenant = f"tenant-cli-{uuid.uuid4().hex[:8]}"
    rc1 = agent_cli_main(
        [
            "register",
            "--manifest",
            str(MOCK_AGENT_PATH),
            "--binding",
            str(MOCK_BINDING_PATH),
        ],
        conn_or_session=db_engine,
        settings=_make_settings(owner_tenant=cli_tenant),
    )
    assert rc1 == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["ok"] is True
    assert out1["agent_id"] == "agent-mock-provider"
    assert out1["agent_created"] is True
    assert out1["binding_created"] is True

    # Re-running CLI on identical fixtures is idempotent.
    rc2 = agent_cli_main(
        [
            "register",
            "--manifest",
            str(MOCK_AGENT_PATH),
            "--binding",
            str(MOCK_BINDING_PATH),
        ],
        conn_or_session=db_engine,
        settings=_make_settings(owner_tenant=cli_tenant),
    )
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["agent_created"] is False
    assert out2["binding_created"] is False

    # Guardrail 1: non-loopback bind refused.
    rc_bind = agent_cli_main(
        ["register", "--manifest", str(MOCK_AGENT_PATH), "--binding", str(MOCK_BINDING_PATH)],
        conn_or_session=db_engine,
        settings=_make_settings(bind="0.0.0.0:8080", owner_tenant=cli_tenant),
    )
    assert rc_bind == 1
    assert "LOOPBACK_BIND_REQUIRED" in capsys.readouterr().err

    # Guardrail 2: non-fixture mode (real roots allowed) refused.
    rc_real = agent_cli_main(
        ["register", "--manifest", str(MOCK_AGENT_PATH), "--binding", str(MOCK_BINDING_PATH)],
        conn_or_session=db_engine,
        settings=_make_settings(allow_real_roots=True, owner_tenant=cli_tenant),
    )
    assert rc_real == 1
    assert "FIXTURES_ONLY_MODE_REQUIRED" in capsys.readouterr().err

    # Guardrail 3: non-owner principal refused.
    rc_principal = agent_cli_main(
        [
            "register",
            "--manifest",
            str(MOCK_AGENT_PATH),
            "--binding",
            str(MOCK_BINDING_PATH),
            "--principal",
            "principal-viewer-01",
        ],
        conn_or_session=db_engine,
        settings=_make_settings(owner_tenant=cli_tenant),
    )
    assert rc_principal == 1
    assert "OWNER_PRINCIPAL_REQUIRED" in capsys.readouterr().err

    # Read detail directly and verify mock fixture shows up cleanly.
    detail = get_agent_detail(
        db_engine, tenant_id=cli_tenant, agent_id="agent-mock-provider"
    )
    assert detail is not None
    assert detail["status"] == "active"
    assert detail["health"]["status"] == "no_data"

    # End-to-end module CLI execution via `python -m eap.agents.cli register`.
    cli_sub_tenant = f"tenant-cli-sub-{uuid.uuid4().hex[:8]}"
    sub_env = os.environ.copy()
    sub_env["EAP_OWNER_TENANT_ID"] = cli_sub_tenant
    sub_env["EAP_BIND"] = "127.0.0.1:8080"
    sub_env["EAP_COLLECTOR_ALLOW_REAL_ROOTS"] = "false"
    sub_env["DATABASE_URL"] = db_engine.url.render_as_string(hide_password=False)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "eap.agents.cli",
            "register",
            "--manifest",
            str(MOCK_AGENT_PATH),
            "--binding",
            str(MOCK_BINDING_PATH),
        ],
        capture_output=True,
        text=True,
        env=sub_env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    sub_out = json.loads(proc.stdout)
    assert sub_out["ok"] is True
    assert sub_out["tenant_id"] == cli_sub_tenant
    assert sub_out["agent_id"] == "agent-mock-provider"
