"""API, contract, scope authorization, and failure mode tests for GET /v1/hud/snapshot (P3a-WP1)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from eap.api.app import create_app
from eap.api.dependencies import Principal, get_db
from eap.api.hud_routes import get_hud_builder
from eap.config import ConfiguredPrincipal, Settings, get_settings
from eap.contracts.models.hud_projection import HudProjection
from eap.contracts.rules import check_hud_projection_rules
from eap.db.assessment_tables import ppm_assessments
from eap.db.snapshot_tables import project_snapshots
from eap.db.tables import commands, event_log, project_current_assessment, runs, tasks
from eap.hud.snapshot import (
    CURSOR_PREFIX,
    CursorExpiredError,
    CursorScopeMismatchError,
    CursorTamperedError,
    HudSnapshotBuilder,
    TaskRunRowBuilder,
    encode_snapshot_cursor,
    load_project_assessment_summaries,
    verify_snapshot_cursor,
    viewer_scope_ref_for_principal,
)
from eap.run.dispatcher import Dispatcher, MockProvider
from eap.task.service import TaskRequest, TaskService

REPO_ROOT = Path(__file__).resolve().parents[2]
HUD_SCHEMA_PATH = REPO_ROOT / "contracts" / "hud-projection.schema.json"
HUD_EXAMPLE_PATH = REPO_ROOT / "examples" / "hud-projection.json"

OWNER_TOKEN = "hud-owner-secret-token-wp1"
SECOND_TOKEN = "hud-second-secret-token-wp1"


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_test_settings(
    *,
    owner_tenant: str = "tenant-a",
    owner_principal: str = "owner-a",
    second_tenant: str = "tenant-b",
    second_principal: str = "admin-b",
    cursor_secret: str = "wp1-test-cursor-hmac-secret-key",
    ttl_seconds: int = 3600,
) -> Settings:
    return Settings(
        hud_cursor_secret=SecretStr(cursor_secret),
        hud_cursor_ttl_seconds=ttl_seconds,
        local_principals=[
            ConfiguredPrincipal(
                tenant_id=owner_tenant,
                principal_id=owner_principal,
                token_hash=SecretStr(_token_hash(OWNER_TOKEN)),
                roles=["owner"],
            ),
            ConfiguredPrincipal(
                tenant_id=second_tenant,
                principal_id=second_principal,
                token_hash=SecretStr(_token_hash(SECOND_TOKEN)),
                roles=["admin"],
            ),
        ],
    )


def _make_client(
    db_conn: sa.Connection,
    settings: Settings,
    *,
    builder: HudSnapshotBuilder | None = None,
) -> TestClient:
    app = create_app(settings=settings)

    def _override_db() -> Any:
        session = Session(bind=db_conn)
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    if builder is not None:
        app.dependency_overrides[get_hud_builder] = lambda: builder
    return TestClient(app, raise_server_exceptions=False)


def _insert_task_run(
    conn: sa.Connection,
    *,
    tenant_id: str,
    task_id: str,
    run_id: str | None = None,
    task_state: str = "active",
    run_state: str = "running",
    remote_outcome: str = "known",
    attempt: int = 1,
    updated_at: datetime | None = None,
) -> None:
    ts = updated_at or datetime(2026, 11, 15, 10, 0, 0, tzinfo=UTC)
    conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_id,
            client_idempotency_key=f"idem-{task_id}",
            task_intent_key=f"intent-{task_id}",
            task_type="code_review",
            agent_id="agent-hud",
            agent_version="1.2.0",
            state=task_state,
            deadline=ts + timedelta(hours=1),
            created_at=ts,
            updated_at=ts,
        )
    )
    if run_id is not None:
        conn.execute(
            runs.insert().values(
                id=f"pk-{run_id}",
                tenant_id=tenant_id,
                task_id=task_id,
                run_id=run_id,
                attempt=attempt,
                binding_id="bind-hud-1",
                state=run_state,
                remote_outcome=remote_outcome,
                lease_fencing_token=1,
                created_at=ts,
                updated_at=ts,
            )
        )


def _insert_project_assessment(
    conn: sa.Connection,
    *,
    tenant_id: str,
    project_id: str,
    assessment_id: str,
    verification: str = "partially_verified",
    validity: str = "current",
    acceptance_readiness: str = "ready_for_review",
    assessed_revision: str = "rev-abc123",
    basis: str = "baseline_weighted",
    verified_weight: float | None = 3.0,
    total_weight: float | None = 5.0,
    scan_at: datetime | None = None,
) -> None:
    ts = scan_at or datetime(2026, 11, 15, 9, 45, 0, tzinfo=UTC)
    snap_id = f"psnap-{assessment_id}"
    conn.execute(
        project_snapshots.insert().values(
            id=snap_id,
            tenant_id=tenant_id,
            snapshot_id=snap_id,
            source_id=f"src-{project_id}",
            project_id=project_id,
            snapshot_seq=1,
            scope_hash=f"scope-{project_id}",
            metadata_root="hash-meta",
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            artifact_aliases={},
            notes=[],
            claims=[],
            created_at=ts,
        )
    )
    output_doc = {
        "contract_version": "1.1",
        "assessment_id": assessment_id,
        "project_id": project_id,
        "baseline_revision": 1,
        "snapshot_refs": [snap_id],
        "validity": validity,
        "verification": verification,
        "acceptance_readiness": acceptance_readiness,
        "criteria": [],
        "progress": {
            "basis": basis,
            "verified_weight": verified_weight,
            "total_weight": total_weight,
            "criteria_total": 2,
            "criteria_with_current_evidence": 1,
        },
        "reported_progress_percent": None,
        "evidence_refs": [],
        "missing_evidence": [],
        "as_of": ts.isoformat().replace("+00:00", "Z"),
        "source_as_of": ts.isoformat().replace("+00:00", "Z"),
        "assessed_revision": assessed_revision,
        "previous_assessment_id": None,
        "snapshot_seq": 1,
        "analysis_status": "complete",
    }
    conn.execute(
        ppm_assessments.insert().values(
            id=assessment_id,
            tenant_id=tenant_id,
            project_id=project_id,
            basis_seq=1,
            snapshot_seq=1,
            project_snapshot_id=snap_id,
            baseline_revision=1,
            assessed_revision=assessed_revision,
            inputs={"fixture": True},
            inputs_hash=f"hash-{assessment_id}",
            output=output_doc,
            findings={},
            trigger="manual",
            analysis_status="complete",
            created_at=ts,
        )
    )
    conn.execute(
        project_current_assessment.insert().values(
            tenant_id=tenant_id,
            project_id=project_id,
            assessment_id=assessment_id,
            basis_seq=1,
            snapshot_seq=1,
            baseline_revision=1,
            updated_at=ts,
        )
    )


def test_contract_schema_and_example_still_validate(db_conn: sa.Connection) -> None:
    """HUD snapshot response and examples/hud-projection.json both validate against schema 1.1."""
    schema = json.loads(HUD_SCHEMA_PATH.read_text(encoding="utf-8"))
    example = json.loads(HUD_EXAMPLE_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(instance=example, schema=schema)
    HudProjection.model_validate(example)
    check_hud_projection_rules(example)

    _insert_task_run(
        db_conn,
        tenant_id="tenant-a",
        task_id="task-a1",
        run_id="run-a1",
        task_state="active",
        run_state="running",
        remote_outcome="known",
    )
    _insert_project_assessment(
        db_conn,
        tenant_id="tenant-a",
        project_id="proj-alpha",
        assessment_id="assess-alpha-1",
    )

    client = _make_client(db_conn, _make_test_settings())
    resp = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()

    jsonschema.validate(instance=body, schema=schema)
    HudProjection.model_validate(body)
    check_hud_projection_rules(body)
    assert body["contract_version"] == "1.1"
    assert body["projection_revision"] >= 1
    assert body["viewer_scope_ref"] == "principal:owner-a/tenant:tenant-a/all-projects"


def test_unknown_cost_and_missing_denominator_never_render_as_zero(db_conn: sa.Connection) -> None:
    """Missing usage renders cost status='unknown' (amount_usd=None), missing denominators stay None."""
    _insert_task_run(
        db_conn,
        tenant_id="tenant-a",
        task_id="task-no-usage",
        run_id="run-no-usage",
        task_state="active",
        run_state="running",
        remote_outcome="known",
    )
    _insert_task_run(
        db_conn,
        tenant_id="tenant-a",
        task_id="task-null-cost",
        run_id="run-null-cost",
        task_state="active",
        run_state="running",
        remote_outcome="known",
    )
    # Explicit unknown usage event (cost_usd=None, cost_status="unknown")
    db_conn.execute(
        event_log.insert().values(
            tenant_id="tenant-a",
            stream_id="run:run-null-cost",
            ingest_offset=1,
            event_id="ev-usage-unknown-1",
            producer_id="worker-1",
            event_type="usage.reported",
            payload={"cost_status": "unknown", "cost_usd": None, "usage_scope": "run_cumulative"},
            occurred_at=datetime(2026, 11, 15, 10, 4, 0, tzinfo=UTC),
            ingested_at=datetime(2026, 11, 15, 10, 4, 1, tzinfo=UTC),
        )
    )
    # Stage-only progress event without total_units
    db_conn.execute(
        event_log.insert().values(
            tenant_id="tenant-a",
            stream_id="run:run-null-cost",
            ingest_offset=2,
            event_id="ev-stage-1",
            producer_id="worker-1",
            event_type="run.progress",
            payload={"stage": "scanning"},
            occurred_at=datetime(2026, 11, 15, 10, 5, 0, tzinfo=UTC),
            ingested_at=datetime(2026, 11, 15, 10, 5, 1, tzinfo=UTC),
        )
    )
    # Project assessment with missing baseline (basis="none", null weights)
    _insert_project_assessment(
        db_conn,
        tenant_id="tenant-a",
        project_id="proj-nobase",
        assessment_id="assess-nobase-1",
        verification="unknown",
        validity="unknown",
        acceptance_readiness="not_ready",
        basis="none",
        verified_weight=None,
        total_weight=None,
    )

    client = _make_client(db_conn, _make_test_settings())
    resp = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert resp.status_code == 200
    body = resp.json()
    rows = {r["task_id"]: r for r in body["rows"]}

    for key in ("task-no-usage", "task-null-cost"):
        row = rows[key]
        assert row["cost"]["status"] == "unknown"
        assert row["cost"]["amount_usd"] is None
        assert row["cost"]["amount_usd"] != 0
        assert row["progress"]["total_units"] is None
        assert row["progress"]["completed_units"] is None
        assert row["project"] is not None
        assert row["project"]["verified_weight"] is None
        assert row["project"]["total_weight"] is None

    assert rows["task-null-cost"]["progress"]["kind"] == "stage_only"
    assert rows["task-null-cost"]["progress"]["stage"] == "scanning"


def test_lost_provider_ack_shows_unknown_remote_outcome_with_last_observed_at(db_conn: sa.Connection) -> None:
    """MockProvider(drop_ack=True) produces run_state='unknown' + remote_outcome='unknown' + last_observed_at."""
    session = Session(bind=db_conn)
    req = TaskRequest(
        contract_version="1.0",
        agent_id="agent-wp1",
        task_type="code_review",
        objective="Dispatch without ACK",
        input_refs=["repo://eam/a"],
        resource_refs=["res://cpu/1"],
        idempotency_key="idem-drop-ack-001",
    )
    task_rec = TaskService.create(session, tenant_id="tenant-a", request=req)
    session.flush()
    run_id = str(
        db_conn.execute(
            sa.select(runs.c.run_id).where(
                runs.c.tenant_id == "tenant-a",
                runs.c.task_id == task_rec.id,
            )
        ).scalar_one()
    )

    dispatch_res = Dispatcher(enabled=True).dispatch(
        session,
        tenant_id="tenant-a",
        run_id=run_id,
        worker_id="worker-hud-1",
        provider=MockProvider(drop_ack=True),
    )
    assert dispatch_res.status == "unknown"

    # Also add an accepted cancel command to verify operator command visibility alongside unknown outcome
    db_conn.execute(
        commands.insert().values(
            id="cmd-cancel-1",
            tenant_id="tenant-a",
            task_id=task_rec.id,
            run_id=run_id,
            command_type="cancel",
            status="accepted",
            payload={},
            created_at=datetime(2026, 11, 15, 10, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 15, 10, 10, 0, tzinfo=UTC),
        )
    )

    client = _make_client(db_conn, _make_test_settings())
    resp = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["task_id"] == task_rec.id
    assert row["run_state"] == "unknown"
    assert row["run_state"] not in ("failed", "succeeded")
    assert row["remote_outcome"] == "unknown"
    assert row["last_observed_at"] is not None
    assert row["pending_command"] == {"command_id": "cmd-cancel-1", "kind": "cancel", "state": "accepted"}


def test_cross_tenant_isolation_and_cursor_rejection_without_leaks(db_conn: sa.Connection) -> None:
    """Tenant B rows/assessments never appear to Tenant A; Tenant B cursor rejected (403) cleanly."""
    _insert_task_run(db_conn, tenant_id="tenant-a", task_id="task-alpha-secret", run_id="run-alpha-secret")
    _insert_project_assessment(
        db_conn,
        tenant_id="tenant-a",
        project_id="proj-alpha",
        assessment_id="assess-alpha",
    )
    _insert_task_run(db_conn, tenant_id="tenant-b", task_id="task-bravo-secret", run_id="run-bravo-secret")
    _insert_project_assessment(
        db_conn,
        tenant_id="tenant-b",
        project_id="proj-bravo",
        assessment_id="assess-bravo",
    )

    client = _make_client(db_conn, _make_test_settings())

    snap_a = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})
    assert snap_a.status_code == 200
    body_a = snap_a.json()
    assert [r["task_id"] for r in body_a["rows"]] == ["task-alpha-secret"]
    assert "tenant-b" not in json.dumps(body_a)
    assert "proj-bravo" not in json.dumps(body_a)

    snap_b = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {SECOND_TOKEN}"})
    assert snap_b.status_code == 200
    body_b = snap_b.json()
    assert [r["task_id"] for r in body_b["rows"]] == ["task-bravo-secret"]
    cursor_b = body_b["snapshot_cursor"]

    # Tenant A tries to present Tenant B's cursor -> 403 resync_required, NO leak of Tenant B identity
    rejected = client.get(
        f"/v1/hud/snapshot?cursor={cursor_b}",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert rejected.status_code == 403
    err = rejected.json()
    assert err["resync_required"] is True
    assert err["code"] == "resync_required"
    assert "tenant-b" not in json.dumps(err).lower()
    assert "admin-b" not in json.dumps(err).lower()
    assert "bravo" not in json.dumps(err).lower()

    # Forged identity header / query rejected (422)
    forged = client.get(
        "/v1/hud/snapshot?tenant_id=tenant-b",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert forged.status_code == 422

    # Same-tenant scope mismatch (cursor issued for project=proj-alpha used on all-projects)
    scoped_a = client.get(
        "/v1/hud/snapshot?project_id=proj-alpha",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert scoped_a.status_code == 200
    scoped_cursor = scoped_a.json()["snapshot_cursor"]
    scope_mismatch = client.get(
        f"/v1/hud/snapshot?cursor={scoped_cursor}",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert scope_mismatch.status_code == 403
    assert scope_mismatch.json()["resync_required"] is True


def test_cursor_opacity_tamper_expiry_and_grant_epoch(db_conn: sa.Connection) -> None:
    """Cursor never leaks internal IDs/offsets in cleartext; tamper/expiry/epoch errors trigger resync."""
    settings = _make_test_settings(ttl_seconds=60)
    principal = Principal(tenant_id="tenant-secret-xyz", principal_id="op-99", roles=["owner"])
    scope = viewer_scope_ref_for_principal(principal)
    now = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

    c1 = encode_snapshot_cursor(
        tenant_id=principal.tenant_id,
        scope=scope,
        offset=42,
        stream_offsets={"run:run-internal-db-999": 42},
        grant_epoch=3,
        settings=settings,
        now=now,
    )
    c2 = encode_snapshot_cursor(
        tenant_id=principal.tenant_id,
        scope=scope,
        offset=42,
        stream_offsets={"run:run-internal-db-999": 42},
        grant_epoch=3,
        settings=settings,
        now=now,
    )
    assert c1.startswith(CURSOR_PREFIX)
    assert c1 != c2  # Random nonce prevents token correlation

    raw_body = c1[len(CURSOR_PREFIX) :]
    padded = raw_body + ("=" * ((4 - len(raw_body) % 4) % 4))
    raw_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
    for forbidden in (b"tenant-secret-xyz", b"op-99", b"run-internal-db-999", b"42"):
        assert forbidden not in c1.encode("utf-8")
        assert forbidden not in raw_bytes

    decoded = verify_snapshot_cursor(
        c1,
        expected_tenant_id=principal.tenant_id,
        expected_scope=scope,
        expected_grant_epoch=3,
        settings=settings,
        now=now + timedelta(seconds=10),
    )
    assert decoded.offset == 42
    assert decoded.grant_epoch == 3
    assert decoded.stream_offsets == {"run:run-internal-db-999": 42}

    # Tamper detection (both direct and via HTTP route)
    tampered_chars = list(c1)
    mid = len(tampered_chars) // 2  # last base64url char may only carry padding bits
    tampered_chars[mid] = "A" if tampered_chars[mid] != "A" else "B"
    tampered = "".join(tampered_chars)
    with pytest.raises(CursorTamperedError):
        verify_snapshot_cursor(
            tampered,
            expected_tenant_id=principal.tenant_id,
            expected_scope=scope,
            expected_grant_epoch=3,
            settings=settings,
            now=now,
        )

    client = _make_client(db_conn, _make_test_settings())
    http_tampered = client.get(
        f"/v1/hud/snapshot?cursor={tampered}",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert http_tampered.status_code == 400
    assert http_tampered.json()["resync_required"] is True

    # Expiry -> CursorExpiredError (400 resync_required)
    with pytest.raises(CursorExpiredError):
        verify_snapshot_cursor(
            c1,
            expected_tenant_id=principal.tenant_id,
            expected_scope=scope,
            expected_grant_epoch=3,
            settings=settings,
            now=now + timedelta(seconds=61),
        )

    # Grant epoch changed -> CursorScopeMismatchError (403 resync_required)
    with pytest.raises(CursorScopeMismatchError):
        verify_snapshot_cursor(
            c1,
            expected_tenant_id=principal.tenant_id,
            expected_scope=scope,
            expected_grant_epoch=4,
            settings=settings,
            now=now,
        )


def test_expired_cursor_rejected_via_http(db_conn: sa.Connection) -> None:
    """Expired snapshot cursor on GET /v1/hud/snapshot?cursor=... returns HTTP 400 resync_required."""
    settings = _make_test_settings(ttl_seconds=10)
    principal = Principal(tenant_id="tenant-a", principal_id="owner-a", roles=["owner"])
    expired_cursor = encode_snapshot_cursor(
        tenant_id=principal.tenant_id,
        scope=viewer_scope_ref_for_principal(principal),
        offset=1,
        settings=settings,
        now=datetime.now(UTC) - timedelta(seconds=120),
        ttl_seconds=10,
    )
    client = _make_client(db_conn, settings)
    resp = client.get(
        f"/v1/hud/snapshot?cursor={expired_cursor}",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["resync_required"] is True
    assert body["code"] == "resync_required"


def test_contract_violation_returns_500_without_partial_body_and_logs(
    db_conn: sa.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If an assembled row violates contract 1.1, route logs an error and returns 500 with NO partial body."""

    class BrokenRowBuilder:
        kind = "task_run"

        def build_rows(
            self,
            conn: sa.Connection,
            *,
            tenant_id: str,
            project_id: str | None = None,
            **context: Any,
        ) -> list[dict[str, Any]]:
            del conn, tenant_id, project_id, context
            return [
                {
                    "task_id": "task-bad",
                    "task_state": "active",
                    "run_id": "run-bad",
                    "attempt": 1,
                    "run_state": "running",
                    "remote_outcome": "known",
                    "connectivity": "healthy",
                    "last_observed_at": "2026-11-15T10:00:00Z",
                    "observation_source": "local_runtime",
                    "progress": {
                        "kind": "none",
                        "completed_units": None,
                        "total_units": None,
                        "unit": None,
                        "stage": None,
                    },
                    "project": None,
                    # Violates contract rule: unknown cost MUST have amount_usd=None, never 0
                    "cost": {
                        "status": "unknown",
                        "amount_usd": 0.0,
                        "as_of": None,
                        "scope": "run",
                    },
                    "pending_command": None,
                }
            ]

    broken_builder = HudSnapshotBuilder(builders=[BrokenRowBuilder()])
    client = _make_client(db_conn, _make_test_settings(), builder=broken_builder)

    with caplog.at_level(logging.ERROR, logger="eap.hud.snapshot"):
        resp = client.get("/v1/hud/snapshot", headers={"Authorization": f"Bearer {OWNER_TOKEN}"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "HUD_CONTRACT_VIOLATION"
    assert "rows" not in body
    assert "snapshot_cursor" not in body
    assert any("HUD snapshot contract validation failed" in rec.message for rec in caplog.records)


def test_project_assessment_summary_and_builder_hook(db_conn: sa.Connection) -> None:
    """Verify P3a-WP5 builder extensibility hook and full ProjectAssessmentSummary metadata."""
    _insert_project_assessment(
        db_conn,
        tenant_id="tenant-a",
        project_id="proj-ext",
        assessment_id="assess-ext-1",
        verification="verified",
        validity="current",
        acceptance_readiness="ready_for_review",
        assessed_revision="deadbeef1234",
        verified_weight=8.0,
        total_weight=8.0,
        scan_at=datetime(2026, 11, 15, 11, 30, 0, tzinfo=UTC),
    )

    summaries = load_project_assessment_summaries(db_conn, tenant_id="tenant-a")
    assert "proj-ext" in summaries
    s = summaries["proj-ext"]
    assert s.verification == "verified"
    assert s.validity == "current"
    assert s.acceptance_readiness == "ready_for_review"
    assert s.assessed_revision == "deadbeef1234"
    assert s.last_successful_scan_at == "2026-11-15T11:30:00Z"

    # Verify strict 8-key schema serialization (ISS-038 boundary)
    contract_dict = s.to_contract_dict()
    assert set(contract_dict.keys()) == {
        "project_id",
        "assessment_id",
        "verification",
        "validity",
        "acceptance",
        "assessment_as_of",
        "verified_weight",
        "total_weight",
    }

    class StubAgentBuilder:
        kind = "agent_inventory_stub"

        def build_rows(
            self,
            conn: sa.Connection,
            *,
            tenant_id: str,
            project_id: str | None = None,
            **context: Any,
        ) -> list[dict[str, Any]]:
            del conn, tenant_id, project_id, context
            return []

    builder = HudSnapshotBuilder()
    assert [b.kind for b in builder.get_row_builders()] == ["task_run"]
    builder.register_row_builder(StubAgentBuilder())
    assert [b.kind for b in builder.get_row_builders()] == ["task_run", "agent_inventory_stub"]
    with pytest.raises(ValueError):
        builder.register_row_builder(StubAgentBuilder())
    builder.register_row_builder(TaskRunRowBuilder(), replace=True)
