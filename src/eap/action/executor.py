"""Action Service and Tool Executor (C29, C29b, DG1-C3, W4-1, W4-2, W4-3).

Implements:
- action_slot issued and validated from declared step list, persisted in task_intents
  BEFORE the first tool call; slot identity is (tenant_id, task_id, action_slot).
- operation_key derived via eap.contracts.keys.derive_operation_key.
- Deduplication against action_executions:
  - same key + same payload_hash:
    - succeeded -> returns journaled result, zero external tool calls.
    - running / sent / ambiguous -> unknown; reconciled via provider lookup by
      client_token (= operation_key). If lookup supported and found -> journal succeeded;
      if not found -> safe to execute once; if unsupported -> 409 REMOTE_OUTCOME_UNKNOWN (W4-2).
    - failed -> retry policy: transition to running and execute tool.
  - same key + different payload_hash -> 409 OPERATION_KEY_CONFLICT, zero tool calls (DG1-C3).
- Concurrent execution conflict -> 409 ACTION_IN_PROGRESS without calling tool (W4-3).
- Agent-proposed undeclared slot after restart (C29b) -> mapped to recovered persisted slot
  or rejected with ACTION_SLOT_UNDECLARED. Declared slots always stay distinct (W4-1).
"""

from __future__ import annotations

import datetime as dt
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.adapters.mock_provider import MockProviderAckDroppedError
from eap.contracts.digest import sha256_digest
from eap.contracts.keys import derive_operation_key, derive_payload_hash
from eap.db import action_executions, task_intents
from eap.run.service import RemoteOutcomeUnknownError


class ActionExecutorError(Exception):
    """Base error for ActionExecutor."""



class OperationKeyConflictError(ActionExecutorError):
    """DG1-C3 / C29: Same operation_key with differing payload_hash (HTTP 409)."""



class ActionSlotUndeclaredError(ActionExecutorError):
    """C29b: Agent proposed an undeclared action slot forbidden by policy (HTTP 422/409)."""



class ActionInProgressError(ActionExecutorError):
    """W4-3: Concurrent execution already in progress or claimed by another worker (HTTP 409)."""



@dataclass(frozen=True)
class ActionResult:
    status: str  # "journaled" or "executed"
    operation_key: str
    payload_hash: str
    action_slot: str
    invoked_tool: bool
    result_payload: Any = None
    external_ref: str | None = None


def _invoke_tool(tool_callable: Callable[..., Any], client_token: str) -> Any:
    """Invoke tool callable passing client_token=operation_key if supported."""
    try:
        sig = inspect.signature(tool_callable)
        params = sig.parameters
        if "client_token" in params or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
        ):
            return tool_callable(client_token=client_token)
        return tool_callable()
    except (ValueError, TypeError):
        try:
            return tool_callable(client_token=client_token)
        except TypeError:
            return tool_callable()


