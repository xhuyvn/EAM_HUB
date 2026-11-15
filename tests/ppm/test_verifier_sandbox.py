"""Failure, isolation, and C40 conformance tests for sandbox verifier (P2a-WP9)."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
import warnings
from pathlib import Path
from typing import Any, cast

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from eap.api.dependencies import Principal
from eap.config import Settings
from eap.db import ppm_evidence
from eap.ppm import verifier as verifier_mod
from eap.ppm.assessments import (
    approve_baseline,
    create_assessment,
    get_assessment_detail,
    load_fixture_evidence,
)
from eap.ppm.cli import main as cli_main
from eap.ppm.collector import PpmCollector, RootNotPermittedSecurityError
from eap.ppm.service import LocationService
from eap.ppm.verifier import (
    PRODUCER_NAME,
    VerifierAllowlistError,
    VerifierRealRootRefusedError,
    build_allowed_verifier_argv,
    run_verifier,
    validate_allowed_argv,
)


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Verifier Fixture")
    env.setdefault("GIT_AUTHOR_EMAIL", "verifier-fixture@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Verifier Fixture")
    env.setdefault("GIT_COMMITTER_EMAIL", "verifier-fixture@example.invalid")
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _make_update_payload(
    project_id: str,
    update_id: str,
    *,
    summary: str,
    checks: list[dict[str, Any]],
) -> str:
    return json.dumps(
        {
            "contract_version": "2.1",
            "kind": "update",
            "project_id": project_id,
            "update_id": update_id,
            "session_id": "sess-1",
            "recorded_at": "2026-11-15T10:00:00Z",
            "actor": "coding-agent",
            "summary": summary,
            "work_package_id": "WP-TEST",
            "base_revision": None,
            "result_revision": "rev-r1",
            "working_tree_state": "clean",
            "supersedes_update_id": None,
            "completed": [],
            "in_progress": [],
            "checks": checks,
            "blockers": [],
            "next_actions": [],
            "decisions_required": [],
        },
        indent=2,
    )


def _init_fixture_repo(repo_dir: Path, project_id: str, files: dict[str, str]) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_dir, "init", "-b", "main")
    (repo_dir / ".ppm" / "updates").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".ppm" / "notes").mkdir(parents=True, exist_ok=True)
    (repo_dir / ".ppm" / "project.yaml").write_text(
        json.dumps(
            {
                "contract_version": "2.1",
                "kind": "project",
                "project_id": project_id,
                "name": "Verifier Fixture",
                "owner_ref": "principal-owner",
                "objectives": [
                    {
                        "objective_id": "OBJ-001",
                        "statement": "Verify sandbox checks",
                        "metric": None,
                    }
                ],
                "artifact_mapping": {
                    "architecture": None,
                    "blueprint": None,
                    "implementation_plan": None,
                },
                "notes_paths": [".ppm/notes/"],
                "relationship_proposals": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for rel, content in files.items():
        target = repo_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "initial fixture commit")
    return _git(repo_dir, "rev-parse", "HEAD")


def _register_and_scan_project(
    db: Session,
    *,
    tenant_id: str,
    project_id: str,
    repo_dir: Path,
    fixtures_root: Path,
) -> str:
    source_id = f"src-{uuid.uuid4().hex[:10]}"
    LocationService.register_source(
        db,
        tenant_id=tenant_id,
        source_id=source_id,
        canonical_root=str(repo_dir),
        repo_identity=f"git@example.com:{tenant_id}/{project_id}.git",
        principal_id="principal-system",
        host_id="host-local-001",
        host_os="windows" if sys.platform == "win32" else "linux",
    )
    loc_id = f"loc-{uuid.uuid4().hex[:10]}"
    LocationService.register_location(
        db,
        tenant_id=tenant_id,
        data={
            "contract_version": "1.1",
            "location_id": loc_id,
            "project_id": project_id,
            "host_id": "host-local-001",
            "connector_id": "connector-test",
            "root_path": str(repo_dir),
            "metadata_root": ".ppm",
            "scan_scope": ["."],
            "artifact_aliases": {
                "architecture": [],
                "blueprint": [],
                "implementation_plan": [],
            },
            "notes_globs": [".ppm/notes/*.md"],
            "follow_symlinks": False,
            "allow_hardlinked_files": False,
            "max_file_bytes": 2097152,
            "deny_globs": [".env", "*.pem"],
            "classification": "internal",
            "egress_policy_ref": "egress:local-only",
            "identity": {
                "canonical_root_fingerprint": None,
                "repo_identity": f"git@example.com:{tenant_id}/{project_id}.git",
            },
            "state": "proposed",
            "revision": 1,
            "registered_by_ref": "principal:principal-system",
            "source_id": source_id,
        },
        principal_id="principal-system",
        host_os="windows" if sys.platform == "win32" else "linux",
    )
    LocationService.approve_location(
        db,
        tenant_id=tenant_id,
        location_id=loc_id,
        principal=Principal(tenant_id=tenant_id, principal_id="principal-owner", roles=["owner"]),
    )
    collector = PpmCollector(fixtures_root=str(fixtures_root))
    collector.scan_source(db, tenant_id=tenant_id, source_id=source_id)
    return source_id


def _verifier_settings(fixtures_root: Path, *, enabled: bool = True, allow_real: bool = False) -> Settings:
    settings_ctor = cast(Any, Settings)
    return cast(
        Settings,
        settings_ctor(
            _env_file=None,
            PPM_VERIFIER_ENABLED=enabled,
            EAP_COLLECTOR_ALLOW_REAL_ROOTS=allow_real,
            EAP_COLLECTOR_FIXTURES_ROOT=str(fixtures_root),
        ),
    )


def _baseline_criteria(project_id: str, mapping: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    return [
        {
            "criterion_id": cid,
            "deliverable_id": "DEL-001",
            "owner_project_id": project_id,
            "weight": weight,
            "required": True,
            "verification_refs": [{"kind": "test_id", "ref": tid}],
        }
        for cid, tid, weight in mapping
    ]


def test_C40_in_repo_junit_pass_cannot_override_failed_real_verifier_execution(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """C40: Repo-resident JUnit XML claiming PASS stays reported_artifact; failing test is contradicted."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-c40-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-c40-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "c40_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "src/export.py": 'def export_csv() -> str:\n    return "broken"\n',
            "tests/test_app.py": (
                "from src.export import export_csv\n\n"
                "def test_ac001_export():\n"
                '    assert export_csv() == "id,name\\n"\n\n'
                "def test_ac002_login():\n"
                "    assert True\n"
            ),
            "reports/ac001-junit.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<testsuite name="ac001" tests="1" failures="0">\n'
                '  <testcase classname="tests.test_app" name="test_ac001_export" time="0.01"/>\n'
                "</testsuite>\n"
            ),
            "reports/ac003-junit.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<testsuite name="ac003" tests="1" failures="0">\n'
                '  <testcase classname="tests.test_audit" name="test_ac003_audit" time="0.01"/>\n'
                "</testsuite>\n"
            ),
            ".ppm/updates/upd-001.yaml": _make_update_payload(
                project_id,
                "upd-001",
                summary="Agent claims AC-001 and AC-003 passed via junit",
                checks=[
                    {
                        "name": "AC-001 export",
                        "result": "passed",
                        "evidence_path": "reports/ac001-junit.xml",
                        "tested_revision": "rev-r1",
                        "environment": "local",
                        "criterion_ids": ["AC-001"],
                        "test_ids": ["test_ac001_export"],
                    },
                    {
                        "name": "AC-003 audit",
                        "result": "passed",
                        "evidence_path": "reports/ac003-junit.xml",
                        "tested_revision": "rev-r1",
                        "environment": "local",
                        "criterion_ids": ["AC-003"],
                        "test_ids": ["test_ac003_audit"],
                    },
                ],
            ),
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(
                project_id,
                [
                    ("AC-001", "test_ac001_export", 2),
                    ("AC-002", "test_ac002_login", 1),
                    ("AC-003", "test_ac003_audit", 1),
                ],
            ),
            approved_by="owner",
            source="fixture:c40",
        )
        # Agent-reported JUnit XML evidence stays trust_class=reported_artifact
        load_fixture_evidence(
            db,
            tenant,
            project_id,
            [
                {
                    "evidence_id": "ev-rep-ac001-xml",
                    "trust_class": "reported_artifact",
                    "producer": "collector",
                    "revision": rev_r1,
                    "observed_at": "2026-11-15T10:00:01Z",
                    "test_id": "test_ac001_export",
                    "covered_test_ids": ["test_ac001_export"],
                    "result": "passed",
                    "artifact_path": "reports/ac001-junit.xml",
                    "simulated": False,
                },
                {
                    "evidence_id": "ev-rep-ac003-xml",
                    "trust_class": "reported_artifact",
                    "producer": "collector",
                    "revision": rev_r1,
                    "observed_at": "2026-11-15T10:00:01Z",
                    "test_id": "test_ac003_audit",
                    "covered_test_ids": ["test_ac003_audit"],
                    "result": "passed",
                    "artifact_path": "reports/ac003-junit.xml",
                    "simulated": False,
                },
            ],
            simulated=False,
        )

        report = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root),
        )
        assert report.enabled is True
        assert report.executed is True
        assert report.workspace_removed is True

        rec = create_assessment(
            db,
            tenant,
            project_id,
            trigger="manual",
            assessed_revision=rev_r1,
        )
        detail = get_assessment_detail(db, tenant, rec.id)
        db.commit()

        crit_map = {c["criterion_id"]: c for c in rec.output["criteria"]}
        assert crit_map["AC-001"]["status"] == "contradicted"
        assert crit_map["AC-001"]["reason_code"] == "evidence_refuted"
        assert crit_map["AC-002"]["status"] == "verified"
        assert crit_map["AC-002"]["reason_code"] == "evidence_current_pass"
        assert crit_map["AC-003"]["status"] == "unverified"
        assert crit_map["AC-003"]["reason_code"] == "reported_artifact_only"

        # Verify DB evidence rows
        ev_rows = db.execute(
            sa.select(ppm_evidence).where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.project_id == project_id,
            )
        ).mappings().all()
        verifier_rows = {str(r["test_id"]): r for r in ev_rows if r["producer"] == PRODUCER_NAME}
        assert set(verifier_rows) == {"test_ac001_export", "test_ac002_login", "test_ac003_audit"}
        assert verifier_rows["test_ac001_export"]["outcome"] == "failed"
        assert verifier_rows["test_ac001_export"]["trust_class"] == "verifier_observed"
        assert verifier_rows["test_ac001_export"]["simulated"] is False
        assert verifier_rows["test_ac001_export"]["exit_status"] == 1
        assert len(str(verifier_rows["test_ac001_export"]["output_hash"])) == 64
        assert int(verifier_rows["test_ac001_export"]["duration_ms"]) >= 0
        assert verifier_rows["test_ac002_login"]["outcome"] == "passed"
        assert verifier_rows["test_ac003_audit"]["outcome"] == "inconclusive"
        assert verifier_rows["test_ac003_audit"]["reason"] == "test_missing"

        # Agent-written XML remains reported_artifact
        xml_rows = [r for r in ev_rows if str(r["evidence_id"]).startswith("ev-rep-")]
        assert all(r["trust_class"] == "reported_artifact" for r in xml_rows)

        excl_ac1 = detail["explanation"]["AC-001"]["verified"]["excluded_evidence"]
        assert any(
            e["evidence_id"] == "ev-rep-ac001-xml"
            and e["trust_class"] == "reported_artifact"
            and e["reason"] == "trust_class_insufficient"
            for e in excl_ac1
        )
        excl_ac3 = detail["explanation"]["AC-003"]["verified"]["excluded_evidence"]
        assert any(
            e["evidence_id"] == "ev-rep-ac003-xml" and e["trust_class"] == "reported_artifact"
            for e in excl_ac3
        )


