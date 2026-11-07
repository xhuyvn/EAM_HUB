"""Test RunService lifecycle, transitions, outbox atomicity, and DG1-C1 unknown retry rejection.

Covers:
- DG1-C1 / C21 / C05: Run in state 'unknown' or remote_outcome 'unknown' -> retry_new_attempt raises
  RemoteOutcomeUnknownError (HTTP 409); attempt count unchanged; outbox unchanged.
- Legal transitions per eap.contracts.rules.ALLOWED atomically update runs + run_attempt_history + outbox.
- Illegal transitions raise IllegalRunTransitionError (HTTP 409); DB state and outbox unchanged.
- Late terminal events after terminal state raise LateTerminalEventError (HTTP 409); DB state unchanged.
- Retry on terminal failed state (with reconciled outcome) creates attempt 2 with outbox event.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.contracts.models.task_request import TaskRequest
from eap.db import outbox, run_attempt_history, runs
from eap.run.service import (
    IllegalRunTransitionError,
    LateTerminalEventError,
    RemoteOutcomeUnknownError,
    RunService,
)
from eap.task.service import TaskService


def create_task_and_initial_run(
    session,
    tenant_id: str,
    client_key: str,
) -> tuple[str, str]:
    """Helper to create a task and return (task_id, run_id)."""
    req = TaskRequest(
        contract_version="1.0",
        agent_id="agent-ppm",
        task_type="code.review",
        objective="Run code review",
        input_refs=["input-1"],
        resource_refs=["res-1"],
        idempotency_key=client_key,
    )
    task = TaskService.create(session, tenant_id=tenant_id, request=req)
    run = session.execute(
        sa.select(runs).where(
            runs.c.tenant_id == tenant_id,
            runs.c.task_id == task.id,
            runs.c.attempt == 1,
        )
    ).mappings().one()
    return task.id, run["run_id"]


def test_legal_run_transitions_and_outbox_atomicity(db_engine: Engine) -> None:
    """Legal transitions update runs, write history, and append outbox events."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        _task_id, run_id = create_task_and_initial_run(session, tenant_id, client_key)

    # Initial state is queued, outbox count is 1 (task creation event)
    with SessionLocal() as session:
        run = RunService.get(session, tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        assert run.state == "queued"

        # 1. queued -> dispatching
        r1 = RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="dispatching")
        assert r1.state == "dispatching"

        # 2. dispatching -> running
        r2 = RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="running")
        assert r2.state == "running"

        # 3. running -> waiting_approval
        r3 = RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="waiting_approval")
        assert r3.state == "waiting_approval"

        # 4. waiting_approval -> running
        r4 = RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="running")
        assert r4.state == "running"

        # 5. running -> succeeded
        r5 = RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="succeeded")
        assert r5.state == "succeeded"

    # Verify history and outbox in DB
    with SessionLocal() as session:
        history_rows = session.execute(
            sa.select(run_attempt_history)
            .where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
            .order_by(run_attempt_history.c.recorded_at.asc())
        ).mappings().all()

        # 1 initial creation + 5 transitions = 6 history records
        assert len(history_rows) == 6
        assert history_rows[0]["to_state"] == "queued"
        assert history_rows[1]["from_state"] == "queued" and history_rows[1]["to_state"] == "dispatching"
        assert history_rows[2]["from_state"] == "dispatching" and history_rows[2]["to_state"] == "running"
        assert history_rows[3]["from_state"] == "running" and history_rows[3]["to_state"] == "waiting_approval"
        assert history_rows[4]["from_state"] == "waiting_approval" and history_rows[4]["to_state"] == "running"
        assert history_rows[5]["from_state"] == "running" and history_rows[5]["to_state"] == "succeeded"

        # Verify outbox has exactly 6 events for this run
        outbox_events = session.execute(
            sa.select(outbox)
            .where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == f"run:{run_id}",
            )
            .order_by(outbox.c.created_at.asc())
        ).mappings().all()
        assert len(outbox_events) == 6


def test_illegal_run_transition_rejected(db_engine: Engine) -> None:
    """Illegal state transition raises IllegalRunTransitionError and leaves state/history/outbox unchanged."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        _task_id, run_id = create_task_and_initial_run(session, tenant_id, client_key)

    with SessionLocal() as session:
        # queued -> succeeded is illegal (must go through dispatching -> running)
        with pytest.raises(IllegalRunTransitionError) as exc_info:
            RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="succeeded")
        assert "ILLEGAL_RUN_TRANSITION" in str(exc_info.value)

        # Expected from mismatch also raises IllegalRunTransitionError
        with pytest.raises(IllegalRunTransitionError) as exc_info2:
            RunService.transition(
                session,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="dispatching",
                expected_from="running",  # mismatch: current is queued
            )
        assert "mismatch" in str(exc_info2.value)

    # Verify run state unchanged, history unchanged, outbox unchanged
    with SessionLocal() as session:
        run = RunService.get(session, tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        assert run.state == "queued"

        hist_count = session.scalar(
            sa.select(sa.func.count()).select_from(run_attempt_history).where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
        )
        assert hist_count == 1  # only initial creation

        outbox_count = session.scalar(
            sa.select(sa.func.count()).select_from(outbox).where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == f"run:{run_id}",
            )
        )
        assert outbox_count == 1


def test_late_terminal_event_rejected(db_engine: Engine) -> None:
    """Attempting a transition on a run already in a terminal state raises LateTerminalEventError."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        _task_id, run_id = create_task_and_initial_run(session, tenant_id, client_key)
        # Advance to terminal state
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="dispatching")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="running")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="succeeded")

    with SessionLocal() as session:
        # Late event attempting to transition terminal run to running or failed
        with pytest.raises(LateTerminalEventError) as exc_info:
            RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="running")
        assert "terminal" in str(exc_info.value)

        with pytest.raises(LateTerminalEventError):
            RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="failed")

    # Verify history still has exactly 4 rows (1 initial + 3 transitions)
    with SessionLocal() as session:
        hist_count = session.scalar(
            sa.select(sa.func.count()).select_from(run_attempt_history).where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
        )
        assert hist_count == 4


