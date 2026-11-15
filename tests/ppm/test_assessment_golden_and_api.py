"""Golden examples/ppm-output.json, Read API (explanation, diff, list), cross-tenant 404, and CLI tests (P2a-WP4)."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal
from eap.db import (
    collector_snapshots,
    ppm_ingested_artifacts,
    ppm_updates,
    project_snapshots,
    sources,
)
from eap.ppm.assessments import (
    BaselineRevisionConflict,
    approve_baseline,
    create_assessment,
    load_fixture_evidence,
)
from eap.ppm.cli import main as cli_main


def _seed_golden_project(
    db: Session,
    *,
    tenant_id: str,
    project_id: str = "proj-example",
) -> tuple[str, str, str]:
    """Seed A1 (rev-r1) and A2 (rev-r2) matching examples/ppm-output.json."""
    src_id = f"src-{uuid.uuid4().hex[:10]}"
    now_r1 = dt.datetime(2026, 11, 5, 8, 0, 0, tzinfo=dt.UTC)
    now_r2 = dt.datetime(2026, 11, 5, 9, 0, 0, tzinfo=dt.UTC)

    db.execute(
        sources.insert().values(
            source_id=src_id,
            tenant_id=tenant_id,
            canonical_root=f"/fixtures/{project_id}",
            repo_identity=f"git@example.com:{tenant_id}/{project_id}.git",
            created_at=now_r1,
            updated_at=now_r1,
        )
    )

    # Approve baseline 1: AC-001 (weight 2, req, test_ac001_export), AC-002 (weight 1, req, test_ac002_login), AC-003 (weight 1, req, test_ac003_audit)
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
                "weight": 2,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_ac001_export"}],
            },
            {
                "criterion_id": "AC-002",
                "deliverable_id": "DEL-001",
                "owner_project_id": project_id,
                "weight": 1,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_ac002_login"}],
            },
            {
                "criterion_id": "AC-003",
                "deliverable_id": "DEL-002",
                "owner_project_id": project_id,
                "weight": 1,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_ac003_audit"}],
            },
        ],
        approved_by="owner",
        source="fixture:golden",
    )

    # Snapshot 1 (rev-r1)
    csnap_r1 = f"csnap-r1-{uuid.uuid4().hex[:8]}"
    psnap_r1 = "snap-example-r1"
    db.execute(
        collector_snapshots.insert().values(
            id=csnap_r1,
            tenant_id=tenant_id,
            source_id=src_id,
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            git_commit="rev-r1",
            git_dirty=False,
            tree_hash="rev-r1",
            manifest={},
            created_at=now_r1,
        )
    )
    db.execute(
        project_snapshots.insert().values(
            id=psnap_r1,
            tenant_id=tenant_id,
            snapshot_id=csnap_r1,
            source_id=src_id,
            project_id=project_id,
            snapshot_seq=1,
            scope_hash="scope-r1",
            metadata_root=".ppm",
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            artifact_aliases={},
            notes=[],
            claims=[],
            created_at=now_r1,
        )
    )

    # Evidence at rev-r1: PASS test_ac001_export
    load_fixture_evidence(
        db,
        tenant_id,
        project_id,
        [
            {
                "evidence_id": "ev-r1-verifier-run",
                "trust_class": "verifier_observed",
                "producer": "simulated-verifier",
                "revision": "rev-r1",
                "observed_at": "2026-11-05T08:05:00Z",
                "test_id": "test_ac001_export",
                "covered_test_ids": ["test_ac001_export"],
                "result": "passed",
                "artifact_path": None,
                "simulated": True,
            }
        ],
    )

    a1 = create_assessment(
        db,
        tenant_id,
        project_id,
        trigger="snapshot",
        assessment_id="asmt-example-001",
        now="2026-11-05T08:10:00Z",
        project_snapshot_id=psnap_r1,
        assessed_revision="rev-r1",
    )

    # Snapshot 2 (rev-r2)
    csnap_r2 = f"csnap-r2-{uuid.uuid4().hex[:8]}"
    psnap_r2 = "snap-example-r2"
    db.execute(
        collector_snapshots.insert().values(
            id=csnap_r2,
            tenant_id=tenant_id,
            source_id=src_id,
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            git_commit="rev-r2",
            git_dirty=False,
            tree_hash="rev-r2",
            manifest={},
            created_at=now_r2,
        )
    )
    db.execute(
        project_snapshots.insert().values(
            id=psnap_r2,
            tenant_id=tenant_id,
            snapshot_id=csnap_r2,
            source_id=src_id,
            project_id=project_id,
            snapshot_seq=2,
            scope_hash="scope-r2",
            metadata_root=".ppm",
            status="captured",
            mixed_snapshot=False,
            partial_reasons=[],
            artifact_aliases={},
            notes=[],
            claims=[],
            created_at=now_r2,
        )
    )

    # Claims at rev-r2: upd-0002 claims AC-001 done and AC-003 done with reports/ac003-junit.xml
    claims_to_seed: list[tuple[str, dict[str, object]]] = [
        (
            "upd-0002",
            {
                "claim_id": "upd-0002",
                "source_file": ".ppm/updates/upd-0002.yaml",
                "criterion_ids": ["AC-001"],
                "test_ids": ["test_ac001_export"],
                "reported_status": "claimed_done",
                "evidence_path": None,
                "working_tree_state": "clean",
                "recorded_at": "2026-11-05T09:02:00Z",
                "summary": "Export done",
            },
        ),
        (
            "upd-0002-ac003",
            {
                "claim_id": "upd-0002-ac003",
                "source_file": ".ppm/updates/upd-0002-ac003.yaml",
                "criterion_ids": ["AC-003"],
                "test_ids": ["test_ac003_audit"],
                "reported_status": "claimed_done",
                "evidence_path": "reports/ac003-junit.xml",
                "working_tree_state": "clean",
                "recorded_at": "2026-11-05T09:02:00Z",
                "summary": "AC-003 passed in local junit",
            },
        ),
    ]
    for uid, claim_payload in claims_to_seed:
        art_id = db.execute(
            ppm_ingested_artifacts.insert()
            .values(
                tenant_id=tenant_id,
                project_id=project_id,
                source_id=src_id,
                artifact_path=claim_payload["source_file"],
                content_hash=f"h-{uid}",
                first_seen_snapshot_id=psnap_r2,
                kind="update",
                outcome="ingested",
            )
            .returning(ppm_ingested_artifacts.c.id)
        ).scalar_one()
        db.execute(
            ppm_updates.insert().values(
                id=f"u-{uuid.uuid4().hex[:14]}",
                tenant_id=tenant_id,
                project_id=project_id,
                update_id=uid,
                content_hash=f"h-{uid}",
                contract_version="2.1",
                supersedes_update_id=None,
                supersession_state="active",
                payload=claim_payload,
                source_id=src_id,
                source_snapshot_id=psnap_r2,
                artifact_path=claim_payload["source_file"],
                ingested_artifact_id=art_id,
                recorded_at=now_r2,
            )
        )

    # Evidence at rev-r2: verifier FAIL on test_ac001_export, verifier PASS on test_ac002_login, reported_artifact on reports/ac003-junit.xml
    load_fixture_evidence(
        db,
        tenant_id,
        project_id,
        [
            {
                "evidence_id": "ev-r2-verifier-fail",
                "trust_class": "verifier_observed",
                "producer": "simulated-verifier",
                "revision": "rev-r2",
                "observed_at": "2026-11-05T09:05:00Z",
                "test_id": "test_ac001_export",
                "covered_test_ids": ["test_ac001_export"],
                "result": "failed",
                "artifact_path": None,
                "simulated": True,
            },
            {
                "evidence_id": "ev-r2-verifier-pass",
                "trust_class": "verifier_observed",
                "producer": "simulated-verifier",
                "revision": "rev-r2",
                "observed_at": "2026-11-05T09:06:00Z",
                "test_id": "test_ac002_login",
                "covered_test_ids": ["test_ac002_login"],
                "result": "passed",
                "artifact_path": None,
                "simulated": True,
            },
            {
                "evidence_id": "ev-r2-ac003-reported",
                "trust_class": "reported_artifact",
                "producer": "collector",
                "revision": "rev-r2",
                "observed_at": "2026-11-05T09:07:00Z",
                "test_id": "test_ac003_audit",
                "covered_test_ids": ["test_ac003_audit"],
                "result": "passed",
                "artifact_path": "reports/ac003-junit.xml",
                "simulated": False,
            },
        ],
    )

    a2 = create_assessment(
        db,
        tenant_id,
        project_id,
        trigger="snapshot",
        assessment_id="asmt-example-002",
        now="2026-11-05T09:10:00Z",
        project_snapshot_id=psnap_r2,
        assessed_revision="rev-r2",
        dependency_refs=[
            {
                "deliverable_id": "DEL-GW-001",
                "owner_project_id": "proj-shared-gateway",
                "observed_status": "verified",
            }
        ],
    )
    db.commit()
    return a1.id, a2.id, project_id


def test_golden_scenario_and_read_api_endpoints(db_engine: sa.Engine) -> None:
    """Golden AC-001/002/003 + GET assessment/diff/list + cross-tenant 404 + cross-project 422."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_a = f"tenant-a-{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant-b-{uuid.uuid4().hex[:8]}"
    active_principal = Principal(
        tenant_id=tenant_a,
        principal_id="principal-owner",
        roles=["owner"],
    )

    with SessionLocal() as db:
        a1_id, a2_id, proj_id = _seed_golden_project(db, tenant_id=tenant_a)
        # Seed a second project in tenant_a for cross-project diff test
        other_proj = "proj-other"
        approve_baseline(
            db,
            tenant_a,
            other_proj,
            baseline_revision=1,
            criteria=[
                {
                    "criterion_id": "AC-X",
                    "deliverable_id": "DEL-X",
                    "owner_project_id": other_proj,
                    "weight": 1,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_x"}],
                }
            ],
            approved_by="owner",
            source="fixture:other",
        )
        a_other = create_assessment(
            db,
            tenant_a,
            other_proj,
            assessed_revision="rev-x1",
        )
        db.commit()
        a_other_id = a_other.id

    def _override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def _override_get_principal():
        return active_principal

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_principal] = _override_get_principal
    try:
        client = TestClient(app)

        # 1. GET /v1/ppm/assessments/{a2_id}
        resp = client.get(f"/v1/ppm/assessments/{a2_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_current"] is True
        assert body["output"]["verification"] == "contradicted"
        assert body["output"]["acceptance_readiness"] == "not_ready"

        crit_by_id = {c["criterion_id"]: c for c in body["output"]["criteria"]}
        assert crit_by_id["AC-001"]["status"] == "contradicted"
        assert crit_by_id["AC-001"]["reason_code"] == "evidence_refuted"
        assert crit_by_id["AC-001"]["reported_status"] == "claimed_done"
        assert crit_by_id["AC-001"]["previous_status"] == "verified"

        assert crit_by_id["AC-002"]["status"] == "verified"
        assert crit_by_id["AC-002"]["evidence_revision"] == "rev-r2"

        assert crit_by_id["AC-003"]["status"] == "unverified"
        assert crit_by_id["AC-003"]["reason_code"] == "reported_artifact_only"

        # Explanation columns
        exp_ac1 = body["explanation"]["AC-001"]
        assert len(exp_ac1["reported"]) == 1
        assert exp_ac1["reported"][0]["source_file"] == ".ppm/updates/upd-0002.yaml"
        assert exp_ac1["verified"]["baseline_revision"] == 1
        assert exp_ac1["verified"]["covered_test_ids"] == ["test_ac001_export"]
        assert exp_ac1["verified"]["refuting_evidence"][0]["trust_class"] == "verifier_observed"
        assert exp_ac1["verified"]["refuting_evidence"][0]["producer"] == "simulated-verifier"
        assert exp_ac1["verified"]["refuting_evidence"][0]["simulated"] is True

        # ISS-036: rev-r1 PASS on AC-001 appears in excluded_evidence with revision_mismatch
        excl_ac1 = exp_ac1["verified"]["excluded_evidence"]
        assert len(excl_ac1) == 1
        assert excl_ac1[0]["evidence_id"] == "ev-r1-verifier-run"
        assert excl_ac1[0]["revision"] == "rev-r1"
        assert excl_ac1[0]["trust_class"] == "verifier_observed"
        assert excl_ac1[0]["test_id"] == "test_ac001_export"
        assert excl_ac1[0]["simulated"] is True
        assert excl_ac1[0]["reason"] == "revision_mismatch"

        # ISS-036: AC-003 reported_artifact appears in excluded_evidence with trust_class_insufficient
        excl_ac3 = body["explanation"]["AC-003"]["verified"]["excluded_evidence"]
        assert len(excl_ac3) == 1
        assert excl_ac3[0]["evidence_id"] == "ev-r2-ac003-reported"
        assert excl_ac3[0]["reason"] == "trust_class_insufficient"

        # 2. GET /v1/ppm/assessments/{a2_id}/diff?against={a1_id}
        diff_resp = client.get(f"/v1/ppm/assessments/{a2_id}/diff", params={"against": a1_id})
        assert diff_resp.status_code == 200
        diff_body = diff_resp.json()
        changes = {c["criterion_id"]: c for c in diff_body["changes"]}
        assert changes["AC-001"]["from_status"] == "verified"
        assert changes["AC-001"]["to_status"] == "contradicted"
        assert changes["AC-001"]["changed"] is True
        assert changes["AC-002"]["from_status"] == "unverified"
        assert changes["AC-002"]["to_status"] == "verified"
        assert changes["AC-002"]["changed"] is True
        assert changes["AC-003"]["from_status"] == "unverified"
        assert changes["AC-003"]["to_status"] == "unverified"
        assert changes["AC-003"]["changed"] is False

        # 3. GET /v1/ppm/projects/{project_id}/assessments
        list_resp = client.get(f"/v1/ppm/projects/{proj_id}/assessments")
        assert list_resp.status_code == 200
        items = list_resp.json()
        assert [(i["id"], i["is_current"]) for i in items] == [
            (a2_id, True),
            (a1_id, False),
        ]

        # 4. Cross-project diff -> 422
        mismatch_resp = client.get(f"/v1/ppm/assessments/{a2_id}/diff", params={"against": a_other_id})
        assert mismatch_resp.status_code == 422

        # 5. Cross-tenant 404 on ALL three endpoints (never 403)
        active_principal = Principal(
            tenant_id=tenant_b,
            principal_id="principal-second",
            roles=["owner"],
        )
        assert client.get(f"/v1/ppm/assessments/{a2_id}").status_code == 404
        assert client.get(f"/v1/ppm/assessments/{a2_id}/diff", params={"against": a1_id}).status_code == 404
        list_b = client.get(f"/v1/ppm/projects/{proj_id}/assessments")
        assert list_b.status_code == 200
        assert list_b.json() == []
    finally:
        app.dependency_overrides.clear()


