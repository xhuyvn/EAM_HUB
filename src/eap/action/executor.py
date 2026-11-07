"""Action Service and Tool Executor (C29, C29b, DG1-C3).

Implements:
- action_slot issued and validated from declared step list, persisted in task_intents
  BEFORE the first tool call.
- operation_key derived via eap.contracts.keys.derive_operation_key.
- Deduplication against action_executions:
  - same key + same payload_hash -> returns journaled result, zero external tool calls.
  - same key + different payload_hash -> 409 OPERATION_KEY_CONFLICT, zero tool calls (DG1-C3).
- Agent-proposed undeclared slot after restart (C29b) -> mapped to recovered persisted slot
  or rejected with ACTION_SLOT_UNDECLARED.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from eap.contracts.digest import sha256_digest
from eap.contracts.keys import derive_operation_key, derive_payload_hash
from eap.db import action_executions, task_intents


class ActionExecutorError(Exception):
    """Base error for ActionExecutor."""



class OperationKeyConflictError(ActionExecutorError):
    """DG1-C3 / C29: Same operation_key with differing payload_hash (HTTP 409)."""



class ActionSlotUndeclaredError(ActionExecutorError):
    """C29b: Agent proposed an undeclared action slot forbidden by policy (HTTP 422/409)."""



@dataclass(frozen=True)
class ActionResult:
    status: str  # "journaled" or "executed"
    operation_key: str
    payload_hash: str
    action_slot: str
    invoked_tool: bool
    result_payload: Any = None
    external_ref: str | None = None


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
        declared_slots: list[str] | None = None,
        strict_slots: bool = False,
    ) -> ActionResult:
        """Execute an action tool call with persistent slot authority and idempotency.

        Invariants:
        1. Slot authority belongs to platform: persisted in task_intents BEFORE first tool call.
        2. operation_key = derive_operation_key(tenant_id, task_id, action_slot).
        3. Identical operation_key + identical payload_hash -> journaled result, 0 calls.
        4. Identical operation_key + divergent payload_hash -> 409 OPERATION_KEY_CONFLICT, 0 calls.
        5. Agent-proposed new slot after restart mapped back or rejected (C29b).
        """
        step = workflow_step or (tool_id.split(".")[-1] if "." in tool_id else tool_id)

        # 1. Resolve or recover action_slot from task_intents
        stmt = (
            sa.select(task_intents)
            .where(
                task_intents.c.tenant_id == tenant_id,
                task_intents.c.task_id == task_id,
                sa.or_(
                    task_intents.c.workflow_step == step,
                    task_intents.c.tool_id == tool_id,
                ),
            )
            .order_by(task_intents.c.created_at.asc())
        )
        existing_intent = db.execute(stmt).mappings().first()

        if existing_intent is not None:
            recovered_slot = existing_intent["action_slot"]
            if action_slot is not None and action_slot != recovered_slot:
                # Agent proposed an undeclared or divergent slot after restart (e.g. create_pr#2)
                if strict_slots:
                    raise ActionSlotUndeclaredError(
                        f"ACTION_SLOT_UNDECLARED: Slot '{action_slot}' was proposed, but step '{step}' is already bound to '{recovered_slot}'"
                    )
                # Map back to recovered slot per DG-1 section 3.2
                effective_slot = recovered_slot
            else:
                effective_slot = recovered_slot
        else:
            # First time declaring slot for this step
            if action_slot is not None:
                if declared_slots is not None and action_slot not in declared_slots:
                    raise ActionSlotUndeclaredError(
                        f"ACTION_SLOT_UNDECLARED: Proposed slot '{action_slot}' is not in declared slots {declared_slots}"
                    )
                effective_slot = action_slot
            else:
                if declared_slots and len(declared_slots) > 0:
                    effective_slot = declared_slots[0]
                else:
                    effective_slot = f"{step}#1"

            # Persist intent in task_intents BEFORE the first tool call
            db.execute(
                task_intents.insert().values(
                    tenant_id=tenant_id,
                    task_id=task_id,
                    action_slot=effective_slot,
                    tool_id=tool_id,
                    workflow_step=step,
                    status="declared",
                    created_at=dt.datetime.now(dt.UTC),
                )
            )
            db.commit()

        # 2. Derive operation_key and payload_hash
        operation_key = derive_operation_key(tenant_id, task_id, effective_slot)

        if arguments_sha256 is None:
            computed_args_hash = sha256_digest(arguments or {})
        else:
            computed_args_hash = arguments_sha256.lower()

        payload_hash = derive_payload_hash(tool_id, computed_args_hash, resource_ref)

        # 3. Check action_executions journal
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
            if existing_exec["payload_hash"] == payload_hash:
                # Idempotent match: return journaled result without calling tool
                return ActionResult(
                    status="journaled",
                    operation_key=operation_key,
                    payload_hash=payload_hash,
                    action_slot=effective_slot,
                    invoked_tool=False,
                    result_payload=existing_exec["result_payload"],
                    external_ref=existing_exec["external_ref"],
                )
            else:
                # DG1-C3 / C29: Divergent payload with same operation key
                raise OperationKeyConflictError(
                    f"OPERATION_KEY_CONFLICT: operation_key '{operation_key}' previously recorded with payload_hash '{existing_exec['payload_hash']}', incoming has '{payload_hash}'"
                )

        # 4. Insert execution journal record in running state
        db.execute(
            action_executions.insert().values(
                tenant_id=tenant_id,
                operation_key=operation_key,
                payload_hash=payload_hash,
                tool_id=tool_id,
                resource_ref=resource_ref,
                state="running",
                created_at=dt.datetime.now(dt.UTC),
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
        db.commit()

        # 5. Invoke tool
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
            tool_result = tool_callable()
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
