"""SC-07 HUD latency baseline benchmark tool (P3a-WP4).

Seeds N tasks/runs/agents/assessments and an event history (e.g. 10k, 100k, 1M events)
in PostgreSQL, starts a real in-process Uvicorn/FastAPI server over loopback, and
measures p50/p95/p99 latencies for:
- GET /v1/hud/snapshot
- SSE event delivery latency (commit -> client receive)
- SSE reconnect replay time for 1,000 missed events (Last-Event-ID)
- Live revocation propagation (POST /v1/hud/grants/revoke -> row.removed SSE frame)
- GET /v1/agents and GET /v1/agents/{agent_id}
- GET /v1/ppm/assessments/{assessment_id}, /diff, and /projects/{project_id}/assessments
- GET /v1/hud/runs/{run_id}/inspector

Records machine specs, PostgreSQL version & settings, DB/table sizes, and run duration.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import datetime as dt
import hashlib
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import sqlalchemy as sa
import uvicorn
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from eap.agents.service import register_agent_and_binding, validate_agent_manifest
from eap.api.app import create_app
from eap.api.dependencies import get_db
from eap.config import Settings, get_settings
from eap.contracts.digest import sha256_digest
from eap.db.assessment_tables import ppm_evidence
from eap.db.snapshot_tables import collector_snapshots, project_snapshots
from eap.db.tables import project_scopes, sources
from eap.events.projection import PROJECTION_NAME_HUD_RUNS
from eap.policy.grants import grant_project_access
from eap.ppm.assessments import approve_baseline, create_assessment

ROOT = Path(__file__).resolve().parent.parent.parent

OWNER_TOKEN = "bench-hud-owner-token"
VIEWER_TOKEN = "bench-hud-viewer-token"
OWNER_PRINCIPAL = "owner-bench"
VIEWER_PRINCIPAL = "viewer-bench"


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 3)
    idx = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    val = sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac
    return round(val, 3)


def _summarize_ms(samples_ms: list[float]) -> dict[str, Any]:
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "p50_ms": _percentile(ordered, 50.0),
        "p95_ms": _percentile(ordered, 95.0),
        "p99_ms": _percentile(ordered, 99.0),
        "min_ms": round(ordered[0], 3) if ordered else 0.0,
        "max_ms": round(ordered[-1], 3) if ordered else 0.0,
        "mean_ms": round(statistics.fmean(ordered), 3) if ordered else 0.0,
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
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return float(round(float(stat.ullTotalPhys) / (1024**3), 2))
    else:
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return float(round(float(int(line.split()[1])) / (1024**2), 2))
        except (OSError, ValueError):
            return 0.0
    return 0.0


def _resolve_db_url(cli_url: str | None) -> str:
    if cli_url:
        return cli_url
    env_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    settings = get_settings()
    if settings.test_database_url is not None:
        return settings.test_database_url.get_secret_value()
    return "postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"


def _ensure_db_migrated(db_url: str, *, reset_db: bool) -> None:
    url = sa.engine.make_url(db_url)
    target_db = url.database
    if target_db and target_db != "postgres":
        maint_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            with maint_engine.connect() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": target_db},
                ).scalar()
                if not exists:
                    quoted = maint_engine.dialect.identifier_preparer.quote(target_db)
                    conn.execute(text(f"CREATE DATABASE {quoted}"))
        finally:
            maint_engine.dispose()

    engine = create_engine(db_url, pool_pre_ping=True)
    try:
        if reset_db:
            with engine.begin() as conn:
                conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    finally:
        engine.dispose()

    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(alembic_cfg, "head")


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
    ]
    with engine.connect() as conn:
        pg_version = str(conn.execute(text("SHOW server_version")).scalar_one())
        pg_full_version = str(conn.execute(text("SELECT version()")).scalar_one())
        pg_settings: dict[str, str] = {}
        for key in settings_keys:
            pg_settings[key] = str(conn.execute(text(f"SHOW {key}")).scalar_one())
    return {
        "server_version": pg_version,
        "full_version": pg_full_version,
        "settings": pg_settings,
    }


def _collect_db_sizes(engine: sa.Engine) -> dict[str, Any]:
    tables = [
        "event_log",
        "tasks",
        "runs",
        "projection_checkpoints",
        "stream_heads",
        "ppm_assessments",
        "agents",
    ]
    with engine.connect() as conn:
        db_bytes = int(conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one())
        table_bytes: dict[str, int] = {}
        for tbl in tables:
            val = conn.execute(
                text("SELECT pg_total_relation_size(:tbl)"),
                {"tbl": tbl},
            ).scalar_one()
            table_bytes[tbl] = int(val)
    return {
        "database_bytes": db_bytes,
        "database_mb": round(db_bytes / (1024 * 1024), 2),
        "table_bytes": table_bytes,
    }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _InProcessServer:
    def __init__(self, app: Any, host: str, port: int) -> None:
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Uvicorn server failed to start within 15s")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10.0)


@dataclass
class SeededContext:
    tenant_id: str
    event_count: int
    run_count: int
    agent_ids: list[str]
    sample_run_id: str
    sample_project_id: str
    sample_assessment_id: str
    prev_assessment_id: str
    seed_duration_s: float
    db_sizes_after_seed: dict[str, Any]


def _seed_scale(
    engine: sa.Engine,
    session_factory: sessionmaker[Session],
    *,
    event_count: int,
    run_count: int,
    agent_count: int,
    assessment_count: int,
) -> SeededContext:
    t0 = time.perf_counter()
    tenant_id = f"tenant-bench-{event_count}-{uuid.uuid4().hex[:6]}"
    now = dt.datetime.now(dt.UTC)
    events_per_run = max(1, event_count // run_count)
    actual_events = events_per_run * run_count
    project_count = max(1, min(10, assessment_count // 2))

    # 1. Seed agents + bindings
    fixtures_dir = ROOT / "src" / "eap" / "agents" / "fixtures"
    mock_manifest = json.loads((fixtures_dir / "mock_agent.json").read_text(encoding="utf-8"))
    mock_binding = json.loads((fixtures_dir / "mock_binding.json").read_text(encoding="utf-8"))
    agent_ids: list[str] = []
    with session_factory() as db:
        for i in range(agent_count):
            aid = f"agent-bench-{i}"
            agent_ids.append(aid)
            manifest = {**mock_manifest, "agent_id": aid, "name": f"Bench Agent {i}"}
            manifest_sha = sha256_digest(validate_agent_manifest(manifest).model_dump(mode="json"))
            binding = {
                **mock_binding,
                "binding_id": f"bind-bench-{i}",
                "agent_id": aid,
                "release_sha256": manifest_sha,
            }
            register_agent_and_binding(
                db,
                tenant_id=tenant_id,
                manifest=manifest,
                binding=binding,
            )
        db.commit()

    # 2. Bulk seed tasks, runs, stream_heads, projection_checkpoints, and event_log
    tid_prefix = f"{tenant_id}-task-"
    rid_prefix = f"{tenant_id}-run-"
    rpk_prefix = f"{tenant_id}-rpk-"
    eid_prefix = f"ev-{tenant_id}-"
    proj_prefix = f"proj-bench-{event_count}-"

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tasks (
                    id, tenant_id, client_idempotency_key, task_intent_key,
                    task_type, agent_id, agent_version, project_id, state, deadline
                )
                SELECT
                    :tid_prefix || g,
                    :tenant_id,
                    :idem_prefix || g,
                    :intent_prefix || g,
                    'code.patch',
                    :agent_id,
                    '1.0.0',
                    :proj_prefix || (((g - 1) % :proj_count) + 1),
                    'active',
                    :deadline
                FROM generate_series(1, :n_runs) AS g
                """
            ),
            {
                "tid_prefix": tid_prefix,
                "tenant_id": tenant_id,
                "idem_prefix": f"idem-{tenant_id}-",
                "intent_prefix": f"intent-{tenant_id}-",
                "agent_id": agent_ids[0],
                "proj_prefix": proj_prefix,
                "proj_count": project_count,
                "deadline": now + dt.timedelta(days=1),
                "n_runs": run_count,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO runs (
                    id, tenant_id, task_id, run_id, attempt, binding_id,
                    project_id, state, remote_outcome, lease_fencing_token
                )
                SELECT
                    :rpk_prefix || g,
                    :tenant_id,
                    :tid_prefix || g,
                    :rid_prefix || g,
                    1,
                    'bind-bench-0',
                    :proj_prefix || (((g - 1) % :proj_count) + 1),
                    'running',
                    'known',
                    1
                FROM generate_series(1, :n_runs) AS g
                """
            ),
            {
                "rpk_prefix": rpk_prefix,
                "tenant_id": tenant_id,
                "tid_prefix": tid_prefix,
                "rid_prefix": rid_prefix,
                "proj_prefix": proj_prefix,
                "proj_count": project_count,
                "n_runs": run_count,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO stream_heads (tenant_id, stream_id, last_offset, producer_state)
                SELECT
                    :tenant_id,
                    'run:' || :rid_prefix || g,
                    :events_per_run,
                    '{}'::jsonb
                FROM generate_series(1, :n_runs) AS g
                """
            ),
            {
                "tenant_id": tenant_id,
                "rid_prefix": rid_prefix,
                "events_per_run": events_per_run,
                "n_runs": run_count,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO projection_checkpoints (
                    tenant_id, stream_id, projection_name, projection_version,
                    last_ingest_offset, state, state_hash
                )
                SELECT
                    CAST(:tenant_id AS text),
                    'run:' || CAST(:rid_prefix AS text) || g,
                    CAST(:proj_name AS text),
                    1,
                    CAST(:events_per_run AS integer),
                    jsonb_build_object(
                        'row',
                        jsonb_build_object(
                            'task_id', CAST(:tid_prefix AS text) || g,
                            'task_state', 'active',
                            'run_id', CAST(:rid_prefix AS text) || g,
                            'attempt', 1,
                            'run_state', 'running',
                            'remote_outcome', 'known',
                            'connectivity', 'healthy',
                            'last_observed_at', CAST(:obs_ts AS text),
                            'observation_source', 'run_service',
                            'progress', jsonb_build_object(
                                'kind', 'units',
                                'completed_units', CAST(:events_per_run AS integer),
                                'total_units', CAST(:events_per_run AS integer),
                                'unit', 'steps',
                                'stage', 'steady'
                            ),
                            'project', NULL,
                            'cost', jsonb_build_object(
                                'status', 'actual',
                                'amount_usd', 0.12,
                                'as_of', CAST(:obs_ts AS text),
                                'scope', 'run'
                            ),
                            'pending_command', NULL
                        )
                    ),
                    md5(CAST(:rid_prefix AS text) || g)
                FROM generate_series(1, CAST(:n_runs AS integer)) AS g
                """
            ),
            {
                "tenant_id": tenant_id,
                "proj_name": PROJECTION_NAME_HUD_RUNS,
                "rid_prefix": rid_prefix,
                "tid_prefix": tid_prefix,
                "proj_prefix": proj_prefix,
                "proj_count": project_count,
                "events_per_run": events_per_run,
                "obs_ts": now.isoformat(),
                "n_runs": run_count,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO event_log (
                    tenant_id, stream_id, ingest_offset, event_id,
                    producer_id, event_type, payload, occurred_at
                )
                SELECT
                    CAST(:tenant_id AS text),
                    'run:' || CAST(:rid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                    ((seq - 1) % CAST(:events_per_run AS integer)) + 1,
                    CAST(:eid_prefix AS text) || seq,
                    'run_service',
                    'run.progress',
                    jsonb_build_object(
                        'task_id', CAST(:tid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                        'run_id', CAST(:rid_prefix AS text) || (((seq - 1) / CAST(:events_per_run AS integer)) + 1),
                        'completed_units', ((seq - 1) % CAST(:events_per_run AS integer)) + 1,
                        'total_units', CAST(:events_per_run AS integer),
                        'unit', 'steps',
                        'stage', 'bench'
                    ),
                    :occurred_at
                FROM generate_series(1, CAST(:total_events AS integer)) AS seq
                """
            ),
            {
                "tenant_id": tenant_id,
                "rid_prefix": rid_prefix,
                "tid_prefix": tid_prefix,
                "eid_prefix": eid_prefix,
                "events_per_run": events_per_run,
                "occurred_at": now,
                "total_events": actual_events,
            },
        )

    # 3. Seed PPM projects, baselines, snapshots, evidence, and assessments
    sample_project_id = f"{proj_prefix}1"
    sample_assessment_id = ""
    prev_assessment_id = ""
    with session_factory() as db:
        src_id = f"src-{tenant_id}"
        db.execute(
            sources.insert().values(
                source_id=src_id,
                tenant_id=tenant_id,
                canonical_root=f"/tmp/{tenant_id}/repo",
                repo_identity=f"git:repo-{tenant_id}",
                created_at=now,
                updated_at=now,
            )
        )
        for p_idx in range(1, project_count + 1):
            pid = f"{proj_prefix}{p_idx}"
            scope_id = f"scope-{tenant_id}-{p_idx}"
            db.execute(
                project_scopes.insert().values(
                    id=scope_id,
                    tenant_id=tenant_id,
                    source_id=src_id,
                    project_id=pid,
                    metadata_root=f"projects/p{p_idx}/.ppm",
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            approve_baseline(
                db,
                tenant_id=tenant_id,
                project_id=pid,
                baseline_revision=1,
                approved_by=OWNER_PRINCIPAL,
                source="bench",
                criteria=[
                    {
                        "criterion_id": "AC-001",
                        "deliverable_id": "DEL-1",
                        "owner_project_id": pid,
                        "title": "Primary check",
                        "weight": 2.0,
                        "required": True,
                        "verification_refs": [{"kind": "test_id", "ref": "test_ac001"}],
                    },
                    {
                        "criterion_id": "AC-002",
                        "deliverable_id": "DEL-1",
                        "owner_project_id": pid,
                        "title": "Secondary check",
                        "weight": 1.0,
                        "required": True,
                        "verification_refs": [{"kind": "test_id", "ref": "test_ac002"}],
                    },
                ],
            )
            csnap_1 = f"csnap-{tenant_id}-{p_idx}-1"
            psnap_1 = f"psnap-{tenant_id}-{p_idx}-1"
            db.execute(
                collector_snapshots.insert().values(
                    id=csnap_1,
                    tenant_id=tenant_id,
                    source_id=src_id,
                    status="captured",
                    mixed_snapshot=False,
                    partial_reasons=[],
                    git_commit="rev-r1",
                    git_dirty=False,
                    tree_hash=f"tree-{p_idx}-1",
                    manifest={},
                    created_at=now - dt.timedelta(hours=1),
                )
            )
            db.execute(
                project_snapshots.insert().values(
                    id=psnap_1,
                    snapshot_id=csnap_1,
                    tenant_id=tenant_id,
                    source_id=src_id,
                    project_id=pid,
                    snapshot_seq=1,
                    scope_hash=f"sh-{p_idx}-1",
                    metadata_root=f"projects/p{p_idx}/.ppm",
                    status="captured",
                    mixed_snapshot=False,
                    partial_reasons=[],
                    claims=[],
                    created_at=now - dt.timedelta(hours=1),
                )
            )
            db.execute(
                ppm_evidence.insert().values(
                    id=f"evpk-{tenant_id}-{p_idx}-1",
                    evidence_id=f"ev-{tenant_id}-{p_idx}-1",
                    tenant_id=tenant_id,
                    project_id=pid,
                    revision="rev-r1",
                    trust_class="verifier_observed",
                    producer="simulated-verifier",
                    test_id="test_ac001",
                    covered_test_ids=["test_ac001"],
                    outcome="passed",
                    output_hash=f"oh-{p_idx}-1",
                    simulated=True,
                    collected_at=now - dt.timedelta(hours=1),
                )
            )
            a1 = create_assessment(
                db,
                tenant_id=tenant_id,
                project_id=pid,
                trigger="snapshot",
                now=now - dt.timedelta(hours=1),
            )
            db.execute(
                ppm_evidence.insert().values(
                    id=f"evpk-{tenant_id}-{p_idx}-2",
                    evidence_id=f"ev-{tenant_id}-{p_idx}-2",
                    tenant_id=tenant_id,
                    project_id=pid,
                    revision="rev-r1",
                    trust_class="verifier_observed",
                    producer="simulated-verifier",
                    test_id="test_ac002",
                    covered_test_ids=["test_ac002"],
                    outcome="passed",
                    output_hash=f"oh-{p_idx}-2",
                    simulated=True,
                    collected_at=now - dt.timedelta(minutes=30),
                )
            )
            a2 = create_assessment(
                db,
                tenant_id=tenant_id,
                project_id=pid,
                trigger="manual",
                now=now - dt.timedelta(minutes=30),
            )
            if p_idx == 1:
                prev_assessment_id = str(a1.id)
                sample_assessment_id = str(a2.id)
        db.commit()

    seed_duration_s = round(time.perf_counter() - t0, 3)
    db_sizes_after_seed = _collect_db_sizes(engine)
    return SeededContext(
        tenant_id=tenant_id,
        event_count=actual_events,
        run_count=run_count,
        agent_ids=agent_ids,
        sample_run_id=f"{rid_prefix}1",
        sample_project_id=sample_project_id,
        sample_assessment_id=sample_assessment_id,
        prev_assessment_id=prev_assessment_id,
        seed_duration_s=seed_duration_s,
        db_sizes_after_seed=db_sizes_after_seed,
    )


def _measure_point_endpoints(
    base_url: str,
    headers: dict[str, str],
    ctx: SeededContext,
    *,
    iterations: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    endpoints: list[tuple[str, str]] = [
        ("snapshot", "/v1/hud/snapshot"),
        ("agents_list", "/v1/agents"),
        ("agent_detail", f"/v1/agents/{ctx.agent_ids[0]}"),
        ("assessment_detail", f"/v1/ppm/assessments/{ctx.sample_assessment_id}"),
        (
            "assessment_diff",
            f"/v1/ppm/assessments/{ctx.sample_assessment_id}/diff?against={ctx.prev_assessment_id}",
        ),
        ("project_assessments", f"/v1/ppm/projects/{ctx.sample_project_id}/assessments"),
        ("run_inspector", f"/v1/hud/runs/{ctx.sample_run_id}/inspector"),
    ]
    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        for name, path in endpoints:
            # Warmup
            for _ in range(3):
                r = client.get(path)
                if r.status_code != 200:
                    raise RuntimeError(f"Warmup failed for {path}: {r.status_code} {r.text}")
            samples: list[float] = []
            payload_bytes = 0
            for _ in range(iterations):
                t0 = time.perf_counter()
                r = client.get(path)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                if r.status_code != 200:
                    raise RuntimeError(f"Request failed for {path}: {r.status_code} {r.text}")
                payload_bytes = len(r.content)
                samples.append(elapsed_ms)
            summary = _summarize_ms(samples)
            summary["response_bytes"] = payload_bytes
            results[name] = summary
    return results


def _insert_run_events(
    engine: sa.Engine,
    *,
    tenant_id: str,
    run_id: str,
    task_id: str,
    start_offset: int,
    count: int,
) -> None:
    stream_id = f"run:{run_id}"
    now = dt.datetime.now(dt.UTC)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE stream_heads
                SET last_offset = :end_offset, updated_at = :now
                WHERE tenant_id = :tenant_id AND stream_id = :stream_id
                """
            ),
            {
                "end_offset": start_offset + count - 1,
                "now": now,
                "tenant_id": tenant_id,
                "stream_id": stream_id,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO event_log (
                    tenant_id, stream_id, ingest_offset, event_id,
                    producer_id, event_type, payload, occurred_at
                )
                SELECT
                    CAST(:tenant_id AS text),
                    CAST(:stream_id AS text),
                    CAST(:start_offset AS integer) + seq - 1,
                    CAST(:eid_prefix AS text) || (CAST(:start_offset AS integer) + seq - 1),
                    'run_service',
                    'run.progress',
                    jsonb_build_object(
                        'task_id', CAST(:task_id AS text),
                        'run_id', CAST(:run_id AS text),
                        'completed_units', CAST(:start_offset AS integer) + seq - 1,
                        'total_units', CAST(:start_offset AS integer) + CAST(:cnt AS integer),
                        'unit', 'steps',
                        'stage', 'bench-live'
                    ),
                    :now
                FROM generate_series(1, CAST(:cnt AS integer)) AS seq
                """
            ),
            {
                "tenant_id": tenant_id,
                "stream_id": stream_id,
                "start_offset": start_offset,
                "eid_prefix": f"ev-live-{uuid.uuid4().hex[:6]}-",
                "task_id": task_id,
                "run_id": run_id,
                "cnt": count,
                "now": now,
            },
        )


async def _measure_sse_delivery_async(
    base_url: str,
    headers: dict[str, str],
    engine: sa.Engine,
    ctx: SeededContext,
    *,
    samples: int,
) -> dict[str, Any]:
    run_id = ctx.sample_run_id
    task_id = f"{ctx.tenant_id}-task-1"
    with engine.connect() as conn:
        current_offset = int(
            conn.execute(
                text(
                    "SELECT last_offset FROM stream_heads WHERE tenant_id = :tid AND stream_id = :sid"
                ),
                {"tid": ctx.tenant_id, "sid": f"run:{run_id}"},
            ).scalar_one()
        )

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0) as client:
        snap = await client.get("/v1/hud/snapshot")
        snap.raise_for_status()
        cursor = snap.json()["snapshot_cursor"]

        latencies_ms: list[float] = []
        async with client.stream(
            "GET",
            "/v1/hud/events",
            params={"cursor": cursor, "max_events": samples},
        ) as resp:
            resp.raise_for_status()
            lines_iter = resp.aiter_lines()
            for i in range(samples):
                next_off = current_offset + 1 + i
                t_commit = time.perf_counter()
                await asyncio.to_thread(
                    _insert_run_events,
                    engine,
                    tenant_id=ctx.tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    start_offset=next_off,
                    count=1,
                )
                # Read SSE lines until we see the blank line terminating an event frame (skip comments)
                current_event: str | None = None
                async for line in lines_iter:
                    if line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                    elif line == "" and current_event is not None:
                        t_recv = time.perf_counter()
                        latencies_ms.append((t_recv - t_commit) * 1000.0)
                        break
    return _summarize_ms(latencies_ms)


