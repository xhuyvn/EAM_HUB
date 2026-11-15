"""Failure and concurrency tests for P2a-WP5 Scan Scheduler & ISS-034 (C35, DG2-C1).

Executed against real PostgreSQL (``eap_test``) with real parallel connections.
Covers:
1. Production config & API interval >= 60s enforcement vs explicit test override hook.
2. Fixture modification auto-scan within debounce + interval (no manual command).
3. Redelivered trigger occurrence_id is a strict idempotent no-op (sequential & parallel).
4. Rescan of unchanged data (ISS-034): 0 new assessments, 0 new domain ingest rows,
   ``last_successful_scan_at`` updated.
5. Two schedulers racing on one source -> at most one active scan; stale fencing token
   cannot commit a snapshot.
6. Host sleep simulation (clock jump past several intervals) -> single catch-up scan.
7. Scheduler disabled (``PPM_SCHEDULER_ENABLED=false``) -> auto triggers do nothing;
   manual scan still works.
8. Scan failure (ISS-028 physical root replacement + canary redaction) -> redacted
   ``last_error`` recorded, 0 new assessments, next attempt scheduled, no tight retry loop.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal
from eap.config import Settings
from eap.db import (
    collector_snapshots,
    location_registrations,
    ppm_assessments,
    ppm_ingested_artifacts,
    ppm_notes,
    ppm_scan_occurrences,
    ppm_scan_schedules,
    ppm_updates,
    project_scopes,
    project_snapshots,
    sources,
    tasks,
)
from eap.ppm.assessments import approve_baseline, get_assessment_detail
from eap.ppm.collector.collector import PpmCollector
from eap.ppm.path import canonicalize_root
from eap.ppm.scheduler import (
    BackgroundScanScheduler,
    PpmScanScheduler,
    compute_occurrence_id,
    validate_schedule_config,
)

TEMPLATE_UPDATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "ppm-v2"
    / "templates"
    / "project"
    / ".ppm"
    / "updates"
    / "upd-example-001.yaml"
)


def _make_valid_update(
    *,
    project_id: str,
    update_id: str,
    supersedes_update_id: str | None = None,
    recorded_at: str = "2026-11-15T10:00:00Z",
    summary: str = "Implemented AC-001",
    result: str = "passed",
) -> dict[str, Any]:
    doc = json.loads(TEMPLATE_UPDATE_PATH.read_text(encoding="utf-8"))
    doc["contract_version"] = "2.1"
    doc["project_id"] = project_id
    doc["update_id"] = update_id
    doc["supersedes_update_id"] = supersedes_update_id
    doc["recorded_at"] = recorded_at
    doc["summary"] = summary
    doc["checks"] = [
        {
            "name": "Project tests",
            "result": result,
            "evidence_path": None,
            "tested_revision": None,
            "environment": None,
            "criterion_ids": ["AC-001"],
            "test_ids": ["test_ac001"],
        }
    ]
    return dict(doc)


def _create_fixture_repo(
    root: Path,
    *,
    project_id: str,
    update_id: str = "upd-001",
    summary: str = "Initial report for AC-001",
    note_text: str = "Kickoff note for project.",
) -> None:
    ppm_dir = root / ".ppm"
    (ppm_dir / "notes").mkdir(parents=True, exist_ok=True)
    (ppm_dir / "updates").mkdir(parents=True, exist_ok=True)

    (ppm_dir / "notes" / "kickoff.md").write_text(note_text, encoding="utf-8")
    upd = _make_valid_update(
        project_id=project_id,
        update_id=update_id,
        summary=summary,
    )
    (ppm_dir / "updates" / f"{update_id}.yaml").write_text(
        yaml.safe_dump(upd, sort_keys=False),
        encoding="utf-8",
    )


def _seed_source_and_baseline(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    root: Path,
    source_id: str | None = None,
    approve_bl: bool = True,
) -> str:
    src_id = source_id or f"src-{uuid.uuid4().hex[:10]}"
    canon = canonicalize_root(str(root), "windows" if os.name == "nt" else "linux")
    now = dt.datetime.now(dt.UTC)
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
            metadata_root=".ppm",
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
            metadata_root=".ppm",
            state="active",
            revision=1,
            registered_by_ref="owner",
            approved_by_ref="admin",
            host_id="host-test",
            connector_id="conn-test",
            root_path=canon,
            scan_scope=[".ppm"],
            artifact_aliases={"architecture": [], "blueprint": [], "implementation_plan": []},
            notes_globs=[".ppm/notes/*.md"],
            deny_globs=[],
            classification="internal",
            egress_policy_ref="local_only",
            identity={"repo_identity": f"git@example.com:{tenant_id}/{src_id}.git"},
            created_at=now,
            updated_at=now,
        )
    )
    if approve_bl:
        approve_baseline(
            db,
            tenant_id,
            project_id,
            baseline_revision=1,
            criteria=[
                {
                    "criterion_id": "AC-001",
                    "deliverable_id": "DEL-001",
                    "owner_project_id": project_id,
                    "weight": 1.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_ac001"}],
                }
            ],
            approved_by="owner",
            source="fixture:c35",
        )
    db.commit()
    return src_id


def test_c35_config_validation_enforces_60s_minimum_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default scheduler config is disabled/300s/5s; production overrides < 60s are rejected."""
    for env_key in (
        "PPM_SCHEDULER_ENABLED",
        "PPM_SCAN_INTERVAL_SECONDS",
        "PPM_SCAN_DEBOUNCE_SECONDS",
    ):
        monkeypatch.delenv(env_key, raising=False)

    defaults = Settings.model_validate({})
    assert defaults.ppm_scheduler_enabled is False
    assert defaults.ppm_scan_interval_seconds == 300
    assert defaults.ppm_scan_debounce_seconds == 5

    with pytest.raises(ValidationError):
        Settings.model_validate({"PPM_SCAN_INTERVAL_SECONDS": 59})

    with pytest.raises(ValidationError):
        Settings.model_validate({"PPM_SCAN_DEBOUNCE_SECONDS": 0})

    # Scheduler / validator production check
    with pytest.raises(ValueError, match=">= 60 seconds"):
        validate_schedule_config(59, 5)

    validate_schedule_config(60, 5)
    validate_schedule_config(300, 5)

    with pytest.raises(ValueError, match=">= 60 seconds"):
        PpmScanScheduler(default_interval_seconds=30, _allow_sub_minimum_interval_for_testing=False)

    # Explicit test-only override permits short test intervals
    validate_schedule_config(2, 1, _allow_sub_minimum_interval_for_testing=True)


