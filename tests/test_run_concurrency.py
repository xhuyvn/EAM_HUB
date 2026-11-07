"""Test run transition concurrency.

Requirement:
Two transitions racing on one run (two connections, barrier) -> exactly one wins,
the other raises 409 (IllegalRunTransitionError or LateTerminalEventError); history has no gap.
"""

from __future__ import annotations

import concurrent.futures
import threading
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.contracts.models.task_request import TaskRequest
from eap.db import outbox, run_attempt_history, runs
from eap.run.service import (
    IllegalRunTransitionError,
    LateTerminalEventError,
    RunService,
)
from eap.task.service import TaskService


def test_concurrent_transitions_race_on_single_run(db_engine: Engine) -> None:
    """Two concurrent transitions racing on a run serialize cleanly: one wins, one gets 409."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    # Setup: create task and run in 'queued'
    with SessionLocal() as session:
        req = TaskRequest(
            contract_version="1.0",
            agent_id="agent-ppm",
            task_type="code.review",
            objective="Test concurrency",
            input_refs=["in-1"],
            resource_refs=["res-1"],
            idempotency_key=client_key,
        )
        task = TaskService.create(session, tenant_id=tenant_id, request=req)
        run_row = session.execute(
            sa.select(runs).where(
                runs.c.tenant_id == tenant_id,
                runs.c.task_id == task.id,
                runs.c.attempt == 1,
            )
        ).mappings().one()
        run_id = run_row["run_id"]

    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def worker_transition(to_state: str) -> None:
        result: dict[str, Any] = {"target": to_state, "success": False, "error": None}
        try:
            with SessionLocal() as session:
                barrier.wait(timeout=5)
                rec = RunService.transition(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state=to_state,
                    expected_from="queued",
                )
                result["success"] = True
                result["record"] = rec
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc
        results.append(result)

    # Worker 1 wants 'dispatching', Worker 2 wants 'cancelled'
    # Both are valid transitions from 'queued'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(worker_transition, "dispatching")
        f2 = executor.submit(worker_transition, "cancelled")
        concurrent.futures.wait([f1, f2], timeout=10)

    assert len(results) == 2
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    assert len(successes) == 1, f"Expected exactly 1 success, got: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failure, got: {results}"

    winner = successes[0]
    loser = failures[0]

    # Loser must have failed with 409 error (IllegalRunTransitionError or LateTerminalEventError)
    assert isinstance(loser["error"], (IllegalRunTransitionError, LateTerminalEventError))

    # Verify database consistency
    with SessionLocal() as session:
        current_run = RunService.get(session, tenant_id=tenant_id, run_id=run_id)
        assert current_run is not None
        assert current_run.state == winner["target"]

        # History has exactly 2 rows (creation + winner transition), with no gaps
        hist = session.execute(
            sa.select(run_attempt_history)
            .where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
            .order_by(run_attempt_history.c.recorded_at.asc())
        ).mappings().all()

        assert len(hist) == 2
        assert hist[0]["from_state"] == "queued" and hist[0]["to_state"] == "queued"
        assert hist[1]["from_state"] == "queued" and hist[1]["to_state"] == winner["target"]

        # Exactly 2 outbox events (task creation + winning transition)
        outbox_events = session.execute(
            sa.select(outbox).where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == f"run:{run_id}",
            )
        ).mappings().all()
        assert len(outbox_events) == 2
