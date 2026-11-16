"""SC-07 PPM scan baseline benchmark tool (P2a-WP8).

Generates fixture Git repositories at configurable file-count scales (e.g. 1k, 10k, 50k files),
registers each source/scope once, and measures:
- File count and total bytes
- Initial full scan: collector scan, ingest, assessment, total duration, DB & WAL growth
- Incremental scans (small edit + new update commit, over N repeats): p50/p95 durations, DB & WAL growth
- Unchanged rescans (0 file changes, over N repeats): p50/p95 durations, DB & WAL growth, dedup counts

Records machine specs, PostgreSQL version & settings, exact command, and run duration.
No acceptance thresholds are invented here (DG-5 / ARCH-30).
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from eap.api.dependencies import Principal
from eap.db.tables import project_scopes
from eap.ppm.assessments import approve_baseline, create_assessment
from eap.ppm.collector.collector import PpmCollector
from eap.ppm.ingest import ingest_snapshot
from eap.ppm.service import LocationService

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_URL = "postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_scan_bench"
TENANT_ID = "tenant-scan-bench"
PRINCIPAL = Principal(tenant_id=TENANT_ID, principal_id="owner-scan-bench", roles=["owner"])


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return round(sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac, 3)


def _summarize(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {
        "n": len(ordered),
        "p50": _percentile(ordered, 50.0),
        "p95": _percentile(ordered, 95.0),
        "min": round(ordered[0], 3) if ordered else 0.0,
        "max": round(ordered[-1], 3) if ordered else 0.0,
        "mean": round(statistics.fmean(ordered), 3) if ordered else 0.0,
    }


def _get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _get_git_version() -> str:
    try:
        res = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _get_total_ram_gb() -> float:
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return float(round(stat.ullTotalPhys / (1024**3), 2))
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024**2), 2)
    except OSError:
        pass
    return 0.0


def _collect_pg_info(engine: sa.Engine) -> dict[str, Any]:
    settings_keys = [
        "shared_buffers",
        "work_mem",
        "effective_cache_size",
        "max_connections",
        "synchronous_commit",
        "fsync",
        "wal_buffers",
        "max_wal_size",
        "checkpoint_timeout",
    ]
    pg_settings: dict[str, str] = {}
    pg_version = "unknown"
    with engine.connect() as conn:
        pg_version = str(conn.execute(text("SELECT version()")).scalar() or "unknown")
        for key in settings_keys:
            val = conn.execute(text(f"SHOW {key}")).scalar()
            pg_settings[key] = str(val)
    return {
        "os": f"{platform.system()}-{platform.release()}-{platform.version()}",
        "cpu_model": platform.processor() or "unknown",
        "cpu_cores_logical": os.cpu_count() or 0,
        "ram_gb": _get_total_ram_gb(),
        "python_version": platform.python_version(),
        "git_version": _get_git_version(),
        "postgres_version": pg_version,
        "postgres_settings": pg_settings,
    }


def _query_wal_lsn(engine: sa.Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT pg_current_wal_lsn()")).scalar())


def _query_wal_diff(engine: sa.Engine, lsn_before: str, lsn_after: str) -> int:
    with engine.connect() as conn:
        diff = conn.execute(
            text("SELECT pg_wal_lsn_diff(:after, :before)"),
            {"after": lsn_after, "before": lsn_before},
        ).scalar()
        return int(diff or 0)


def _query_db_size_bytes(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar() or 0)


def _ensure_database_ready(db_url: str, *, reset_db: bool) -> sa.Engine:
    parsed = make_url(db_url)
    target_db = parsed.database
    if reset_db and target_db and target_db not in ("postgres", "template0", "template1"):
        admin_url = parsed.set(database="postgres")
        admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with admin_engine.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :db AND pid <> pg_backend_pid()"
                    ),
                    {"db": target_db},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{target_db}"'))
                conn.execute(text(f'CREATE DATABASE "{target_db}"'))
        finally:
            admin_engine.dispose()

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old_db = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if old_db is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_db

    return create_engine(db_url, pool_pre_ping=True)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Scan Bench"
    env["GIT_AUTHOR_EMAIL"] = "scan-bench@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Scan Bench"
    env["GIT_COMMITTER_EMAIL"] = "scan-bench@example.invalid"
    return env


def _git(repo_dir: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        env=_git_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


def _write_utf8(path: Path, text_content: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = text_content.encode("utf-8")
    path.write_bytes(raw)
    return len(raw)


def _generate_fixture_repo(repo_dir: Path, project_id: str, target_files: int) -> tuple[int, int]:
    """Create a fixture git repository with exactly `target_files` tracked files."""
    if target_files < 4:
        raise ValueError("target_files must be >= 4")

    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    repo_dir.mkdir(parents=True, exist_ok=True)

    _git(repo_dir, "init", "-b", "main")

    total_bytes = 0
    file_count = 0

    # 4 PPM metadata files
    total_bytes += _write_utf8(
        repo_dir / ".ppm" / "project.yaml",
        json.dumps(
            {
                "contract_version": "2.1",
                "kind": "project",
                "project_id": project_id,
                "name": f"Scan Bench {target_files}",
                "owner_ref": PRINCIPAL.principal_id,
                "objectives": [
                    {
                        "objective_id": "OBJ-001",
                        "statement": "Measure scan baseline",
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
        )
        + "\n",
    )
    file_count += 1

    total_bytes += _write_utf8(
        repo_dir / ".ppm" / "acceptance.yaml",
        json.dumps(
            {
                "contract_version": "2.1",
                "kind": "acceptance",
                "project_id": project_id,
                "state": "draft",
                "criteria": [
                    {
                        "criterion_id": "AC-001",
                        "deliverable_ref": "DEL-001",
                        "description": "Benchmark criterion 1",
                        "weight": 1,
                        "required": True,
                        "verification_method": "test_report",
                        "verification_refs": [{"kind": "test_id", "ref": "test_ac001"}],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    file_count += 1

    total_bytes += _write_utf8(
        repo_dir / ".ppm" / "updates" / "upd-0001.yaml",
        json.dumps(
            {
                "contract_version": "2.1",
                "kind": "update",
                "project_id": project_id,
                "update_id": "upd-0001",
                "session_id": "bench-s1",
                "recorded_at": "2026-11-16T00:00:00Z",
                "actor": "bench-agent",
                "summary": "Initial benchmark revision",
                "work_package_id": "WP-BENCH",
                "base_revision": None,
                "result_revision": "rev-1",
                "working_tree_state": "clean",
                "supersedes_update_id": None,
                "completed": [{"item_ref": "AC-001", "description": "Initial impl"}],
                "in_progress": [],
                "checks": [],
                "blockers": [],
                "next_actions": [],
                "decisions_required": [],
            },
            indent=2,
        )
        + "\n",
    )
    file_count += 1

    total_bytes += _write_utf8(
        repo_dir / ".ppm" / "notes" / "note-0001.md",
        "# Benchmark Note\n\nInitial observation note for scan baseline.\n",
    )
    file_count += 1

    # Remaining N - 4 source/test files across subdirectories (100 files per dir)
    remaining = target_files - 4
    pad = "x" * 256
    for idx in range(remaining):
        dir_idx = idx // 100
        rel = Path("src") / f"pkg_{dir_idx:03d}" / f"mod_{idx:05d}.py"
        content = (
            f'"""Synthetic benchmark module {idx}."""\n'
            f"MODULE_ID = {idx}\n"
            f'PAYLOAD = "{pad}"\n'
        )
        total_bytes += _write_utf8(repo_dir / rel, content)
        file_count += 1

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "Initial benchmark fixture")
    return file_count, total_bytes


def _commit_incremental_change(
    repo_dir: Path,
    project_id: str,
    step: int,
) -> int:
    """Modify 1 source file and add 1 update artifact; return delta bytes on modified/added files."""
    delta_bytes = 0
    mod_file = repo_dir / "src" / "pkg_000" / "mod_00000.py"
    old_len = mod_file.stat().st_size if mod_file.exists() else 0
    new_len = _write_utf8(
        mod_file,
        f'"""Synthetic benchmark module 0 (step {step})."""\n'
        f"MODULE_ID = 0\n"
        f"STEP = {step}\n"
        f'PAYLOAD = "{"x" * 256}"\n',
    )
    delta_bytes += new_len - old_len

    upd_id = f"upd-{step + 1:04d}"
    prev_id = f"upd-{step:04d}"
    upd_len = _write_utf8(
        repo_dir / ".ppm" / "updates" / f"{upd_id}.yaml",
        json.dumps(
            {
                "contract_version": "2.1",
                "kind": "update",
                "project_id": project_id,
                "update_id": upd_id,
                "session_id": f"bench-s{step + 1}",
                "recorded_at": f"2026-11-16T00:{step:02d}:00Z",
                "actor": "bench-agent",
                "summary": f"Incremental benchmark step {step}",
                "work_package_id": "WP-BENCH",
                "base_revision": None,
                "result_revision": f"rev-{step + 1}",
                "working_tree_state": "clean",
                "supersedes_update_id": prev_id,
                "completed": [],
                "in_progress": [],
                "checks": [],
                "blockers": [],
                "next_actions": [],
                "decisions_required": [],
            },
            indent=2,
        )
        + "\n",
    )
    delta_bytes += upd_len

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", f"Incremental step {step}")
    return delta_bytes


def _run_one_pipeline_cycle(
    db: Session,
    engine: sa.Engine,
    collector: PpmCollector,
    *,
    source_id: str,
    project_id: str,
) -> dict[str, Any]:
    db_before = _query_db_size_bytes(engine)
    wal_before = _query_wal_lsn(engine)

    t0 = time.perf_counter()
    scan_res = collector.scan_source(db, tenant_id=TENANT_ID, source_id=source_id)
    t1 = time.perf_counter()

    psnap_id = scan_res.project_snapshot_ids.get(project_id)
    if not psnap_id:
        raise RuntimeError(f"Missing project_snapshot_id for {project_id}; status={scan_res.status}")

    t2 = time.perf_counter()
    ingest_report = ingest_snapshot(db, TENANT_ID, psnap_id)
    t3 = time.perf_counter()

    t4 = time.perf_counter()
    asmt_res = create_assessment(
        db,
        TENANT_ID,
        project_id,
        trigger="snapshot",
        project_snapshot_id=psnap_id,
    )
    db.commit()
    t5 = time.perf_counter()

    wal_after = _query_wal_lsn(engine)
    db_after = _query_db_size_bytes(engine)

    return {
        "scan_ms": round((t1 - t0) * 1000.0, 3),
        "ingest_ms": round((t3 - t2) * 1000.0, 3),
        "assessment_ms": round((t5 - t4) * 1000.0, 3),
        "total_ms": round(((t1 - t0) + (t3 - t2) + (t5 - t4)) * 1000.0, 3),
        "db_growth_bytes": db_after - db_before,
        "wal_bytes": _query_wal_diff(engine, wal_before, wal_after),
        "ingested_artifacts": ingest_report.counts.get("ingested", 0),
        "assessment_deduplicated": asmt_res.deduplicated,
    }


def benchmark_scale(
    engine: sa.Engine,
    session_factory: sessionmaker[Session],
    fixtures_root: Path,
    target_files: int,
    repeats: int,
) -> dict[str, Any]:
    project_id = f"proj-bench-{target_files}"
    source_id = f"src-bench-{target_files}"
    repo_dir = fixtures_root / f"repo_{target_files}"

    print(f"[scale={target_files}] Generating {target_files} fixture files in {repo_dir}...")
    t_gen0 = time.perf_counter()
    file_count, total_bytes = _generate_fixture_repo(repo_dir, project_id, target_files)
    gen_seconds = round(time.perf_counter() - t_gen0, 3)

    host_os = "windows" if os.name == "nt" else "linux"
    location_id = f"loc-bench-{target_files}"
    repo_identity = f"git:bench-{target_files}"

    with session_factory() as db:
        LocationService.register_source(
            db,
            tenant_id=TENANT_ID,
            source_id=source_id,
            canonical_root=str(repo_dir),
            repo_identity=repo_identity,
            principal_id="principal-system",
            host_id="host-bench-001",
            host_os=host_os,
        )
        LocationService.register_location(
            db,
            tenant_id=TENANT_ID,
            data={
                "contract_version": "1.1",
                "location_id": location_id,
                "project_id": project_id,
                "host_id": "host-bench-001",
                "connector_id": "connector-bench",
                "root_path": str(repo_dir),
                "metadata_root": ".ppm",
                "scan_scope": ["."],
                "artifact_aliases": {
                    "architecture": ["architecture.md", "docs/ARCHITECTURE.md"],
                    "blueprint": ["blueprint.md", "MASTER_BLUEPRINT.md"],
                    "implementation_plan": ["implement_plan.md", "docs/IMPLEMENTATION_PLAN.md"],
                },
                "notes_globs": [".ppm/notes/*.md"],
                "follow_symlinks": False,
                "allow_hardlinked_files": False,
                "max_file_bytes": 10 * 1024 * 1024,
                "deny_globs": ["**/.git/**", ".env", ".env.*", "*.pem", "*.key"],
                "classification": "internal",
                "egress_policy_ref": "egress:local-only",
                "identity": {
                    "canonical_root_fingerprint": None,
                    "repo_identity": repo_identity,
                },
                "state": "proposed",
                "revision": 1,
                "registered_by_ref": "principal:principal-system",
                "approved_by_ref": None,
                "source_id": source_id,
            },
            principal_id="principal-system",
            host_os=host_os,
        )
        LocationService.approve_location(
            db,
            tenant_id=TENANT_ID,
            location_id=location_id,
            principal=PRINCIPAL,
        )
        db.commit()

        # Approve baseline so create_assessment evaluates against a real baseline
        approve_baseline(
            db,
            TENANT_ID,
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
            approved_by=PRINCIPAL.principal_id,
            source="bench",
        )
        db.commit()

    collector = PpmCollector(
        allow_real_roots=False,
        fixtures_root=str(fixtures_root),
        ingest_enabled=False,
    )

    # 1. Full initial scan
    print(f"[scale={target_files}] Running initial full scan...")
    with session_factory() as db:
        full_metrics = _run_one_pipeline_cycle(
            db, engine, collector, source_id=source_id, project_id=project_id
        )

    # 2. Incremental scans (1 modified file + 1 new update committed each repeat)
    print(f"[scale={target_files}] Running {repeats} incremental scans...")
    inc_runs: list[dict[str, Any]] = []
    for step in range(1, repeats + 1):
        delta = _commit_incremental_change(repo_dir, project_id, step)
        total_bytes += delta
        file_count += 1
        with session_factory() as db:
            m = _run_one_pipeline_cycle(
                db, engine, collector, source_id=source_id, project_id=project_id
            )
            inc_runs.append(m)

    # 3. Unchanged rescans (0 changes)
    print(f"[scale={target_files}] Running {repeats} unchanged rescans...")
    unchanged_runs: list[dict[str, Any]] = []
    for _ in range(repeats):
        with session_factory() as db:
            m = _run_one_pipeline_cycle(
                db, engine, collector, source_id=source_id, project_id=project_id
            )
            unchanged_runs.append(m)

    # Verify scope row count
    with session_factory() as db:
        scope_exists = db.execute(
            sa.select(sa.func.count())
            .select_from(project_scopes)
            .where(project_scopes.c.project_id == project_id)
        ).scalar_one()

    return {
        "scale_target_files": target_files,
        "initial_file_count": target_files,
        "final_file_count": file_count,
        "total_bytes": total_bytes,
        "repo_gen_seconds": gen_seconds,
        "repeats": repeats,
        "scopes_registered": int(scope_exists),
        "full_scan": full_metrics,
        "incremental_scan": {
            "scan_ms": _summarize([r["scan_ms"] for r in inc_runs]),
            "ingest_ms": _summarize([r["ingest_ms"] for r in inc_runs]),
            "assessment_ms": _summarize([r["assessment_ms"] for r in inc_runs]),
            "total_ms": _summarize([r["total_ms"] for r in inc_runs]),
            "db_growth_bytes": _summarize([float(r["db_growth_bytes"]) for r in inc_runs]),
            "wal_bytes": _summarize([float(r["wal_bytes"]) for r in inc_runs]),
        },
        "unchanged_rescan": {
            "scan_ms": _summarize([r["scan_ms"] for r in unchanged_runs]),
            "ingest_ms": _summarize([r["ingest_ms"] for r in unchanged_runs]),
            "assessment_ms": _summarize([r["assessment_ms"] for r in unchanged_runs]),
            "total_ms": _summarize([r["total_ms"] for r in unchanged_runs]),
            "db_growth_bytes": _summarize([float(r["db_growth_bytes"]) for r in unchanged_runs]),
            "wal_bytes": _summarize([float(r["wal_bytes"]) for r in unchanged_runs]),
            "deduplicated_count": sum(1 for r in unchanged_runs if r["assessment_deduplicated"]),
        },
    }


def _fmt_mb(num_bytes: float) -> str:
    return f"{num_bytes / (1024 * 1024):.3f} MB"


def _render_markdown(report: dict[str, Any]) -> str:
    env = report["environment"]
    pg = env["postgres_settings"]
    lines: list[str] = [
        "# SC-07 PPM Scan Baseline Measurement Report (P2a-WP8)",
        "",
        "- **Work Package**: P2a-WP8 Scan baseline (measurement, SC-07)",
        f"- **Commit**: `{report['git_commit']}`",
        f"- **Execution Timestamp**: `{report['timestamp_utc']}`",
        f"- **Total Benchmark Duration**: `{report['total_duration_seconds']:.3f} s`",
        f"- **Exact Command**: `{report['command']}`",
        "",
        "---",
        "",
        "## 1. Environment and PostgreSQL Configuration",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| Operating System | {env['os']} |",
        f"| CPU Model | {env['cpu_model']} |",
        f"| Logical CPU Cores | {env['cpu_cores_logical']} |",
        f"| Physical Memory (RAM) | {env['ram_gb']} GB |",
        f"| Python Version | {env['python_version']} |",
        f"| Git Version | {env['git_version']} |",
        f"| PostgreSQL Version | {env['postgres_version']} |",
    ]
    for k, v in pg.items():
        lines.append(f"| `{k}` | `{v}` |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Measured Scales: Initial Full Scan",
            "",
            "| Target Files | Measured Files | Total Bytes | Repo Gen (s) | Scan (ms) | Ingest (ms) | Assessment (ms) | Total (ms) | DB Growth | WAL Growth |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for sc in report["scales"]:
        fs = sc["full_scan"]
        lines.append(
            f"| {sc['scale_target_files']:,} | {sc['initial_file_count']:,} | {sc['total_bytes']:,} "
            f"| {sc['repo_gen_seconds']:.3f} | {fs['scan_ms']:.2f} | {fs['ingest_ms']:.2f} "
            f"| {fs['assessment_ms']:.2f} | {fs['total_ms']:.2f} "
            f"| {fs['db_growth_bytes']:,} B ({_fmt_mb(fs['db_growth_bytes'])}) "
            f"| {fs['wal_bytes']:,} B ({_fmt_mb(fs['wal_bytes'])}) |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Incremental Scan Durations & Storage Growth (1 modified file + 1 new `.ppm/updates/` commit)",
            "",
            "| Scale | Repeats | Scan p50 / p95 (ms) | Ingest p50 / p95 (ms) | Assessment p50 / p95 (ms) | Total p50 / p95 (ms) | DB Growth / scan (p50) | WAL Growth / scan (p50) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for sc in report["scales"]:
        inc = sc["incremental_scan"]
        lines.append(
            f"| {sc['scale_target_files']:,} | {sc['repeats']} "
            f"| {inc['scan_ms']['p50']:.2f} / {inc['scan_ms']['p95']:.2f} "
            f"| {inc['ingest_ms']['p50']:.2f} / {inc['ingest_ms']['p95']:.2f} "
            f"| {inc['assessment_ms']['p50']:.2f} / {inc['assessment_ms']['p95']:.2f} "
            f"| {inc['total_ms']['p50']:.2f} / {inc['total_ms']['p95']:.2f} "
            f"| {int(inc['db_growth_bytes']['p50']):,} B ({_fmt_mb(inc['db_growth_bytes']['p50'])}) "
            f"| {int(inc['wal_bytes']['p50']):,} B ({_fmt_mb(inc['wal_bytes']['p50'])}) |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Unchanged Rescan Durations & Semantic Deduplication (0 file changes)",
            "",
            "| Scale | Repeats | Scan p50 / p95 (ms) | Ingest p50 / p95 (ms) | Assessment p50 / p95 (ms) | Total p50 / p95 (ms) | Deduplicated | DB Growth / scan (p50) | WAL Growth / scan (p50) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for sc in report["scales"]:
        unc = sc["unchanged_rescan"]
        lines.append(
            f"| {sc['scale_target_files']:,} | {sc['repeats']} "
            f"| {unc['scan_ms']['p50']:.2f} / {unc['scan_ms']['p95']:.2f} "
            f"| {unc['ingest_ms']['p50']:.2f} / {unc['ingest_ms']['p95']:.2f} "
            f"| {unc['assessment_ms']['p50']:.2f} / {unc['assessment_ms']['p95']:.2f} "
            f"| {unc['total_ms']['p50']:.2f} / {unc['total_ms']['p95']:.2f} "
            f"| {unc['deduplicated_count']}/{sc['repeats']} "
            f"| {int(unc['db_growth_bytes']['p50']):,} B ({_fmt_mb(unc['db_growth_bytes']['p50'])}) "
            f"| {int(unc['wal_bytes']['p50']):,} B ({_fmt_mb(unc['wal_bytes']['p50'])}) |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. Methodology & Threshold Policy (SC-07 / ARCH-30)",
            "",
            "- **Collector behavior**: `PpmCollector.scan_source` walks all files matching `scan_scope` (`[\".\"]`), opens each file via handle-contained `safe_open`, computes SHA-256, and persists a `collector_snapshots` + `project_snapshots` manifest. Consequently, collector scan duration scales linearly with repository file count on both initial and incremental scans.",
            "- **Ingest & assessment deduplication**: `ingest_snapshot` deduplicates unchanged `.ppm/` artifacts, and `create_assessment` (`ISS-034`) deduplicates unchanged semantic `inputs_hash` (`deduplicated=True` on unchanged rescans).",
            "- **No invented thresholds**: Per `docs/IMPLEMENTATION_PLAN.md` (`ARCH-30`, `SC-07`), this report records baseline measurements only. Formal acceptance thresholds belong to **DG-5** (prior to `P5-WP4`).",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="SC-07 PPM scan baseline benchmark (P2a-WP8)")
    parser.add_argument("--db-url", default=os.environ.get("TEST_DATABASE_URL", DEFAULT_DB_URL))
    parser.add_argument("--reset-db", action="store_true", help="Drop & recreate target DB before running")
    parser.add_argument("--scales", default="1000,10000,50000", help="Comma-separated file counts")
    parser.add_argument("--repeats", type=int, default=5, help="Repeats for incremental and unchanged scans")
    parser.add_argument("--fixtures-root", default=None, help="Directory for generated fixture repos")
    parser.add_argument("--keep-fixtures", action="store_true", help="Keep generated fixture repos after run")
    parser.add_argument(
        "--md-output",
        default=str(ROOT / "docs" / "measurements" / "SCAN_BASELINE.md"),
        help="Path to write Markdown report",
    )
    parser.add_argument("--json-output", default=None, help="Optional path to write raw JSON report")
    args = parser.parse_args()

    scales = [int(x.strip()) for x in args.scales.split(",") if x.strip()]
    if not scales:
        raise ValueError("At least one scale must be specified")

    t_start = time.perf_counter()
    engine = _ensure_database_ready(args.db_url, reset_db=args.reset_db)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    env_info = _collect_pg_info(engine)

    temp_ctx = None
    if args.fixtures_root:
        fixtures_root = Path(args.fixtures_root).resolve()
        fixtures_root.mkdir(parents=True, exist_ok=True)
    else:
        temp_ctx = tempfile.TemporaryDirectory(prefix="eap_scan_bench_")
        fixtures_root = Path(temp_ctx.name).resolve()

    try:
        scale_results: list[dict[str, Any]] = []
        for s in scales:
            res = benchmark_scale(engine, session_factory, fixtures_root, s, args.repeats)
            scale_results.append(res)

        total_duration = round(time.perf_counter() - t_start, 3)
        sanitized_argv = []
        skip_next = False
        for tok in sys.argv[1:]:
            if skip_next:
                sanitized_argv.append("<redacted-db-url>")
                skip_next = False
                continue
            if tok == "--db-url":
                sanitized_argv.append(tok)
                skip_next = True
            elif tok.startswith("--db-url="):
                sanitized_argv.append("--db-url=<redacted-db-url>")
            else:
                sanitized_argv.append(tok)

        report: dict[str, Any] = {
            "work_package": "P2a-WP8",
            "git_commit": _get_git_commit(),
            "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
            "total_duration_seconds": total_duration,
            "command": f"uv run python tools/bench/scan_baseline.py {' '.join(sanitized_argv)}".strip(),
            "environment": env_info,
            "scales": scale_results,
        }

        md_text = _render_markdown(report)
        md_path = Path(args.md_output)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md_text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
        print(f"Wrote Markdown report to {md_path}")

        if args.json_output:
            jp = Path(args.json_output)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"Wrote JSON report to {jp}")
    finally:
        engine.dispose()
        if temp_ctx is not None and not args.keep_fixtures:
            temp_ctx.cleanup()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