def test_c35_auto_scan_on_fixture_modification_within_debounce_and_interval(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modify a note + report in fixture repo -> scan runs automatically, no manual command."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-auto-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-auto"
    repo_root = tmp_path / "fixture_repo_auto"
    _create_fixture_repo(repo_root, project_id=project_id, update_id="upd-001")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))
    monkeypatch.setenv("PPM_SCHEDULER_ENABLED", "true")

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            root=repo_root,
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        default_interval_seconds=2,
        default_debounce_seconds=1,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
        _allow_sub_minimum_interval_for_testing=True,
    )

    with SessionLocal() as db:
        scheduler.ensure_schedule(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            interval_seconds=2,
            debounce_seconds=1,
            next_due_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=2),
            _allow_sub_minimum_interval_for_testing=True,
        )
        initial_res = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id)
        assert initial_res.status == "succeeded"
        assert initial_res.assessments_created == 1

    bg = BackgroundScanScheduler(
        scheduler,
        SessionLocal,
        poll_interval_seconds=0.15,
        run_startup_catchup=False,
    )
    bg.start()
    try:
        # Modify a note and add a superseding report WITHOUT calling any manual scan trigger
        (repo_root / ".ppm" / "notes" / "kickoff.md").write_text(
            "Updated kickoff note with verified progress details.",
            encoding="utf-8",
        )
        upd2 = _make_valid_update(
            project_id=project_id,
            update_id="upd-002",
            supersedes_update_id="upd-001",
            recorded_at="2026-11-15T10:05:00Z",
            summary="Follow-up report after fixture modification",
        )
        (repo_root / ".ppm" / "updates" / "upd-002.yaml").write_text(
            yaml.safe_dump(upd2, sort_keys=False),
            encoding="utf-8",
        )

        # Wait within debounce (1s) + interval (2s) + small CI headroom
        deadline = time.monotonic() + 8.0
        auto_occ_count = 0
        while time.monotonic() < deadline:
            with SessionLocal() as check_db:
                auto_occ_count = int(
                    check_db.execute(
                        sa.select(sa.func.count())
                        .select_from(ppm_scan_occurrences)
                        .where(
                            ppm_scan_occurrences.c.tenant_id == tenant_id,
                            ppm_scan_occurrences.c.source_id == source_id,
                            ppm_scan_occurrences.c.trigger.in_(["watcher", "interval"]),
                            ppm_scan_occurrences.c.status == "succeeded",
                        )
                    ).scalar_one()
                )
            if auto_occ_count >= 1:
                break
            time.sleep(0.15)

        assert auto_occ_count >= 1, "Background scheduler did not trigger automatic scan after modification"
    finally:
        bg.stop(timeout=5.0)

    # Verify ingested domain state, task creation via TaskService, assessment creation, and API readability
    with SessionLocal() as check_db:
        asmt_count = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.project_id == project_id,
                )
            ).scalar_one()
        )
        assert asmt_count == 2

        note_bodies = check_db.execute(
            sa.select(ppm_notes.c.body).where(
                ppm_notes.c.tenant_id == tenant_id,
                ppm_notes.c.project_id == project_id,
            )
        ).scalars().all()
        assert any("Updated kickoff note" in b for b in note_bodies)

        upd_ids = check_db.execute(
            sa.select(ppm_updates.c.update_id).where(
                ppm_updates.c.tenant_id == tenant_id,
                ppm_updates.c.project_id == project_id,
            )
        ).scalars().all()
        assert "upd-002" in upd_ids

        scan_tasks = check_db.execute(
            sa.select(tasks).where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.task_type == "ppm.inventory.scan",
            )
        ).mappings().all()
        assert len(scan_tasks) >= 2

        obs = PpmScanScheduler.get_source_observability(
            check_db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs is not None
        assert obs.last_scan_started_at is not None
        assert obs.last_successful_scan_at is not None
        assert obs.last_error is None
        assert obs.next_due_at is not None

        # Verify HUD-readable HTTP endpoint /v1/ppm/sources/{source_id}/schedule
        app.dependency_overrides[get_db] = lambda: check_db
        app.dependency_overrides[get_principal] = lambda: Principal(
            tenant_id=tenant_id, principal_id="hud-reader", roles=["admin"]
        )
        try:
            with TestClient(app) as client:
                r = client.get(f"/v1/ppm/sources/{source_id}/schedule")
                assert r.status_code == 200
                body = r.json()
                assert body["last_scan_started_at"] is not None
                assert body["last_successful_scan_at"] is not None
                assert body["last_error"] is None
                assert body["next_due_at"] is not None

                # API rejects interval override < 60s
                r_bad = client.put(
                    f"/v1/ppm/sources/{source_id}/schedule",
                    json={"interval_seconds": 30, "debounce_seconds": 5},
                )
                assert r_bad.status_code == 422
        finally:
            app.dependency_overrides.clear()


def test_c35_same_occurrence_delivered_twice_runs_one_scan(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redelivering the same occurrence_id executes once and treats redelivery as a no-op."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-occ-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-occ"
    repo_root = tmp_path / "fixture_repo_occ"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )
    occ_id = compute_occurrence_id(
        tenant_id=tenant_id,
        source_id=source_id,
        trigger="interval",
        window_key="interval:1763200000",
    )

    with SessionLocal() as db:
        res1 = scheduler.run_occurrence(
            db, tenant_id=tenant_id, source_id=source_id, trigger="interval", occurrence_id=occ_id
        )
        res2 = scheduler.run_occurrence(
            db, tenant_id=tenant_id, source_id=source_id, trigger="interval", occurrence_id=occ_id
        )

    assert res1.executed is True
    assert res1.status == "succeeded"
    assert res2.executed is False
    assert res2.status == "duplicate_occurrence"

    with SessionLocal() as check_db:
        snap_count = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(
                    collector_snapshots.c.tenant_id == tenant_id,
                    collector_snapshots.c.source_id == source_id,
                )
            ).scalar_one()
        )
        task_count = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(tasks)
                .where(tasks.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        occ_count = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_scan_occurrences)
                .where(
                    ppm_scan_occurrences.c.tenant_id == tenant_id,
                    ppm_scan_occurrences.c.source_id == source_id,
                )
            ).scalar_one()
        )
        assert snap_count == 1
        assert task_count == 1
        assert occ_count == 1