def test_cli_approve_baseline_load_evidence_and_assess(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI smoke: approve-baseline, load-evidence, assess using fixtures-only mode."""
    fixture_path = tmp_path / "d0_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "baseline": {
                    "baseline_revision": 1,
                    "criteria": [
                        {
                            "criterion_id": "AC-001",
                            "deliverable_id": "DEL-1",
                            "owner_project_id": "proj-cli",
                            "weight": 1,
                            "required": True,
                            "verification_refs": [{"kind": "test_id", "ref": "test_cli_1"}],
                        }
                    ],
                },
                "evidence": [
                    {
                        "evidence_id": "ev-cli-1",
                        "trust_class": "verifier_observed",
                        "revision": "rev-cli-1",
                        "observed_at": "2026-11-15T10:00:00Z",
                        "test_id": "test_cli_1",
                        "covered_test_ids": ["test_cli_1"],
                        "result": "passed",
                        "artifact_path": None,
                    }
                ],
                "waivers": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    tenant = f"tenant-cli-{uuid.uuid4().hex[:8]}"
    assert cli_main(["approve-baseline", "--tenant", tenant, "--project", "proj-cli", "--fixture", str(fixture_path)]) == 0
    assert cli_main(["load-evidence", "--tenant", tenant, "--project", "proj-cli", "--fixture", str(fixture_path)]) == 0
    assert cli_main(["assess", "--tenant", tenant, "--project", "proj-cli", "--assessed-revision", "rev-cli-1"]) == 0
    captured = capsys.readouterr().out
    assert '"verification": "verified"' in captured
    assert '"acceptance_readiness": "ready_for_review"' in captured


def test_iss035_approved_baseline_immutable_noop_and_conflict(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ISS-035: identical criteria -> no-op returning existing row; different criteria -> BaselineRevisionConflict."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-bl-{uuid.uuid4().hex[:8]}"
    project_id = "proj-iss035"
    criteria_v1 = [
        {
            "criterion_id": "AC-001",
            "deliverable_id": "DEL-1",
            "owner_project_id": project_id,
            "weight": 1,
            "required": True,
            "verification_refs": [{"kind": "test_id", "ref": "test_1"}],
        }
    ]
    criteria_v2_different = [
        {
            "criterion_id": "AC-001",
            "deliverable_id": "DEL-1",
            "owner_project_id": project_id,
            "weight": 2,
            "required": True,
            "verification_refs": [{"kind": "test_id", "ref": "test_1"}],
        }
    ]

    with SessionLocal() as db:
        first = approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=criteria_v1,
            approved_by="owner-1",
            source="fixture:v1",
        )
        db.commit()

        # Re-approving with identical criteria is a no-op returning the exact existing row
        second = approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=criteria_v1,
            approved_by="owner-2",
            source="fixture:v1-again",
        )
        assert second["id"] == first["id"]
        assert second["approved_by"] == "owner-1"
        assert second["source"] == "fixture:v1"

        # Re-approving same revision with different criteria raises BaselineRevisionConflict
        with pytest.raises(BaselineRevisionConflict, match="baseline_revision_conflict"):
            approve_baseline(
                db,
                tenant,
                project_id,
                baseline_revision=1,
                criteria=criteria_v2_different,
                approved_by="owner-2",
                source="fixture:v2",
            )
        db.rollback()

    # CLI check: exit code != 0 and reason baseline_revision_conflict
    conflict_fixture = tmp_path / "conflict_bl.json"
    conflict_fixture.write_text(
        json.dumps({"baseline": {"baseline_revision": 1, "criteria": criteria_v2_different}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("DATABASE_URL", db_engine.url.render_as_string(hide_password=False))
    rc = cli_main(["approve-baseline", "--tenant", tenant, "--project", project_id, "--fixture", str(conflict_fixture)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "baseline_revision_conflict" in err


def test_iss036_excluded_evidence_reasons_ttl_and_unmapped(db_engine: sa.Engine) -> None:
    """ISS-036: excluded_evidence reports ttl_expired and test_not_mapped without altering reducer."""
    from eap.ppm.assessments import get_assessment_detail

    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-excl-{uuid.uuid4().hex[:8]}"
    project_id = "proj-iss036"
    now_iso = "2026-11-15T12:00:00Z"

    with SessionLocal() as db:
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=[
                {
                    "criterion_id": "AC-TTL",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": project_id,
                    "weight": 1,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_ttl"}],
                },
                {
                    "criterion_id": "AC-UNMAP",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": project_id,
                    "weight": 1,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "test_mapped_only"}],
                },
            ],
            approved_by="owner",
            source="fixture:iss036",
        )

        # Seed a snapshot + update claiming AC-UNMAP via an unmapped test_id
        csnap = f"csnap-{uuid.uuid4().hex[:8]}"
        psnap = f"psnap-{uuid.uuid4().hex[:8]}"
        src_id = f"src-{uuid.uuid4().hex[:8]}"
        ts = dt.datetime(2026, 11, 15, 12, 0, 0, tzinfo=dt.UTC)
        db.execute(
            sources.insert().values(
                source_id=src_id,
                tenant_id=tenant,
                canonical_root="/fixtures/iss036",
                repo_identity=f"git@example.com:{tenant}/iss036.git",
                created_at=ts,
                updated_at=ts,
            )
        )
        db.execute(
            collector_snapshots.insert().values(
                id=csnap,
                tenant_id=tenant,
                source_id=src_id,
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                git_commit="rev-1",
                git_dirty=False,
                tree_hash="rev-1",
                manifest={},
                created_at=ts,
            )
        )
        db.execute(
            project_snapshots.insert().values(
                id=psnap,
                tenant_id=tenant,
                snapshot_id=csnap,
                source_id=src_id,
                project_id=project_id,
                snapshot_seq=1,
                scope_hash="scope-1",
                metadata_root=".ppm",
                status="captured",
                mixed_snapshot=False,
                partial_reasons=[],
                artifact_aliases={},
                notes=[],
                claims=[],
                created_at=ts,
            )
        )
        art_id = db.execute(
            ppm_ingested_artifacts.insert()
            .values(
                tenant_id=tenant,
                project_id=project_id,
                source_id=src_id,
                artifact_path=".ppm/updates/upd-unmap.yaml",
                content_hash="h-unmap",
                first_seen_snapshot_id=psnap,
                kind="update",
                outcome="ingested",
            )
            .returning(ppm_ingested_artifacts.c.id)
        ).scalar_one()
        db.execute(
            ppm_updates.insert().values(
                id=f"u-{uuid.uuid4().hex[:14]}",
                tenant_id=tenant,
                project_id=project_id,
                update_id="upd-unmap",
                content_hash="h-unmap",
                contract_version="2.1",
                supersedes_update_id=None,
                supersession_state="active",
                payload={
                    "claim_id": "upd-unmap",
                    "source_file": ".ppm/updates/upd-unmap.yaml",
                    "criterion_ids": ["AC-UNMAP"],
                    "test_ids": ["test_other_unmapped"],
                    "reported_status": "claimed_done",
                    "recorded_at": now_iso,
                },
                source_id=src_id,
                source_snapshot_id=psnap,
                artifact_path=".ppm/updates/upd-unmap.yaml",
                ingested_artifact_id=art_id,
                recorded_at=ts,
            )
        )

        load_fixture_evidence(
            db,
            tenant,
            project_id,
            [
                {
                    "evidence_id": "ev-ttl-old",
                    "trust_class": "verifier_observed",
                    "producer": "simulated-verifier",
                    "revision": "rev-1",
                    "observed_at": "2026-11-01T12:00:00Z",
                    "test_id": "test_ttl",
                    "covered_test_ids": ["test_ttl"],
                    "result": "passed",
                    "simulated": True,
                },
                {
                    "evidence_id": "ev-unmapped-pass",
                    "trust_class": "verifier_observed",
                    "producer": "simulated-verifier",
                    "revision": "rev-1",
                    "observed_at": now_iso,
                    "test_id": "test_other_unmapped",
                    "covered_test_ids": ["test_other_unmapped"],
                    "result": "passed",
                    "simulated": True,
                },
            ],
        )

        rec = create_assessment(
            db,
            tenant,
            project_id,
            now=now_iso,
            project_snapshot_id=psnap,
            assessed_revision="rev-1",
        )
        detail = get_assessment_detail(db, tenant, rec.id)
        db.commit()

        excl_ttl = detail["explanation"]["AC-TTL"]["verified"]["excluded_evidence"]
        assert len(excl_ttl) == 1
        assert excl_ttl[0]["evidence_id"] == "ev-ttl-old"
        assert excl_ttl[0]["reason"] == "ttl_expired"

        excl_unmap = detail["explanation"]["AC-UNMAP"]["verified"]["excluded_evidence"]
        assert len(excl_unmap) == 1
        assert excl_unmap[0]["evidence_id"] == "ev-unmapped-pass"
        assert excl_unmap[0]["reason"] == "test_not_mapped"
