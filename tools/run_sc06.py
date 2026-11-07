"""SC-06 Verification Script: Alembic migration up/down/up cycles.

Executes:
1. Migration upgrade head -> downgrade base -> upgrade head on an empty database.
2. Seeds a small fixture dataset across core tables.
3. Migration downgrade base -> upgrade head on the seeded database.
Displays 'alembic current' after each transition.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine.url import make_url

from eap.config import get_settings
from eap.db import (
    action_executions,
    commands,
    event_log,
    leases,
    outbox,
    project_scopes,
    runs,
    sources,
    tasks,
)

ROOT = Path(__file__).resolve().parent.parent


def get_db_url() -> str:
    settings = get_settings()
    if settings.test_database_url is not None:
        return settings.test_database_url.get_secret_value()
    env_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    raise RuntimeError("Neither TEST_DATABASE_URL nor DATABASE_URL is configured")


def get_alembic_config(db_url: str) -> Config:
    alembic_cfg = Config(str(ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return alembic_cfg


def print_current(alembic_cfg: Config, step_label: str) -> None:
    print(f"\n--- [alembic current] {step_label} ---")
    command.current(alembic_cfg, verbose=False)


def reset_database(db_url: str) -> None:
    url = make_url(db_url)
    target_db = url.database
    if not target_db:
        raise RuntimeError("Database name missing in URL")
    maint_url = url.set(database="postgres")
    maint_engine = create_engine(maint_url, isolation_level="AUTOCOMMIT")
    with maint_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {target_db} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {target_db} OWNER {url.username or 'eap'}"))
    maint_engine.dispose()


def seed_fixture_data(db_url: str) -> None:
    engine = create_engine(db_url)
    tenant = "tenant-sc06-fixture"
    now = dt.datetime.now(dt.UTC)
    deadline = now + dt.timedelta(hours=2)

    with engine.begin() as conn:
        conn.execute(
            tasks.insert().values(
                id="task-sc06-1",
                tenant_id=tenant,
                client_idempotency_key="client-sc06-1",
                task_intent_key="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                task_type="ppm.assessment",
                agent_id="agent-sc06",
                agent_version="1.0",
                state="queued",
                deadline=deadline,
            )
        )
        conn.execute(
            runs.insert().values(
                id="run-sc06-1",
                tenant_id=tenant,
                task_id="task-sc06-1",
                run_id="run-001",
                attempt=1,
                binding_id="bind-sc06",
                state="running",
            )
        )
        conn.execute(
            sources.insert().values(
                source_id="src-sc06-1",
                tenant_id=tenant,
                canonical_root="/repos/sc06-demo",
                repo_identity="git@example.com:sc06.git",
            )
        )
        conn.execute(
            project_scopes.insert().values(
                id="scope-sc06-1",
                tenant_id=tenant,
                source_id="src-sc06-1",
                project_id="proj-sc06-1",
                metadata_root="apps/demo/.ppm",
                status="active",
            )
        )
        conn.execute(
            leases.insert().values(
                tenant_id=tenant,
                resource_type="run",
                resource_id="run-001",
                fencing_token=1,
                holder_id="worker-sc06",
                expires_at=now + dt.timedelta(seconds=60),
            )
        )
        conn.execute(
            commands.insert().values(
                id="cmd-sc06-1",
                tenant_id=tenant,
                task_id="task-sc06-1",
                run_id="run-001",
                command_type="cancel",
                status="accepted",
            )
        )
        conn.execute(
            outbox.insert().values(
                tenant_id=tenant,
                event_id="evt-outbox-1",
                stream_id="stream-sc06",
                event_type="run.started",
                payload={"run_id": "run-001"},
            )
        )
        conn.execute(
            event_log.insert().values(
                tenant_id=tenant,
                stream_id="stream-sc06",
                ingest_offset=1,
                event_id="evt-log-1",
                producer_id="relay-sc06",
                event_type="run.started",
                payload={"run_id": "run-001"},
                occurred_at=now,
            )
        )
        conn.execute(
            action_executions.insert().values(
                tenant_id=tenant,
                operation_key="56330e2b4ebffade1ad3d90131e6073fc5ec8bb149b229524b565fd829e50f08",
                payload_hash="15329b88e5b0ce51f22ce4c524791cc529fe0922a59e62dcf8362fa2cd9ff441",
                tool_id="engineering.create_pr",
                resource_ref="repo:sc06",
                state="running",
            )
        )

    # Verify seed
    with engine.connect() as conn:
        count = conn.execute(select(tasks.c.id)).fetchall()
        assert len(count) == 1, "Fixture seed verification failed"
    engine.dispose()
    print("Fixture dataset seeded successfully.")


def run_sc06() -> None:
    db_url = get_db_url()
    alembic_cfg = get_alembic_config(db_url)

    print("============================================================")
    print("SC-06 Cycle 1: Empty database up/down/up")
    print("============================================================")
    reset_database(db_url)

    print("\n[Step 1.1] alembic upgrade head on empty database...")
    command.upgrade(alembic_cfg, "head")
    print_current(alembic_cfg, "after upgrade head (empty DB)")

    print("\n[Step 1.2] alembic downgrade base...")
    command.downgrade(alembic_cfg, "base")
    print_current(alembic_cfg, "after downgrade base (empty DB)")

    print("\n[Step 1.3] alembic upgrade head...")
    command.upgrade(alembic_cfg, "head")
    print_current(alembic_cfg, "after second upgrade head (empty DB)")

    print("\n============================================================")
    print("SC-06 Cycle 2: Seeded database down/up")
    print("============================================================")
    print("\n[Step 2.1] Seeding fixture dataset...")
    seed_fixture_data(db_url)

    print("\n[Step 2.2] alembic downgrade base on seeded database...")
    command.downgrade(alembic_cfg, "base")
    print_current(alembic_cfg, "after downgrade base (seeded DB)")

    print("\n[Step 2.3] alembic upgrade head after drop...")
    command.upgrade(alembic_cfg, "head")
    print_current(alembic_cfg, "after upgrade head (post-seeded DB)")

    print("\n============================================================")
    print("SC-06 Verification: ALL CYCLES PASSED")
    print("============================================================")


if __name__ == "__main__":
    try:
        run_sc06()
    except Exception as e:  # noqa: BLE001
        print(f"SC-06 FAILED: {e}", file=sys.stderr)
        sys.exit(1)
