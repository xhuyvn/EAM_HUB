"""Task Service: Lifecycle and admission management.

Implements:
- TaskService.create: Idempotent on (tenant_id, client_idempotency_key) (C01).
- Issues task_intent_key via eap.contracts.keys (DG-1 §2).
- Pins agent_version for all attempts (C19).
- Atomic transaction: tasks + runs (attempt 1) + run_attempt_history + ONE outbox event.
- Concurrent C01 race handling via unique constraint recovery.
- TaskService.get and TaskService.get_runs with strict tenant isolation (no existence oracle).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eap.contracts.keys import derive_task_intent_key, validate_identifier
from eap.contracts.models.task_request import TaskRequest
from eap.db import run_attempt_history, runs, tasks
from eap.run.outbox import build_event, create_outbox_record


class TaskServiceError(Exception):
    """Base error for TaskService."""



class TaskNotFoundError(TaskServiceError):
    """Task not found for tenant."""



@dataclass(frozen=True)
class TaskRecord:
    id: str
    tenant_id: str
    client_idempotency_key: str
    task_intent_key: str
    task_type: str
    agent_id: str
    agent_version: str
    state: str
    deadline: dt.datetime
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> TaskRecord:
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            client_idempotency_key=row["client_idempotency_key"],
            task_intent_key=row["task_intent_key"],
            task_type=row["task_type"],
            agent_id=row["agent_id"],
            agent_version=row["agent_version"],
            state=row["state"],
            deadline=row["deadline"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class TaskService:
    @staticmethod
    def create(
        db: Session,
        *,
        tenant_id: str,
        request: TaskRequest,
        agent_version: str | None = None,
        deadline: dt.datetime | None = None,
        task_id: str | None = None,
        binding_id: str | None = None,
    ) -> TaskRecord:
        """Create a new logical task idempotently on (tenant_id, client_idempotency_key).

        If a task with the same (tenant_id, client_idempotency_key) exists, returns
        the existing record without inserting duplicate rows or events (C01).
        """
        validate_identifier("tenant_id", tenant_id)
        client_key = request.idempotency_key

        # 1. Fast check for existing task
        query = sa.select(tasks).where(
            tasks.c.tenant_id == tenant_id,
            tasks.c.client_idempotency_key == client_key,
        )
        existing = db.execute(query).mappings().one_or_none()
        if existing is not None:
            return TaskRecord.from_row(existing)

        # 2. Derive task_intent_key
        actual_task_id = task_id or f"task-{uuid.uuid4().hex[:12]}"
        validate_identifier("task_id", actual_task_id)
        task_intent_key = derive_task_intent_key(tenant_id, actual_task_id)

        # Pin agent_version for all attempts (C19)
        actual_agent_version = agent_version or "1.0.0"
        actual_deadline = deadline or (dt.datetime.now(dt.UTC) + dt.timedelta(hours=24))
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        actual_binding = binding_id or f"binding-{request.task_type.replace('.', '-')}"

        # 3. Insert tasks + run attempt 1 + history + outbox atomically
        try:
            with db.begin_nested():
                # 3a. Insert task
                db.execute(
                    tasks.insert().values(
                        id=actual_task_id,
                        tenant_id=tenant_id,
                        client_idempotency_key=client_key,
                        task_intent_key=task_intent_key,
                        task_type=request.task_type,
                        agent_id=request.agent_id,
                        agent_version=actual_agent_version,
                        state="ready",
                        deadline=actual_deadline,
                    )
                )

                # 3b. Insert run attempt 1
                db.execute(
                    runs.insert().values(
                        id=run_id,
                        tenant_id=tenant_id,
                        task_id=actual_task_id,
                        run_id=run_id,
                        attempt=1,
                        binding_id=actual_binding,
                        state="queued",
                        remote_outcome=None,
                        lease_fencing_token=0,
                    )
                )

                # 3c. Insert run_attempt_history
                db.execute(
                    run_attempt_history.insert().values(
                        tenant_id=tenant_id,
                        task_id=actual_task_id,
                        run_id=run_id,
                        attempt=1,
                        from_state="queued",
                        to_state="queued",
                        reason="task_created",
                    )
                )

                # 3d. Insert ONE outbox row
                event = build_event(
                    tenant_id=tenant_id,
                    task_id=actual_task_id,
                    run_id=run_id,
                    attempt=1,
                    event_type="run.state_changed",
                    producer_id="task_service",
                    producer_seq=1,
                    payload={
                        "from_state": "queued",
                        "to_state": "queued",
                        "reason_code": "task_created",
                        "evidence_ref": None,
                    },
                )
                from eap.db import outbox

                db.execute(outbox.insert().values(**create_outbox_record(event, stream_id=f"run:{run_id}")))

            db.commit()
        except IntegrityError:
            db.rollback()
            # Handle concurrent race: if another transaction won the insert for this client key
            existing = db.execute(query).mappings().one_or_none()
            if existing is not None:
                return TaskRecord.from_row(existing)
            raise

        created = db.execute(query).mappings().one()
        return TaskRecord.from_row(created)

    @staticmethod
    def get(db: Session, *, tenant_id: str, task_id: str) -> TaskRecord | None:
        """Retrieve task by id strictly scoped by tenant (no existence oracle)."""
        query = sa.select(tasks).where(
            tasks.c.tenant_id == tenant_id,
            tasks.c.id == task_id,
        )
        row = db.execute(query).mappings().one_or_none()
        if row is None:
            return None
        return TaskRecord.from_row(row)

    @staticmethod
    def get_runs(db: Session, *, tenant_id: str, task_id: str) -> list[dict[str, Any]] | None:
        """Retrieve all runs for a task, returning None if task does not exist in tenant."""
        task = TaskService.get(db, tenant_id=tenant_id, task_id=task_id)
        if task is None:
            return None

        query = (
            sa.select(runs)
            .where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task_id,
            )
            .order_by(runs.c.attempt.asc())
        )
        results = db.execute(query).mappings().all()
        return [dict(r) for r in results]