async def _measure_reconnect_replay_async(
    base_url: str,
    headers: dict[str, str],
    engine: sa.Engine,
    ctx: SeededContext,
    *,
    missed_events: int,
    samples: int,
) -> dict[str, Any]:
    run_id = ctx.sample_run_id
    task_id = f"{ctx.tenant_id}-task-1"
    durations_ms: list[float] = []

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=60.0) as client:
        for _ in range(samples):
            snap = await client.get("/v1/hud/snapshot")
            snap.raise_for_status()
            cursor = snap.json()["snapshot_cursor"]

            with engine.connect() as conn:
                current_offset = int(
                    conn.execute(
                        text(
                            "SELECT last_offset FROM stream_heads WHERE tenant_id = :tid AND stream_id = :sid"
                        ),
                        {"tid": ctx.tenant_id, "sid": f"run:{run_id}"},
                    ).scalar_one()
                )
            await asyncio.to_thread(
                _insert_run_events,
                engine,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                task_id=task_id,
                start_offset=current_offset + 1,
                count=missed_events,
            )

            t0 = time.perf_counter()
            received = 0
            async with client.stream(
                "GET",
                "/v1/hud/events",
                params={"max_events": missed_events, "max_buffer_size": 4096},
                headers={"Last-Event-ID": cursor},
            ) as resp:
                resp.raise_for_status()
                current_event: str | None = None
                last_data = ""
                async for line in resp.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                    elif line.startswith("data: "):
                        last_data = line[6:].strip()
                    elif line == "" and current_event is not None:
                        if current_event == "resync_required":
                            raise RuntimeError(f"Unexpected resync_required during replay after {received} events: {last_data}")
                        received += 1
                        current_event = None
                        if received >= missed_events:
                            break
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if received != missed_events:
                raise RuntimeError(f"Expected {missed_events} replayed events, got {received} (last_data={last_data})")
            durations_ms.append(elapsed_ms)

    return _summarize_ms(durations_ms)


