"""Real parallel DB connection tests for DG2-C1 CAS and ISS-033 ingest failure/race handling (P2a-WP4)."""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from eap.contracts.rules import apply_assessment
from eap.db import (
    collector_snapshots,
    location_registrations,
    outbox,
    ppm_assessments,
    project_current_assessment,
    project_scopes,
    project_snapshots,
    sources,
)
from eap.ppm.assessments import allocate_basis_seq, approve_baseline, create_assessment
from eap.ppm.collector.collector import PpmCollector
from eap.ppm.path import canonicalize_root


def _seed_project_for_cas(db: Session, tenant_id: str, project_id: str) -> str:
    src_id = f"src-{uuid.uuid4().hex[:10]}"
    csnap_id = f"csnap-{uuid.uuid4().hex[:10]}"
    psnap_id = f"psnap-{uuid.uuid4().hex[:10]}"
    now = dt.datetime(2026, 11, 15, 12, 0, 0, tzinfo=dt.UTC)
    db.execute(
        sources.insert().values(
            source_id=src_id,
            tenant_id=tenant_id,
            canonical_root=f"/fixtures/{project_id}",
            repo_identity=f"git@example.com:{tenant_id}/{project_id}.git",
            created_at=now,
            updated_at=now,
        )
    )
    db.execute(
        collector_snapshots.insert().values(
            id=csnap_id,
            tenant_id=tenant_id,
            source_id=src_id,
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            git_commit="rev-cas-1",
            git_dirty=False,
            tree_hash="rev-cas-1",
            manifest={},
            created_at=now,
        )
    )
    db.execute(
        project_snapshots.insert().values(
            id=psnap_id,
            tenant_id=tenant_id,
            snapshot_id=csnap_id,
            source_id=src_id,
            project_id=project_id,
            snapshot_seq=1,
            scope_hash=f"scope-{uuid.uuid4().hex[:12]}",
            metadata_root=".ppm",
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            artifact_aliases={},
            notes=[],
            claims=[],
            created_at=now,
        )
    )
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
                "verification_refs": [{"kind": "test_id", "ref": "test_1"}],
            }
        ],
        approved_by="owner",
        source="fixture:cas",
    )
    db.commit()
    return psnap_id


def test_cas_parallel_db_connections_reverse_completion_never_regresses(
    db_engine: sa.Engine,
) -> None:
    """DG2-C1: Two real parallel DB connections with basis_seq n and n+1 finishing in reverse order."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-cas-{uuid.uuid4().hex[:8]}"
    project_id = "proj-cas"

    with SessionLocal() as setup_db:
        psnap_id = _seed_project_for_cas(setup_db, tenant_id, project_id)
        seq_n = allocate_basis_seq(setup_db, tenant_id, project_id)
        seq_n1 = allocate_basis_seq(setup_db, tenant_id, project_id)
        setup_db.commit()

    assert seq_n1 == seq_n + 1

    n1_committed = threading.Event()
    results: dict[str, Any] = {}
    errors: list[Exception] = []

    def worker_n1() -> None:
        try:
            with SessionLocal() as s2:
                rec = create_assessment(
                    s2,
                    tenant_id,
                    project_id,
                    trigger="snapshot",
                    basis_seq=seq_n1,
                    project_snapshot_id=psnap_id,
                    assessed_revision="rev-cas-1",
                )
                s2.commit()
                results["n1"] = rec
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            n1_committed.set()

    def worker_n() -> None:
        try:
            # Wait until n+1 has committed first (reverse completion order)
            assert n1_committed.wait(timeout=10.0)
            with SessionLocal() as s1:
                rec = create_assessment(
                    s1,
                    tenant_id,
                    project_id,
                    trigger="snapshot",
                    basis_seq=seq_n,
                    project_snapshot_id=psnap_id,
                    assessed_revision="rev-cas-1",
                )
                s1.commit()
                results["n"] = rec
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker_n)
    t2 = threading.Thread(target=worker_n1)
    t1.start()
    t2.start()
    t1.join(timeout=15.0)
    t2.join(timeout=15.0)

    assert not errors, f"Parallel CAS workers raised: {errors}"
    rec_n1 = results["n1"]
    rec_n = results["n"]
    assert rec_n1.is_current is True
    assert rec_n.is_current is False

    # Verify DB state: pointer stays at n+1, both n and n+1 stored in ppm_assessments
    with SessionLocal() as check_db:
        ptr = check_db.execute(
            sa.select(project_current_assessment).where(
                project_current_assessment.c.tenant_id == tenant_id,
                project_current_assessment.c.project_id == project_id,
            )
        ).mappings().one()
        assert int(ptr["basis_seq"]) == seq_n1
        assert str(ptr["assessment_id"]) == rec_n1.id

        rows = check_db.execute(
            sa.select(ppm_assessments.c.id, ppm_assessments.c.basis_seq)
            .where(
                ppm_assessments.c.tenant_id == tenant_id,
                ppm_assessments.c.project_id == project_id,
            )
            .order_by(ppm_assessments.c.basis_seq.asc())
        ).mappings().all()
        assert [(r["id"], int(r["basis_seq"])) for r in rows] == [
            (rec_n.id, seq_n),
            (rec_n1.id, seq_n1),
        ]

    # Contract rule parity check
    winner, loser = apply_assessment(
        {"assessment_id": rec_n1.id, "basis_seq": seq_n1},
        {"assessment_id": rec_n.id, "basis_seq": seq_n},
    )
    assert winner["basis_seq"] == seq_n1
    assert loser["basis_seq"] == seq_n


def _seed_collector_fixture_source(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    root: Path,
) -> str:
    source_id = f"src-{uuid.uuid4().hex[:10]}"
    canon = canonicalize_root(str(root), "windows" if str(root)[1:2] == ":" else "linux")
    now = dt.datetime.now(dt.UTC)
    db.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant_id,
            canonical_root=canon,
            repo_identity=f"git@example.com:{tenant_id}/{source_id}.git",
            created_at=now,
            updated_at=now,
        )
    )
    db.execute(
        project_scopes.insert().values(
            id=f"scope-{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            source_id=source_id,
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
            source_id=source_id,
            project_id=project_id,
            metadata_root=".ppm",
            state="active",
            revision=1,
            registered_by_ref="owner",
            host_id="host-test",
            connector_id="conn-test",
            root_path=str(root),
            scan_scope=[".ppm"],
            artifact_aliases={"architecture": [], "blueprint": [], "implementation_plan": []},
            notes_globs=[".ppm/notes/*.md"],
            deny_globs=[],
            max_file_bytes=65536,
            classification="internal",
            egress_policy_ref="local_only",
            identity={"repo_identity": f"git@example.com:{tenant_id}/{source_id}.git"},
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return source_id


def test_iss033_injected_ingest_exception_commits_snapshot_and_records_outbox(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISS-033: Ingest exception rolls back cleanly, writes ppm.ingest.failed, and returns ingest_status=failed."""
    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-iss033-{uuid.uuid4().hex[:8]}"
    project_id = "proj-iss033"
    repo_root = tmp_path / "repo_fail"
    (repo_root / ".ppm").mkdir(parents=True)
    (repo_root / ".ppm" / "notes.md").write_text("hello", encoding="utf-8")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_collector_fixture_source(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected poison ingest failure")

    monkeypatch.setattr("eap.ppm.ingest.ingest_snapshot", _boom)

    collector = PpmCollector(ingest_enabled=True)
    with SessionLocal() as db:
        res = collector.scan_source(db, tenant_id=tenant_id, source_id=source_id)
        assert res.ingest_status == "failed"
        assert res.already_ingested is False

        # Session must remain usable afterwards without PendingRollbackError
        snap_count = db.execute(
            sa.select(sa.func.count())
            .select_from(project_snapshots)
            .where(project_snapshots.c.tenant_id == tenant_id)
        ).scalar_one()
        assert snap_count == 1

        fail_evt = db.execute(
            sa.select(outbox).where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.event_type == "ppm.ingest.failed",
            )
        ).mappings().one()
        assert "injected poison ingest failure" in fail_evt["payload"]["reason"]
        assert fail_evt["payload"]["project_snapshot_id"]


