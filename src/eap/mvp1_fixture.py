"""Idempotent D0/D1 fixture seeder for MVP1 local deployment (`python -m eap.mvp1_fixture`)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from eap.agents.service import register_agent_and_binding
from eap.config import Settings, get_settings
from eap.db import get_engine
from eap.db.assessment_tables import ppm_assessments, ppm_evidence
from eap.db.snapshot_tables import collector_snapshots, project_snapshots
from eap.db.tables import (
    artifacts,
    outbox,
    project_scopes,
    runs,
    sources,
    tasks,
)
from eap.events.binding import ProducerBinding
from eap.events.relay import OutboxRelayWorker
from eap.observability import configure_logging
from eap.ppm.assessments import approve_baseline, create_assessment
from eap.run.outbox import build_event, create_outbox_record

FIXTURES_DIR = Path(__file__).resolve().parent / "agents" / "fixtures"
DEFAULT_PROJECT_ID = "proj-d0-demo"
DEFAULT_SOURCE_ID = "src-d0-demo"


def seed_mvp1_fixtures(
    db: Session,
    *,
    tenant_id: str,
    principal_id: str,
    project_id: str = DEFAULT_PROJECT_ID,
    source_id: str = DEFAULT_SOURCE_ID,
) -> dict[str, Any]:
    """Idempotently seed mock agent + binding, tasks/runs, and D0/D1 fixture assessments."""
    now = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)

    # 1. Register mock agent + binding (idempotent)
    mock_manifest = json.loads((FIXTURES_DIR / "mock_agent.json").read_text(encoding="utf-8"))
    mock_binding = json.loads((FIXTURES_DIR / "mock_binding.json").read_text(encoding="utf-8"))
    register_agent_and_binding(
        db,
        tenant_id=tenant_id,
        manifest=mock_manifest,
        binding=mock_binding,
    )

    # 2. Seed sample tasks & runs if absent
    existing_task = db.execute(
        sa.select(tasks.c.id).where(
            tasks.c.tenant_id == tenant_id,
            tasks.c.id == "task-mvp1-demo-1",
        )
    ).first()
    if existing_task is None:
        db.execute(
            pg_insert(tasks)
            .values(
                [
                    {
                        "id": "task-mvp1-demo-1",
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "client_idempotency_key": "idem-mvp1-demo-1",
                        "task_intent_key": "intent-mvp1-demo-1",
                        "agent_id": "agent-mock-provider",
                        "agent_version": "1.0.0",
                        "task_type": "ppm.project.assess",
                        "state": "completed",
                        "created_at": now,
                        "updated_at": now,
                        "deadline": now + timedelta(hours=2),
                    },
                    {
                        "id": "task-mvp1-demo-2",
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "client_idempotency_key": "idem-mvp1-demo-2",
                        "task_intent_key": "intent-mvp1-demo-2",
                        "agent_id": "agent-mock-provider",
                        "agent_version": "1.0.0",
                        "task_type": "code.patch",
                        "state": "active",
                        "created_at": now + timedelta(minutes=2),
                        "updated_at": now + timedelta(minutes=2),
                        "deadline": now + timedelta(hours=2),
                    },
                ]
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(runs)
            .values(
                [
                    {
                        "id": "r-mvp1-demo-1",
                        "run_id": "run-mvp1-demo-1",
                        "tenant_id": tenant_id,
                        "task_id": "task-mvp1-demo-1",
                        "project_id": project_id,
                        "attempt": 1,
                        "binding_id": "binding-mock-provider-local",
                        "state": "succeeded",
                        "remote_outcome": "known",
                        "lease_fencing_token": 1,
                        "created_at": now,
                        "updated_at": now + timedelta(minutes=1),
                    },
                    {
                        "id": "r-mvp1-demo-2",
                        "run_id": "run-mvp1-demo-2",
                        "tenant_id": tenant_id,
                        "task_id": "task-mvp1-demo-2",
                        "project_id": project_id,
                        "attempt": 1,
                        "binding_id": "binding-mock-provider-local",
                        "state": "running",
                        "remote_outcome": "known",
                        "lease_fencing_token": 1,
                        "created_at": now + timedelta(minutes=2),
                        "updated_at": now + timedelta(minutes=2),
                    },
                ]
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(artifacts)
            .values(
                id="art-pk-mvp1-1",
                artifact_id="art-mvp1-demo-1",
                tenant_id=tenant_id,
                task_id="task-mvp1-demo-1",
                run_id="run-mvp1-demo-1",
                content_hash="mvp1-demo-hash-1",
                content_type="application/json",
                size_bytes=128,
                storage_uri="file://fixtures/mvp1-demo.json",
                created_at=now,
            )
            .on_conflict_do_nothing()
        )

        seed_events: list[tuple[str, str, str, str, dict[str, Any], int]] = [
            (
                "run:run-mvp1-demo-1",
                "task-mvp1-demo-1",
                "run-mvp1-demo-1",
                "run.started",
                {"execution_ref": "exec-mvp1-1", "agent_version": "1.0.0"},
                1,
            ),
            (
                "run:run-mvp1-demo-1",
                "task-mvp1-demo-1",
                "run-mvp1-demo-1",
                "run.finished",
                {"outcome": "succeeded", "result_ref": "artifact:art-mvp1-demo-1"},
                2,
            ),
            (
                "run:run-mvp1-demo-2",
                "task-mvp1-demo-2",
                "run-mvp1-demo-2",
                "run.started",
                {"execution_ref": "exec-mvp1-2", "agent_version": "1.0.0"},
                1,
            ),
            (
                "run:run-mvp1-demo-2",
                "task-mvp1-demo-2",
                "run-mvp1-demo-2",
                "run.progress",
                {"completed_units": 1, "total_units": 2, "unit": "steps", "stage": "verifying"},
                2,
            ),
        ]
        relay = OutboxRelayWorker(enabled=True, batch_size=100)
        for stream_id, t_id, r_id, ev_type, payload, seq in seed_events:
            ev_obj = build_event(
                event_type=ev_type,  # type: ignore[arg-type]
                tenant_id=tenant_id,
                task_id=t_id,
                run_id=r_id,
                attempt=1,
                producer_id="mock-provider",
                producer_seq=seq,
                payload=payload,
                occurred_at=now,
            )
            ob = create_outbox_record(ev_obj, stream_id=stream_id)
            db.execute(outbox.insert().values(**ob))
            relay.relay_stream_batch(
                db.connection(),
                tenant_id=tenant_id,
                stream_id=stream_id,
                binding_provider=lambda tid, pid: ProducerBinding(
                    tenant_id=tid,
                    producer_id=pid,
                    binding_id="binding-mock-provider-local",
                    certified_capabilities={"events": True},
                ),
            )
        db.commit()

    # 3. Seed PPM source, scope, baseline, snapshots, evidence, and assessments A1 & A2
    existing_assessments = db.execute(
        sa.select(sa.func.count())
        .select_from(ppm_assessments)
        .where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.project_id == project_id,
        )
    ).scalar_one()

    if existing_assessments == 0:
        db.execute(
            pg_insert(sources)
            .values(
                source_id=source_id,
                tenant_id=tenant_id,
                canonical_root="/fixtures/proj-d0-demo",
                repo_identity="git:mvp1-d0-demo",
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(project_scopes)
            .values(
                id="scope-mvp1-demo",
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                metadata_root=".ppm",
                status="active",
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing()
        )
        approve_baseline(
            db,
            tenant_id=tenant_id,
            project_id=project_id,
            baseline_revision=1,
            approved_by=principal_id,
            source="mvp1-fixture",
            criteria=[
                {
                    "criterion_id": "AC-001",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": project_id,
                    "title": "Export CSV",
                    "weight": 2.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_ac001_export"}],
                },
                {
                    "criterion_id": "AC-002",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": project_id,
                    "title": "Login",
                    "weight": 1.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_ac002_login"}],
                },
                {
                    "criterion_id": "AC-003",
                    "deliverable_id": "DEL-2",
                    "owner_project_id": project_id,
                    "title": "Report",
                    "weight": 1.0,
                    "required": False,
                    "verification_refs": [{"kind": "test_id", "ref": "test_ac003_report"}],
                },
            ],
        )

        # Snapshot 1 (rev-r1) -> A1
        db.execute(
            pg_insert(collector_snapshots)
            .values(
                id="csnap-mvp1-1",
                tenant_id=tenant_id,
                source_id=source_id,
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                git_commit="rev-r1",
                git_dirty=False,
                tree_hash="tree-r1",
                manifest={},
                created_at=now,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(project_snapshots)
            .values(
                id="psnap-mvp1-1",
                snapshot_id="csnap-mvp1-1",
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                snapshot_seq=1,
                scope_hash="scope-h1",
                metadata_root=".ppm",
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                claims=[
                    {
                        "criterion_id": "AC-001",
                        "reported_status": "claimed_done",
                        "source_file": ".ppm/updates/upd-0001.yaml",
                        "claim_id": "upd-0001:chk:0",
                    }
                ],
                created_at=now,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(ppm_evidence)
            .values(
                id="ev-pk-mvp1-1",
                evidence_id="ev-mvp1-r1-pass",
                tenant_id=tenant_id,
                project_id=project_id,
                revision="rev-r1",
                trust_class="verifier_observed",
                producer="simulated-verifier",
                test_id="test_ac001_export",
                covered_test_ids=["test_ac001_export"],
                outcome="passed",
                output_hash="h1",
                simulated=True,
                collected_at=now,
            )
            .on_conflict_do_nothing()
        )
        db.commit()
        create_assessment(db, tenant_id=tenant_id, project_id=project_id, trigger="manual", now=now)
        db.commit()

        # Snapshot 2 (rev-r2) -> A2
        t2 = now + timedelta(minutes=5)
        db.execute(
            pg_insert(collector_snapshots)
            .values(
                id="csnap-mvp1-2",
                tenant_id=tenant_id,
                source_id=source_id,
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                git_commit="rev-r2",
                git_dirty=False,
                tree_hash="tree-r2",
                manifest={},
                created_at=t2,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(project_snapshots)
            .values(
                id="psnap-mvp1-2",
                snapshot_id="csnap-mvp1-2",
                tenant_id=tenant_id,
                source_id=source_id,
                project_id=project_id,
                snapshot_seq=2,
                scope_hash="scope-h2",
                metadata_root=".ppm",
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                claims=[
                    {
                        "criterion_id": "AC-001",
                        "reported_status": "claimed_done",
                        "source_file": ".ppm/updates/upd-0002.yaml",
                        "claim_id": "upd-0002:chk:0",
                        "summary": "Export done",
                    },
                    {
                        "criterion_id": "AC-003",
                        "reported_status": "claimed_done",
                        "source_file": ".ppm/updates/upd-0003.yaml",
                        "claim_id": "upd-0003:chk:0",
                        "summary": "Self-written junit only",
                    },
                ],
                created_at=t2,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            pg_insert(ppm_evidence)
            .values(
                [
                    {
                        "id": "ev-pk-mvp1-2",
                        "evidence_id": "ev-mvp1-r2-fail",
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "revision": "rev-r2",
                        "trust_class": "verifier_observed",
                        "producer": "simulated-verifier",
                        "test_id": "test_ac001_export",
                        "covered_test_ids": ["test_ac001_export"],
                        "outcome": "failed",
                        "output_hash": "h2",
                        "simulated": True,
                        "collected_at": t2,
                    },
                    {
                        "id": "ev-pk-mvp1-3",
                        "evidence_id": "ev-mvp1-r2-pass",
                        "tenant_id": tenant_id,
                        "project_id": project_id,
                        "revision": "rev-r2",
                        "trust_class": "verifier_observed",
                        "producer": "simulated-verifier",
                        "test_id": "test_ac002_login",
                        "covered_test_ids": ["test_ac002_login"],
                        "outcome": "passed",
                        "output_hash": "h3",
                        "simulated": True,
                        "collected_at": t2,
                    },
                ]
            )
            .on_conflict_do_nothing()
        )
        db.commit()
        create_assessment(db, tenant_id=tenant_id, project_id=project_id, trigger="manual", now=t2)
        db.commit()

    assessment_count = db.execute(
        sa.select(sa.func.count())
        .select_from(ppm_assessments)
        .where(
            ppm_assessments.c.tenant_id == tenant_id,
            ppm_assessments.c.project_id == project_id,
        )
    ).scalar_one()

    return {
        "tenant_id": tenant_id,
        "project_id": project_id,
        "assessments": int(assessment_count),
    }


def main(argv: Sequence[str] | None = None, *, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eap.mvp1_fixture",
        description="Idempotently seed MVP1 D0/D1 fixture data.",
    )
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    args = parser.parse_args(list(argv) if argv is not None else None)

    cfg = settings or get_settings()
    configure_logging(cfg)
    engine = get_engine(cfg.database_url.get_secret_value() if cfg.database_url else None)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        with session_factory() as db:
            summary = seed_mvp1_fixtures(
                db,
                tenant_id=cfg.eap_owner_tenant_id,
                principal_id=cfg.eap_owner_principal_id,
                project_id=args.project_id,
                source_id=args.source_id,
            )
        print(json.dumps(summary, sort_keys=True))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