async def _measure_revocation_async(
    base_url: str,
    owner_headers: dict[str, str],
    viewer_headers: dict[str, str],
    session_factory: sessionmaker[Session],
    ctx: SeededContext,
    *,
    samples: int,
) -> dict[str, Any]:
    latencies_ms: list[float] = []
    target_project = ctx.sample_project_id

    async with (
        httpx.AsyncClient(base_url=base_url, headers=owner_headers, timeout=30.0) as owner_client,
        httpx.AsyncClient(base_url=base_url, headers=viewer_headers, timeout=30.0) as viewer_client,
    ):
        for i in range(samples):
            with session_factory() as db:
                grant_project_access(
                    db.connection(),
                    tenant_id=ctx.tenant_id,
                    principal_id=VIEWER_PRINCIPAL,
                    project_id=target_project,
                    actor_id=OWNER_PRINCIPAL,
                    reason=f"bench-restore-{i}",
                )
                db.commit()

            snap = await viewer_client.get("/v1/hud/snapshot")
            snap.raise_for_status()
            cursor = snap.json()["snapshot_cursor"]

            async with viewer_client.stream(
                "GET",
                "/v1/hud/events",
                params={"cursor": cursor},
            ) as resp:
                resp.raise_for_status()
                t_revoke_start = time.perf_counter()
                revoke_resp = await owner_client.post(
                    "/v1/hud/grants/revoke",
                    json={
                        "principal_id": VIEWER_PRINCIPAL,
                        "project_id": target_project,
                        "reason": f"bench-revoke-{i}",
                    },
                )
                revoke_resp.raise_for_status()

                current_event: str | None = None
                async for line in resp.aiter_lines():
                    if line.startswith(":"):
                        continue
                    if line.startswith("event: "):
                        current_event = line[7:].strip()
                    elif line == "" and current_event == "row.removed":
                        t_recv = time.perf_counter()
                        latencies_ms.append((t_recv - t_revoke_start) * 1000.0)
                        break

    return _summarize_ms(latencies_ms)


