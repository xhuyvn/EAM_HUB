"""Tests for tasks, usage_ledger, and outbox constraints.

Enforces:
- tasks.task_intent_key unique per tenant (uq_tasks_tenant_task_intent_key).
- tasks.client_idempotency_key unique per tenant (uq_tasks_tenant_client_key).
- tasks.state check constraint (ck_tasks_state).
- usage_ledger.provider_usage_id unique per tenant (uq_usage_ledger_provider_id).
- outbox has no offset column (ADR-020).
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from eap.db import tasks, usage_ledger


def _assert_constraint(exc_info: pytest.ExceptionInfo[IntegrityError], constraint_name: str) -> None:
    orig = exc_info.value.orig
    diag_name = getattr(getattr(orig, "diag", None), "constraint_name", None)
    assert (
        diag_name == constraint_name or constraint_name in str(exc_info.value)
    ), f"Expected constraint '{constraint_name}', got diag={diag_name}, str={exc_info.value}"


def test_tasks_task_intent_key_unique_per_tenant(db_conn: sa.Connection) -> None:
    """Duplicate task_intent_key in same tenant is rejected."""
    tenant = "tenant-tasks"
    tik = "bd26959f46630d52b7362f53608330f10ba7d2607ccbe80f51c33025fdfb1d06"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    db_conn.execute(
        tasks.insert().values(
            id="task-001",
            tenant_id=tenant,
            client_idempotency_key="client-key-1",
            task_intent_key=tik,
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="queued",
            deadline=deadline,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            tasks.insert().values(
                id="task-002",
                tenant_id=tenant,
                client_idempotency_key="client-key-2",
                task_intent_key=tik,  # DUPLICATE
                task_type="ppm.assessment",
                agent_id="agent-1",
                agent_version="1.0",
                state="queued",
                deadline=deadline,
            )
        )

    _assert_constraint(exc_info, "uq_tasks_tenant_task_intent_key")


def test_tasks_client_key_unique_per_tenant(db_conn: sa.Connection) -> None:
    """Duplicate client_idempotency_key in same tenant is rejected."""
    tenant = "tenant-tasks-client"
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)

    db_conn.execute(
        tasks.insert().values(
            id="task-c1",
            tenant_id=tenant,
            client_idempotency_key="client-req-abc",
            task_intent_key="tik-alpha",
            task_type="ppm.assessment",
            agent_id="agent-1",
            agent_version="1.0",
            state="queued",
            deadline=deadline,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            tasks.insert().values(
                id="task-c2",
                tenant_id=tenant,
                client_idempotency_key="client-req-abc",  # DUPLICATE
                task_intent_key="tik-beta",
                task_type="ppm.assessment",
                agent_id="agent-1",
                agent_version="1.0",
                state="queued",
                deadline=deadline,
            )
        )

    _assert_constraint(exc_info, "uq_tasks_tenant_client_key")


def test_tasks_state_check_constraint(db_conn: sa.Connection) -> None:
    """Invalid task state violates ck_tasks_state."""
    deadline = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            tasks.insert().values(
                id="task-bad-state",
                tenant_id="tenant-bad",
                client_idempotency_key="ck-bad",
                task_intent_key="tik-bad",
                task_type="ppm.assessment",
                agent_id="agent-1",
                agent_version="1.0",
                state="unknown_task_state",
                deadline=deadline,
            )
        )

    _assert_constraint(exc_info, "ck_tasks_state")


def test_usage_ledger_provider_usage_id_unique_per_tenant(db_conn: sa.Connection) -> None:
    """usage_ledger.provider_usage_id unique per tenant (C38)."""
    tenant = "tenant-usage"
    puid = "provider-usage-evt-999"

    db_conn.execute(
        usage_ledger.insert().values(
            tenant_id=tenant,
            provider_usage_id=puid,
            run_id="run-1",
            model_profile="gpt-4o",
            prompt_tokens=100,
            completion_tokens=20,
            cost_estimate=0.005,
        )
    )

    with pytest.raises(IntegrityError) as exc_info, db_conn.begin_nested():
        db_conn.execute(
            usage_ledger.insert().values(
                tenant_id=tenant,
                provider_usage_id=puid,  # DUPLICATE
                run_id="run-2",
                model_profile="gpt-4o",
                prompt_tokens=50,
                completion_tokens=10,
                cost_estimate=0.002,
            )
        )

    _assert_constraint(exc_info, "uq_usage_ledger_provider_id")

    # Allowed in different tenant
    db_conn.execute(
        usage_ledger.insert().values(
            tenant_id="tenant-usage-other",
            provider_usage_id=puid,
            run_id="run-3",
            model_profile="gpt-4o",
            prompt_tokens=100,
            completion_tokens=20,
            cost_estimate=0.005,
        )
    )


def test_outbox_has_no_offset_column(db_engine: sa.Engine) -> None:
    """ADR-020: outbox table has no offset column."""
    inspector = inspect(db_engine)
    columns = [col["name"] for col in inspector.get_columns("outbox")]
    assert "offset" not in columns
    assert "ingest_offset" not in columns
    assert "stream_offset" not in columns
    assert "event_id" in columns
    assert "stream_id" in columns
    assert "payload" in columns
