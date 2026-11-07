"""Dispatcher, lease management, and dispatch journaling (C05, C06, C15, C21, C38).

Implements:
- Distributed lease with monotonically increasing fencing token per run (leases table).
- Stale fencing token write rejection (C15).
- Dispatch journal in dispatch_intents table: prepared -> sent -> acknowledged | ambiguous.
- Each journal step committed BEFORE the next external call.
- On ambiguous response or drop_ack, run transitions to unknown (remote_outcome=unknown).
- Reconciliation via provider lookup; if lookup_supported is False, run remains unknown (C38, DG1-C2).
- Pending cancel execution post-reconciliation (C21, C06).
- Feature flag dispatch.enabled (rollback).
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.adapters.mock_provider import MockProvider, MockProviderError
from eap.contracts.keys import derive_dispatch_idempotency_key
from eap.db import commands, dispatch_intents, leases, runs, tasks
from eap.run.service import RunService, StaleFencingTokenError


class DispatcherError(Exception):
    """Base error for Dispatcher."""



class DispatchDisabledError(DispatcherError):
    """Raised when dispatching is attempted while dispatch.enabled is false (rollback)."""



class StaleLeaseError(DispatcherError):
    """Raised when an operation is attempted with an expired or stolen lease."""



@dataclass(frozen=True)
class DispatchResult:
    status: str  # "running", "unknown", "failed", "cancelled", "journaled"
    execution_ref: str | None = None
    idempotency_key: str | None = None
    fencing_token: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class ReconcileResult:
    status: str  # "running", "cancelled", "failed", "unknown"
    execution_ref: str | None = None
    fencing_token: int | None = None
    attention_required: bool = False
    message: str | None = None


class Dispatcher:
    """Run dispatcher with durable journaling, leases, and reconciliation."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        if enabled is not None:
            self.enabled = enabled
        else:
            # Check environment variable first; fallback to True
            env_val = os.environ.get("DISPATCH_ENABLED")
            if env_val is not None:
                self.enabled = env_val.strip().lower() in ("true", "1", "yes")
            else:
                self.enabled = True

    def claim_lease(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str,
        holder_id: str,
        ttl_seconds: int = 60,
    ) -> int:
        """Claim or advance an execution lease on a run.

        Returns the new monotonically increasing fencing token.
        Updates runs.lease_fencing_token in the same transaction.
        """
        now = dt.datetime.now(dt.UTC)
        expires_at = now + dt.timedelta(seconds=ttl_seconds)

        stmt = (
            sa.select(leases)
            .where(
                leases.c.tenant_id == tenant_id,
                leases.c.resource_type == "run",
                leases.c.resource_id == run_id,
            )
            .with_for_update()
        )
        existing_lease = db.execute(stmt).mappings().one_or_none()

        if existing_lease is None:
            new_fencing_token = 1
            db.execute(
                leases.insert().values(
                    tenant_id=tenant_id,
                    resource_type="run",
                    resource_id=run_id,
                    fencing_token=new_fencing_token,
                    holder_id=holder_id,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            new_fencing_token = existing_lease["fencing_token"] + 1
            db.execute(
                leases.update()
                .where(
                    leases.c.tenant_id == tenant_id,
                    leases.c.resource_type == "run",
                    leases.c.resource_id == run_id,
                )
                .values(
                    fencing_token=new_fencing_token,
                    holder_id=holder_id,
                    expires_at=expires_at,
                    updated_at=now,
                )
            )

        # Update run row lease_fencing_token
        db.execute(
            runs.update()
            .where(
                runs.c.tenant_id == tenant_id,
                runs.c.run_id == run_id,
            )
            .values(
                lease_fencing_token=new_fencing_token,
                updated_at=now,
            )
        )
        db.commit()
        return new_fencing_token

    def verify_fencing(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str,
        fencing_token: int,
    ) -> None:
        """Verify that worker's fencing token is not stale.

        Raises StaleFencingTokenError if token is strictly less than run's lease_fencing_token.
        """
        stmt = sa.select(runs.c.lease_fencing_token).where(
            runs.c.tenant_id == tenant_id,
            runs.c.run_id == run_id,
        )
        current = db.scalar(stmt)
        if current is not None and fencing_token < current:
            raise StaleFencingTokenError(
                f"STALE_FENCING_TOKEN: Worker token {fencing_token} is strictly less than current run lease fencing token {current}"
            )

    def dispatch(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str,
        worker_id: str,
        provider: MockProvider,
        fencing_token: int | None = None,
        task_envelope: Mapping[str, Any] | None = None,
        crash_point: str | None = None,  # For crash matrix: "after_prepared", "after_sent", "after_acknowledged"
    ) -> DispatchResult:
        """Execute the dispatch lifecycle for a queued or dispatching run.

        Journal steps committed to dispatch_intents:
          1. prepared -> committed BEFORE next step
          2. sent -> committed BEFORE external provider call
          3. acknowledged | ambiguous -> committed
        """
        # 1. Feature flag check (rollback mechanism)
        if not self.enabled:
            raise DispatchDisabledError("dispatch.enabled is false; dispatching aborted")

        # 2. Acquire or verify lease fencing token
        if fencing_token is None:
            fencing_token = self.claim_lease(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                holder_id=worker_id,
            )
        else:
            self.verify_fencing(db, tenant_id=tenant_id, run_id=run_id, fencing_token=fencing_token)

        # 3. Retrieve run record
        run_record = RunService.get(db, tenant_id=tenant_id, run_id=run_id)
        if run_record is None:
            raise DispatcherError(f"Run {run_id} not found in tenant {tenant_id}")

        # If run is queued, transition to dispatching
        if run_record.state == "queued":
            run_record = RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="dispatching",
                fencing_token=fencing_token,
            )

        # Fetch task to get task_intent_key (client request token)
        task_stmt = sa.select(tasks.c.task_intent_key).where(
            tasks.c.tenant_id == tenant_id,
            tasks.c.id == run_record.task_id,
        )
        task_intent_key = db.scalar(task_stmt) or ""

        # Derive stable idempotency key for dispatch
        idempotency_key = derive_dispatch_idempotency_key(
            run_record.binding_id,
            run_record.run_id,
            "submit",
        )

        now = dt.datetime.now(dt.UTC)

        # 4. Check if a dispatch intent already exists
        intent_stmt = (
            sa.select(dispatch_intents)
            .where(
                dispatch_intents.c.tenant_id == tenant_id,
                dispatch_intents.c.binding_id == run_record.binding_id,
                dispatch_intents.c.run_id == run_record.run_id,
                dispatch_intents.c.operation == "submit",
            )
            .with_for_update()
        )
        intent_row = db.execute(intent_stmt).mappings().one_or_none()

        if intent_row is None:
            # Step 1: Journal 'prepared' committed BEFORE next external call
            db.execute(
                dispatch_intents.insert().values(
                    tenant_id=tenant_id,
                    binding_id=run_record.binding_id,
                    run_id=run_record.run_id,
                    operation="submit",
                    idempotency_key=idempotency_key,
                    state="prepared",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()

            if crash_point == "after_prepared":
                raise DispatcherError("CRASH_POINT: after_prepared")

            # Step 2: Journal 'sent' committed BEFORE external provider call
            db.execute(
                dispatch_intents.update()
                .where(
                    dispatch_intents.c.tenant_id == tenant_id,
                    dispatch_intents.c.binding_id == run_record.binding_id,
                    dispatch_intents.c.run_id == run_record.run_id,
                    dispatch_intents.c.operation == "submit",
                )
                .values(
                    state="sent",
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()
        elif intent_row["state"] == "prepared":
            if crash_point == "after_prepared":
                raise DispatcherError("CRASH_POINT: after_prepared")

            # Advance from prepared to sent
            db.execute(
                dispatch_intents.update()
                .where(
                    dispatch_intents.c.tenant_id == tenant_id,
                    dispatch_intents.c.binding_id == run_record.binding_id,
                    dispatch_intents.c.run_id == run_record.run_id,
                    dispatch_intents.c.operation == "submit",
                )
                .values(
                    state="sent",
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()
        elif intent_row["state"] == "acknowledged":
            # Already acknowledged: ensure run is running or beyond
            return DispatchResult(
                status="journaled",
                execution_ref=intent_row["execution_ref"],
                idempotency_key=idempotency_key,
                fencing_token=fencing_token,
            )
        elif intent_row["state"] in ("sent", "ambiguous"):
            # Already in flight or ambiguous: do not resubmit; proceed to reconciliation
            rec_res = self.reconcile(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                worker_id=worker_id,
                provider=provider,
                fencing_token=fencing_token,
            )
            return DispatchResult(
                status=rec_res.status,
                execution_ref=rec_res.execution_ref,
                fencing_token=rec_res.fencing_token,
                error=rec_res.message,
            )

        if crash_point == "after_sent":
            raise DispatcherError("CRASH_POINT: after_sent")

        # Step 3: Call external provider
        try:
            submit_result = provider.submit(
                task_envelope=task_envelope,
                idempotency_key=idempotency_key,
                client_token=task_intent_key,
            )
            execution_ref = submit_result.get("execution_ref", f"mock-{idempotency_key[:8]}")

            # Step 4a: Journal 'acknowledged' committed
            db.execute(
                dispatch_intents.update()
                .where(
                    dispatch_intents.c.tenant_id == tenant_id,
                    dispatch_intents.c.binding_id == run_record.binding_id,
                    dispatch_intents.c.run_id == run_record.run_id,
                    dispatch_intents.c.operation == "submit",
                )
                .values(
                    state="acknowledged",
                    execution_ref=execution_ref,
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )

            # Transition run to running
            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="running",
                remote_outcome="known",
                fencing_token=fencing_token,
            )

            if crash_point == "after_acknowledged":
                raise DispatcherError("CRASH_POINT: after_acknowledged")

            return DispatchResult(
                status="running",
                execution_ref=execution_ref,
                idempotency_key=idempotency_key,
                fencing_token=fencing_token,
            )

        except (MockProviderError, OSError, TimeoutError) as exc:
            # Ambiguous submission / lost ACK / crash:
            # Commit intent as 'ambiguous' and transition run to 'unknown'
            db.rollback()
            # Verify fencing before writing failure state
            self.verify_fencing(db, tenant_id=tenant_id, run_id=run_id, fencing_token=fencing_token)

            db.execute(
                dispatch_intents.update()
                .where(
                    dispatch_intents.c.tenant_id == tenant_id,
                    dispatch_intents.c.binding_id == run_record.binding_id,
                    dispatch_intents.c.run_id == run_record.run_id,
                    dispatch_intents.c.operation == "submit",
                )
                .values(
                    state="ambiguous",
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()

            RunService.transition(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                to_state="unknown",
                remote_outcome="unknown",
                fencing_token=fencing_token,
            )
            return DispatchResult(
                status="unknown",
                idempotency_key=idempotency_key,
                fencing_token=fencing_token,
                error=str(exc),
            )

    def reconcile(
        self,
        db: Session,
        *,
        tenant_id: str,
        run_id: str,
        worker_id: str,
        provider: MockProvider,
        fencing_token: int | None = None,
    ) -> ReconcileResult:
        """Reconcile an in-flight or unknown run with the remote provider.

        - If provider confirms execution: transitions unknown -> running -> (cancelling -> cancelled if pending cancel).
        - If provider confirms absent: transitions unknown -> failed.
        - If provider lacks lookup support (lookup_supported=False): stays unknown (C38, DG1-C2).
        """
        if fencing_token is None:
            fencing_token = self.claim_lease(
                db,
                tenant_id=tenant_id,
                run_id=run_id,
                holder_id=worker_id,
            )
        else:
            self.verify_fencing(db, tenant_id=tenant_id, run_id=run_id, fencing_token=fencing_token)

        run_record = RunService.get(db, tenant_id=tenant_id, run_id=run_id)
        if run_record is None:
            raise DispatcherError(f"Run {run_id} not found in tenant {tenant_id}")

        intent_stmt = sa.select(dispatch_intents).where(
            dispatch_intents.c.tenant_id == tenant_id,
            dispatch_intents.c.binding_id == run_record.binding_id,
            dispatch_intents.c.run_id == run_record.run_id,
            dispatch_intents.c.operation == "submit",
        )
        intent_row = db.execute(intent_stmt).mappings().one_or_none()
        if intent_row is None:
            return ReconcileResult(
                status=run_record.state,
                message="No dispatch intent found to reconcile",
                fencing_token=fencing_token,
            )

        idempotency_key = intent_row["idempotency_key"]

        # Call provider lookup
        lookup_res = provider.lookup(idempotency_key)
        lookup_state = lookup_res.get("state")

        if lookup_state == "unsupported":
            # DG1-C2 / C38: Run stays unknown, requires operator review
            return ReconcileResult(
                status="unknown",
                fencing_token=fencing_token,
                attention_required=True,
                message="Adapter does not support lookup; run remains unknown",
            )

        if lookup_state == "found":
            execution_ref = lookup_res.get("execution_ref", f"mock-{idempotency_key[:8]}")

            # Update dispatch intent to acknowledged
            db.execute(
                dispatch_intents.update()
                .where(
                    dispatch_intents.c.tenant_id == tenant_id,
                    dispatch_intents.c.binding_id == run_record.binding_id,
                    dispatch_intents.c.run_id == run_record.run_id,
                    dispatch_intents.c.operation == "submit",
                )
                .values(
                    state="acknowledged",
                    execution_ref=execution_ref,
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()

            # Transition run unknown -> running
            if run_record.state == "unknown":
                run_record = RunService.transition(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state="running",
                    remote_outcome="known",
                    fencing_token=fencing_token,
                )

            # Check if there is an accepted cancel command pending (C21, C06)
            cmd_stmt = (
                sa.select(commands)
                .where(
                    commands.c.tenant_id == tenant_id,
                    commands.c.run_id == run_id,
                    commands.c.command_type == "cancel",
                    commands.c.status.in_(["accepted", "acknowledged"]),
                )
                .order_by(commands.c.created_at.asc())
            )
            pending_cmd = db.execute(cmd_stmt).mappings().first()

            if pending_cmd is not None:
                # Mark command acknowledged
                db.execute(
                    commands.update()
                    .where(commands.c.id == pending_cmd["id"])
                    .values(status="acknowledged", updated_at=dt.datetime.now(dt.UTC))
                )
                db.commit()

                # Transition running -> cancelling
                run_record = RunService.transition(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state="cancelling",
                    reason="execute_pending_cancel",
                    fencing_token=fencing_token,
                )

                # Execute provider cancellation
                provider.cancel(execution_ref=execution_ref, command_id=pending_cmd["id"])

                # Transition cancelling -> cancelled
                run_record = RunService.transition(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state="cancelled",
                    reason="cancel_confirmed",
                    fencing_token=fencing_token,
                )

                # Mark command completed
                db.execute(
                    commands.update()
                    .where(commands.c.id == pending_cmd["id"])
                    .values(status="completed", updated_at=dt.datetime.now(dt.UTC))
                )
                db.commit()

                return ReconcileResult(
                    status="cancelled",
                    execution_ref=execution_ref,
                    fencing_token=fencing_token,
                    message="Reconciled and executed pending cancel",
                )

            return ReconcileResult(
                status="running",
                execution_ref=execution_ref,
                fencing_token=fencing_token,
                message="Reconciled successfully, run is running",
            )

        if lookup_state == "not_found":
            # Remote never received execution; transition to failed to permit clean retry
            if run_record.state == "unknown":
                run_record = RunService.transition(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    to_state="failed",
                    reason="remote_not_found",
                    fencing_token=fencing_token,
                )
            return ReconcileResult(
                status="failed",
                fencing_token=fencing_token,
                message="Remote execution confirmed absent",
            )

        return ReconcileResult(
            status=run_record.state,
            fencing_token=fencing_token,
            message=f"Unhandled lookup state: {lookup_state}",
        )
