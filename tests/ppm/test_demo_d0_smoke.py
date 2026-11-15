"""End-to-end smoke test for scripts/demo_d0.sh (.ps1 on Windows), idempotency, and API auth (D0-thin-slice)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from eap.api.app import app
from eap.db import (
    ppm_assessments,
    ppm_baselines,
    project_current_assessment,
    project_scopes,
    sources,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_d0_script_idempotent_and_api_auth(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run demo_d0 twice against eap_test; assert 2 assessments, A2 current, excluded_evidence, 401/404."""
    tenant_owner = f"tenant-d0-{uuid.uuid4().hex[:8]}"
    tenant_second = f"tenant-d0-sec-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-d0-{uuid.uuid4().hex[:8]}"
    source_id = f"src-d0-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures_root"
    fixtures_root.mkdir()

    db_url = db_engine.url.render_as_string(hide_password=False)
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["EAP_COLLECTOR_FIXTURES_ROOT"] = str(fixtures_root)
    env["EAP_COLLECTOR_ALLOW_REAL_ROOTS"] = "false"
    env["EAP_OWNER_TENANT_ID"] = tenant_owner
    env["D0_PROJECT_ID"] = project_id
    env["D0_SOURCE_ID"] = source_id

    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            pytest.skip("PowerShell not available")
        cmd = [pwsh, "-NoProfile", "-File", str(REPO_ROOT / "scripts" / "demo_d0.ps1")]
    else:
        cmd = ["bash", str(REPO_ROOT / "scripts" / "demo_d0.sh")]

    # First run
    res1 = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)
    assert res1.returncode == 0, f"demo_d0 first run failed:\nSTDOUT:\n{res1.stdout}\nSTDERR:\n{res1.stderr}"

    # Second run (idempotency)
    res2 = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)
    assert res2.returncode == 0, f"demo_d0 rerun failed:\nSTDOUT:\n{res2.stdout}\nSTDERR:\n{res2.stderr}"

    with db_engine.connect() as conn:
        asmt_rows = conn.execute(
            sa.select(
                ppm_assessments.c.id,
                ppm_assessments.c.basis_seq,
                ppm_assessments.c.assessed_revision,
            )
            .where(
                ppm_assessments.c.tenant_id == tenant_owner,
                ppm_assessments.c.project_id == project_id,
            )
            .order_by(ppm_assessments.c.basis_seq.asc())
        ).mappings().all()
        assert len(asmt_rows) == 2
        a1_id = str(asmt_rows[0]["id"])
        a2_id = str(asmt_rows[1]["id"])
        rev_r2 = str(asmt_rows[1]["assessed_revision"])

        curr_id = conn.execute(
            sa.select(project_current_assessment.c.assessment_id).where(
                project_current_assessment.c.tenant_id == tenant_owner,
                project_current_assessment.c.project_id == project_id,
            )
        ).scalar_one()
        assert curr_id == a2_id

        # No duplicate source, project scope, or baseline on rerun
        src_count = conn.execute(
            sa.select(sa.func.count()).select_from(sources).where(
                sources.c.tenant_id == tenant_owner,
                sources.c.source_id == source_id,
            )
        ).scalar_one()
        scope_count = conn.execute(
            sa.select(sa.func.count()).select_from(project_scopes).where(
                project_scopes.c.tenant_id == tenant_owner,
                project_scopes.c.project_id == project_id,
            )
        ).scalar_one()
        bl_count = conn.execute(
            sa.select(sa.func.count()).select_from(ppm_baselines).where(
                ppm_baselines.c.tenant_id == tenant_owner,
                ppm_baselines.c.project_id == project_id,
            )
        ).scalar_one()
        assert src_count == 1
        assert scope_count == 1
        assert bl_count == 1

    # Configure real bearer token auth for API checks
    owner_token = f"tok-owner-{uuid.uuid4().hex}"
    second_token = f"tok-second-{uuid.uuid4().hex}"
    owner_hash = hashlib.sha256(owner_token.encode()).hexdigest()
    second_hash = hashlib.sha256(second_token.encode()).hexdigest()

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("EAP_OWNER_TENANT_ID", tenant_owner)
    monkeypatch.setenv("EAP_OWNER_PRINCIPAL_ID", "principal-owner")
    monkeypatch.setenv("EAP_OWNER_TOKEN_HASH", owner_hash)
    monkeypatch.setenv("EAP_SECOND_TENANT_ID", tenant_second)
    monkeypatch.setenv("EAP_SECOND_PRINCIPAL_ID", "principal-second")
    monkeypatch.setenv("EAP_SECOND_TOKEN_HASH", second_hash)
    client = TestClient(app)

    # No token -> 401
    assert client.get(f"/v1/ppm/assessments/{a2_id}").status_code == 401
    assert client.get(f"/v1/ppm/projects/{project_id}/assessments").status_code == 401

    # Second principal (other tenant) -> 404 for A1 and A2
    sec_headers = {"Authorization": f"Bearer {second_token}"}
    assert client.get(f"/v1/ppm/assessments/{a1_id}", headers=sec_headers).status_code == 404
    assert client.get(f"/v1/ppm/assessments/{a2_id}", headers=sec_headers).status_code == 404

    # Owner token -> 200 with expected D0 state
    own_headers = {"Authorization": f"Bearer {owner_token}"}
    resp = client.get(f"/v1/ppm/assessments/{a2_id}", headers=own_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_current"] is True
    assert body["last_successful_scan_at"] is not None

    crit_by_id = {c["criterion_id"]: c for c in body["output"]["criteria"]}
    assert crit_by_id["AC-001"]["status"] == "contradicted"
    assert crit_by_id["AC-002"]["status"] == "verified"
    assert crit_by_id["AC-002"]["evidence_revision"] == rev_r2
    assert crit_by_id["AC-003"]["status"] == "unverified"
    assert crit_by_id["AC-003"]["reason_code"] == "reported_artifact_only"

    excl_ac1 = body["explanation"]["AC-001"]["verified"]["excluded_evidence"]
    assert any(
        ev["evidence_id"] == "ev-r1-verifier-run" and ev["reason"] == "revision_mismatch"
        for ev in excl_ac1
    )
    assert any("<script>" in n["body"] for n in body.get("notes", []))
