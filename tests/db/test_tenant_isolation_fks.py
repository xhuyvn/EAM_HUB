"""Tests for tenant isolation at the database level via composite foreign keys (P1-1).

Enforces:
- Catalog invariant: Every foreign key in the schema has tenant_id in both
  constrained_columns and referred_columns (auto-covers future FKs).
- Cross-tenant rejection: For each of the 7 child FKs:
  - Inserting a child for tenant-B pointing at tenant-A's parent fails with the exact named FK constraint.
  - Inserting the same child for tenant-A succeeds.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from eap.db import (
    artifacts,
    commands,
    metadata,
    ppm_scan_schedules,
    project_scopes,
    run_attempt_history,
    runs,
    sources,
    task_intents,
    tasks,
)


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_catalog_every_fk_includes_tenant_id(db_conn: sa.Connection) -> None:
    """(a) Catalog test: Every FK in the schema has tenant_id as a column of the constraint.

    Inspects both SQLAlchemy Core metadata and the live PostgreSQL catalog.
    """
    # 1. Verify in-memory metadata
    assert len(metadata.tables) >= 16
    for table_name, table in metadata.tables.items():
        if table_name.startswith("_"):
            continue
        for fkc in table.foreign_key_constraints:
            col_names = [c.name for c in fkc.columns]
            ref_col_names = [elem.column.name for elem in fkc.elements]
            assert "tenant_id" in col_names, (
                f"Metadata FK '{fkc.name}' on '{table_name}' missing 'tenant_id' in columns: {col_names}"
            )
            assert "tenant_id" in ref_col_names, (
                f"Metadata FK '{fkc.name}' on '{table_name}' missing 'tenant_id' in target: {ref_col_names}"
            )

    # 2. Verify live PostgreSQL information schema / catalog
    inspector = inspect(db_conn)
    fk_count = 0
    for table_name in inspector.get_table_names():
        if table_name.startswith("_"):
            continue
        fks = inspector.get_foreign_keys(table_name)
        for fk in fks:
            fk_count += 1
            con_name = fk.get("name")
            constrained_cols = fk.get("constrained_columns", [])
            referred_cols = fk.get("referred_columns", [])
            assert "tenant_id" in constrained_cols, (
                f"Live DB FK '{con_name}' on table '{table_name}' does not include 'tenant_id' in constrained columns: {constrained_cols}"
            )
            assert "tenant_id" in referred_cols, (
                f"Live DB FK '{con_name}' on table '{table_name}' does not include 'tenant_id' in referred columns: {referred_cols}"
            )

    assert fk_count == 7, f"Expected exactly 7 foreign keys in schema, found {fk_count}"


def test_cross_tenant_fk_runs_rejected(db_conn: sa.Connection) -> None:
    """(b.1) runs -> tasks: Cross-tenant reference rejected by fk_runs_tenant_task."""
    tenant_a = "tenant-a-runs"
    tenant_b = "tenant-b-runs"
    task_id = "task-runs-iso-1"
    now = dt.datetime.now(dt.UTC)

    # Insert parent task in tenant A
    db_conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_a,
            client_idempotency_key="cid-runs-1",
            task_intent_key="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            task_type="ppm.assessment",
            agent_id="agent-iso",
            agent_version="1.0",
            state="queued",
            deadline=now + dt.timedelta(hours=1),
        )
    )

    # Attempt to insert child run in tenant B pointing to tenant A's task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            runs.insert().values(
                id="run-iso-b",
                tenant_id=tenant_b,
                task_id=task_id,
                run_id="run-b-1",
                attempt=1,
                binding_id="bind-b",
                state="queued",
            )
        )
    _assert_constraint(exc_info, "fk_runs_tenant_task")

    # Same-tenant insert succeeds
    db_conn.execute(
        runs.insert().values(
            id="run-iso-a",
            tenant_id=tenant_a,
            task_id=task_id,
            run_id="run-a-1",
            attempt=1,
            binding_id="bind-a",
            state="queued",
        )
    )


def test_cross_tenant_fk_run_attempt_history_rejected(db_conn: sa.Connection) -> None:
    """(b.2) run_attempt_history -> tasks: Cross-tenant reference rejected by fk_run_attempt_history_tenant_task."""
    tenant_a = "tenant-a-hist"
    tenant_b = "tenant-b-hist"
    task_id = "task-hist-iso-1"
    now = dt.datetime.now(dt.UTC)

    # Insert parent task in tenant A
    db_conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_a,
            client_idempotency_key="cid-hist-1",
            task_intent_key="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
            task_type="ppm.assessment",
            agent_id="agent-iso",
            agent_version="1.0",
            state="queued",
            deadline=now + dt.timedelta(hours=1),
        )
    )

    # Attempt to insert attempt history in tenant B pointing to tenant A's task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            run_attempt_history.insert().values(
                tenant_id=tenant_b,
                task_id=task_id,
                run_id="run-hist-b",
                attempt=1,
                from_state="queued",
                to_state="running",
            )
        )
    _assert_constraint(exc_info, "fk_run_attempt_history_tenant_task")

    # Same-tenant insert succeeds
    db_conn.execute(
        run_attempt_history.insert().values(
            tenant_id=tenant_a,
            task_id=task_id,
            run_id="run-hist-a",
            attempt=1,
            from_state="queued",
            to_state="running",
        )
    )


def test_cross_tenant_fk_task_intents_rejected(db_conn: sa.Connection) -> None:
    """(b.3) task_intents -> tasks: Cross-tenant reference rejected by fk_task_intents_tenant_task."""
    tenant_a = "tenant-a-intent"
    tenant_b = "tenant-b-intent"
    task_id = "task-intent-iso-1"
    now = dt.datetime.now(dt.UTC)

    # Insert parent task in tenant A
    db_conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_a,
            client_idempotency_key="cid-intent-1",
            task_intent_key="11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff",
            task_type="ppm.assessment",
            agent_id="agent-iso",
            agent_version="1.0",
            state="queued",
            deadline=now + dt.timedelta(hours=1),
        )
    )

    # Attempt to insert task intent in tenant B pointing to tenant A's task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            task_intents.insert().values(
                tenant_id=tenant_b,
                task_id=task_id,
                action_slot="slot-1",
                tool_id="tools.lint",
                workflow_step="step-1",
                status="declared",
            )
        )
    _assert_constraint(exc_info, "fk_task_intents_tenant_task")

    # Same-tenant insert succeeds
    db_conn.execute(
        task_intents.insert().values(
            tenant_id=tenant_a,
            task_id=task_id,
            action_slot="slot-1",
            tool_id="tools.lint",
            workflow_step="step-1",
            status="declared",
        )
    )


def test_cross_tenant_fk_commands_rejected(db_conn: sa.Connection) -> None:
    """(b.4) commands -> tasks: Cross-tenant reference rejected by fk_commands_tenant_task."""
    tenant_a = "tenant-a-cmd"
    tenant_b = "tenant-b-cmd"
    task_id = "task-cmd-iso-1"
    now = dt.datetime.now(dt.UTC)

    # Insert parent task in tenant A
    db_conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_a,
            client_idempotency_key="cid-cmd-1",
            task_intent_key="22334455667788990011aabbccddeeff22334455667788990011aabbccddeeff",
            task_type="ppm.assessment",
            agent_id="agent-iso",
            agent_version="1.0",
            state="queued",
            deadline=now + dt.timedelta(hours=1),
        )
    )

    # Attempt to insert command in tenant B pointing to tenant A's task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            commands.insert().values(
                id="cmd-b-1",
                tenant_id=tenant_b,
                task_id=task_id,
                run_id="run-b-cmd",
                command_type="cancel",
                status="accepted",
            )
        )
    _assert_constraint(exc_info, "fk_commands_tenant_task")

    # Same-tenant insert succeeds
    db_conn.execute(
        commands.insert().values(
            id="cmd-a-1",
            tenant_id=tenant_a,
            task_id=task_id,
            run_id="run-a-cmd",
            command_type="cancel",
            status="accepted",
        )
    )


def test_cross_tenant_fk_artifacts_rejected(db_conn: sa.Connection) -> None:
    """(b.5) artifacts -> tasks: Cross-tenant reference rejected by fk_artifacts_tenant_task."""
    tenant_a = "tenant-a-art"
    tenant_b = "tenant-b-art"
    task_id = "task-art-iso-1"
    now = dt.datetime.now(dt.UTC)

    # Insert parent task in tenant A
    db_conn.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_a,
            client_idempotency_key="cid-art-1",
            task_intent_key="33445566778899001122aabbccddeeff33445566778899001122aabbccddeeff",
            task_type="ppm.assessment",
            agent_id="agent-iso",
            agent_version="1.0",
            state="queued",
            deadline=now + dt.timedelta(hours=1),
        )
    )

    # Attempt to insert artifact in tenant B pointing to tenant A's task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            artifacts.insert().values(
                id="art-b-1",
                tenant_id=tenant_b,
                artifact_id="artifact-001",
                task_id=task_id,
                run_id="run-art-b",
                content_hash="hash-b",
                content_type="text/plain",
                size_bytes=100,
                storage_uri="s3://bucket/b",
            )
        )
    _assert_constraint(exc_info, "fk_artifacts_tenant_task")

    # Same-tenant insert succeeds
    db_conn.execute(
        artifacts.insert().values(
            id="art-a-1",
            tenant_id=tenant_a,
            artifact_id="artifact-001",
            task_id=task_id,
            run_id="run-art-a",
            content_hash="hash-a",
            content_type="text/plain",
            size_bytes=100,
            storage_uri="s3://bucket/a",
        )
    )


def test_cross_tenant_fk_project_scopes_rejected(db_conn: sa.Connection) -> None:
    """(b.6) project_scopes -> sources: Cross-tenant reference rejected by fk_project_scopes_tenant_source."""
    tenant_a = "tenant-a-scope"
    tenant_b = "tenant-b-scope"
    source_id = "src-scope-iso-1"

    # Insert parent source in tenant A
    db_conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant_a,
            canonical_root="/repos/tenant_a_repo",
            repo_identity="git@example.com:tenant_a.git",
        )
    )

    # Attempt to insert project_scope in tenant B pointing to tenant A's source
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            project_scopes.insert().values(
                id="scope-b-1",
                tenant_id=tenant_b,
                source_id=source_id,
                project_id="proj-b",
                metadata_root="apps/b/.ppm",
                status="active",
            )
        )
    _assert_constraint(exc_info, "fk_project_scopes_tenant_source")

    # Same-tenant insert succeeds
    db_conn.execute(
        project_scopes.insert().values(
            id="scope-a-1",
            tenant_id=tenant_a,
            source_id=source_id,
            project_id="proj-a",
            metadata_root="apps/a/.ppm",
            status="active",
        )
    )


def test_cross_tenant_fk_ppm_scan_schedules_rejected(db_conn: sa.Connection) -> None:
    """(b.7) ppm_scan_schedules -> sources: Cross-tenant reference rejected by fk_ppm_scan_schedules_tenant_source."""
    tenant_a = "tenant-a-sched"
    tenant_b = "tenant-b-sched"
    source_id = "src-sched-iso-1"

    # Insert parent source in tenant A
    db_conn.execute(
        sources.insert().values(
            source_id=source_id,
            tenant_id=tenant_a,
            canonical_root="/repos/tenant_a_sched_repo",
            repo_identity="git@example.com:tenant_a_sched.git",
        )
    )

    # Attempt to insert scan schedule in tenant B pointing to tenant A's source
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            ppm_scan_schedules.insert().values(
                id="sched-b-1",
                tenant_id=tenant_b,
                source_id=source_id,
                interval_seconds=300,
                debounce_seconds=5,
            )
        )
    _assert_constraint(exc_info, "fk_ppm_scan_schedules_tenant_source")

    # Same-tenant insert succeeds
    db_conn.execute(
        ppm_scan_schedules.insert().values(
            id="sched-a-1",
            tenant_id=tenant_a,
            source_id=source_id,
            interval_seconds=300,
            debounce_seconds=5,
        )
    )