def test_verifier_pass_at_r1_fail_at_r2_creates_two_rows_and_contradicts_r2(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """PASS at rev-r1, FAIL at rev-r2 -> two evidence rows (simulated=False); rev-r2 contradicted."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-rev-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-rev-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "rev_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "src/export.py": 'def export_csv() -> str:\n    return "id,name\\n"\n',
            "tests/test_app.py": (
                "from src.export import export_csv\n\n"
                "def test_ac001_export():\n"
                '    assert export_csv() == "id,name\\n"\n'
            ),
        },
    )

    with SessionLocal() as db:
        source_id = _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-001", "test_ac001_export", 1)]),
            approved_by="owner",
            source="fixture:rev",
        )

        # Verify at rev-r1
        rep_r1 = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root),
        )
        assert rep_r1.results[0].outcome == "passed"
        a1 = create_assessment(db, tenant, project_id, trigger="snapshot", assessed_revision=rev_r1)
        assert a1.output["criteria"][0]["status"] == "verified"

        # Break src/export.py at rev-r2
        (repo_dir / "src" / "export.py").write_text(
            'def export_csv() -> str:\n    return "corrupted\\n"\n',
            encoding="utf-8",
            newline="\n",
        )
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "commit", "-m", "rev-r2 regression")
        rev_r2 = _git(repo_dir, "rev-parse", "HEAD")

        PpmCollector(fixtures_root=str(fixtures_root)).scan_source(
            db,
            tenant_id=tenant,
            source_id=source_id,
        )
        rep_r2 = run_verifier(
            db,
            tenant,
            project_id,
            rev_r2,
            settings=_verifier_settings(fixtures_root),
        )
        assert rep_r2.results[0].outcome == "failed"

        a2 = create_assessment(db, tenant, project_id, trigger="snapshot", assessed_revision=rev_r2)
        detail_a2 = get_assessment_detail(db, tenant, a2.id)
        db.commit()

        assert a2.output["criteria"][0]["status"] == "contradicted"
        assert a2.output["criteria"][0]["reason_code"] == "evidence_refuted"

        ev_rows = db.execute(
            sa.select(ppm_evidence)
            .where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.project_id == project_id,
                ppm_evidence.c.test_id == "test_ac001_export",
            )
            .order_by(ppm_evidence.c.revision.asc())
        ).mappings().all()
        assert len(ev_rows) == 2
        assert all(r["simulated"] is False and r["producer"] == PRODUCER_NAME for r in ev_rows)
        by_rev = {str(r["revision"]): str(r["outcome"]) for r in ev_rows}
        assert by_rev == {rev_r1: "passed", rev_r2: "failed"}

        excl = detail_a2["explanation"]["AC-001"]["verified"]["excluded_evidence"]
        assert any(
            e["revision"] == rev_r1
            and e["outcome"] == "passed"
            and e["simulated"] is False
            and e["reason"] == "revision_mismatch"
            for e in excl
        )


def test_verifier_network_socket_isolation_or_not_enforced_flag(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Socket test fails if netns is enforced; otherwise records network_isolation=not_enforced."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-net-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-net-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "net_repo"

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    listen_port = listener.getsockname()[1]
    stop_evt = threading.Event()

    def _accept_loop() -> None:
        while not stop_evt.is_set():
            try:
                conn_sock, _ = listener.accept()
                conn_sock.close()
            except TimeoutError:
                continue
            except OSError:
                break

    accept_thread = threading.Thread(target=_accept_loop, daemon=True)
    accept_thread.start()

    try:
        rev_r1 = _init_fixture_repo(
            repo_dir,
            project_id,
            {
                "tests/test_net.py": (
                    "import socket\n\n"
                    "def test_open_socket():\n"
                    f'    with socket.create_connection(("127.0.0.1", {listen_port}), timeout=1.5):\n'
                    "        pass\n"
                ),
            },
        )

        with SessionLocal() as db:
            _register_and_scan_project(
                db,
                tenant_id=tenant,
                project_id=project_id,
                repo_dir=repo_dir,
                fixtures_root=fixtures_root,
            )
            approve_baseline(
                db,
                tenant,
                project_id,
                baseline_revision=1,
                criteria=_baseline_criteria(project_id, [("AC-NET", "test_open_socket", 1)]),
                approved_by="owner",
                source="fixture:net",
            )

            # 1. Real platform detection (on Windows: not_enforced; on Linux with unshare: enforced)
            report = run_verifier(
                db,
                tenant,
                project_id,
                rev_r1,
                settings=_verifier_settings(fixtures_root),
            )
            ev_row = db.execute(
                sa.select(ppm_evidence).where(
                    ppm_evidence.c.tenant_id == tenant,
                    ppm_evidence.c.project_id == project_id,
                    ppm_evidence.c.test_id == "test_open_socket",
                )
            ).mappings().one()

            r0 = report.results[0]
            warnings.warn(
                UserWarning(
                    "VERIFIER_CI_NET_ISOLATION: "
                    f"platform={sys.platform} mode={report.network_isolation} "
                    f"outcome={r0.outcome} exit_status={r0.exit_status}"
                ),
                stacklevel=1,
            )

            if report.network_isolation == "enforced":
                assert r0.outcome == "failed"
                assert ev_row["network_isolation"] == "enforced"
            else:
                assert report.network_isolation == "not_enforced"
                assert ev_row["network_isolation"] == "not_enforced"

            # 2. Deterministic check of enforced isolation using an unprivileged socket blocker wrapper
            blocker = tmp_path / "block_net_wrapper.py"
            monkeypatch.setattr(
                verifier_mod,
                "_detect_network_isolation",
                lambda: ("enforced", [sys.executable, str(blocker)]),
            )
            # _run_single_test_in_workspace prepends netns_prefix before [sys.executable, "-m", ...]
            blocker.write_text(
                (
                    "import runpy, socket, sys\n"
                    "def _blocked(*args, **kwargs):\n"
                    '    raise OSError(101, "Network is unreachable (sandbox enforced)")\n'
                    "socket.socket = _blocked\n"
                    "inner = sys.argv[2:]\n"
                    "sys.argv = ['pytest', *inner[2:]]\n"
                    "runpy.run_module('pytest', run_name='__main__')\n"
                ),
                encoding="utf-8",
            )
            enforced_rep = run_verifier(
                db,
                tenant,
                project_id,
                rev_r1,
                settings=_verifier_settings(fixtures_root),
            )
            db.commit()
            assert enforced_rep.network_isolation == "enforced"
            assert enforced_rep.results[0].outcome == "failed"
            assert enforced_rep.results[0].network_isolation == "enforced"
    finally:
        stop_evt.set()
        listener.close()
        accept_thread.join(timeout=2.0)


def test_verifier_timeout_is_inconclusive_never_passed(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """Wall-clock timeout -> outcome='inconclusive', reason='timeout', exit_status=None."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-to-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-to-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "timeout_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_slow.py": (
                "import time\n\n"
                "def test_slow_op():\n"
                "    time.sleep(5.0)\n"
                "    assert True\n"
            ),
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-TO", "test_slow_op", 1)]),
            approved_by="owner",
            source="fixture:timeout",
        )

        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            timeout_seconds=0.35,
            settings=_verifier_settings(fixtures_root),
        )
        assert rep.workspace_removed is True
        assert rep.results[0].outcome == "inconclusive"
        assert rep.results[0].reason == "timeout"
        assert rep.results[0].exit_status is None

        rec = create_assessment(db, tenant, project_id, trigger="manual", assessed_revision=rev_r1)
        db.commit()
        assert rec.output["criteria"][0]["status"] == "unverified"
        assert rec.output["criteria"][0]["status"] != "verified"


