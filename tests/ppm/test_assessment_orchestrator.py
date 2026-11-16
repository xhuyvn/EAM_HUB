"""Tests for P2a-WP6 Assessment Orchestrator (C32, C34, C36, C39, timers, crash, rollback)."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal
from eap.config import Settings
from eap.db import (
    location_registrations,
    ppm_assessments,
    ppm_waivers,
    project_current_assessment,
    project_scopes,
    runs,
    sources,
)
from eap.model_gateway import FakeModelProvider, FakeSuccess, FakeTimeout
from eap.ppm.assessments import approve_baseline, create_assessment, load_fixture_evidence
from eap.ppm.collector import PpmCollector
from eap.ppm.orchestrator import AssessmentOrchestrator
from eap.ppm.path import canonicalize_root
from eap.ppm.scheduler import PpmScanScheduler
from ppm_reducer import rollup_portfolio

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFORMANCE_DIR = REPO_ROOT / "artifacts" / "conformance" / "P2a"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "EAP Test",
        "GIT_AUTHOR_EMAIL": "test@eap.local",
        "GIT_COMMITTER_NAME": "EAP Test",
        "GIT_COMMITTER_EMAIL": "test@eap.local",
    }
    res = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _create_git_fixture_repo(
    root: Path,
    *,
    project_id: str,
    test_passes: bool = True,
    note_text: str = "Initial note",
) -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    (root / "src").mkdir(exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / ".ppm" / "notes").mkdir(parents=True, exist_ok=True)
    (root / ".ppm" / "updates").mkdir(parents=True, exist_ok=True)

    (root / ".ppm" / "project.yaml").write_text(
        f"contract_version: '2.0'\nproject_id: {project_id}\nname: {project_id}\n",
        encoding="utf-8",
    )
    (root / ".ppm" / "notes" / "status.md").write_text(note_text, encoding="utf-8")
    (root / "src" / "export.py").write_text(
        f"def export_ok():\n    return {test_passes!s}\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_export.py").write_text(
        "from src.export import export_ok\n\ndef test_ac001_export():\n    assert export_ok() is True\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial commit")
    return _git(root, "rev-parse", "HEAD")


def _seed_source_scope_and_baseline(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    root: Path,
    baseline_revision: int = 1,
    verification_refs: list[dict[str, str]] | None = None,
    metadata_root: str = ".ppm",
    scan_scope: list[str] | None = None,
    source_id: str | None = None,
) -> str:
    src_id = source_id or f"src-{uuid.uuid4().hex[:10]}"
    canon = canonicalize_root(str(root), "windows" if os.name == "nt" else "linux")
    now = dt.datetime.now(dt.UTC)
    existing_src = db.execute(
        sa.select(sources.c.source_id).where(sources.c.tenant_id == tenant_id, sources.c.source_id == src_id)
    ).scalar_one_or_none()
    if existing_src is None:
        db.execute(
            sources.insert().values(
                source_id=src_id,
                tenant_id=tenant_id,
                canonical_root=canon,
                repo_identity=f"git@example.com:{tenant_id}/{src_id}.git",
                created_at=now,
                updated_at=now,
            )
        )
    db.execute(
        project_scopes.insert().values(
            id=f"scope-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=src_id,
            project_id=project_id,
            metadata_root=metadata_root,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db.execute(
        location_registrations.insert().values(
            id=f"loc-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=src_id,
            project_id=project_id,
            metadata_root=metadata_root,
            state="active",
            revision=1,
            registered_by_ref="owner",
            approved_by_ref="admin",
            host_id="host-test",
            connector_id="conn-test",
            root_path=canon,
            scan_scope=scan_scope or [metadata_root],
            artifact_aliases={"architecture": [], "blueprint": [], "implementation_plan": []},
            notes_globs=[f"{metadata_root}/notes/**/*.md", f"{metadata_root}/notes/*.md"],
            deny_globs=[".git/**"],
            classification="internal",
            egress_policy_ref="local_only",
            identity={"repo_identity": f"git@example.com:{tenant_id}/{src_id}.git"},
            created_at=now,
            updated_at=now,
        )
    )
    approve_baseline(
        db,
        tenant_id,
        project_id,
        baseline_revision=baseline_revision,
        criteria=[
            {
                "criterion_id": "AC-001",
                "deliverable_id": f"deliv-{project_id}",
                "owner_project_id": project_id,
                "title": "Export works",
                "weight": 1.0,
                "required": True,
                "verification_refs": verification_refs or [{"kind": "test_id", "ref": "tests/test_export.py::test_ac001_export"}],
            }
        ],
        approved_by="owner",
        source="owner_console",
    )
    db.commit()
    return src_id


def _write_sc03(name: str, payload: dict[str, Any]) -> None:
    if os.environ.get("EAP_UPDATE_CONFORMANCE_ARTIFACTS") == "1":
        out_dir = CONFORMANCE_DIR
    elif os.environ.get("EAP_CONFORMANCE_OUT_DIR"):
        out_dir = Path(os.environ["EAP_CONFORMANCE_OUT_DIR"])
    else:
        out_dir = Path(tempfile.gettempdir()) / "eap_conformance" / "P2a"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. C34: Out-of-order scan completion (real parallel PG connections)
# ---------------------------------------------------------------------------


def test_c34_out_of_order_scan_completion_parallel_pg(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan A starts first, Scan B (newer rev) finishes first, Scan A finishes last -> B current."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c34-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c34"
    repo_root = tmp_path / "repo_c34"
    rev_r1 = _create_git_fixture_repo(repo_root, project_id=project_id, note_text="rev1")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        src_id = _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)
        # Advance outbox cursor past initial baseline_revision=1 approval
        AssessmentOrchestrator().consume_outbox_events(db, tenant_id=tenant_id, execute=False)
        db.commit()

    collector = PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True)
    orch = AssessmentOrchestrator()

    # Scan A captures rev-r1
    with SessionLocal() as db:
        scan_a = collector.scan_source(db, tenant_id=tenant_id, source_id=src_id)
        psnap_a = scan_a.project_snapshot_ids[project_id]
        db.commit()
        res_a_admit = orch.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="snapshot",
            trigger_ref=psnap_a,
            project_snapshot_id=psnap_a,
            assessed_revision=rev_r1,
        )
        db.commit()

    # Commit rev-r2 and Scan B captures rev-r2
    (repo_root / ".ppm" / "notes" / "status.md").write_text("rev2 update", encoding="utf-8")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "rev-r2")
    rev_r2 = _git(repo_root, "rev-parse", "HEAD")

    with SessionLocal() as db:
        scan_b = collector.scan_source(db, tenant_id=tenant_id, source_id=src_id)
        psnap_b = scan_b.project_snapshot_ids[project_id]
        db.commit()
        res_b_admit = orch.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="snapshot",
            trigger_ref=psnap_b,
            project_snapshot_id=psnap_b,
            assessed_revision=rev_r2,
        )
        db.commit()

    assert res_a_admit.basis_seq < res_b_admit.basis_seq

    # Parallel execution: Thread A pauses inside before_reduce_hook until Thread B commits
    a_entered = threading.Event()
    b_finished = threading.Event()
    results: dict[str, Any] = {}

    def run_a() -> None:
        with SessionLocal() as db_a:
            def hook(_task: Any) -> None:
                a_entered.set()
                assert b_finished.wait(timeout=10.0), "Timed out waiting for B to finish"

            results["a"] = orch.execute_task(db_a, tenant_id, res_a_admit.id, before_reduce_hook=hook)
            db_a.commit()

    def run_b() -> None:
        assert a_entered.wait(timeout=10.0), "Timed out waiting for A to enter hook"
        with SessionLocal() as db_b:
            results["b"] = orch.execute_task(db_b, tenant_id, res_b_admit.id)
            db_b.commit()
        b_finished.set()

    t_a = threading.Thread(target=run_a)
    t_b = threading.Thread(target=run_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=15.0)
    t_b.join(timeout=15.0)

    assert results["b"].status == "succeeded"
    assert results["a"].status == "succeeded"
    assert results["b"].assessment is not None
    assert results["a"].assessment is not None
    assert results["b"].assessment.is_current is True
    assert results["a"].assessment.is_current is False

    # Verify DB pointer & history
    with SessionLocal() as db:
        ptr = db.execute(
            sa.select(project_current_assessment).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).mappings().one()
        assert str(ptr["assessment_id"]) == results["b"].assessment_id
        assert int(ptr["basis_seq"]) == res_b_admit.basis_seq
        assert int(ptr["snapshot_seq"]) == 2

        hist_count = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.project_id == project_id,
                )
            ).scalar_one()
        )
        assert hist_count == 2

    # HUD snapshot shows B
    def _override_get_db():
        with SessionLocal() as s:
            yield s

    def _override_get_principal() -> Principal:
        return Principal(tenant_id=tenant_id, principal_id="principal-owner", roles=["owner", "admin"])

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_principal] = _override_get_principal
    try:
        with TestClient(app) as client:
            resp = client.get("/v1/hud/snapshot")
            assert resp.status_code == 200
            hud_data = resp.json()
            proj_rows = [
                r for r in hud_data.get("rows", [])
                if r.get("project_id") == project_id and r.get("assessment_id") is not None
            ]
            assert all(r.get("assessment_id") == results["b"].assessment_id for r in proj_rows)
            ppm_resp = client.get(f"/v1/ppm/projects/{project_id}/assessments")
            assert ppm_resp.status_code == 200
            current_items = [a for a in ppm_resp.json() if a["is_current"]]
            assert len(current_items) == 1
            assert current_items[0]["id"] == results["b"].assessment_id
    finally:
        app.dependency_overrides.clear()

    _write_sc03(
        "C34",
        {
            "case": "C34",
            "status": "PASS",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "scan_a": {
                "assessment_id": results["a"].assessment_id,
                "basis_seq": res_a_admit.basis_seq,
                "snapshot_seq": 1,
                "is_current": False,
            },
            "scan_b": {
                "assessment_id": results["b"].assessment_id,
                "basis_seq": res_b_admit.basis_seq,
                "snapshot_seq": 2,
                "is_current": True,
            },
            "pointer_before": None,
            "pointer_after": {
                "assessment_id": str(ptr["assessment_id"]),
                "basis_seq": int(ptr["basis_seq"]),
                "snapshot_seq": int(ptr["snapshot_seq"]),
            },
        },
    )


# ---------------------------------------------------------------------------
# 2. C39: Baseline change while old-baseline assessment is in flight
# ---------------------------------------------------------------------------


def test_c39_baseline_change_while_old_assessment_in_flight_parallel_pg(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged repo; owner approves baseline rev 2 while rev 1 assessment is in flight -> rev 2 current."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c39-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c39"
    repo_root = tmp_path / "repo_c39"
    rev_r1 = _create_git_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        src_id = _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)
        # Advance outbox cursor past initial baseline_revision=1 approval
        AssessmentOrchestrator().consume_outbox_events(db, tenant_id=tenant_id, execute=False)
        db.commit()

    collector = PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True)
    orch = AssessmentOrchestrator()

    with SessionLocal() as db:
        scan_res = collector.scan_source(db, tenant_id=tenant_id, source_id=src_id)
        psnap_id = scan_res.project_snapshot_ids[project_id]
        db.commit()
        old_admit = orch.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="snapshot",
            trigger_ref=psnap_id,
            project_snapshot_id=psnap_id,
            assessed_revision=rev_r1,
        )
        db.commit()

    assert old_admit.baseline_revision == 1

    old_entered = threading.Event()
    new_finished = threading.Event()
    results: dict[str, Any] = {}

    def run_old() -> None:
        with SessionLocal() as db_old:
            def hook(_task: Any) -> None:
                old_entered.set()
                assert new_finished.wait(timeout=10.0), "Timed out waiting for baseline 2 task"

            results["old"] = orch.execute_task(db_old, tenant_id, old_admit.id, before_reduce_hook=hook)
            db_old.commit()

    def run_new_baseline() -> None:
        assert old_entered.wait(timeout=10.0), "Timed out waiting for old task to enter hook"
        with SessionLocal() as db_new:
            approve_baseline(
                db_new,
                tenant_id,
                project_id,
                baseline_revision=2,
                criteria=[
                    {
                        "criterion_id": "AC-001",
                        "deliverable_id": f"deliv-{project_id}",
                        "owner_project_id": project_id,
                        "title": "Export works v2",
                        "weight": 1.0,
                        "required": True,
                        "verification_refs": [{"kind": "test_id", "ref": "tests/test_export.py::test_ac001_export"}],
                    },
                    {
                        "criterion_id": "AC-002",
                        "deliverable_id": f"deliv-{project_id}",
                        "owner_project_id": project_id,
                        "title": "New criterion",
                        "weight": 1.0,
                        "required": True,
                        "verification_refs": [],
                    },
                ],
                approved_by="owner",
                source="owner_console",
            )
            db_new.commit()
            outbox_res = orch.consume_outbox_events(
                db_new,
                tenant_id=tenant_id,
                event_types=("ppm.baseline.approved",),
                execute=True,
            )
            db_new.commit()
            assert len(outbox_res) == 1
            results["new"] = outbox_res[0]
        new_finished.set()

    t_old = threading.Thread(target=run_old)
    t_new = threading.Thread(target=run_new_baseline)
    t_old.start()
    t_new.start()
    t_old.join(timeout=15.0)
    t_new.join(timeout=15.0)

    assert results["new"].status == "succeeded"
    assert results["old"].status == "succeeded"
    assert results["new"].assessment.baseline_revision == 2
    assert results["old"].assessment.baseline_revision == 1
    assert results["new"].assessment.is_current is True
    assert results["old"].assessment.is_current is False

    with SessionLocal() as db:
        ptr = db.execute(
            sa.select(project_current_assessment).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).mappings().one()
        assert str(ptr["assessment_id"]) == results["new"].assessment_id
        assert int(ptr["baseline_revision"]) == 2
        assert int(ptr["basis_seq"]) == results["new"].basis_seq

    _write_sc03(
        "C39",
        {
            "case": "C39",
            "status": "PASS",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "old_baseline_assessment": {
                "assessment_id": results["old"].assessment_id,
                "basis_seq": results["old"].basis_seq,
                "baseline_revision": 1,
                "is_current": False,
            },
            "new_baseline_assessment": {
                "assessment_id": results["new"].assessment_id,
                "basis_seq": results["new"].basis_seq,
                "baseline_revision": 2,
                "is_current": True,
            },
            "pointer_after": {
                "assessment_id": str(ptr["assessment_id"]),
                "basis_seq": int(ptr["basis_seq"]),
                "baseline_revision": int(ptr["baseline_revision"]),
            },
        },
    )


# ---------------------------------------------------------------------------
# 3. C36: Model timeout -> model_failed -> model_recovered -> 1 re-assessment
# ---------------------------------------------------------------------------


def test_c36_model_timeout_and_recovery_reassessment(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake model timeout -> analysis_status=model_failed; recovery -> exactly one re-assessment."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c36-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c36"
    repo_root = tmp_path / "repo_c36"
    _create_git_fixture_repo(repo_root, project_id=project_id, note_text="AC-001 export implemented")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))
    monkeypatch.setenv("PPM_ANALYSIS_ENABLED", "true")

    with SessionLocal() as db:
        src_id = _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)
        # Advance outbox cursor past initial baseline_revision=1 approval
        AssessmentOrchestrator().consume_outbox_events(db, tenant_id=tenant_id, execute=False)
        db.commit()

    collector = PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True)
    with SessionLocal() as db:
        collector.scan_source(db, tenant_id=tenant_id, source_id=src_id)
        db.commit()

    # 1. Timeout on initial snapshot
    failing_provider = FakeModelProvider([FakeTimeout()])
    cfg_analysis = Settings.model_validate(
        {
            "EAP_COLLECTOR_ALLOW_REAL_ROOTS": False,
            "EAP_COLLECTOR_FIXTURES_ROOT": str(tmp_path),
            "PPM_ANALYSIS_ENABLED": True,
        }
    )
    orch_fail = AssessmentOrchestrator(settings=cfg_analysis, model_provider=failing_provider)
    with SessionLocal() as db:
        res_list = orch_fail.consume_outbox_events(
            db,
            tenant_id=tenant_id,
            event_types=("ppm.snapshot.captured",),
            execute=True,
        )
        db.commit()
    assert len(res_list) == 1
    fail_res = res_list[0]
    assert fail_res.status == "succeeded"
    assert fail_res.analysis_status == "model_failed"
    assert fail_res.assessment is not None
    assert fail_res.assessment.is_current is True

    # 2. Recovery -> exactly one re-assessment even if notify_model_recovered called twice
    recovered_payload = json.dumps(
        {
            "claims": [
                {
                    "proposed_criterion_id": "AC-001",
                    "claim_kind": "claimed_done",
                    "source_artifact_path": ".ppm/notes/status.md",
                    "excerpt": "AC-001 export implemented",
                    "quote_span": {"start": 0, "end": 25},
                    "confidence": 0.9,
                }
            ]
        }
    )
    ok_provider = FakeModelProvider([FakeSuccess(payload=recovered_payload)])
    orch_ok = AssessmentOrchestrator(settings=cfg_analysis, model_provider=ok_provider)
    with SessionLocal() as db:
        rec1 = orch_ok.notify_model_recovered(db, tenant_id=tenant_id)
        db.commit()
        rec2 = orch_ok.notify_model_recovered(db, tenant_id=tenant_id)
        db.commit()

    assert len(rec1) == 1
    assert rec1[0].status == "succeeded"
    assert rec1[0].analysis_status == "complete"
    assert rec1[0].assessment is not None
    assert rec1[0].assessment.is_current is True
    assert len(rec2) == 0  # already recovered, no duplicate re-assessment

    _write_sc03(
        "C36",
        {
            "case": "C36",
            "status": "PASS",
            "tenant_id": tenant_id,
            "project_id": project_id,
            "failed_assessment": {
                "assessment_id": fail_res.assessment_id,
                "basis_seq": fail_res.basis_seq,
                "analysis_status": fail_res.analysis_status,
            },
            "recovered_assessment": {
                "assessment_id": rec1[0].assessment_id,
                "basis_seq": rec1[0].basis_seq,
                "analysis_status": rec1[0].analysis_status,
            },
            "pointer_before": {"assessment_id": fail_res.assessment_id, "basis_seq": fail_res.basis_seq},
            "pointer_after": {"assessment_id": rec1[0].assessment_id, "basis_seq": rec1[0].basis_seq},
        },
    )


# ---------------------------------------------------------------------------
# 4. Evidence TTL expiry & Waiver expiry without file change (ISS-034)
# ---------------------------------------------------------------------------


def test_iss034_evidence_ttl_expiry_without_rescan(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence TTL expiry with no file change -> criterion becomes stale via timer trigger."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-ttl-{uuid.uuid4().hex[:8]}"
    project_id = "proj-ttl"
    repo_root = tmp_path / "repo_ttl"
    rev_r1 = _create_git_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    t0 = dt.datetime(2026, 11, 1, 12, 0, 0, tzinfo=dt.UTC)
    with SessionLocal() as db:
        _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)
        load_fixture_evidence(
            db,
            tenant_id,
            project_id,
            [
                {
                    "evidence_id": "ev-ttl-1",
                    "test_id": "tests/test_export.py::test_ac001_export",
                    "result": "passed",
                    "trust_class": "verifier_observed",
                    "revision": rev_r1,
                    "observed_at": t0.isoformat(),
                }
            ],
        )
        asmt1 = create_assessment(
            db,
            tenant_id,
            project_id,
            trigger="manual",
            assessed_revision=rev_r1,
            now=t0,
            evidence_ttl_days=7.0,
        )
        db.commit()

    assert asmt1.output["criteria"][0]["status"] == "verified"

    orch = AssessmentOrchestrator()
    # At exactly 7 days: still fresh -> 0 tasks
    with SessionLocal() as db:
        res_boundary = orch.check_evidence_ttl_timers(
            db, tenant_id=tenant_id, now=t0 + dt.timedelta(days=7)
        )
        db.commit()
    assert res_boundary == []

    # At 7 days + 1 second: expires -> 1 task -> criterion becomes stale (no rescan!)
    with SessionLocal() as db:
        res_expired = orch.check_evidence_ttl_timers(
            db, tenant_id=tenant_id, now=t0 + dt.timedelta(days=7, seconds=1)
        )
        db.commit()
    assert len(res_expired) == 1
    assert res_expired[0].status == "succeeded"
    assert res_expired[0].assessment is not None
    assert res_expired[0].assessment.output["criteria"][0]["status"] == "stale"

    # Re-running timer is idempotent no-op
    with SessionLocal() as db:
        res_again = orch.check_evidence_ttl_timers(
            db, tenant_id=tenant_id, now=t0 + dt.timedelta(days=7, seconds=60)
        )
        db.commit()
    assert res_again == []


def test_iss034_waiver_expiry_without_rescan(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiver expiry with no file change -> status changes from waived to unverified via timer."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-waiver-{uuid.uuid4().hex[:8]}"
    project_id = "proj-waiver"
    repo_root = tmp_path / "repo_waiver"
    rev_r1 = _create_git_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    t0 = dt.datetime(2026, 11, 1, 12, 0, 0, tzinfo=dt.UTC)
    exp = t0 + dt.timedelta(days=2)
    with SessionLocal() as db:
        _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)
        db.execute(
            ppm_waivers.insert().values(
                id=f"w-{uuid.uuid4().hex[:10]}",
                tenant_id=tenant_id,
                project_id=project_id,
                waiver_id="waiver-ac001",
                criterion_id="AC-001",
                scope="criterion",
                rationale="Temporary waiver for demo",
                approved_by="owner",
                approved_at=t0,
                expires_at=exp,
            )
        )
        asmt1 = create_assessment(
            db,
            tenant_id,
            project_id,
            trigger="manual",
            assessed_revision=rev_r1,
            now=t0,
        )
        db.commit()

    assert asmt1.output["criteria"][0]["status"] == "waived"

    orch = AssessmentOrchestrator()
    with SessionLocal() as db:
        res_before = orch.check_waiver_expiry_timers(
            db, tenant_id=tenant_id, now=exp - dt.timedelta(seconds=1)
        )
        db.commit()
    assert res_before == []

    with SessionLocal() as db:
        res_expired = orch.check_waiver_expiry_timers(
            db, tenant_id=tenant_id, now=exp + dt.timedelta(seconds=1)
        )
        db.commit()
    assert len(res_expired) == 1
    assert res_expired[0].status == "succeeded"
    assert res_expired[0].assessment is not None
    assert res_expired[0].assessment.output["criteria"][0]["status"] == "unverified"

    # Re-running is idempotent
    with SessionLocal() as db:
        res_again = orch.check_waiver_expiry_timers(
            db, tenant_id=tenant_id, now=exp + dt.timedelta(seconds=60)
        )
        db.commit()
    assert res_again == []


# ---------------------------------------------------------------------------
# 5. C32: Shared component (monorepo A, B, shared)
# ---------------------------------------------------------------------------


def test_c32_shared_component_isolated_reassessment_and_rollup(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edit in proj-app-a creates new assessment only for A; B keeps its id; rollup counts shared once."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c32-{uuid.uuid4().hex[:8]}"
    repo_root = tmp_path / "monorepo_c32"
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-b", "main")

    for sub, pid in [("apps/a", "proj-app-a"), ("apps/b", "proj-app-b"), ("libs/shared", "proj-shared")]:
        (repo_root / sub / ".ppm" / "notes").mkdir(parents=True, exist_ok=True)
        (repo_root / sub / ".ppm" / "project.yaml").write_text(
            f"contract_version: '2.0'\nproject_id: {pid}\nname: {pid}\n",
            encoding="utf-8",
        )
        (repo_root / sub / ".ppm" / "notes" / "status.md").write_text(f"{pid} initial", encoding="utf-8")

    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", "initial monorepo")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        src_id = _seed_source_scope_and_baseline(
            db,
            tenant_id=tenant_id,
            project_id="proj-app-a",
            root=repo_root,
            metadata_root="apps/a/.ppm",
            scan_scope=["apps/a", "libs/shared"],
        )
        _seed_source_scope_and_baseline(
            db,
            tenant_id=tenant_id,
            project_id="proj-app-b",
            root=repo_root,
            metadata_root="apps/b/.ppm",
            scan_scope=["apps/b", "libs/shared"],
            source_id=src_id,
        )
        _seed_source_scope_and_baseline(
            db,
            tenant_id=tenant_id,
            project_id="proj-shared",
            root=repo_root,
            metadata_root="libs/shared/.ppm",
            scan_scope=["libs/shared"],
            source_id=src_id,
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    with SessionLocal() as db:
        r1 = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=src_id)
        assert r1.status == "succeeded"
        assert r1.assessments_created == 3

        ptrs_before = {
            str(r["project_id"]): str(r["assessment_id"])
            for r in db.execute(
                sa.select(project_current_assessment).where(project_current_assessment.c.tenant_id == tenant_id)
            ).mappings().all()
        }

    # Edit only proj-app-a (working tree edit so git_commit stays unchanged for B and shared)
    (repo_root / "apps/a/.ppm/notes/status.md").write_text("proj-app-a edited!", encoding="utf-8")

    with SessionLocal() as db:
        r2 = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=src_id)
        assert r2.status == "succeeded"
        assert r2.assessments_created == 1
        assert r2.assessments_deduplicated == 2

        ptrs_after = {
            str(r["project_id"]): str(r["assessment_id"])
            for r in db.execute(
                sa.select(project_current_assessment).where(project_current_assessment.c.tenant_id == tenant_id)
            ).mappings().all()
        }

    assert ptrs_after["proj-app-a"] != ptrs_before["proj-app-a"]
    assert ptrs_after["proj-app-b"] == ptrs_before["proj-app-b"]
    assert ptrs_after["proj-shared"] == ptrs_before["proj-shared"]

    # Portfolio rollup counts proj-shared once (deliverable_id = deliv-shared)
    with SessionLocal() as db:
        asmt_a_row = db.execute(
            sa.select(ppm_assessments.c.inputs).where(
                ppm_assessments.c.tenant_id == tenant_id,
                ppm_assessments.c.id == ptrs_after["proj-app-a"],
            )
        ).scalar_one()

    rollup_inputs = dict(asmt_a_row)
    rollup_inputs["context"] = {
        "viewer_authorized_project_ids": ["proj-app-a", "proj-app-b", "proj-shared"],
        "portfolio": {
            "portfolio_id": "port-c32",
            "projects": [
                {
                    "project_id": "proj-app-a",
                    "deliverables": [
                        {
                            "deliverable_id": "deliv-proj-app-a",
                            "owner_project_id": "proj-app-a",
                            "weight": 1.0,
                            "verified": False,
                        }
                    ],
                },
                {
                    "project_id": "proj-app-b",
                    "deliverables": [
                        {
                            "deliverable_id": "deliv-proj-app-b",
                            "owner_project_id": "proj-app-b",
                            "weight": 1.0,
                            "verified": False,
                        }
                    ],
                },
                {
                    "project_id": "proj-shared",
                    "deliverables": [
                        {
                            "deliverable_id": "deliv-proj-shared",
                            "owner_project_id": "proj-shared",
                            "weight": 2.0,
                            "verified": True,
                        }
                    ],
                },
            ],
        },
    }
    rollup = rollup_portfolio(rollup_inputs).to_dict()
    assert rollup["progress"]["total_weight"] == 4.0  # 1 + 1 + 2 (shared counted once)

    _write_sc03(
        "C32",
        {
            "case": "C32",
            "status": "PASS",
            "tenant_id": tenant_id,
            "pointer_before": ptrs_before,
            "pointer_after": ptrs_after,
            "rollup_total_weight": rollup["progress"]["total_weight"],
        },
    )


# ---------------------------------------------------------------------------
# 6. Duplicate trigger, crash recovery, reducer error, verifier+analysis, rollback
# ---------------------------------------------------------------------------


def test_duplicate_trigger_and_crash_recovery_and_reducer_error_and_rollback(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Covers duplicate delivery, crash recovery, reducer error, verifier+analysis, and flag rollback."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-ops-{uuid.uuid4().hex[:8]}"
    project_id = "proj-ops"
    repo_root = tmp_path / "repo_ops"
    rev_r1 = _create_git_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        src_id = _seed_source_scope_and_baseline(db, tenant_id=tenant_id, project_id=project_id, root=repo_root)

    orch = AssessmentOrchestrator()

    # A. Duplicate trigger delivery -> 1 task
    with SessionLocal() as db:
        a1 = orch.admit_trigger(
            db, tenant_id, project_id, trigger="manual", trigger_ref="req-dup-1", assessed_revision=rev_r1
        )
        a2 = orch.admit_trigger(
            db, tenant_id, project_id, trigger="manual", trigger_ref="req-dup-1", assessed_revision=rev_r1
        )
        db.commit()
    assert a1.deduplicated is False
    assert a2.deduplicated is True
    assert a1.id == a2.id
    assert a1.basis_seq == a2.basis_seq

    # B. Crash recovery: task admitted (status=pending), crash before execution -> recover_pending_tasks finishes it once
    with SessionLocal() as db:
        rec_list = orch.recover_pending_tasks(db, tenant_id=tenant_id)
        db.commit()
        rec_list_2 = orch.recover_pending_tasks(db, tenant_id=tenant_id)
        db.commit()
    assert len(rec_list) == 1
    assert rec_list[0].status == "succeeded"
    assert len(rec_list_2) == 0

    # C. Reducer error -> task failed with reason, pointer unchanged
    with SessionLocal() as db:
        ptr_before = db.execute(
            sa.select(project_current_assessment.c.assessment_id).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).scalar_one()

        bad_admit = orch.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="manual",
            trigger_ref="req-bad-reducer",
            dependency_refs=[{"bad": "schema_violation"}],
        )
        db.commit()
        bad_res = orch.execute_task(db, tenant_id, bad_admit.id)
        db.commit()

        ptr_after = db.execute(
            sa.select(project_current_assessment.c.assessment_id).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).scalar_one()
        run_row = db.execute(
            sa.select(runs.c.state).where(runs.c.tenant_id == tenant_id, runs.c.id == bad_admit.run_id)
        ).scalar_one()

    assert bad_res.status == "failed"
    assert bad_res.error_reason is not None
    assert ptr_after == ptr_before
    assert run_row == "failed"

    # D. Verifier + Analysis pipeline enabled
    cfg_full = Settings.model_validate(
        {
            "EAP_COLLECTOR_ALLOW_REAL_ROOTS": False,
            "EAP_COLLECTOR_FIXTURES_ROOT": str(tmp_path),
            "PPM_VERIFIER_ENABLED": True,
            "PPM_ANALYSIS_ENABLED": True,
        }
    )
    orch_full = AssessmentOrchestrator(settings=cfg_full, model_provider=FakeModelProvider([FakeSuccess(payload='{"claims": []}')]))
    with SessionLocal() as db:
        full_admit = orch_full.admit_trigger(
            db,
            tenant_id,
            project_id,
            trigger="manual",
            trigger_ref="req-full-pipeline",
            assessed_revision=rev_r1,
        )
        full_res = orch_full.execute_task(db, tenant_id, full_admit.id)
        db.commit()
    assert full_res.status == "succeeded"
    assert full_res.verifier_executed is True
    assert full_res.analysis_status == "complete"
    assert full_res.assessment is not None
    assert full_res.assessment.output["criteria"][0]["status"] == "verified"

    # E. Rollback: PPM_ORCHESTRATOR_ENABLED=false -> scheduler requests 0 assessments
    monkeypatch.setenv("PPM_ORCHESTRATOR_ENABLED", "false")
    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )
    with SessionLocal() as db:
        before_cnt = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(ppm_assessments.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        sched_res = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=src_id)
        after_cnt = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(ppm_assessments.c.tenant_id == tenant_id)
            ).scalar_one()
        )
    assert sched_res.status == "succeeded"
    assert sched_res.assessments_created == 0
    assert after_cnt == before_cnt
