"""Test C19: Pinned agent_version preserved across all run attempts.

Conformance Case C19:
- agent_version recorded at task creation is used by all attempts.
- When an agent release changes globally or a retry occurs, all attempts of
  the original task maintain the pinned agent_version recorded at creation.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.contracts.models.task_request import TaskRequest
from eap.db import tasks
from eap.run.service import RunService
from eap.task.service import TaskService


def test_c19_pinned_agent_version_preserved_across_attempts(db_engine: Engine) -> None:
    """Task agent_version recorded at creation is preserved for all subsequent attempts."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"
    pinned_version = "2.4.1-rc3"

    req = TaskRequest(
        contract_version="1.0",
        agent_id="agent-ppm",
        task_type="code.review",
        objective="Verify pinned version across attempts",
        input_refs=["in-1"],
        resource_refs=["res-1"],
        idempotency_key=client_key,
    )

    with SessionLocal() as session:
        task = TaskService.create(
            session,
            tenant_id=tenant_id,
            request=req,
            agent_version=pinned_version,
        )
        assert task.agent_version == pinned_version
        task_id = task.id

        # Verify initial attempt 1
        runs_list = TaskService.get_runs(session, tenant_id=tenant_id, task_id=task_id)
        assert runs_list is not None
        assert len(runs_list) == 1
        run1_id = runs_list[0]["run_id"]

        # Advance attempt 1 to terminal failure
        RunService.transition(session, tenant_id=tenant_id, run_id=run1_id, to_state="dispatching")
        RunService.transition(session, tenant_id=tenant_id, run_id=run1_id, to_state="running")
        RunService.transition(
            session,
            tenant_id=tenant_id,
            run_id=run1_id,
            to_state="failed",
            remote_outcome="known",
        )

        # Retry creates attempt 2
        attempt2 = RunService.retry_new_attempt(session, tenant_id=tenant_id, task_id=task_id)
        assert attempt2.attempt == 2

    # Query task and all runs: verify pinned agent_version governs all attempts
    with SessionLocal() as session:
        persisted_task = TaskService.get(session, tenant_id=tenant_id, task_id=task_id)
        assert persisted_task is not None
        assert persisted_task.agent_version == pinned_version

        all_runs = TaskService.get_runs(session, tenant_id=tenant_id, task_id=task_id)
        assert all_runs is not None
        assert len(all_runs) == 2
        for r in all_runs:
            # Each run belongs to the task with the pinned agent_version
            t_row = session.execute(
                sa.select(tasks.c.agent_version).where(
                    tasks.c.tenant_id == tenant_id,
                    tasks.c.id == r["task_id"],
                )
            ).scalar_one()
            assert t_row == pinned_version
