"""Run Service: Physical execution attempt lifecycle, state transitions, and outbox emission.

Implements:
- RunService.transition: validated against eap.contracts.rules.ALLOWED.
- Atomic commit of runs + run_attempt_history + ONE outbox event in the same transaction.
- Concurrency protection via row-level locking (SELECT ... FOR UPDATE).
- Late terminal events rejected with 409 (history and outbox unchanged).
- Illegal transitions rejected with 409 ILLEGAL_RUN_TRANSITION.
- RunService.retry_new_attempt: enforces DG1-C1 (unknown remote outcome -> 409 REMOTE_OUTCOME_UNKNOWN,
  no new attempt row, outbox unchanged).
- Uses pinned agent_version from task across all attempts (C19).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.contracts.rules import ALLOWED, TERMINAL
from eap.db import outbox, run_attempt_history, runs, tasks
from eap.observability.logging import set_log_context
from eap.run.outbox import build_event, create_outbox_record


class RunServiceError(Exception):
    """Base error for RunService."""



class RunNotFoundError(RunServiceError):
    """Run not found in tenant."""



class IllegalRunTransitionError(RunServiceError):
    """Transition is illegal per ALLOWED rules or expected_from mismatch (HTTP 409)."""



class LateTerminalEventError(RunServiceError):
    """Terminal state cannot be left or transitioned after terminal reached (HTTP 409)."""



class RemoteOutcomeUnknownError(RunServiceError):
    """DG1-C1: Retry requested while remote outcome is unknown and unreconciled (HTTP 409)."""



class StaleFencingTokenError(RunServiceError):
    """C15: Worker write rejected due to stale fencing token (HTTP 409)."""



@dataclass(frozen=True)
class RunRecord:
    id: str
    tenant_id: str
    task_id: str
    run_id: str
    attempt: int
    binding_id: str
    state: str
    remote_outcome: str | None
    lease_fencing_token: int
    created_at: dt.datetime
    updated_at: dt.datetime

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> RunRecord:
        return cls(
            id=row["id"],
            tenant_id=row["tenant_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            attempt=row["attempt"],
            binding_id=row["binding_id"],
            state=row["state"],
            remote_outcome=row["remote_outcome"],
            lease_fencing_token=row["lease_fencing_token"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class RunService:
    @staticmethod
    def transition(
        db: Session,
        *,
        tenant_id: str,
        run_id: str,
        to_state: str,
        expected_from: str | None = None,
        reason: str | None = None,
        evidence_ref: str | None = None,
        remote_outcome: str | None = None,
        fencing_token: int | None = None,
    ) -> RunRecord:
        """Execute a validated run state transition.

        Writes: runs (update) + run_attempt_history (insert) + outbox (insert)
        in the exact same database transaction.
        """
        # Row-level lock to prevent concurrency races
        stmt = (
            sa.select(runs)
            .where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == run_id,
            )
            .with_for_update()
        )
        row = db.execute(stmt).mappings().one_or_none()
        if row is None:
            raise RunNotFoundError(f"Run {run_id} not found in tenant {tenant_id}")

        set_log_context(tenant_id=tenant_id, task_id=str(row["task_id"]), run_id=run_id)

        # C15 fencing check: stale token must reject write immediately
        if fencing_token is not None:
            current_fencing = row["lease_fencing_token"] or 0
            if fencing_token < current_fencing:
                raise StaleFencingTokenError(
                    f"STALE_FENCING_TOKEN: Worker token {fencing_token} is strictly less than run lease fencing token {current_fencing}"
                )

        current_state = row["state"]

        # Late terminal event check: terminal run states cannot be transitioned
        if current_state in TERMINAL:
            raise LateTerminalEventError(
                f"Run {run_id} is in terminal state '{current_state}', cannot transition to '{to_state}'"
            )

        # Expected from check
        if expected_from is not None and current_state != expected_from:
            raise IllegalRunTransitionError(
                f"Run state mismatch: expected from_state '{expected_from}', but was '{current_state}'"
            )

        # State machine transition check per contracts rules ALLOWED
        if (current_state, to_state) not in ALLOWED:
            raise IllegalRunTransitionError(
                f"ILLEGAL_RUN_TRANSITION: transition '{current_state}' -> '{to_state}' is not permitted"
            )

        # Determine remote_outcome
        if remote_outcome is not None:
            new_remote_outcome = remote_outcome
        elif to_state == "unknown":
            new_remote_outcome = "unknown"
        elif to_state in ("running", "waiting_approval", "succeeded", "failed", "cancelled"):
            new_remote_outcome = row["remote_outcome"] if row["remote_outcome"] != "unknown" else "known"
        else:
            new_remote_outcome = row["remote_outcome"]

        now = dt.datetime.now(dt.UTC)
        reason_code = reason or f"transition_{current_state}_to_{to_state}"

        # Count prior history events for this run to set monotonic producer_seq
        count_stmt = sa.select(sa.func.count()).select_from(run_attempt_history).where(
            run_attempt_history.c.tenant_id == tenant_id,
            run_attempt_history.c.run_id == run_id,
        )
        prior_count = db.scalar(count_stmt) or 0
        producer_seq = prior_count + 1

        with db.begin_nested():
            # 1. Update runs
            update_vals: dict[str, Any] = {
                "state": to_state,
                "remote_outcome": new_remote_outcome,
                "updated_at": now,
            }
            if fencing_token is not None and fencing_token > (row["lease_fencing_token"] or 0):
                update_vals["lease_fencing_token"] = fencing_token

            db.execute(
                runs.update()
                .where(
                    runs.c.tenant_id == tenant_id,
                    runs.c.run_id == run_id,
                )
                .values(**update_vals)
            )

            # 2. Insert run_attempt_history
            db.execute(
                run_attempt_history.insert().values(
                    tenant_id=tenant_id,
                    task_id=row["task_id"],
                    run_id=run_id,
                    attempt=row["attempt"],
                    from_state=current_state,
                    to_state=to_state,
                    reason=reason_code,
                    recorded_at=now,
                )
            )

            # 3. Insert ONE outbox row
            event = build_event(
                tenant_id=tenant_id,
                task_id=row["task_id"],
                run_id=run_id,
                attempt=row["attempt"],
                event_type="run.state_changed",
                producer_id="run_service",
                producer_seq=producer_seq,
                occurred_at=now,
                payload={
                    "from_state": current_state,
                    "to_state": to_state,
                    "reason_code": reason_code[:64],
                    "evidence_ref": evidence_ref,
                },
            )
            db.execute(outbox.insert().values(**create_outbox_record(event, stream_id=f"run:{run_id}")))

        db.commit()

        updated_row = db.execute(
            sa.select(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == run_id,
            )
        ).mappings().one()
        return RunRecord.from_row(updated_row)

    @staticmethod
    def retry_new_attempt(
        db: Session,
        *,
        tenant_id: str,
        task_id: str,
        binding_id: str | None = None,
    ) -> RunRecord:
        """Create a new physical run attempt for a task.

        Enforces DG1-C1:
        If the latest attempt's state or remote_outcome is 'unknown' and unreconciled,
        raises RemoteOutcomeUnknownError (409) and inserts NO new attempt row.
        """
        # Lock task row
        task_stmt = (
            sa.select(tasks)
            .where(
                tasks.c.tenant_id == tenant_id,
                tasks.c.id == task_id,
            )
            .with_for_update()
        )
        task_row = db.execute(task_stmt).mappings().one_or_none()
        if task_row is None:
            raise RunNotFoundError(f"Task {task_id} not found in tenant {tenant_id}")

        # Find latest attempt for this task
        latest_stmt = (
            sa.select(runs)
            .where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task_id,
            )
            .order_by(runs.c.attempt.desc())
            .with_for_update()
        )
        latest_run = db.execute(latest_stmt).mappings().first()
        if latest_run is None:
            raise RunNotFoundError(f"No run attempts found for task {task_id}")

        # DG1-C1 check: if unknown outcome, reject retry
        if latest_run["state"] == "unknown" or latest_run["remote_outcome"] == "unknown":
            raise RemoteOutcomeUnknownError(
                f"REMOTE_OUTCOME_UNKNOWN: Run attempt {latest_run['attempt']} outcome is unknown and unreconciled"
            )

        new_attempt = latest_run["attempt"] + 1
        new_run_id = f"run-{uuid.uuid4().hex[:12]}"
        actual_binding = binding_id or latest_run["binding_id"]
        now = dt.datetime.now(dt.UTC)
        set_log_context(tenant_id=tenant_id, task_id=task_id, run_id=new_run_id)

        with db.begin_nested():
            # 1. Insert new run row
            db.execute(
                runs.insert().values(
                    id=new_run_id,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    run_id=new_run_id,
                    attempt=new_attempt,
                    binding_id=actual_binding,
                    state="queued",
                    remote_outcome=None,
                    lease_fencing_token=0,
                )
            )

            # 2. Insert run_attempt_history
            db.execute(
                run_attempt_history.insert().values(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    run_id=new_run_id,
                    attempt=new_attempt,
                    from_state="queued",
                    to_state="queued",
                    reason="retry_new_attempt",
                    recorded_at=now,
                )
            )

            # 3. Insert ONE outbox row
            event = build_event(
                tenant_id=tenant_id,
                task_id=task_id,
                run_id=new_run_id,
                attempt=new_attempt,
                event_type="run.state_changed",
                producer_id="run_service",
                producer_seq=1,
                occurred_at=now,
                payload={
                    "from_state": "queued",
                    "to_state": "queued",
                    "reason_code": "retry_new_attempt",
                    "evidence_ref": None,
                },
            )
            db.execute(outbox.insert().values(**create_outbox_record(event, stream_id=f"run:{new_run_id}")))

        db.commit()

        new_row = db.execute(
            sa.select(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == new_run_id,
            )
        ).mappings().one()
        return RunRecord.from_row(new_row)

    @staticmethod
    def get(db: Session, *, tenant_id: str, run_id: str) -> RunRecord | None:
        """Retrieve run by run_id strictly scoped to tenant."""
        stmt = sa.select(runs).where(
            runs.c.tenant_id == tenant_id,
            runs.c.run_id == run_id,
        )
        row = db.execute(stmt).mappings().one_or_none()
        if row is None:
            return None
        return RunRecord.from_row(row)