class ActionExecutor:
    """Action Service executing tools with strict idempotency and slot recovery."""

    @staticmethod
    def execute(
        db: Session,
        *,
        tenant_id: str,
        task_id: str,
        run_id: str,
        tool_id: str,
        workflow_step: str = "",
        action_slot: str | None = None,
        arguments: dict[str, Any] | None = None,
        arguments_sha256: str | None = None,
        resource_ref: str = "",
        tool_callable: Callable[..., Any] | None = None,
        provider: Any | None = None,
        lookup_callable: Callable[[str], dict[str, Any]] | None = None,
        declared_slots: list[str] | None = None,
        strict_slots: bool = False,
    ) -> ActionResult:
        """Execute an action tool call with persistent slot authority and idempotency.

        Invariants:
        1. Slot authority belongs to platform: persisted in task_intents BEFORE first tool call.
           Slot identity is (tenant_id, task_id, action_slot). Declared slots stay distinct (W4-1).
        2. operation_key = derive_operation_key(tenant_id, task_id, action_slot).
        3. Provider client token = operation_key for that slot, passed to tool callable.
        4. Identical operation_key + identical payload_hash:
           - succeeded -> journaled result, 0 calls.
           - running/ambiguous -> unknown outcome reconciled via provider lookup (W4-2).
           - failed -> retry eligible (transitions to running and re-executes).
        5. Identical operation_key + divergent payload_hash -> 409 OPERATION_KEY_CONFLICT, 0 calls (DG1-C3).
        6. Concurrent insert collision -> 409 ACTION_IN_PROGRESS, 0 calls (W4-3).
        7. Agent-proposed undeclared slot after restart mapped back or rejected (C29b).
        """
        step = workflow_step or (tool_id.split(".")[-1] if "." in tool_id else tool_id)
        now = dt.datetime.now(dt.UTC)

        # 1. Fetch existing task_intents for (tenant_id, task_id)
        stmt = (
            sa.select(task_intents)
            .where(
                task_intents.c.tenant_id == tenant_id,
                task_intents.c.task_id == task_id,
            )
            .order_by(task_intents.c.created_at.asc())
        )
        existing_intents = list(db.execute(stmt).mappings().all())
        declared_in_db = {row["action_slot"]: dict(row) for row in existing_intents}

        # 2. Persist declared step list (all slots) in task_intents at first use (W4-1)
        if declared_slots is not None:
            for slot in declared_slots:
                if slot not in declared_in_db:
                    try:
                        db.execute(
                            task_intents.insert().values(
                                tenant_id=tenant_id,
                                task_id=task_id,
                                action_slot=slot,
                                tool_id=tool_id,
                                workflow_step=step,
                                status="declared",
                                created_at=now,
                            )
                        )
                        db.commit()
                        declared_in_db[slot] = {
                            "tenant_id": tenant_id,
                            "task_id": task_id,
                            "action_slot": slot,
                            "tool_id": tool_id,
                            "workflow_step": step,
                            "status": "declared",
                            "created_at": now,
                        }
                    except sa.exc.IntegrityError:
                        db.rollback()
                        refreshed = db.execute(stmt).mappings().all()
                        declared_in_db = {r["action_slot"]: dict(r) for r in refreshed}
        elif not declared_in_db:
            # First use without explicit declared_slots list: persist initial slot
            initial_slot = action_slot or f"{step}#1"
            try:
                db.execute(
                    task_intents.insert().values(
                        tenant_id=tenant_id,
                        task_id=task_id,
                        action_slot=initial_slot,
                        tool_id=tool_id,
                        workflow_step=step,
                        status="declared",
                        created_at=now,
                    )
                )
                db.commit()
                declared_in_db[initial_slot] = {
                    "tenant_id": tenant_id,
                    "task_id": task_id,
                    "action_slot": initial_slot,
                    "tool_id": tool_id,
                    "workflow_step": step,
                    "status": "declared",
                    "created_at": now,
                }
            except sa.exc.IntegrityError:
                db.rollback()
                refreshed = db.execute(stmt).mappings().all()
                declared_in_db = {r["action_slot"]: dict(r) for r in refreshed}

        # 3. Resolve effective action_slot (W4-1)
        if action_slot is not None:
            if action_slot in declared_in_db:
                # Declared slot is always used as-is (W4-1)
                effective_slot = action_slot
            else:
                # Agent proposed an undeclared slot (e.g. create_pr#3 or drifted slot)
                if strict_slots:
                    raise ActionSlotUndeclaredError(
                        f"ACTION_SLOT_UNDECLARED: Slot '{action_slot}' is not declared for task '{task_id}'"
                    )
                # Non-strict recovery per DG-1 section 3.2: map back to declared slot
                step_prefix = action_slot.split("#")[0] if "#" in action_slot else step
                candidate_slots = [
                    slot
                    for slot, row in declared_in_db.items()
                    if row.get("workflow_step") == step or slot.split("#")[0] == step_prefix
                ]
                if not candidate_slots:
                    raise ActionSlotUndeclaredError(
                        f"ACTION_SLOT_UNDECLARED: Slot '{action_slot}' has no matching declared slots for task '{task_id}'"
                    )
                # Prefer one whose execution is not succeeded, else candidate_slots[0]
                effective_slot = candidate_slots[0]
                for slot in candidate_slots:
                    op_key = derive_operation_key(tenant_id, task_id, slot)
                    exec_state_val = db.execute(
                        sa.select(action_executions.c.state).where(
                            action_executions.c.tenant_id == tenant_id,
                            action_executions.c.operation_key == op_key,
                        )
                    ).scalar()
                    if exec_state_val != "succeeded":
                        effective_slot = slot
                        break
        else:
            # No action_slot passed; pick first matching declared slot
            step_prefix = step
            candidate_slots = [
                slot
                for slot, row in declared_in_db.items()
                if row.get("workflow_step") == step or slot.split("#")[0] == step_prefix
            ]
            if candidate_slots:
                effective_slot = candidate_slots[0]
                for slot in candidate_slots:
                    op_key = derive_operation_key(tenant_id, task_id, slot)
                    exec_state_val = db.execute(
                        sa.select(action_executions.c.state).where(
                            action_executions.c.tenant_id == tenant_id,
                            action_executions.c.operation_key == op_key,
                        )
                    ).scalar()
                    if exec_state_val != "succeeded":
                        effective_slot = slot
                        break
            else:
                effective_slot = f"{step}#1"

        # 4. Derive operation_key and payload_hash
        operation_key = derive_operation_key(tenant_id, task_id, effective_slot)

        if arguments_sha256 is None:
            computed_args_hash = sha256_digest(arguments or {})
        else:
            computed_args_hash = arguments_sha256.lower()

        payload_hash = derive_payload_hash(tool_id, computed_args_hash, resource_ref)

        # 5. Check action_executions journal
        exec_stmt = (
            sa.select(action_executions)
            .where(
                action_executions.c.tenant_id == tenant_id,
                action_executions.c.operation_key == operation_key,
            )
            .with_for_update()
        )
        existing_exec = db.execute(exec_stmt).mappings().one_or_none()

        if existing_exec is not None:
            if existing_exec["payload_hash"] != payload_hash:
                # DG1-C3 / C29: Divergent payload with same operation key
                raise OperationKeyConflictError(
                    f"OPERATION_KEY_CONFLICT: operation_key '{operation_key}' previously recorded with payload_hash '{existing_exec['payload_hash']}', incoming has '{payload_hash}'"
                )

            exec_state = existing_exec["state"]

            if exec_state == "succeeded":
                # Only "succeeded" rows return journaled (W4-2)
                return ActionResult(
                    status="journaled",
                    operation_key=operation_key,
                    payload_hash=payload_hash,
                    action_slot=effective_slot,
                    invoked_tool=False,
                    result_payload=existing_exec["result_payload"],
                    external_ref=existing_exec["external_ref"],
                )

            elif exec_state in ("running", "unknown", "sent", "ambiguous"):
                # Existing row in running/unknown represents in-flight / crashed execution (W4-2).
                # Outcome is UNKNOWN until reconciled via provider lookup by client_token (= operation_key).
                lookup_func = None
                lookup_supported = False

                if lookup_callable is not None:
                    lookup_func = lookup_callable
                    lookup_supported = True
                elif provider is not None:
                    lookup_supported = getattr(provider, "lookup_supported", True)
                    if hasattr(provider, "lookup_pr"):
                        lookup_func = provider.lookup_pr
                    elif hasattr(provider, "lookup"):
                        lookup_func = provider.lookup

                if not lookup_supported or lookup_func is None:
                    raise RemoteOutcomeUnknownError(
                        f"REMOTE_OUTCOME_UNKNOWN: Action execution '{operation_key}' is in state '{exec_state}' and provider lookup is unsupported"
                    )

                lookup_res = lookup_func(operation_key)
                if not isinstance(lookup_res, dict) or lookup_res.get("state") == "unsupported":
                    raise RemoteOutcomeUnknownError(
                        f"REMOTE_OUTCOME_UNKNOWN: Provider status lookup unsupported for '{operation_key}'"
                    )

                if lookup_res.get("state") == "found":
                    # Remote effect was already created! Journal succeeded with external_ref and result
                    pr_or_res = lookup_res.get("pr") or lookup_res.get("result") or lookup_res
                    ext_ref = None
                    if isinstance(pr_or_res, dict):
                        ext_ref = pr_or_res.get("pr_id") or pr_or_res.get("execution_ref")
                    elif "execution_ref" in lookup_res:
                        ext_ref = lookup_res.get("execution_ref")

                    result_payload = pr_or_res if isinstance(pr_or_res, dict) else {"result": pr_or_res}

                    db.execute(
                        action_executions.update()
                        .where(
                            action_executions.c.tenant_id == tenant_id,
                            action_executions.c.operation_key == operation_key,
                        )
                        .values(
                            state="succeeded",
                            external_ref=ext_ref,
                            result_payload=result_payload,
                            updated_at=dt.datetime.now(dt.UTC),
                        )
                    )
                    db.execute(
                        task_intents.update()
                        .where(
                            task_intents.c.tenant_id == tenant_id,
                            task_intents.c.task_id == task_id,
                            task_intents.c.action_slot == effective_slot,
                        )
                        .values(status="completed")
                    )
                    db.commit()

                    return ActionResult(
                        status="journaled",
                        operation_key=operation_key,
                        payload_hash=payload_hash,
                        action_slot=effective_slot,
                        invoked_tool=False,
                        result_payload=result_payload,
                        external_ref=ext_ref,
                    )

                elif lookup_res.get("state") == "not_found":
                    # Remote effect not found -> safe to call once (same operation_key) and journal
                    pass
                else:
                    raise RemoteOutcomeUnknownError(
                        f"REMOTE_OUTCOME_UNKNOWN: Ambiguous provider status '{lookup_res.get('state')}' for '{operation_key}'"
                    )

            elif exec_state == "failed":
                # Documented policy: previous failure allows retry with same operation_key;
                # transition back to running and execute tool below.
                db.execute(
                    action_executions.update()
                    .where(
                        action_executions.c.tenant_id == tenant_id,
                        action_executions.c.operation_key == operation_key,
                    )
                    .values(
                        state="running",
                        updated_at=dt.datetime.now(dt.UTC),
                    )
                )
                db.commit()

        else:
            # 6. Insert execution journal record in running state (W4-3)
            try:
                db.execute(
                    action_executions.insert().values(
                        tenant_id=tenant_id,
                        operation_key=operation_key,
                        payload_hash=payload_hash,
                        tool_id=tool_id,
                        resource_ref=resource_ref,
                        state="running",
                        created_at=now,
                        updated_at=now,
                    )
                )
                db.commit()
            except sa.exc.IntegrityError as err:
                db.rollback()
                raise ActionInProgressError(
                    f"ACTION_IN_PROGRESS: Action for operation_key '{operation_key}' is already in progress or claimed by a concurrent worker"
                ) from err

        # 7. Invoke tool
        if tool_callable is None:
            return ActionResult(
                status="executed",
                operation_key=operation_key,
                payload_hash=payload_hash,
                action_slot=effective_slot,
                invoked_tool=False,
                result_payload={"status": "no_callable_provided"},
            )

        try:
            tool_result = _invoke_tool(tool_callable, client_token=operation_key)
            ext_ref = None
            if isinstance(tool_result, dict):
                ext_ref = tool_result.get("pr_id") or tool_result.get("execution_ref")

            # Update execution journal to succeeded
            db.execute(
                action_executions.update()
                .where(
                    action_executions.c.tenant_id == tenant_id,
                    action_executions.c.operation_key == operation_key,
                )
                .values(
                    state="succeeded",
                    external_ref=ext_ref,
                    result_payload=tool_result if isinstance(tool_result, dict) else {"result": tool_result},
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )

            # Update task_intents status to completed
            db.execute(
                task_intents.update()
                .where(
                    task_intents.c.tenant_id == tenant_id,
                    task_intents.c.task_id == task_id,
                    task_intents.c.action_slot == effective_slot,
                )
                .values(status="completed")
            )
            db.commit()

            return ActionResult(
                status="executed",
                operation_key=operation_key,
                payload_hash=payload_hash,
                action_slot=effective_slot,
                invoked_tool=True,
                result_payload=tool_result,
                external_ref=ext_ref,
            )

        except MockProviderAckDroppedError:
            # Network ACK dropped: remote outcome is unknown, NOT failed
            db.rollback()
            db.execute(
                action_executions.update()
                .where(
                    action_executions.c.tenant_id == tenant_id,
                    action_executions.c.operation_key == operation_key,
                )
                .values(
                    state="unknown",
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()
            raise

        except Exception:
            db.rollback()
            db.execute(
                action_executions.update()
                .where(
                    action_executions.c.tenant_id == tenant_id,
                    action_executions.c.operation_key == operation_key,
                )
                .values(
                    state="failed",
                    updated_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()
            raise