def test_verifier_collection_error_and_missing_test_inconclusive(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """Syntax/import collection error and missing test both record inconclusive with reasons."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-col-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-col-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "collect_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_import_err.py": (
                'raise RuntimeError("import boom during collection")\n\n'
                "def test_import_err():\n"
                "    assert True\n"
            ),
            "tests/test_syntax_err.py": "def test_syntax_err(:\n    pass\n",
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(
                project_id,
                [
                    ("AC-IMP", "test_import_err", 1),
                    ("AC-SYN", "test_syntax_err", 1),
                    ("AC-MIS", "test_does_not_exist", 1),
                ],
            ),
            approved_by="owner",
            source="fixture:collect",
        )

        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()

        by_tid = {r.test_id: r for r in rep.results}
        assert by_tid["test_import_err"].outcome == "inconclusive"
        assert by_tid["test_import_err"].reason == "collection_error"
        assert by_tid["test_syntax_err"].outcome == "inconclusive"
        assert by_tid["test_syntax_err"].reason == "collection_error"
        assert by_tid["test_does_not_exist"].outcome == "inconclusive"
        assert by_tid["test_does_not_exist"].reason == "test_missing"


def test_verifier_unmapped_test_id_not_executed(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """Tests not in approved baseline mapping -> test_not_mapped and never executed."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-unm-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-unm-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "unmapped_repo"
    canary_path = tmp_path / "unmapped_executed.canary"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_app.py": (
                "from pathlib import Path\n\n"
                "def test_mapped_ok():\n"
                "    assert True\n\n"
                "def test_unmapped_side_effect():\n"
                f'    Path(r"{canary_path}").write_text("executed", encoding="utf-8")\n'
                "    assert True\n"
            ),
            ".ppm/updates/upd-001.yaml": _make_update_payload(
                project_id,
                "upd-001",
                summary="Reports unmapped test",
                checks=[
                    {
                        "name": "Unmapped",
                        "result": "passed",
                        "evidence_path": None,
                        "tested_revision": "rev-r1",
                        "environment": "local",
                        "criterion_ids": ["AC-001"],
                        "test_ids": ["test_unmapped_side_effect"],
                    }
                ],
            ),
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-001", "test_mapped_ok", 1)]),
            approved_by="owner",
            source="fixture:unmapped",
        )

        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            requested_test_ids=["test_mapped_ok", "test_unmapped_side_effect"],
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()

        assert canary_path.exists() is False
        by_tid = {r.test_id: r for r in rep.results}
        assert by_tid["test_mapped_ok"].outcome == "passed"
        assert by_tid["test_mapped_ok"].executed is True
        assert by_tid["test_unmapped_side_effect"].outcome == "test_not_mapped"
        assert by_tid["test_unmapped_side_effect"].executed is False
        assert by_tid["test_unmapped_side_effect"].evidence_id is None
        assert "test_unmapped_side_effect" in rep.unmapped_test_ids

        unmapped_db = db.execute(
            sa.select(ppm_evidence).where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.test_id == "test_unmapped_side_effect",
            )
        ).first()
        assert unmapped_db is None


