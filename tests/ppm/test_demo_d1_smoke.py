"""End-to-end smoke test for scripts/demo_d1.sh (.ps1 on Windows), idempotency, C34, Step 5, and C21/C29/C38."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from eap.db import (
    commands,
    ppm_assessments,
    ppm_baselines,
    project_current_assessment,
    project_scopes,
    runs,
    sources,
)
from eap.ppm.assessments import diff_assessments, get_assessment_detail

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_demo_d1_script_end_to_end_and_idempotent(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """Run demo_d1 twice against PostgreSQL; assert A1/A2, C34, Step 5 unchanged, and C21/C29/C38."""
    tenant_owner = f"tenant-d1-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-d1-{uuid.uuid4().hex[:8]}"
    source_id = f"src-d1-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures_root"
    fixtures_root.mkdir()

    db_url = db_engine.url.render_as_string(hide_password=False)
    env = os.environ.copy()
    env["DATABASE_URL"] = db_url
    env["EAP_COLLECTOR_FIXTURES_ROOT"] = str(fixtures_root)
    env["EAP_COLLECTOR_ALLOW_REAL_ROOTS"] = "false"
    env["EAP_OWNER_TENANT_ID"] = tenant_owner
    env["D1_PROJECT_ID"] = project_id
    env["D1_SOURCE_ID"] = source_id

    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not pwsh:
            pytest.skip("PowerShell not available")
        cmd = [pwsh, "-NoProfile", "-File", str(REPO_ROOT / "scripts" / "demo_d1.ps1")]
    else:
        cmd = ["bash", str(REPO_ROOT / "scripts" / "demo_d1.sh")]

    # First run (full 7-step D1 flow)
    res1 = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)
    assert res1.returncode == 0, f"demo_d1 first run failed:\nSTDOUT:\n{res1.stdout}\nSTDERR:\n{res1.stderr}"
    assert "D1-fixture sign-off demo complete." in res1.stdout
    assert "C34 out-of-order:        PASS" in res1.stdout
    assert "C21=PASS C29=PASS C38=PASS" in res1.stdout

    # Second run (idempotent rerun -> Step 5 unchanged rescan, 0 new assessments)
    res2 = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)
    assert res2.returncode == 0, f"demo_d1 rerun failed:\nSTDOUT:\n{res2.stdout}\nSTDERR:\n{res2.stderr}"

    with db_engine.connect() as conn:
        asmt_rows = (
            conn.execute(
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
            )
            .mappings()
            .all()
        )
        # A1 (rev-r1), slow C34 rev-r1 (basis_seq 2), A2 (rev-r2, basis_seq 3)
        assert len(asmt_rows) == 3

        curr_id = str(
            conn.execute(
                sa.select(project_current_assessment.c.assessment_id).where(
                    project_current_assessment.c.tenant_id == tenant_owner,
                    project_current_assessment.c.project_id == project_id,
                )
            ).scalar_one()
        )
        a1_id = str(asmt_rows[0]["id"])
        a2_id = str(asmt_rows[2]["id"])
        assert curr_id == a2_id

        # No duplicate source, scope, or baseline
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(sources)
                .where(sources.c.tenant_id == tenant_owner, sources.c.source_id == source_id)
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(project_scopes)
                .where(project_scopes.c.tenant_id == tenant_owner, project_scopes.c.project_id == project_id)
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                sa.select(sa.func.count())
                .select_from(ppm_baselines)
                .where(ppm_baselines.c.tenant_id == tenant_owner, ppm_baselines.c.project_id == project_id)
            ).scalar_one()
            == 1
        )

        # Verify A1 and A2 criterion statuses + diff
        a1 = get_assessment_detail(conn, tenant_owner, a1_id)
        a2 = get_assessment_detail(conn, tenant_owner, a2_id)
        diff = diff_assessments(conn, tenant_owner, a2_id, a1_id)

        a1_crit = {c["criterion_id"]: c for c in a1["output"]["criteria"]}
        a2_crit = {c["criterion_id"]: c for c in a2["output"]["criteria"]}

        assert a1_crit["AC-001"]["status"] == "verified"
        assert a1_crit["AC-002"]["status"] == "unverified"
        assert a1_crit["AC-003"]["status"] == "unverified"

        assert a2_crit["AC-001"]["status"] == "contradicted"
        assert a2_crit["AC-002"]["status"] == "verified"
        assert a2_crit["AC-003"]["status"] == "unverified"
        assert a2_crit["AC-003"]["reason_code"] == "reported_artifact_only"
        assert a2["output"]["acceptance_readiness"] == "not_ready"
        assert a2["last_successful_scan_at"] is not None

        diff_map = {ch["criterion_id"]: ch for ch in diff["changes"]}
        assert diff_map["AC-002"]["from_status"] == "unverified"
        assert diff_map["AC-002"]["to_status"] == "verified"

        # Verify C21/C38 mock provider runs & single command
        c21_run = (
            conn.execute(
                sa.select(runs.c.state, runs.c.remote_outcome).where(
                    runs.c.tenant_id == tenant_owner,
                    runs.c.id == f"run-d1-c21-{project_id}",
                )
            )
            .mappings()
            .one()
        )
        assert c21_run["state"] == "cancelled"
        c21_cmds = conn.execute(
            sa.select(sa.func.count())
            .select_from(commands)
            .where(commands.c.tenant_id == tenant_owner, commands.c.run_id == f"run-d1-c21-{project_id}")
        ).scalar_one()
        assert c21_cmds == 1

        c38_run = (
            conn.execute(
                sa.select(runs.c.state, runs.c.remote_outcome).where(
                    runs.c.tenant_id == tenant_owner,
                    runs.c.id == f"run-d1-c38-{project_id}",
                )
            )
            .mappings()
            .one()
        )
        assert c38_run["remote_outcome"] == "unknown"