def test_iss033_concurrent_first_ingest_checkpoint_race_returns_already_ingested(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISS-033: Two concurrent first ingests racing on ppm_ingest_checkpoints -> one ok, one already_ingested."""
    import eap.ppm.ingest as ingest_mod

    SessionLocal = sessionmaker(bind=db_engine, future=True)
    tenant_id = f"tenant-race-{uuid.uuid4().hex[:8]}"
    project_id = "proj-race"
    repo_root = tmp_path / "repo_race"
    (repo_root / ".ppm").mkdir(parents=True)
    (repo_root / ".ppm" / "notes.md").write_text("concurrent note", encoding="utf-8")

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(tmp_path))

    with SessionLocal() as db:
        source_id = _seed_collector_fixture_source(
            db, tenant_id=tenant_id, project_id=project_id, root=repo_root
        )
        # Capture snapshot with ingest_enabled=False first
        collector_no_ingest = PpmCollector(ingest_enabled=False)
        res0 = collector_no_ingest.scan_source(db, tenant_id=tenant_id, source_id=source_id)
        assert res0.ingest_status == "disabled"
        psnap_id = db.execute(
            sa.select(project_snapshots.c.id).where(
                project_snapshots.c.tenant_id == tenant_id,
                project_snapshots.c.project_id == project_id,
            )
        ).scalar_one()

    # Synchronize two threads so both call ingest_snapshot before either inserts checkpoint
    barrier = threading.Barrier(2, timeout=10.0)
    orig_ingest = ingest_mod.ingest_snapshot
    first_call_lock = threading.Lock()
    entered = 0

    def synced_ingest(conn: Any, t_id: str, snap_id: str) -> Any:
        nonlocal entered
        with first_call_lock:
            entered += 1
            should_wait = entered <= 2
        if should_wait:
            barrier.wait()
        return orig_ingest(conn, t_id, snap_id)

    outcomes: list[tuple[str, bool]] = []
    errors: list[Exception] = []
    out_lock = threading.Lock()

    def run_ingest_worker() -> None:
        from sqlalchemy.exc import IntegrityError

        try:
            with SessionLocal() as s:
                already = False
                try:
                    rep = synced_ingest(s, tenant_id, psnap_id)
                    s.commit()
                    already = rep.already_ingested
                except IntegrityError:
                    s.rollback()
                    rep = synced_ingest(s, tenant_id, psnap_id)
                    s.commit()
                    already = rep.already_ingested
                with out_lock:
                    outcomes.append(("ok", already))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=run_ingest_worker)
    t2 = threading.Thread(target=run_ingest_worker)
    t1.start()
    t2.start()
    t1.join(timeout=15.0)
    t2.join(timeout=15.0)

    assert not errors, f"Concurrent ingest workers raised: {errors}"
    assert sorted(outcomes) == [("ok", False), ("ok", True)]
