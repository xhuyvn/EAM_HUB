"""Hypothesis property-based tests for run state machine transitions.

Properties:
- Random transition sequences are applied to a run.
- Every persisted history entry is legal per eap.contracts.rules.ALLOWED.
- Every committed state change has exactly ONE outbox row.
- Rejected transitions leave zero rows (state, history, and outbox remain intact).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from eap.contracts.models.task_request import TaskRequest
from eap.contracts.rules import ALLOWED, TERMINAL
from eap.db import outbox, run_attempt_history, runs
from eap.run.service import (
    IllegalRunTransitionError,
    LateTerminalEventError,
    RunService,
)
from eap.task.service import TaskService

ALL_RUN_STATES = [
    "queued",
    "dispatching",
    "running",
    "waiting_approval",
    "cancelling",
    "cancelled",
    "succeeded",
    "failed",
    "timed_out",
    "unknown",
]


@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(target_states=st.lists(st.sampled_from(ALL_RUN_STATES), min_size=1, max_size=10))
def test_property_random_run_transitions(db_engine: Engine, target_states: list[str]) -> None:
    """Property test verifying legality of all committed transitions and outbox 1:1 mapping."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)
    tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"
    client_key = f"idemp-{uuid.uuid4().hex[:12]}"

    with SessionLocal() as session:
        req = TaskRequest(
            contract_version="1.0",
            agent_id="agent-ppm",
            task_type="code.review",
            objective="Hypothesis property transition test",
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

    current_state = "queued"
    successful_transitions = 0

    with SessionLocal() as session:
        for target in target_states:
            is_legal = (current_state, target) in ALLOWED and current_state not in TERMINAL
            hist_count_before = (
                session.scalar(
                    sa.select(sa.func.count()).select_from(run_attempt_history).where(
                        run_attempt_history.c.tenant_id == tenant_id,
                        run_attempt_history.c.run_id == run_id,
                    )
                )
                or 0
            )
            outbox_count_before = (
                session.scalar(
                    sa.select(sa.func.count()).select_from(outbox).where(
                        outbox.c.tenant_id == tenant_id,
                        outbox.c.stream_id == f"run:{run_id}",
                    )
                )
                or 0
            )

            if is_legal:
                record = RunService.transition(
                    session,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state=target,
                )
                assert record.state == target
                current_state = target
                successful_transitions += 1

                # Verify exactly 1 history row added
                hist_count_after = session.scalar(
                    sa.select(sa.func.count()).select_from(run_attempt_history).where(
                        run_attempt_history.c.tenant_id == tenant_id,
                        run_attempt_history.c.run_id == run_id,
                    )
                )
                assert hist_count_after == hist_count_before + 1

                # Verify exactly 1 outbox event added
                outbox_count_after = session.scalar(
                    sa.select(sa.func.count()).select_from(outbox).where(
                        outbox.c.tenant_id == tenant_id,
                        outbox.c.stream_id == f"run:{run_id}",
                    )
                )
                assert outbox_count_after == outbox_count_before + 1
            else:
                expected_exc = LateTerminalEventError if current_state in TERMINAL else IllegalRunTransitionError
                with pytest.raises(expected_exc):
                    RunService.transition(
                        session,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        to_state=target,
                    )

                # Rejected transitions leave ZERO rows in history and outbox
                hist_count_after = session.scalar(
                    sa.select(sa.func.count()).select_from(run_attempt_history).where(
                        run_attempt_history.c.tenant_id == tenant_id,
                        run_attempt_history.c.run_id == run_id,
                    )
                )
                assert hist_count_after == hist_count_before

                outbox_count_after = session.scalar(
                    sa.select(sa.func.count()).select_from(outbox).where(
                        outbox.c.tenant_id == tenant_id,
                        outbox.c.stream_id == f"run:{run_id}",
                    )
                )
                assert outbox_count_after == outbox_count_before

    # Verify global invariants on the persisted records
    with SessionLocal() as session:
        # 1. Final run state matches current_state
        final_run = RunService.get(session, tenant_id=tenant_id, run_id=run_id)
        assert final_run is not None
        assert final_run.state == current_state

        # 2. History rows count matches 1 (creation) + successful_transitions
        all_hist = session.execute(
            sa.select(run_attempt_history)
            .where(
                run_attempt_history.c.tenant_id == tenant_id,
                run_attempt_history.c.run_id == run_id,
            )
            .order_by(run_attempt_history.c.recorded_at.asc(), run_attempt_history.c.id.asc())
        ).mappings().all()
        assert len(all_hist) == 1 + successful_transitions

        # 3. Every persisted transition is strictly legal per ALLOWED
        for i in range(1, len(all_hist)):
            from_st = all_hist[i]["from_state"]
            to_st = all_hist[i]["to_state"]
            assert (from_st, to_st) in ALLOWED, f"Illegal transition persisted in history: {from_st} -> {to_st}"

        # 4. Outbox events count matches history count exactly
        all_outbox = session.execute(
            sa.select(outbox).where(
                outbox.c.tenant_id == tenant_id,
                outbox.c.stream_id == f"run:{run_id}",
            )
        ).mappings().all()
        assert len(all_outbox) == len(all_hist)