def _render_markdown(report: dict[str, Any], command_str: str) -> str:
    env = report["environment"]
    pg = report["postgresql"]
    lines: list[str] = [
        "# SC-07 HUD Latency Baseline Measurement Report (P3a-WP4)",
        "",
        "- **Work Package**: P3a-WP4 HUD latency baseline (SC-07)",
        f"- **Commit**: `{env['git_commit']}`",
        f"- **Execution Timestamp**: `{env['timestamp_utc']}`",
        f"- **Total Benchmark Duration**: `{report['total_duration_s']} s`",
        f"- **Exact Command**: `{command_str}`",
        "",
        "---",
        "",
        "## 1. Environment and PostgreSQL Configuration",
        "",
        "| Parameter | Value |",
        "|---|---|",
        f"| Operating System | {env['os']} |",
        f"| CPU Model | {env['cpu_model']} |",
        f"| Logical CPU Cores | {env['cpu_cores']} |",
        f"| Physical Memory (RAM) | {env['ram_gb']} GB |",
        f"| Python Version | {env['python_version']} |",
        f"| PostgreSQL Version | {pg['server_version']} ({pg['full_version'].split(',')[0]}) |",
    ]
    for k, v in pg["settings"].items():
        lines.append(f"| `{k}` | `{v}` |")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Seeded Scales and Database Footprint",
            "",
            "| Scale (events) | Runs | Agents | Projects | Seed Duration | DB Size (MB) | `event_log` (MB) | `tasks` (MB) | `runs` (MB) | `projection_checkpoints` (MB) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for sc in report["scales"]:
        tb = sc["db_sizes_after_seed"]["table_bytes"]
        lines.append(
            f"| {sc['event_count']:,} | {sc['run_count']} | {sc['agent_count']} | {sc['project_count']} | "
            f"{sc['seed_duration_s']} s | {sc['db_sizes_after_seed']['database_mb']} | "
            f"{round(tb['event_log'] / (1024 * 1024), 2)} | "
            f"{round(tb['tasks'] / (1024 * 1024), 3)} | "
            f"{round(tb['runs'] / (1024 * 1024), 3)} | "
            f"{round(tb['projection_checkpoints'] / (1024 * 1024), 3)} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Measured Latencies by Scale (ms)",
            "",
            "All measurements were executed against a live local Uvicorn/FastAPI server over loopback (`127.0.0.1`) backed by PostgreSQL with `DEFAULT_SSE_POLL_INTERVAL_MS=25`.",
            "",
        ]
    )

    metric_labels = [
        ("snapshot", "GET /v1/hud/snapshot"),
        ("sse_delivery", "SSE event delivery (commit -> client receive)"),
        ("reconnect_replay_1k", "SSE reconnect replay (1,000 missed events)"),
        ("revocation_propagation", "Live revocation propagation (revoke -> row.removed)"),
        ("agents_list", "GET /v1/agents"),
        ("agent_detail", "GET /v1/agents/{agent_id}"),
        ("assessment_detail", "GET /v1/ppm/assessments/{assessment_id}"),
        ("assessment_diff", "GET /v1/ppm/assessments/{id}/diff"),
        ("project_assessments", "GET /v1/ppm/projects/{id}/assessments"),
        ("run_inspector", "GET /v1/hud/runs/{run_id}/inspector"),
    ]

    for sc in report["scales"]:
        lines.extend(
            [
                f"### Scale: {sc['event_count']:,} events ({sc['run_count']} runs, {sc['agent_count']} agents)",
                "",
                "| Endpoint / Flow | Samples (n) | p50 (ms) | p95 (ms) | p99 (ms) | min (ms) | max (ms) | mean (ms) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        m = sc["metrics"]
        for key, label in metric_labels:
            s = m[key]
            lines.append(
                f"| {label} | {s['n']} | {s['p50_ms']:.2f} | {s['p95_ms']:.2f} | {s['p99_ms']:.2f} | "
                f"{s['min_ms']:.2f} | {s['max_ms']:.2f} | {s['mean_ms']:.2f} |"
            )
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## 4. Comparison with Existing Documented Targets & Candidate DG-5 Proposals",
            "",
            "Per `docs/IMPLEMENTATION_PLAN.md` (`ARCH-30`, `SC-07`), baseline measurements do **not** invent acceptance thresholds; formal acceptance thresholds belong to **DG-5** (before `P5-WP4`).",
            "",
            "### Existing Documented Targets (Non-Binding Proposals in Spec)",
            "- `docs/03_HUD_SPEC.md` & `docs/07_DELIVERY_AND_ACCEPTANCE.md`: `p95 event-to-HUD < 2,000 ms` (2 s) under pilot profile (`<= 20` active runs, `<= 10` events/s/run, `<= 10` concurrent SSE clients).",
            "- `docs/03_HUD_SPEC.md`: `p95 revocation propagation < 2,000 ms` (2 s) (`C30` test harness uses `< 500 ms`).",
            "- Point-read endpoints (`/v1/hud/snapshot`, `/v1/agents`, `/v1/ppm/assessments/*`, `/v1/hud/runs/*/inspector`) have **no** thresholds defined in existing docs.",
            "",
            "### Candidate Thresholds for DG-5 (Proposals Only — Not Binding)",
            "| Metric | Proposed DG-5 Candidate Threshold | Basis |",
            "|---|---|---|",
            "| `GET /v1/hud/snapshot` p95 | `< 100 ms` | Bounded checkpoint+tail reads (`ISS-040`) stay O(active runs), independent of `event_log` size |",
            "| SSE event delivery p95 | `< 250 ms` (well below 2,000 ms spec target) | `DEFAULT_SSE_POLL_INTERVAL_MS=25` + single-row indexed poll |",
            "| SSE 1k-event reconnect replay p95 | `< 1,000 ms` | Sequential cursor-paged read over `ix_event_log_tenant_id_pk` |",
            "| Live revocation propagation p95 | `< 500 ms` (well below 2,000 ms spec target) | Matches `C30` test assertion and `policy.changed` epoch check |",
            "| `GET /v1/agents` & `/v1/agents/{id}` p95 | `< 100 ms` | Indexed per-tenant agent/binding + run aggregation |",
            "| Assessment detail / diff / list p95 | `< 100 ms` | Primary-key / indexed `(tenant_id, project_id)` lookups |",
            "| `GET /v1/hud/runs/{id}/inspector` p95 | `< 100 ms` | Indexed single-run attempts/commands/artifacts/events |",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="SC-07 HUD latency baseline benchmark")
    parser.add_argument("--db-url", default=None, help="PostgreSQL connection URL")
    parser.add_argument(
        "--scales",
        default="10000,100000,1000000",
        help="Comma-separated event counts to benchmark (default: 10000,100000,1000000)",
    )
    parser.add_argument("--runs", type=int, default=100, help="Runs per scale (default: 100)")
    parser.add_argument("--agents", type=int, default=10, help="Agents per scale (default: 10)")
    parser.add_argument("--assessments", type=int, default=20, help="Assessments per scale (default: 20)")
    parser.add_argument("--iterations", type=int, default=50, help="Point endpoint iterations (default: 50)")
    parser.add_argument("--sse-samples", type=int, default=25, help="SSE delivery samples (default: 25)")
    parser.add_argument("--revocation-samples", type=int, default=20, help="Revocation samples (default: 20)")
    parser.add_argument("--replay-samples", type=int, default=5, help="1k replay samples (default: 5)")
    parser.add_argument("--missed-events", type=int, default=1000, help="Missed events for replay (default: 1000)")
    parser.add_argument("--reset-db", action="store_true", help="Drop/recreate public schema before running")
    parser.add_argument("--json-output", default=None, help="Optional path to write JSON report")
    parser.add_argument("--md-output", default=None, help="Optional path to write Markdown report")
    args = parser.parse_args()

    scales = [int(x.strip()) for x in args.scales.split(",") if x.strip()]
    db_url = _resolve_db_url(args.db_url)
    _ensure_db_migrated(db_url, reset_db=args.reset_db)

    engine = create_engine(db_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    pg_info = _collect_pg_info(engine)

    port = _find_free_port()
    settings = Settings.model_validate(
        {
            "eap_env": "test",
            "eap_bind": f"127.0.0.1:{port}",
            "eap_owner_tenant_id": "tenant-placeholder",
            "eap_owner_principal_id": OWNER_PRINCIPAL,
            "eap_owner_token_hash": SecretStr(_token_hash(OWNER_TOKEN)),
            "hud_cursor_secret": SecretStr("bench-hud-cursor-secret-32-bytes-ok"),
        }
    )

    # Dynamic multi-tenant principal override per scale
    active_tenant = {"id": "tenant-placeholder"}
    app = create_app(settings)

    def _override_db() -> Any:
        with session_factory() as session:
            yield session

    from fastapi import Header, HTTPException, status

    from eap.api.dependencies import Principal, get_principal

    def _override_principal(authorization: str | None = Header(default=None)) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
        raw = authorization.removeprefix("Bearer ").strip()
        if raw == OWNER_TOKEN:
            return Principal(tenant_id=active_tenant["id"], principal_id=OWNER_PRINCIPAL, roles=["owner", "admin"])
        if raw == VIEWER_TOKEN:
            return Principal(tenant_id=active_tenant["id"], principal_id=VIEWER_PRINCIPAL, roles=["viewer"])
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_principal] = _override_principal

    server = _InProcessServer(app, "127.0.0.1", port)
    server.start()
    base_url = f"http://127.0.0.1:{port}"
    owner_headers = {"Authorization": f"Bearer {OWNER_TOKEN}"}
    viewer_headers = {"Authorization": f"Bearer {VIEWER_TOKEN}"}

    bench_start = time.perf_counter()
    scale_reports: list[dict[str, Any]] = []
    try:
        for ev_count in scales:
            print(f"[hud_baseline] Seeding scale={ev_count:,} events ...", flush=True)
            ctx = _seed_scale(
                engine,
                session_factory,
                event_count=ev_count,
                run_count=args.runs,
                agent_count=args.agents,
                assessment_count=args.assessments,
            )
            active_tenant["id"] = ctx.tenant_id
            print(
                f"[hud_baseline] Seeded {ctx.event_count:,} events in {ctx.seed_duration_s}s "
                f"(DB={ctx.db_sizes_after_seed['database_mb']} MB). Measuring...",
                flush=True,
            )

            metrics = _measure_point_endpoints(
                base_url,
                owner_headers,
                ctx,
                iterations=args.iterations,
            )
            metrics["sse_delivery"] = asyncio.run(
                _measure_sse_delivery_async(
                    base_url,
                    owner_headers,
                    engine,
                    ctx,
                    samples=args.sse_samples,
                )
            )
            metrics["reconnect_replay_1k"] = asyncio.run(
                _measure_reconnect_replay_async(
                    base_url,
                    owner_headers,
                    engine,
                    ctx,
                    missed_events=args.missed_events,
                    samples=args.replay_samples,
                )
            )
            metrics["revocation_propagation"] = asyncio.run(
                _measure_revocation_async(
                    base_url,
                    owner_headers,
                    viewer_headers,
                    session_factory,
                    ctx,
                    samples=args.revocation_samples,
                )
            )
            scale_reports.append(
                {
                    "event_count": ctx.event_count,
                    "run_count": ctx.run_count,
                    "agent_count": args.agents,
                    "project_count": max(1, min(10, args.assessments // 2)),
                    "seed_duration_s": ctx.seed_duration_s,
                    "db_sizes_after_seed": ctx.db_sizes_after_seed,
                    "metrics": metrics,
                }
            )
            print(
                f"[hud_baseline] Scale={ctx.event_count:,}: "
                f"snapshot p50={metrics['snapshot']['p50_ms']}ms p95={metrics['snapshot']['p95_ms']}ms | "
                f"sse p95={metrics['sse_delivery']['p95_ms']}ms | "
                f"replay1k p95={metrics['reconnect_replay_1k']['p95_ms']}ms | "
                f"revoke p95={metrics['revocation_propagation']['p95_ms']}ms",
                flush=True,
            )
    finally:
        server.stop()
        engine.dispose()

    total_duration_s = round(time.perf_counter() - bench_start, 3)
    report: dict[str, Any] = {
        "environment": {
            "os": f"{platform.system()}-{platform.release()}-{platform.version()}",
            "cpu_model": platform.processor() or platform.machine(),
            "cpu_cores": os.cpu_count() or 0,
            "ram_gb": _get_total_ram_gb(),
            "python_version": platform.python_version(),
            "git_commit": _get_git_commit(),
            "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(),
        },
        "postgresql": pg_info,
        "total_duration_s": total_duration_s,
        "scales": scale_reports,
    }

    if args.json_output:
        out_json = Path(args.json_output)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if args.md_output:
        cmd_str = "uv run python tools/bench/hud_baseline.py " + " ".join(sys.argv[1:])
        md_text = _render_markdown(report, cmd_str)
        out_md = Path(args.md_output)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(md_text, encoding="utf-8")


if __name__ == "__main__":
    main()