def _collect_domain_counts(db: Session, tenant_id: str, project_id: str) -> dict[str, int]:
    return {
        "assessments": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.project_id == project_id,
                )
            ).scalar_one()
        ),
        "ppm_notes": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_notes)
                .where(
                    ppm_notes.c.tenant_id == tenant_id,
                    ppm_notes.c.project_id == project_id,
                )
            ).scalar_one()
        ),
        "ppm_updates": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_updates)
                .where(
                    ppm_updates.c.tenant_id == tenant_id,
                    ppm_updates.c.project_id == project_id,
                )
            ).scalar_one()
        ),
        "ppm_ingested_artifacts": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_ingested_artifacts)
                .where(
                    ppm_ingested_artifacts.c.tenant_id == tenant_id,
                    ppm_ingested_artifacts.c.project_id == project_id,
                )
            ).scalar_one()
        ),
        "collector_snapshots": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(collector_snapshots.c.tenant_id == tenant_id)
            ).scalar_one()
        ),
        "project_snapshots": int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(project_snapshots)
                .where(
                    project_snapshots.c.tenant_id == tenant_id,
                    project_snapshots.c.project_id == project_id,
                )
            ).scalar_one()
        ),
    }


def test_c35_rescan_unchanged_data_zero_new_assessments_and_domain_rows(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISS-034: Rescanning unchanged repo produces 0 new assessments and 0 new notes/updates rows."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-iss034-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-iss034"
    repo_root = tmp_path / "fixture_repo_unchanged"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    with SessionLocal() as db:
        r1 = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id)
        assert r1.status == "succeeded"
        assert r1.assessments_created == 1

        before_counts = _collect_domain_counts(db, tenant_id, project_id)
        obs_before = PpmScanScheduler.get_source_observability(
            db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs_before is not None and obs_before.last_successful_scan_at is not None

        asmt_id = db.execute(
            sa.select(ppm_assessments.c.id).where(
                ppm_assessments.c.tenant_id == tenant_id,
                ppm_assessments.c.project_id == project_id,
            )
        ).scalar_one()
        detail_before = get_assessment_detail(db, tenant_id, str(asmt_id))

        # Rescan unchanged repo twice (across new snapshot ids & timestamps)
        time.sleep(0.02)
        r2 = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id)
        time.sleep(0.02)
        r3 = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id)

        assert r2.status == "succeeded"
        assert r2.assessments_created == 0
        assert r2.assessments_deduplicated == 1
        assert r3.status == "succeeded"
        assert r3.assessments_created == 0
        assert r3.assessments_deduplicated == 1

        after_counts = _collect_domain_counts(db, tenant_id, project_id)
        obs_after = PpmScanScheduler.get_source_observability(
            db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs_after is not None and obs_after.last_successful_scan_at is not None
        detail_after = get_assessment_detail(db, tenant_id, str(asmt_id))

    assert after_counts["assessments"] == before_counts["assessments"] == 1
    assert after_counts["ppm_notes"] == before_counts["ppm_notes"] == 1
    assert after_counts["ppm_updates"] == before_counts["ppm_updates"] == 1
    assert after_counts["ppm_ingested_artifacts"] == before_counts["ppm_ingested_artifacts"] == 2
    assert after_counts["collector_snapshots"] == 3
    assert after_counts["project_snapshots"] == 3
    assert obs_after.last_successful_scan_at > obs_before.last_successful_scan_at
    assert str(detail_after["last_successful_scan_at"]) > str(detail_before["last_successful_scan_at"])


def test_c35_two_schedulers_race_and_stale_fencing_token_cannot_commit(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two schedulers race on one source -> one scan; stale fencing token cannot commit."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-race-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-race"
    repo_root = tmp_path / "fixture_repo_race"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    # Part A: Two schedulers race concurrently while Winner holds active lease
    scan_entered = threading.Event()
    release_winner = threading.Event()

    class BlockingCollector(PpmCollector):
        def scan_source(self, db: Session, **kwargs: Any) -> Any:
            scan_entered.set()
            assert release_winner.wait(timeout=10.0)
            return super().scan_source(db, **kwargs)

    sched_a = PpmScanScheduler(
        SessionLocal,
        scheduler_id="sched-race-a",
        enabled=True,
        collector=BlockingCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )
    sched_b = PpmScanScheduler(
        SessionLocal,
        scheduler_id="sched-race-b",
        enabled=True,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    out_a: dict[str, Any] = {}
    out_b: dict[str, Any] = {}

    def run_a() -> None:
        with SessionLocal() as s_a:
            out_a["res"] = sched_a.run_occurrence(
                s_a,
                tenant_id=tenant_id,
                source_id=source_id,
                trigger="interval",
                occurrence_id="occ-race-a",
            )

    def run_b() -> None:
        assert scan_entered.wait(timeout=10.0)
        with SessionLocal() as s_b:
            out_b["res"] = sched_b.run_occurrence(
                s_b,
                tenant_id=tenant_id,
                source_id=source_id,
                trigger="interval",
                occurrence_id="occ-race-b",
            )
        release_winner.set()

    t_a = threading.Thread(target=run_a)
    t_b = threading.Thread(target=run_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=15.0)
    t_b.join(timeout=15.0)

    assert out_a["res"].executed is True
    assert out_a["res"].status == "succeeded"
    assert out_b["res"].executed is False
    assert out_b["res"].status == "lease_busy"

    # Part B: Stale fencing token cannot commit a snapshot when overtaken by higher token
    t0 = dt.datetime(2026, 11, 15, 12, 0, 0, tzinfo=dt.UTC)
    before_snaps = 0
    with SessionLocal() as db:
        before_snaps = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(collector_snapshots.c.tenant_id == tenant_id)
            ).scalar_one()
        )

    stale_ready = threading.Event()
    winner_committed = threading.Event()
    stale_out: dict[str, Any] = {}
    winner_out: dict[str, Any] = {}

    class SlowBeforeCommitCollector(PpmCollector):
        def scan_source(self, db: Session, **kwargs: Any) -> Any:
            # Pause right before snapshot persistence so Scheduler B steals expired lease
            stale_ready.set()
            assert winner_committed.wait(timeout=10.0)
            return super().scan_source(db, **kwargs)

    sched_stale = PpmScanScheduler(
        SessionLocal,
        scheduler_id="sched-stale",
        enabled=True,
        lease_ttl_seconds=10,
        collector=SlowBeforeCommitCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )
    sched_winner = PpmScanScheduler(
        SessionLocal,
        scheduler_id="sched-winner",
        enabled=True,
        lease_ttl_seconds=60,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    def run_stale() -> None:
        with SessionLocal() as s1:
            stale_out["res"] = sched_stale.run_occurrence(
                s1,
                tenant_id=tenant_id,
                source_id=source_id,
                trigger="manual",
                occurrence_id="occ-stale-token",
                now=t0,
            )

    def run_winner() -> None:
        assert stale_ready.wait(timeout=10.0)
        try:
            with SessionLocal() as s2:
                # Lease acquired at t0 with TTL=10s is expired at t0 + 30s -> Winner steals lease
                winner_out["res"] = sched_winner.run_occurrence(
                    s2,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    trigger="manual",
                    occurrence_id="occ-winner-token",
                    now=t0 + dt.timedelta(seconds=30),
                )
        finally:
            winner_committed.set()

    ts = threading.Thread(target=run_stale)
    tw = threading.Thread(target=run_winner)
    ts.start()
    tw.start()
    ts.join(timeout=15.0)
    tw.join(timeout=15.0)

    assert winner_out["res"].status == "succeeded"
    assert winner_out["res"].executed is True
    assert int(winner_out["res"].fencing_token) > int(stale_out["res"].fencing_token)
    assert stale_out["res"].status == "stale_fencing_token"
    assert stale_out["res"].executed is False

    with SessionLocal() as check_db:
        after_snaps = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(collector_snapshots.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        # Only winner's snapshot was committed (+1), stale worker's snapshot rolled back!
        assert after_snaps == before_snaps + 1
        sched_row = check_db.execute(
            sa.select(ppm_scan_schedules).where(
                ppm_scan_schedules.c.tenant_id == tenant_id,
                ppm_scan_schedules.c.source_id == source_id,
            )
        ).mappings().one()
        assert int(sched_row["fencing_token"]) == int(winner_out["res"].fencing_token)


def test_c35_host_sleep_clock_jump_produces_single_catchup_scan(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulating host sleep across multiple missed intervals runs ONE catch-up scan."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-sleep-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-sleep"
    repo_root = tmp_path / "fixture_repo_sleep"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        default_interval_seconds=300,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    t0 = dt.datetime(2026, 11, 15, 8, 0, 0, tzinfo=dt.UTC)
    due_at = t0 + dt.timedelta(seconds=300)
    wake_at = t0 + dt.timedelta(seconds=3600)  # 12 intervals missed during host sleep

    with SessionLocal() as db:
        scheduler.ensure_schedule(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            interval_seconds=300,
            debounce_seconds=5,
            next_due_at=due_at,
            now=t0,
        )
        res_wake = scheduler.tick_interval(db, tenant_id=tenant_id, now=wake_at)
        res_immediate_follow = scheduler.tick_interval(
            db, tenant_id=tenant_id, now=wake_at + dt.timedelta(seconds=1)
        )

    assert len(res_wake) == 1
    assert res_wake[0].status == "succeeded"
    assert res_wake[0].executed is True
    assert res_immediate_follow == []

    with SessionLocal() as check_db:
        snap_count = int(
            check_db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(collector_snapshots.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        assert snap_count == 1
        obs = PpmScanScheduler.get_source_observability(
            check_db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs is not None
        assert obs.next_due_at == wake_at + dt.timedelta(seconds=300)


def test_c35_scheduler_disabled_blocks_auto_triggers_but_manual_works(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When PPM_SCHEDULER_ENABLED=false, auto triggers do nothing while manual scan still works."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-dis-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-dis"
    repo_root = tmp_path / "fixture_repo_disabled"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))
    monkeypatch.setenv("PPM_SCHEDULER_ENABLED", "false")

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )
    assert scheduler.is_enabled is False

    t_now = dt.datetime.now(dt.UTC)
    with SessionLocal() as db:
        assert scheduler.startup_catch_up(db, tenant_id=tenant_id, now=t_now) == []
        assert scheduler.tick_interval(db, tenant_id=tenant_id, now=t_now) == []
        scheduler.record_file_change(
            tenant_id=tenant_id,
            source_id=source_id,
            changed_path=".ppm/notes/kickoff.md",
            now=t_now - dt.timedelta(seconds=30),
        )
        assert scheduler.scan_filesystem_changes(db, tenant_id=tenant_id, now=t_now) == []

        ignored = scheduler.run_occurrence(
            db,
            tenant_id=tenant_id,
            source_id=source_id,
            trigger="interval",
            occurrence_id="occ-disabled-check",
            now=t_now,
        )
        assert ignored.executed is False
        assert ignored.status == "scheduler_disabled"

        snap_count_before_manual = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(collector_snapshots)
                .where(collector_snapshots.c.tenant_id == tenant_id)
            ).scalar_one()
        )
        assert snap_count_before_manual == 0

        # Manual scan still succeeds cleanly even when PPM_SCHEDULER_ENABLED=false
        manual_res = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id)
        assert manual_res.executed is True
        assert manual_res.status == "succeeded"
        assert manual_res.assessments_created == 1


def test_c35_scan_failure_iss028_records_redacted_error_schedules_next_attempt(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scan failure (ISS-028 replaced root) records redacted last_error, 0 new assessments, no tight retry loop."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-c35-fail-{uuid.uuid4().hex[:8]}"
    project_id = "proj-c35-fail"
    repo_root = tmp_path / "fixture_repo_fail"
    _create_fixture_repo(repo_root, project_id=project_id)

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_source_and_baseline(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    scheduler = PpmScanScheduler(
        SessionLocal,
        enabled=True,
        default_interval_seconds=300,
        collector=PpmCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
    )

    t_ok = dt.datetime(2026, 11, 15, 9, 0, 0, tzinfo=dt.UTC)
    with SessionLocal() as db:
        first_ok = scheduler.trigger_manual(db, tenant_id=tenant_id, source_id=source_id, now=t_ok)
        assert first_ok.status == "succeeded"
        asmt_before = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.project_id == project_id,
                )
            ).scalar_one()
        )
        assert asmt_before == 1

    # Replace physical root (ISS-028): delete and recreate directory so physical fs_identity changes
    # Keep a sibling directory with token=canary in path or verify redaction directly on error
    shutil.rmtree(repo_root)
    _create_fixture_repo(repo_root, project_id=project_id)

    # First verify real RootReplacedSecurityError path
    t_fail = t_ok + dt.timedelta(seconds=300)
    with SessionLocal() as db:
        res_fail = scheduler.tick_interval(db, tenant_id=tenant_id, now=t_fail)
        assert len(res_fail) == 1
        assert res_fail[0].status == "failed"
        assert res_fail[0].error is not None
        assert "RootReplacedSecurityError" in res_fail[0].error

        asmt_after = int(
            db.execute(
                sa.select(sa.func.count())
                .select_from(ppm_assessments)
                .where(
                    ppm_assessments.c.tenant_id == tenant_id,
                    ppm_assessments.c.project_id == project_id,
                )
            ).scalar_one()
        )
        assert asmt_after == asmt_before

        obs = PpmScanScheduler.get_source_observability(
            db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs is not None
        assert obs.last_error is not None
        assert "RootReplacedSecurityError" in obs.last_error
        # Previous last_successful_scan_at preserved, next_due_at scheduled 300s later
        assert obs.last_successful_scan_at == t_ok
        assert obs.next_due_at == t_fail + dt.timedelta(seconds=300)

        # Calling tick_interval 1s later does NOT retry in a tight loop
        no_tight_retry = scheduler.tick_interval(
            db, tenant_id=tenant_id, now=t_fail + dt.timedelta(seconds=1)
        )
        assert no_tight_retry == []

        # Also verify secret/canary redaction in persisted last_error
        class CanaryErrorCollector(PpmCollector):
            def scan_source(self, db: Session, **kwargs: Any) -> Any:
                raise RuntimeError(
                    "Collector failed near Bearer secret-token-999 and EAP-CANARY-XYZ123"
                )

        sched_redact = PpmScanScheduler(
            SessionLocal,
            enabled=True,
            collector=CanaryErrorCollector(fixtures_root=str(tmp_path), ingest_enabled=True),
        )
        res_redact = sched_redact.tick_interval(
            db, tenant_id=tenant_id, now=t_fail + dt.timedelta(seconds=300)
        )
        assert len(res_redact) == 1
        assert res_redact[0].status == "failed"
        obs_redact = PpmScanScheduler.get_source_observability(
            db, tenant_id=tenant_id, source_id=source_id
        )
        assert obs_redact is not None and obs_redact.last_error is not None
        assert "secret-token-999" not in obs_redact.last_error
        assert "EAP-CANARY-XYZ123" not in obs_redact.last_error
        assert "***" in obs_redact.last_error
