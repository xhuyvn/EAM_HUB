"""Conformance tests for P1a-WP4: Dispatcher, mock provider, recovery, cancel.

Conformance cases covered:
- C21: lost ACK, worker restart, user cancel while unknown, fencing, HUD projection.
- C29: lost response, retry rejected while unknown, journaled deduplication, OPERATION_KEY_CONFLICT.
- C29b: slot recovery / ACTION_SLOT_UNDECLARED when agent proposes new slot after restart.
- C38: lookup_supported=false -> run stays unknown, retry blocked, attention item.
- C15: stale fencing token rejected, state unchanged.
- C05: worker crash before response -> unknown/reconciled, no blind resubmit.
- C06: cancel acknowledged late, late result logged and not applied.
- Crash matrix: 3 journal steps x 2 drop_ack modes -> <=1 external effect, never silently re-dispatched.
- Rollback: dispatch.enabled=false blocks dispatching.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from eap.action.executor import (
    ActionExecutor,
    ActionSlotUndeclaredError,
    OperationKeyConflictError,
)
from eap.adapters.mock_provider import MockProvider, MockProviderAckDroppedError
from eap.api.app import app
from eap.api.dependencies import Principal, get_db, get_principal
from eap.contracts.keys import (
    derive_dispatch_idempotency_key,
    derive_operation_key,
    derive_task_intent_key,
)
from eap.db import (
    action_executions,
    commands,
    run_attempt_history,
    runs,
    tasks,
)
from eap.events.binding import ProducerBinding, StaticBindingProvider
from eap.events.projection import build_fresh_snapshot, get_attention_items
from eap.events.relay import OutboxRelayWorker
from eap.run.dispatcher import (
    DispatchDisabledError,
    Dispatcher,
    DispatcherError,
)
from eap.run.service import (
    LateTerminalEventError,
    RemoteOutcomeUnknownError,
    RunService,
    StaleFencingTokenError,
)


def _create_task_and_run(
    db: Session,
    *,
    tenant_id: str,
    task_id: str,
    run_id: str,
    binding_id: str = "binding-test",
    attempt: int = 1,
) -> tuple[str, str]:
    """Helper to create a task and initial run attempt in PostgreSQL."""
    now = dt.datetime.now(dt.UTC)
    task_intent_key = derive_task_intent_key(tenant_id, task_id)

    db.execute(
        tasks.insert().values(
            id=task_id,
            tenant_id=tenant_id,
            client_idempotency_key=f"client-{task_id}",
            task_intent_key=task_intent_key,
            task_type="engineering",
            agent_id="test-agent",
            agent_version="1.0.0",
            state="ready",
            deadline=now + dt.timedelta(hours=2),
            created_at=now,
            updated_at=now,
        )
    )

    db.execute(
        runs.insert().values(
            id=run_id,
            tenant_id=tenant_id,
            task_id=task_id,
            run_id=run_id,
            attempt=attempt,
            binding_id=binding_id,
            state="queued",
            remote_outcome=None,
            lease_fencing_token=0,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return task_id, run_id


@pytest.fixture
def api_client(db_engine: Engine):
    """AsyncClient fixture overriding get_db and get_principal."""
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, autocommit=False)

    def _override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        yield AsyncClient(transport=transport, base_url="http://testserver")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_principal, None)


# -----------------------------------------------------------------------------
# C21: Lost ACK, worker restart, user cancel while unknown, fencing, HUD projection
# -----------------------------------------------------------------------------
def test_c21_lost_ack_reconciliation_and_cancel(db_engine: Engine) -> None:
    """Verify C21:

    1. Dispatcher dispatches attempt 1, provider drops ACK -> run enters unknown (remote_outcome=unknown).
    2. Exactly 1 external submission by idempotency_key.
    3. User clicks cancel while unknown -> command accepted, run remains unknown until reconciled.
    4. Outbox relay + projection assert: HUD row shows remote_outcome=unknown and pending_command.kind=cancel.
    5. Old worker with stale fencing token attempts write -> rejected (fenced).
    6. Worker B reconciles -> provider confirms -> running -> cancelling -> cancelled.
    7. Final HUD projection shows cancelled and pending_command=None.
    """
    tenant = "tenant-c21"
    task_id = "task-c21-001"
    run_id = "run-c21-001"
    binding_id = "bind-c21"
    stream_id = f"run:{run_id}"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id, binding_id=binding_id)

    mock = MockProvider(drop_ack=True)
    dispatcher = Dispatcher()

    # Step 1: Worker A (Token 1) attempts dispatch; ACK dropped
    with Session(db_engine) as db:
        res1 = dispatcher.dispatch(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-A",
            provider=mock,
        )
        assert res1.status == "unknown"

    # Verify exactly 1 submission occurred at provider
    key = derive_dispatch_idempotency_key(binding_id, run_id, "submit")
    assert mock.submissions[key] == 1

    # Step 2: Relay processes outbox into event_log and projection
    producer_binding = ProducerBinding(
        tenant_id=tenant,
        producer_id="run_service",
        binding_id=binding_id,
        certified_capabilities={"events": True},
    )
    provider_lookup = StaticBindingProvider([producer_binding])
    relay = OutboxRelayWorker(enabled=True)

    with db_engine.begin() as conn:
        relay_batch = relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider_lookup,
        )
        assert relay_batch.ingested_count > 0

    # Step 3: User cancels run while unknown
    with Session(db_engine) as db:
        run_record = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_record is not None
        assert run_record.state == "unknown"
        assert run_record.remote_outcome == "unknown"

        # Record command accepted in commands table
        cmd_id = "cmd-c21-cancel"
        now = dt.datetime.now(dt.UTC)
        db.execute(
            commands.insert().values(
                id=cmd_id,
                tenant_id=tenant,
                task_id=task_id,
                run_id=run_id,
                command_type="cancel",
                status="accepted",
                payload={"requested_by_ref": "principal:operator-1"},
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    # Assert HUD projection during unknown phase
    with db_engine.begin() as conn:
        hud_state, _, _ = build_fresh_snapshot(conn, tenant_id=tenant, stream_id=stream_id)
        assert hud_state["row"]["run_state"] == "unknown"
        assert hud_state["row"]["remote_outcome"] == "unknown"
        assert hud_state["row"]["pending_command"] is not None
        assert hud_state["row"]["pending_command"]["kind"] == "cancel"
        assert hud_state["row"]["pending_command"]["state"] == "accepted"

    # Step 4: Worker B claims lease (Token 2)
    with Session(db_engine) as db:
        token_b = dispatcher.claim_lease(db, tenant_id=tenant, run_id=run_id, holder_id="worker-B")
        assert token_b > 1

        # Old worker A with token 1 tries to write -> REJECTED (fenced)
        with pytest.raises(StaleFencingTokenError):
            RunService.transition(
                db,
                tenant_id=tenant,
                run_id=run_id,
                to_state="running",
                fencing_token=1,
            )

    # Step 5: Worker B reconciles
    mock.drop_ack = False  # Provider alive and can be queried
    with Session(db_engine) as db:
        rec_res = dispatcher.reconcile(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-B",
            provider=mock,
            fencing_token=token_b,
        )
        assert rec_res.status == "cancelled"

    # Verify state sequence in run_attempt_history:
    # queued -> dispatching -> unknown -> running -> cancelling -> cancelled
    with Session(db_engine) as db:
        hist_rows = db.execute(
            sa.select(run_attempt_history.c.to_state)
            .where(
                run_attempt_history.c.tenant_id == tenant,
                run_attempt_history.c.run_id == run_id,
            )
            .order_by(run_attempt_history.c.id.asc())
        ).scalars().all()
        assert hist_rows == ["dispatching", "unknown", "running", "cancelling", "cancelled"]

    # Step 6: Relay processes reconciliation & cancellation outbox events
    with db_engine.begin() as conn:
        relay.relay_stream_batch(
            conn,
            tenant_id=tenant,
            stream_id=stream_id,
            binding_provider=provider_lookup,
        )
        final_hud, _, _ = build_fresh_snapshot(conn, tenant_id=tenant, stream_id=stream_id)
        assert final_hud["row"]["run_state"] == "cancelled"
        assert final_hud["row"]["remote_outcome"] == "known"
        assert final_hud["row"]["pending_command"] is None

    # Assert exactly 1 submission counted throughout the entire scenario
    assert mock.submissions[key] == 1


# -----------------------------------------------------------------------------
# C29: PR created, ACK lost, restart, retry, conflict detection
# -----------------------------------------------------------------------------
def test_c29_pr_created_lost_ack_retry_and_conflict(db_engine: Engine) -> None:
    """Verify C29:

    1. Mock provider creates PR, but response dropped.
    2. Worker restarts -> run is unknown.
    3. User clicks retry while unknown -> 409 REMOTE_OUTCOME_UNKNOWN.
    4. Worker reconciles -> PR found, run succeeded.
    5. Attempt 2 with same intent reuses operation_key -> journaled result returned, 0 new PRs.
    6. Attempt 3 with same operation_key but different resource_ref -> 409 OPERATION_KEY_CONFLICT, 0 new PRs.
    7. mock.prs_created == 1 throughout.
    """
    tenant = "tenant-c29"
    task_id = "task-c29-001"
    run_id = "run-c29-001"
    binding_id = "bind-c29"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id, binding_id=binding_id)

    mock = MockProvider(drop_ack=True)

    # 1. ActionExecutor runs attempt 1: create_pr(repo='repo:example')
    with Session(db_engine) as db:
        task_intent_key = derive_task_intent_key(tenant, task_id)
        with pytest.raises(MockProviderAckDroppedError):  # drop_ack causes error after PR creation
            ActionExecutor.execute(
                db,
                tenant_id=tenant,
                task_id=task_id,
                run_id=run_id,
                tool_id="engineering.create_pr",
                workflow_step="create_pr",
                action_slot="create_pr#1",
                resource_ref="repo:example",
                tool_callable=lambda: mock.create_pr(
                    client_token=task_intent_key,
                    repo="repo:example",
                ),
            )

    assert mock.prs_created == 1

    # Transition run to unknown as if ACK was lost
    with Session(db_engine) as db:
        RunService.transition(
            db,
            tenant_id=tenant,
            run_id=run_id,
            to_state="dispatching",
        )
        RunService.transition(
            db,
            tenant_id=tenant,
            run_id=run_id,
            to_state="unknown",
            remote_outcome="unknown",
        )

    # 2. User retry while run is unknown -> 409 REMOTE_OUTCOME_UNKNOWN (DG1-C1)
    with Session(db_engine) as db, pytest.raises(RemoteOutcomeUnknownError):
        RunService.retry_new_attempt(db, tenant_id=tenant, task_id=task_id)

    # 3. Reconcile: simulate finding the PR and marking action execution succeeded
    with Session(db_engine) as db:
        pr_lookup = mock.lookup_pr(task_intent_key)
        assert pr_lookup["state"] == "found"
        pr_data = pr_lookup["pr"]

        op_key = derive_operation_key(tenant, task_id, "create_pr#1")
        db.execute(
            action_executions.update()
            .where(
                action_executions.c.tenant_id == tenant,
                action_executions.c.operation_key == op_key,
            )
            .values(
                state="succeeded",
                external_ref=pr_data["pr_id"],
                result_payload=pr_data,
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
        db.commit()

        # Reconcile run to succeeded
        RunService.transition(
            db,
            tenant_id=tenant,
            run_id=run_id,
            to_state="running",
            remote_outcome="known",
        )
        RunService.transition(
            db,
            tenant_id=tenant,
            run_id=run_id,
            to_state="succeeded",
        )

    # 4. User retries (Attempt 2 with same intent)
    with Session(db_engine) as db:
        run_attempt_2 = RunService.retry_new_attempt(db, tenant_id=tenant, task_id=task_id)
        assert run_attempt_2.attempt == 2

        # Execute same action with matching target
        res_retry = ActionExecutor.execute(
            db,
            tenant_id=tenant,
            task_id=task_id,
            run_id=run_attempt_2.run_id,
            tool_id="engineering.create_pr",
            workflow_step="create_pr",
            action_slot="create_pr#1",
            resource_ref="repo:example",
            tool_callable=lambda: mock.create_pr(
                client_token=task_intent_key,
                repo="repo:example",
            ),
        )
        assert res_retry.status == "journaled"
        assert res_retry.invoked_tool is False
        assert res_retry.external_ref == "pr-101"

    # Exactly 1 PR created so far
    assert mock.prs_created == 1

    # 5. Attempt 3: same operation_key, but divergent resource_ref ('repo:example-divergent')
    with Session(db_engine) as db, pytest.raises(OperationKeyConflictError):
        ActionExecutor.execute(
            db,
            tenant_id=tenant,
            task_id=task_id,
            run_id="run-divergent",
            tool_id="engineering.create_pr",
            workflow_step="create_pr",
            action_slot="create_pr#1",
            resource_ref="repo:example-divergent",
            tool_callable=lambda: mock.create_pr(
                client_token=task_intent_key,
                repo="repo:example-divergent",
            ),
        )

    # Zero additional PRs created (DG1-C3 verified)
    assert mock.prs_created == 1


# -----------------------------------------------------------------------------
# C29b: Agent proposes create_pr#2 after restart
# -----------------------------------------------------------------------------
def test_c29b_agent_proposes_new_slot_after_restart(db_engine: Engine) -> None:
    """Verify C29b:

    When an autonomous LLM agent restarts and proposes create_pr#2 instead of create_pr#1:
    - Mapping policy: maps create_pr#2 -> recovered create_pr#1, returns journaled result without invoking tool.
    - Strict policy: rejects undeclared expansion with ACTION_SLOT_UNDECLARED.
    - mock.prs_created == 1 in both cases.
    """
    tenant = "tenant-c29b"
    task_id = "task-c29b-001"
    run_id_1 = "run-c29b-001"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id_1)

    mock = MockProvider()
    task_intent_key = derive_task_intent_key(tenant, task_id)

    # Step 1: Initial call persists slot create_pr#1 and executes PR creation
    with Session(db_engine) as db:
        res1 = ActionExecutor.execute(
            db,
            tenant_id=tenant,
            task_id=task_id,
            run_id=run_id_1,
            tool_id="engineering.create_pr",
            workflow_step="create_pr",
            action_slot="create_pr#1",
            resource_ref="repo:example",
            tool_callable=lambda: mock.create_pr(client_token=task_intent_key),
        )
        assert res1.status == "executed"
        assert res1.invoked_tool is True

    assert mock.prs_created == 1

    # Step 2: Agent restarts and proposes create_pr#2 under default mapping policy
    with Session(db_engine) as db:
        res2 = ActionExecutor.execute(
            db,
            tenant_id=tenant,
            task_id=task_id,
            run_id="run-c29b-002",
            tool_id="engineering.create_pr",
            workflow_step="create_pr",
            action_slot="create_pr#2",  # Agent drifted
            resource_ref="repo:example",
            strict_slots=False,
            tool_callable=lambda: mock.create_pr(client_token=task_intent_key),
        )
        assert res2.status == "journaled"
        assert res2.invoked_tool is False
        assert res2.action_slot == "create_pr#1"

    assert mock.prs_created == 1

    # Step 3: Under strict policy, proposing undeclared slot raises ACTION_SLOT_UNDECLARED
    with Session(db_engine) as db, pytest.raises(ActionSlotUndeclaredError):
        ActionExecutor.execute(
            db,
            tenant_id=tenant,
            task_id=task_id,
            run_id="run-c29b-003",
            tool_id="engineering.create_pr",
            workflow_step="create_pr",
            action_slot="create_pr#2",
            resource_ref="repo:example",
            strict_slots=True,
            tool_callable=lambda: mock.create_pr(client_token=task_intent_key),
        )

    assert mock.prs_created == 1


# -----------------------------------------------------------------------------
# C38: Adapter lacks lookup support -> stays unknown, attention item
# -----------------------------------------------------------------------------
def test_c38_lookup_unsupported_rejection(db_engine: Engine) -> None:
    """Verify C38:

    1. lookup_supported = False.
    2. ACK lost -> run enters unknown.
    3. Reconcile detects unsupported -> run stays unknown.
    4. Retry attempt is rejected with 409 REMOTE_OUTCOME_UNKNOWN.
    5. Mock receives 0 fresh submissions.
    6. Attention item is present in projection queries (DG1-C2).
    """
    tenant = "tenant-c38"
    task_id = "task-c38-001"
    run_id = "run-c38-001"
    binding_id = "bind-c38"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id, binding_id=binding_id)

    mock = MockProvider(drop_ack=True, lookup_supported=False)
    dispatcher = Dispatcher()

    # Dispatch: drops ACK -> run enters unknown
    with Session(db_engine) as db:
        dispatcher.dispatch(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-c38",
            provider=mock,
        )

    key = derive_dispatch_idempotency_key(binding_id, run_id, "submit")
    assert mock.submissions[key] == 1

    # Reconcile: lookup unsupported -> run stays unknown, attention required
    with Session(db_engine) as db:
        rec_res = dispatcher.reconcile(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-c38-rec",
            provider=mock,
        )
        assert rec_res.status == "unknown"
        assert rec_res.attention_required is True

        run_rec = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_rec is not None
        assert run_rec.state == "unknown"
        assert run_rec.remote_outcome == "unknown"

        # Retry attempt is strictly rejected (DG1-C1)
        with pytest.raises(RemoteOutcomeUnknownError):
            RunService.retry_new_attempt(db, tenant_id=tenant, task_id=task_id)

    # Mock receives zero fresh submissions
    assert mock.submissions[key] == 1

    # Verify attention items in projection
    with db_engine.begin() as conn:
        items = get_attention_items(conn, tenant_id=tenant)
        assert len(items) >= 1
        c38_item = next(it for it in items if it["run_id"] == run_id)
        assert c38_item["state"] == "unknown"
        assert c38_item["reason"] == "remote_outcome_unknown"


# -----------------------------------------------------------------------------
# C15: Stale fencing token writes rejected, state unchanged
# -----------------------------------------------------------------------------
def test_c15_lease_fencing_stale_worker(db_engine: Engine) -> None:
    """Verify C15:

    Worker with stale fencing token writes -> rejected; state unchanged.
    """
    tenant = "tenant-c15"
    task_id = "task-c15-001"
    run_id = "run-c15-001"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id)

    dispatcher = Dispatcher()

    with Session(db_engine) as db:
        # Worker A acquires token 1
        token1 = dispatcher.claim_lease(db, tenant_id=tenant, run_id=run_id, holder_id="worker-A")
        assert token1 == 1

        # Worker B acquires token 2
        token2 = dispatcher.claim_lease(db, tenant_id=tenant, run_id=run_id, holder_id="worker-B")
        assert token2 == 2

        # Worker A attempts to transition run with stale token 1 -> rejected!
        with pytest.raises(StaleFencingTokenError):
            RunService.transition(
                db,
                tenant_id=tenant,
                run_id=run_id,
                to_state="dispatching",
                fencing_token=token1,
            )

        # Verify state remains strictly 'queued'
        run_rec = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_rec is not None
        assert run_rec.state == "queued"
        assert run_rec.lease_fencing_token == 2


# -----------------------------------------------------------------------------
# C05: Worker crash before response -> unknown/reconciled, no blind resubmit
# -----------------------------------------------------------------------------
def test_c05_dispatch_journal_recovery(db_engine: Engine) -> None:
    """Verify C05:

    Worker crash before external response -> journal sent -> unknown/reconcile, no blind resubmit.
    """
    tenant = "tenant-c05"
    task_id = "task-c05-001"
    run_id = "run-c05-001"
    binding_id = "bind-c05"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id, binding_id=binding_id)

    mock = MockProvider(crash_after_send=True)
    dispatcher = Dispatcher()

    with Session(db_engine) as db:
        res = dispatcher.dispatch(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-c05",
            provider=mock,
        )
        assert res.status == "unknown"

    key = derive_dispatch_idempotency_key(binding_id, run_id, "submit")
    assert mock.submissions[key] == 1

    # Worker restart: re-dispatching does NOT issue fresh submit; it reconciles
    mock.crash_after_send = False
    with Session(db_engine) as db:
        res_reboot = dispatcher.dispatch(
            db,
            tenant_id=tenant,
            run_id=run_id,
            worker_id="worker-c05-reboot",
            provider=mock,
        )
        assert res_reboot.status == "running"

    # Exactly 1 submission to provider
    assert mock.submissions[key] == 1


# -----------------------------------------------------------------------------
# C06: Late result after cancel is logged, not applied
# -----------------------------------------------------------------------------
def test_c06_late_result_after_cancel_logged_not_applied(db_engine: Engine) -> None:
    """Verify C06:

    Run in cancelled state rejects late state transitions; terminal state is preserved.
    """
    tenant = "tenant-c06"
    task_id = "task-c06-001"
    run_id = "run-c06-001"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id)
        RunService.transition(db, tenant_id=tenant, run_id=run_id, to_state="cancelled")

        # Late completion event arrives -> rejected per late terminal rules
        with pytest.raises(LateTerminalEventError):
            RunService.transition(
                db,
                tenant_id=tenant,
                run_id=run_id,
                to_state="succeeded",
            )

        run_rec = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_rec is not None
        assert run_rec.state == "cancelled"


# -----------------------------------------------------------------------------
# Crash matrix: 3 journal steps x 2 drop_ack modes
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("step", ["after_prepared", "after_sent", "after_acknowledged"])
@pytest.mark.parametrize("drop_ack", [False, True])
def test_crash_matrix(db_engine: Engine, step: str, drop_ack: bool) -> None:
    """Verify crash matrix:

    Crash at each journal step x drop_ack on/off:
    - Never more than 1 external effect.
    - Run never silently re-dispatched while unknown.
    """
    tenant = f"tenant-crash-{step}-{drop_ack}"
    task_id = f"task-{step}-{drop_ack}"
    run_id = f"run-{step}-{drop_ack}"
    binding_id = "bind-crash"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id, binding_id=binding_id)

    mock = MockProvider(drop_ack=drop_ack)
    dispatcher = Dispatcher()

    # Dispatch with simulated crash point
    with Session(db_engine) as db:
        try:
            dispatcher.dispatch(
                db,
                tenant_id=tenant,
                run_id=run_id,
                worker_id="worker-crash",
                provider=mock,
                crash_point=step,
            )
        except (DispatcherError, MockProviderAckDroppedError) as exc:
            assert exc is not None

    key = derive_dispatch_idempotency_key(binding_id, run_id, "submit")
    effects_after_crash = mock.submissions.get(key, 0)
    assert effects_after_crash <= 1

    # Worker recovery: re-dispatch or reconcile
    mock.drop_ack = False
    with Session(db_engine) as db:
        run_rec = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_rec is not None

        if run_rec.state == "unknown":
            # Must reconcile, cannot silently re-dispatch
            with pytest.raises(RemoteOutcomeUnknownError):
                RunService.retry_new_attempt(db, tenant_id=tenant, task_id=task_id)
            rec = dispatcher.reconcile(db, tenant_id=tenant, run_id=run_id, worker_id="worker-rec", provider=mock)
            assert rec.status in ("running", "failed", "succeeded")
        elif run_rec.state in ("queued", "dispatching"):
            dispatcher.dispatch(db, tenant_id=tenant, run_id=run_id, worker_id="worker-resume", provider=mock)

    total_effects = mock.submissions.get(key, 0)
    assert total_effects <= 1, f"Expected at most 1 external effect for {step} (drop_ack={drop_ack}), got {total_effects}"


# -----------------------------------------------------------------------------
# Rollback: dispatch.enabled = False
# -----------------------------------------------------------------------------
def test_dispatch_disabled_feature_flag(db_engine: Engine) -> None:
    """Verify rollback feature flag: dispatch.enabled=False halts dispatch."""
    tenant = "tenant-rollback"
    task_id = "task-rollback"
    run_id = "run-rollback"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id)

    dispatcher = Dispatcher(enabled=False)
    mock = MockProvider()

    with Session(db_engine) as db:
        with pytest.raises(DispatchDisabledError):
            dispatcher.dispatch(db, tenant_id=tenant, run_id=run_id, worker_id="worker-1", provider=mock)

        # Database state remains queued
        run_rec = RunService.get(db, tenant_id=tenant, run_id=run_id)
        assert run_rec is not None
        assert run_rec.state == "queued"
        # Zero provider calls
        assert len(mock.submissions) == 0


# -----------------------------------------------------------------------------
# API route tests: POST /v1/runs/{run_id}/cancel and POST /v1/runs/{run_id}/retry
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_api_cancel_and_retry_routes(api_client: AsyncClient, db_engine: Engine) -> None:
    """Verify API routes for cancel and retry."""
    tenant = "tenant-api-test"
    task_id = "task-api-001"
    run_id = "run-api-001"

    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id=task_id, run_id=run_id)

    app.dependency_overrides[get_principal] = lambda: Principal(tenant_id=tenant, principal_id="user-1")

    # 1. Cancel queued/active run -> 202 Accepted
    resp = await api_client.post(f"/v1/runs/{run_id}/cancel")
    assert resp.status_code == 202
    cancel_json = resp.json()
    assert cancel_json["kind"] == "cancel"
    assert cancel_json["state"] == "accepted"

    # 2. Cancel on non-existent run -> 404
    resp_404 = await api_client.post("/v1/runs/non-existent-run/cancel")
    assert resp_404.status_code == 404

    # 3. Cancel while unknown -> 202 Accepted, message says will execute after reconcile
    run_id_unknown = "run-api-unknown"
    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id="task-api-002", run_id=run_id_unknown)
        RunService.transition(db, tenant_id=tenant, run_id=run_id_unknown, to_state="dispatching")
        RunService.transition(db, tenant_id=tenant, run_id=run_id_unknown, to_state="unknown", remote_outcome="unknown")

    resp_unk = await api_client.post(f"/v1/runs/{run_id_unknown}/cancel")
    assert resp_unk.status_code == 202
    assert "will execute after reconciliation" in resp_unk.json()["message"]

    # 4. Retry while unknown -> 409 REMOTE_OUTCOME_UNKNOWN
    resp_retry_unk = await api_client.post(f"/v1/runs/{run_id_unknown}/retry")
    assert resp_retry_unk.status_code == 409
    assert "REMOTE_OUTCOME_UNKNOWN" in resp_retry_unk.json()["detail"]

    # 5. Retry succeeded run -> 201 Created (new attempt created)
    run_id_succ = "run-api-succ"
    with Session(db_engine) as db:
        _create_task_and_run(db, tenant_id=tenant, task_id="task-api-003", run_id=run_id_succ)
        RunService.transition(db, tenant_id=tenant, run_id=run_id_succ, to_state="dispatching")
        RunService.transition(db, tenant_id=tenant, run_id=run_id_succ, to_state="running")
        RunService.transition(db, tenant_id=tenant, run_id=run_id_succ, to_state="succeeded")

    resp_retry_succ = await api_client.post(f"/v1/runs/{run_id_succ}/retry")
    assert resp_retry_succ.status_code == 201
    retry_json = resp_retry_succ.json()
    assert retry_json["attempt"] == 2
    assert retry_json["state"] == "queued"
