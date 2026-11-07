"""Introspection and constraint tests across all 16 core tables."""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from eap.db import (
    commands,
    dispatch_intents,
    project_current_assessment,
    run_attempt_history,
    runs,
    sources,
    task_intents,
    tasks,
)

CORE_TABLES = [
    "tasks",
    "runs",
    "run_attempt_history",
    "task_intents",
    "dispatch_intents",
    "action_executions",
    "leases",
    "commands",
    "outbox",
    "event_log",
    "artifacts",
    "usage_ledger",
    "sources",
    "project_scopes",
    "ppm_scan_schedules",
    "project_current_assessment",
]


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_every_core_table_has_tenant_id_not_null(db_engine: sa.Engine) -> None:
    """Introspection: Every one of the 16 core tables must have tenant_id NOT NULL."""
    inspector = inspect(db_engine)
    existing_tables = set(inspector.get_table_names())

    for tbl in CORE_TABLES:
        assert tbl in existing_tables, f"Table '{tbl}' is missing from the database"
        columns = {col["name"]: col for col in inspector.get_columns(tbl)}
        assert "tenant_id" in columns, f"Table '{tbl}' is missing 'tenant_id' column"
        assert columns["tenant_id"]["nullable"] is False, f"Table '{tbl}.tenant_id' must be NOT NULL"


def test_runs_constraints(db_conn: sa.Connection) -> None:
    """Test runs table unique constraints and check constraints."""
    tenant = "tenant-runs"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    # Insert parent task
    db_conn.execute(
        tasks.insert().values(
            id="task-r1",
            tenant_id=tenant,
            client_idempotency_key="ck-r1",
            task_intent_key="tik-r1",
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="ready",
            deadline=deadline,
        )
    )

    # Insert first run
    db_conn.execute(
        runs.insert().values(
            id="run-uuid-1",
            tenant_id=tenant,
            task_id="task-r1",
            run_id="run-001",
            attempt=1,
            binding_id="bind-1",
            state="queued",
        )
    )

    # Duplicate run_id in same tenant rejected
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            runs.insert().values(
                id="run-uuid-2",
                tenant_id=tenant,
                task_id="task-r1",
                run_id="run-001",  # DUPLICATE run_id
                attempt=2,
                binding_id="bind-1",
                state="queued",
            )
        )
    _assert_constraint(exc_info, "uq_runs_tenant_run_id")

    # Duplicate (task_id, attempt) rejected
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            runs.insert().values(
                id="run-uuid-3",
                tenant_id=tenant,
                task_id="task-r1",
                run_id="run-002",
                attempt=1,  # DUPLICATE attempt for task-r1
                binding_id="bind-1",
                state="queued",
            )
        )
    _assert_constraint(exc_info, "uq_runs_tenant_task_attempt")

    # Invalid state rejected by ck_runs_state
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            runs.insert().values(
                id="run-uuid-4",
                tenant_id=tenant,
                task_id="task-r1",
                run_id="run-003",
                attempt=3,
                binding_id="bind-1",
                state="invalid_run_state",
            )
        )
    _assert_constraint(exc_info, "ck_runs_state")

    # Invalid remote_outcome rejected by ck_runs_remote_outcome
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            runs.insert().values(
                id="run-uuid-5",
                tenant_id=tenant,
                task_id="task-r1",
                run_id="run-004",
                attempt=4,
                binding_id="bind-1",
                state="queued",
                remote_outcome="bogus_outcome",
            )
        )
    _assert_constraint(exc_info, "ck_runs_remote_outcome")


def test_run_attempt_history_constraints(db_conn: sa.Connection) -> None:
    """Test run_attempt_history check constraints."""
    tenant = "tenant-history"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    db_conn.execute(
        tasks.insert().values(
            id="task-h1",
            tenant_id=tenant,
            client_idempotency_key="ck-h1",
            task_intent_key="tik-h1",
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="ready",
            deadline=deadline,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            run_attempt_history.insert().values(
                tenant_id=tenant,
                task_id="task-h1",
                run_id="run-001",
                attempt=1,
                from_state="bad_from_state",
                to_state="running",
            )
        )
    _assert_constraint(exc_info, "ck_run_attempt_history_from_state")

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            run_attempt_history.insert().values(
                tenant_id=tenant,
                task_id="task-h1",
                run_id="run-001",
                attempt=1,
                from_state="queued",
                to_state="bad_to_state",
            )
        )
    _assert_constraint(exc_info, "ck_run_attempt_history_to_state")