def test_c06_late_cancel_acknowledgement_rejected(db_engine: Engine) -> None:
    """C06: Run in cancelling state transitions to cancelled upon confirmation.

    Any late event attempting to transition after cancelled is rejected (HTTP 409 LateTerminalEventError)
    and history is strictly unchanged.
    """
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        _task_id, run_id = create_task_and_initial_run(session, tenant_id, client_key)
        # Advance: queued -> dispatching -> running -> cancelling -> cancelled
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="dispatching")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="running")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="cancelling")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="cancelled")

    # Verify run is terminal 'cancelled'
    with SessionLocal() as session:
        run = RunService.get(session, tenant_id=tenant_id, run_id=run_id)
        assert run is not None
        assert run.state == "cancelled"

        # Late execution result attempting to transition to succeeded or running
        with pytest.raises(LateTerminalEventError) as exc_info:
            RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="succeeded")
        assert "terminal" in str(exc_info.value)

        # History remains strictly 5 rows
        hist_count = session.scalar(
            sa.select(sa.func.count()).select_from(run_attempt_history).where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
        )
        assert hist_count == 5


def test_dg1_c1_retry_rejected_when_outcome_unknown(db_engine: Engine) -> None:
    """DG1-C1 / C05 / C21: When run is in unknown state or unreconciled, retry_new_attempt raises 409.

    Attempt count in runs is unchanged; outbox count is unchanged.
    """
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        task_id, run_id = create_task_and_initial_run(session, tenant_id, client_key)
        # Move run: queued -> dispatching -> unknown (lost ACK / crash before response, C05)
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="dispatching")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id, to_state="unknown")

    # Record outbox and run count before retry attempt
    with SessionLocal() as session:
        runs_before = session.scalar(
            sa.select(sa.func.count()).select_from(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task_id,
            )
        )
        assert runs_before == 1

        outbox_before = session.scalar(
            sa.select(sa.func.count()).select_from(outbox).where(
                outbox.c.tenant_id == tenant_id,
            )
        )

        # Attempt retry while unreconciled: MUST raise RemoteOutcomeUnknownError (409)
        with pytest.raises(RemoteOutcomeUnknownError) as exc_info:
            RunService.retry_new_attempt(session, tenant_id=tenant_id, task_id=task_id)
        assert "REMOTE_OUTCOME_UNKNOWN" in str(exc_info.value)

    # Verify attempt count in runs is strictly UNCHANGED
    with SessionLocal() as session:
        runs_after = session.scalar(
            sa.select(sa.func.count()).select_from(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task_id,
            )
        )
        assert runs_after == 1

        # Verify outbox count is strictly UNCHANGED
        outbox_after = session.scalar(
            sa.select(sa.func.count()).select_from(outbox).where(
                outbox.c.tenant_id == tenant_id,
            )
        )
        assert outbox_after == outbox_before


def test_retry_new_attempt_succeeds_when_reconciled_failed(db_engine: Engine) -> None:
    """When latest attempt is terminal failed (reconciled/known), retry_new_attempt creates attempt 2."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        task_id, run_id1 = create_task_and_initial_run(session, tenant_id, client_key)
        # Advance attempt 1 to terminal failure: queued -> dispatching -> running -> failed
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id1, to_state="dispatching")
        RunService.transition(session, tenant_id=tenant_id, run_id=run_id1, to_state="running")
        RunService.transition(
            session,
            tenant_id=tenant_id,
            run_id=run_id1,
            to_state="failed",
            remote_outcome="known",
        )

    # Retry creates attempt 2
    with SessionLocal() as session:
        attempt2 = RunService.retry_new_attempt(session, tenant_id=tenant_id, task_id=task_id)
        assert attempt2.attempt == 2
        assert attempt2.state == "queued"
        assert attempt2.run_id != run_id1

    # Verify database state
    with SessionLocal() as session:
        all_runs = session.execute(
            sa.select(runs)
            .where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task_id,
            )
            .order_by(runs.c.attempt.asc())
        ).mappings().all()

        assert len(all_runs) == 2
        assert all_runs[0]["attempt"] == 1 and all_runs[0]["state"] == "failed"
        assert all_runs[1]["attempt"] == 2 and all_runs[1]["state"] == "queued"

        # Outbox event emitted for attempt 2
        events = session.execute(
            sa.select(outbox).where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == f"run:{attempt2.run_id}",
            )
        ).mappings().all()
        assert len(events) == 1
        assert events[0]["payload"]["attempt"] == 2
        assert events[0]["payload"]["payload"]["reason_code"] == "retry_new_attempt"