def test_verifier_workspace_isolation_blocks_parent_traversal_and_protects_root(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """Test reading ../../secret or writing to cwd cannot reach registered_root or leak secrets."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-iso-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-iso-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "nested" / "iso_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_escape.py": (
                "from pathlib import Path\n\n"
                "def test_cannot_escape_or_dirty_root():\n"
                "    for rel in ('../secret.txt', '../../secret.txt', '../../../secret.txt'):\n"
                "        p = Path(rel)\n"
                "        assert not (p.exists() and 'TOP_SECRET_CANARY' in p.read_text(encoding='utf-8'))\n"
                "    Path('dirty_from_test.txt').write_text('should_not_touch_registered_root', encoding='utf-8')\n"
            ),
        },
    )

    # Place secrets outside repo_dir relative to registered root (not committed, adjacent to root)
    (fixtures_root / "secret.txt").write_text("TOP_SECRET_CANARY", encoding="utf-8")
    (fixtures_root / "nested" / "secret.txt").write_text("TOP_SECRET_CANARY", encoding="utf-8")

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-ISO", "test_cannot_escape_or_dirty_root", 1)]),
            approved_by="owner",
            source="fixture:iso",
        )

        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()

        assert rep.workspace_removed is True
        assert rep.results[0].outcome == "passed"
        assert (repo_dir / "dirty_from_test.txt").exists() is False
        # Verify host network hostname doesn't affect git root cleanliness
        assert _git(repo_dir, "status", "--porcelain") == ""


def test_verifier_scrubbed_environment_hides_parent_secrets(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parent secret env vars and proxy vars are stripped; HOME points to temp sandbox."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-env-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-env-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "env_repo"

    monkeypatch.setenv("EAP_SECRET_CANARY", "canary-parent-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://parent:secret@127.0.0.1/db")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-parent-secret")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_env.py": (
                "import os\n\n"
                "def test_env_scrubbed():\n"
                "    for k in ('EAP_SECRET_CANARY', 'DATABASE_URL', 'OPENAI_API_KEY', 'HTTP_PROXY', 'HTTPS_PROXY'):\n"
                "        assert k not in os.environ, f'leaked {k}'\n"
                "    assert 'eap_verifier_sandbox_' in os.environ['HOME']\n"
            ),
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-ENV", "test_env_scrubbed", 1)]),
            approved_by="owner",
            source="fixture:env",
        )

        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()
        assert rep.results[0].outcome == "passed"


def test_verifier_flag_off_real_roots_guard_allowlist_and_cli(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Flag off -> no execution/no rows; real roots -> refused; allowlist & CLI verify work."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-flg-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-flg-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    outside_root = tmp_path / "outside_fixtures"
    repo_dir = fixtures_root / "flag_repo"
    canary_path = tmp_path / "flag_off_canary.txt"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_flag.py": (
                "from pathlib import Path\n\n"
                "def test_should_not_run_when_disabled():\n"
                f'    Path(r"{canary_path}").write_text("ran", encoding="utf-8")\n'
                "    assert True\n"
            ),
            ".ppm/updates/upd-001.yaml": _make_update_payload(
                project_id,
                "upd-001",
                summary="Claim only while verifier disabled",
                checks=[
                    {
                        "name": "Flag check",
                        "result": "passed",
                        "evidence_path": None,
                        "tested_revision": "rev-r1",
                        "environment": "local",
                        "criterion_ids": ["AC-FLG"],
                        "test_ids": ["test_should_not_run_when_disabled"],
                    }
                ],
            ),
        },
    )

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(
                project_id,
                [("AC-FLG", "test_should_not_run_when_disabled", 1)],
            ),
            approved_by="owner",
            source="fixture:flag",
        )

        # 1. Feature flag off (default False): nothing runs, 0 evidence rows, criteria stay claims
        rep_off = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            settings=_verifier_settings(fixtures_root, enabled=False),
        )
        assert rep_off.enabled is False
        assert rep_off.executed is False
        assert rep_off.skipped_reason == "verifier_disabled"
        assert canary_path.exists() is False
        ev_count = db.execute(
            sa.select(sa.func.count()).select_from(ppm_evidence).where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.project_id == project_id,
            )
        ).scalar_one()
        assert ev_count == 0
        rec = create_assessment(db, tenant, project_id, trigger="manual", assessed_revision=rev_r1)
        assert rec.output["criteria"][0]["status"] == "unverified"
        assert rec.output["criteria"][0]["reported_status"] == "claimed_done"

        # 2. Real roots refused
        with pytest.raises(VerifierRealRootRefusedError):
            run_verifier(
                db,
                tenant,
                project_id,
                rev_r1,
                settings=_verifier_settings(fixtures_root, enabled=True, allow_real=True),
            )

        # 3. Root outside EAP_COLLECTOR_FIXTURES_ROOT refused
        outside_root.mkdir(parents=True, exist_ok=True)
        with pytest.raises(RootNotPermittedSecurityError):
            run_verifier(
                db,
                tenant,
                project_id,
                rev_r1,
                settings=_verifier_settings(outside_root, enabled=True, allow_real=False),
            )
        db.commit()

    # 4. Allowlist rejects flag injection & traversal
    for bad in ("-k test_x", "--rootdir=/tmp", "../escape.py", "a;rm -rf /", ""):
        with pytest.raises(VerifierAllowlistError):
            build_allowed_verifier_argv(bad)
    with pytest.raises(VerifierAllowlistError):
        validate_allowed_argv(["bash", "-c", "id", "-q", "-p", "no:cacheprovider"])

    # 5. CLI `verify` smoke
    db_url = db_engine.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("PPM_VERIFIER_ENABLED", "true")
    monkeypatch.setenv("EAP_COLLECTOR_ALLOW_REAL_ROOTS", "false")
    monkeypatch.setenv("EAP_COLLECTOR_FIXTURES_ROOT", str(fixtures_root))
    rc = cli_main(
        ["verify", "--tenant", tenant, "--project", project_id, "--revision", rev_r1]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert '"executed": true' in stdout
    assert canary_path.exists() is True


def test_verifier_caps_large_output(
    db_engine: sa.Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output exceeding max_output_bytes stops disk writes at cap and records truncation."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-cap-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-cap-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "cap_repo"

    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_noisy.py": (
                "def test_noisy_fail():\n"
                '    print("NOISE_" * 20000)\n'
                '    assert False, "expected failure after huge stdout"\n'
            ),
        },
    )

    observed_log_sizes: list[int] = []
    real_rmtree = verifier_mod.shutil.rmtree

    def _spy_rmtree(path: Any, *args: Any, **kwargs: Any) -> None:
        out_dir = Path(path) / "out"
        if out_dir.is_dir():
            for log_file in out_dir.glob("*.log"):
                observed_log_sizes.append(log_file.stat().st_size)
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(verifier_mod.shutil, "rmtree", _spy_rmtree)

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-CAP", "test_noisy_fail", 1)]),
            approved_by="owner",
            source="fixture:cap",
        )
        rep = run_verifier(
            db,
            tenant,
            project_id,
            rev_r1,
            max_output_bytes=128,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()

        assert len(rep.results) == 1
        r0 = rep.results[0]
        assert r0.outcome == "failed"
        assert r0.exit_status == 1
        assert r0.reason is None
        assert r0.output_hash is not None
        assert len(r0.output_hash) == 64
        assert r0.output_truncated is True
        assert r0.truncated_bytes > 100_000
        assert observed_log_sizes == [128]

        row = db.execute(
            sa.select(ppm_evidence).where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.project_id == project_id,
                ppm_evidence.c.test_id == "test_noisy_fail",
            )
        ).mappings().one()
        assert row["exit_status"] == 1
        assert row["output_hash"] == r0.output_hash
        assert row["output_truncated"] is True
        assert row["truncated_bytes"] == r0.truncated_bytes

        # Second revision with much higher cap captures full output without truncation
        (repo_dir / "tests" / "test_noisy.py").write_text(
            "def test_noisy_fail():\n"
            '    print("NOISE_" * 20000)\n'
            '    assert False, "expected failure after huge stdout v2"\n',
            encoding="utf-8",
        )
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "commit", "-m", "second noisy commit")
        rev_r2 = _git(repo_dir, "rev-parse", "HEAD")
        rep_full = run_verifier(
            db,
            tenant,
            project_id,
            rev_r2,
            max_output_bytes=262_144,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()
        assert len(rep_full.results) == 1
        assert rep_full.results[0].outcome == "failed"
        assert rep_full.results[0].output_truncated is False
        assert rep_full.results[0].truncated_bytes == 0
        assert rep_full.results[0].output_hash != r0.output_hash


def test_verifier_conftest_os_exit_zero_and_skip_all_cannot_pass(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """V1: conftest os._exit(0) and skip-all -> inconclusive; normal pass -> passed; XXE rejected."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-v1-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-v1-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "v1_conftest_repo"

    conftest_exit0 = (
        "import os\n\n"
        "def pytest_sessionstart(session):\n"
        "    os._exit(0)\n"
    )
    failing_test = (
        "def test_guarded_target():\n"
        '    assert False, "must never be reported passed via conftest os._exit(0)"\n'
    )
    rev_exit0 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "conftest.py": conftest_exit0,
            "tests/test_guarded.py": failing_test,
        },
    )

    expected_conftest_hash_1 = hashlib.sha256(conftest_exit0.encode("utf-8")).hexdigest()

    # Entity expansion / DTD declaration and missing-nodeid XML spoofing must fail closed
    xxe_path = tmp_path / "xxe.xml"
    xxe_path.write_bytes(
        b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe "pwn">]>'
        b'<testsuites><testsuite errors="0" failures="0" skipped="0" tests="1">'
        b'<testcase classname="tests.test_guarded" name="test_guarded_target"/>'
        b"</testsuite></testsuites>"
    )
    assert verifier_mod._verify_junit_xml_result(
        xxe_path, "tests/test_guarded.py::test_guarded_target"
    ) == (False, "no_result_record")

    wrong_tc_path = tmp_path / "wrong_tc.xml"
    wrong_tc_path.write_bytes(
        b'<?xml version="1.0"?>'
        b'<testsuites><testsuite errors="0" failures="0" skipped="0" tests="1">'
        b'<testcase classname="tests.test_other" name="test_dummy"/>'
        b"</testsuite></testsuites>"
    )
    assert verifier_mod._verify_junit_xml_result(
        wrong_tc_path, "tests/test_guarded.py::test_guarded_target"
    ) == (False, "nodeid_not_executed")

    with SessionLocal() as db:
        _register_and_scan_project(
            db,
            tenant_id=tenant,
            project_id=project_id,
            repo_dir=repo_dir,
            fixtures_root=fixtures_root,
        )
        approve_baseline(
            db,
            tenant,
            project_id,
            baseline_revision=1,
            criteria=_baseline_criteria(project_id, [("AC-V1", "test_guarded_target", 1)]),
            approved_by="owner",
            source="fixture:v1",
        )

        # 1. conftest.py os._exit(0) + failing test -> inconclusive (no_result_record)
        rep_exit0 = run_verifier(
            db,
            tenant,
            project_id,
            rev_exit0,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()
        r0 = rep_exit0.results[0]
        assert r0.outcome == "inconclusive"
        assert r0.outcome != "passed"
        assert r0.reason == "no_result_record"
        assert r0.test_config_files == [{"path": "conftest.py", "sha256": expected_conftest_hash_1}]

        ev_exit0 = db.execute(
            sa.select(ppm_evidence).where(
                ppm_evidence.c.tenant_id == tenant,
                ppm_evidence.c.project_id == project_id,
                ppm_evidence.c.revision == rev_exit0,
            )
        ).mappings().one()
        assert ev_exit0["outcome"] == "inconclusive"
        assert ev_exit0["test_config_files"] == [{"path": "conftest.py", "sha256": expected_conftest_hash_1}]

        rec0 = create_assessment(db, tenant, project_id, trigger="manual", assessed_revision=rev_exit0)
        db.commit()
        assert rec0.output["criteria"][0]["status"] == "unverified"
        detail0 = get_assessment_detail(db, tenant, rec0.id)
        assert detail0["explanation"]["AC-V1"]["verified"]["excluded_evidence"][0][
            "test_config_files"
        ] == [{"path": "conftest.py", "sha256": expected_conftest_hash_1}]

        # 2. conftest.py marking every test skipped + pytest.ini -> inconclusive (result_mismatch)
        conftest_skip = (
            "import pytest\n\n"
            "def pytest_collection_modifyitems(items):\n"
            "    for item in items:\n"
            '        item.add_marker(pytest.mark.skip(reason="spoofed skip"))\n'
        )
        pytest_ini_text = "[pytest]\ntestpaths = tests\n"
        (repo_dir / "conftest.py").write_text(conftest_skip, encoding="utf-8")
        (repo_dir / "pytest.ini").write_text(pytest_ini_text, encoding="utf-8")
        (repo_dir / "tests" / "test_guarded.py").write_text(
            "def test_guarded_target():\n    assert True\n",
            encoding="utf-8",
        )
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "commit", "-m", "rev2 skip-all conftest")
        rev_skip = _git(repo_dir, "rev-parse", "HEAD")

        rep_skip = run_verifier(
            db,
            tenant,
            project_id,
            rev_skip,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()
        r_skip = rep_skip.results[0]
        assert r_skip.outcome == "inconclusive"
        assert r_skip.reason == "result_mismatch"
        expected_cfg_rev2 = sorted(
            [
                {
                    "path": "conftest.py",
                    "sha256": hashlib.sha256(conftest_skip.encode("utf-8")).hexdigest(),
                },
                {
                    "path": "pytest.ini",
                    "sha256": hashlib.sha256(pytest_ini_text.encode("utf-8")).hexdigest(),
                },
            ],
            key=lambda x: x["path"],
        )
        assert r_skip.test_config_files == expected_cfg_rev2

        # 3. Clean conftest + normal passing test -> passed with JUnit confirmation
        (repo_dir / "conftest.py").unlink()
        (repo_dir / "pytest.ini").unlink()
        _git(repo_dir, "add", "-A")
        _git(repo_dir, "commit", "-m", "rev3 clean pass")
        rev_pass = _git(repo_dir, "rev-parse", "HEAD")

        rep_pass = run_verifier(
            db,
            tenant,
            project_id,
            rev_pass,
            settings=_verifier_settings(fixtures_root),
        )
        db.commit()
        r_pass = rep_pass.results[0]
        assert r_pass.outcome == "passed"
        assert r_pass.exit_status == 0
        assert r_pass.reason is None
        assert r_pass.test_config_files == []


def test_verifier_timeout_kills_detached_grandchild_process(
    db_engine: sa.Engine,
    tmp_path: Path,
) -> None:
    """V2: Timeout terminates even start_new_session=True grandchildren (Win32 Job / Linux pidns)."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant = f"tenant-v2-{uuid.uuid4().hex[:8]}"
    project_id = f"proj-v2-{uuid.uuid4().hex[:8]}"
    fixtures_root = tmp_path / "fixtures"
    repo_dir = fixtures_root / "v2_grandchild_repo"
    marker = tmp_path / "detached_grandchild_marker.txt"
    marker.unlink(missing_ok=True)

    marker_posix = marker.as_posix()
    rev_r1 = _init_fixture_repo(
        repo_dir,
        project_id,
        {
            "tests/test_grandchild.py": (
                "import subprocess, sys, time\n\n"
                "def test_spawn_detached_grandchild():\n"
                "    code = (\n"
                '        "import time\\n"\n'
                '        "time.sleep(5.0)\\n"\n'
                f'        "with open({marker_posix!r}, \\"w\\", encoding=\\"utf-8\\") as f:\\n"\n'
                '        "    f.write(\\"leaked\\")\\n"\n'
                "    )\n"
                "    subprocess.Popen([sys.executable, '-c', code], start_new_session=True)\n"
                "    time.sleep(10.0)\n"
            ),
        },
    )

    try:
        with SessionLocal() as db:
            _register_and_scan_project(
                db,
                tenant_id=tenant,
                project_id=project_id,
                repo_dir=repo_dir,
                fixtures_root=fixtures_root,
            )
            approve_baseline(
                db,
                tenant,
                project_id,
                baseline_revision=1,
                criteria=_baseline_criteria(
                    project_id,
                    [("AC-V2", "test_spawn_detached_grandchild", 1)],
                ),
                approved_by="owner",
                source="fixture:v2",
            )
            rep = run_verifier(
                db,
                tenant,
                project_id,
                rev_r1,
                timeout_seconds=1.5,
                settings=_verifier_settings(fixtures_root),
            )
            db.commit()
            assert rep.results[0].outcome == "inconclusive"
            assert rep.results[0].reason == "timeout"
            assert rep.results[0].exit_status is None

        # Wait past the grandchild's 5s sleep; if not killed, it would write the marker.
        time.sleep(6.0)
        assert marker.exists() is False
    finally:
        marker.unlink(missing_ok=True)