def test_commands_status_check(db_conn: sa.Connection) -> None:
    """Test commands status check constraint."""
    tenant = "tenant-cmd"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    db_conn.execute(
        tasks.insert().values(
            id="task-cmd1",
            tenant_id=tenant,
            client_idempotency_key="ck-cmd1",
            task_intent_key="tik-cmd1",
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="ready",
            deadline=deadline,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            commands.insert().values(
                id="cmd-1",
                tenant_id=tenant,
                task_id="task-cmd1",
                run_id="run-1",
                command_type="cancel",
                status="invalid_cmd_status",
            )
        )
    _assert_constraint(exc_info, "ck_commands_status")


def test_dispatch_intents_constraints(db_conn: sa.Connection) -> None:
    """Test dispatch_intents uq_dispatch_intents_op and ck_dispatch_intents_state."""
    tenant = "tenant-dispatch"

    db_conn.execute(
        dispatch_intents.insert().values(
            tenant_id=tenant,
            binding_id="bind-1",
            run_id="run-1",
            operation="submit",
            idempotency_key="dispatch-key-1",
            state="prepared",
        )
    )

    # Duplicate (binding_id, run_id, operation)
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            dispatch_intents.insert().values(
                tenant_id=tenant,
                binding_id="bind-1",
                run_id="run-1",
                operation="submit",  # DUPLICATE
                idempotency_key="dispatch-key-2",
                state="prepared",
            )
        )
    _assert_constraint(exc_info, "uq_dispatch_intents_op")

    # Invalid state
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            dispatch_intents.insert().values(
                tenant_id=tenant,
                binding_id="bind-1",
                run_id="run-2",
                operation="submit",
                idempotency_key="dispatch-key-3",
                state="invalid_dispatch_state",
            )
        )
    _assert_constraint(exc_info, "ck_dispatch_intents_state")


def test_task_intents_constraints(db_conn: sa.Connection) -> None:
    """Test task_intents uq_task_intents_slot and ck_task_intents_status."""
    tenant = "tenant-ti"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    db_conn.execute(
        tasks.insert().values(
            id="task-ti1",
            tenant_id=tenant,
            client_idempotency_key="ck-ti1",
            task_intent_key="tik-ti1",
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="ready",
            deadline=deadline,
        )
    )

    db_conn.execute(
        task_intents.insert().values(
            tenant_id=tenant,
            task_id="task-ti1",
            action_slot="create_pr#1",
            tool_id="engineering.create_pr",
            workflow_step="pr_step",
            status="declared",
        )
    )

    # Duplicate action_slot for same task
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            task_intents.insert().values(
                tenant_id=tenant,
                task_id="task-ti1",
                action_slot="create_pr#1",  # DUPLICATE
                tool_id="engineering.create_pr",
                workflow_step="pr_step",
                status="declared",
            )
        )
    _assert_constraint(exc_info, "uq_task_intents_slot")

    # Invalid status
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            task_intents.insert().values(
                tenant_id=tenant,
                task_id="task-ti1",
                action_slot="create_pr#2",
                tool_id="engineering.create_pr",
                workflow_step="pr_step",
                status="invalid_status",
            )
        )
    _assert_constraint(exc_info, "ck_task_intents_status")


def test_sources_unique_constraints(db_conn: sa.Connection) -> None:
    """Test sources canonical_root and repo_identity uniqueness."""
    tenant = "tenant-src"

    db_conn.execute(
        sources.insert().values(
            source_id="src-1",
            tenant_id=tenant,
            canonical_root="/path/to/repo1",
            repo_identity="git@example.com:repo1.git",
        )
    )

    # Duplicate canonical_root
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            sources.insert().values(
                source_id="src-2",
                tenant_id=tenant,
                canonical_root="/path/to/repo1",  # DUPLICATE
                repo_identity="git@example.com:repo2.git",
            )
        )
    _assert_constraint(exc_info, "uq_sources_tenant_canonical_root")

    # Duplicate repo_identity
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            sources.insert().values(
                source_id="src-3",
                tenant_id=tenant,
                canonical_root="/path/to/repo3",
                repo_identity="git@example.com:repo1.git",  # DUPLICATE
            )
        )
    _assert_constraint(exc_info, "uq_sources_tenant_repo_identity")


def test_project_current_assessment_pk(db_conn: sa.Connection) -> None:
    """Test project_current_assessment primary key constraint on (tenant_id, project_id)."""
    tenant = "tenant-pca"
    proj = "proj-1"

    db_conn.execute(
        project_current_assessment.insert().values(
            tenant_id=tenant,
            project_id=proj,
            assessment_id="asmt-1",
            basis_seq=1,
            snapshot_seq=1,
        )
    )

    # Duplicate (tenant_id, project_id) violates PK
    with pytest.raises(IntegrityError), db_conn.begin_nested():
        db_conn.execute(
            project_current_assessment.insert().values(
                tenant_id=tenant,
                project_id=proj,  # DUPLICATE PK
                assessment_id="asmt-2",
                basis_seq=2,
                snapshot_seq=2,
            )
        )
