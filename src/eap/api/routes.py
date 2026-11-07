"""Task and Run platform API routes."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from eap.api.dependencies import (
    Principal,
    check_forbidden_body_fields,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)
from eap.contracts.models.task_request import TaskRequest
from eap.contracts.rules import TERMINAL
from eap.db import commands
from eap.run.service import RemoteOutcomeUnknownError, RunService, RunServiceError
from eap.task.service import TaskService

router = APIRouter(
    prefix="/v1",
    tags=["tasks"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)


@router.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a task for execution (idempotent on tenant + client key)",
)
async def create_task(
    request: Request,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Admit a task request idempotently.

    Re-submitting with the same tenant and client idempotency key returns
    the existing task reference (C01).
    """
    try:
        task_req = TaskRequest.model_validate(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid task request: {exc}",
        ) from exc

    task = TaskService.create(
        db,
        tenant_id=principal.tenant_id,
        request=task_req,
    )

    return {
        "task_id": task.id,
        "task_intent_key": task.task_intent_key,
        "agent_id": task.agent_id,
        "agent_version": task.agent_version,
        "task_type": task.task_type,
        "state": task.state,
        "created_at": task.created_at.isoformat(),
        "deadline": task.deadline.isoformat(),
    }


@router.get(
    "/tasks/{task_id}",
    summary="Get task status (strictly scoped to tenant)",
)
async def get_task(
    task_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Retrieve task details. Cross-tenant queries return 404 (no existence oracle)."""
    task = TaskService.get(db, tenant_id=principal.tenant_id, task_id=task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )

    return {
        "task_id": task.id,
        "client_idempotency_key": task.client_idempotency_key,
        "task_intent_key": task.task_intent_key,
        "agent_id": task.agent_id,
        "agent_version": task.agent_version,
        "task_type": task.task_type,
        "state": task.state,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "deadline": task.deadline.isoformat(),
    }


@router.get(
    "/tasks/{task_id}/runs",
    summary="Get runs for a task (strictly scoped to tenant)",
)
async def get_task_runs(
    task_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Retrieve all physical run attempts for a task. Cross-tenant queries return 404."""
    runs_list = TaskService.get_runs(db, tenant_id=principal.tenant_id, task_id=task_id)
    if runs_list is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{task_id}' not found",
        )

    formatted_runs = [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "attempt": r["attempt"],
            "binding_id": r["binding_id"],
            "state": r["state"],
            "remote_outcome": r["remote_outcome"],
            "lease_fencing_token": r["lease_fencing_token"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in runs_list
    ]

    return {
        "task_id": task_id,
        "runs": formatted_runs,
    }


@router.post(
    "/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request cancellation of a run (C06, C21)",
)
async def cancel_run(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Request run cancellation.

    If run is unknown, command stays 'accepted' and is executed only after reconciliation (C21, C06).
    If run is terminal, rejects with 409.
    """
    run = RunService.get(db, tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    if run.state in TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run '{run_id}' is in terminal state '{run.state}', cannot cancel",
        )

    cmd_id = f"cmd-{uuid.uuid4().hex[:12]}"
    now = dt.datetime.now(dt.UTC)
    db.execute(
        commands.insert().values(
            id=cmd_id,
            tenant_id=principal.tenant_id,
            task_id=run.task_id,
            run_id=run.run_id,
            command_type="cancel",
            status="accepted",
            payload={"requested_by_ref": f"principal:{principal.principal_id}"},
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    if run.state == "unknown" or run.remote_outcome == "unknown":
        return {
            "command_id": cmd_id,
            "run_id": run.run_id,
            "kind": "cancel",
            "state": "accepted",
            "message": "Cancel requested while run outcome unknown; command accepted and will execute after reconciliation",
        }

    # If run is in active state (running, waiting_approval, dispatching)
    if run.state in ("running", "waiting_approval", "dispatching"):
        try:
            RunService.transition(
                db,
                tenant_id=principal.tenant_id,
                run_id=run.run_id,
                to_state="cancelling",
                reason="user_cancel",
            )
        except RunServiceError:
            pass

    return {
        "command_id": cmd_id,
        "run_id": run.run_id,
        "kind": "cancel",
        "state": "accepted",
    }


@router.post(
    "/runs/{run_id}/retry",
    status_code=status.HTTP_201_CREATED,
    summary="Retry a task by creating a new run attempt (DG1-C1, C29)",
)
async def retry_run(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    """Retry a task attempt.

    Enforces DG1-C1: If the run or remote outcome is 'unknown', rejects with 409 REMOTE_OUTCOME_UNKNOWN.
    """
    run = RunService.get(db, tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run '{run_id}' not found",
        )

    if run.state == "unknown" or run.remote_outcome == "unknown":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"REMOTE_OUTCOME_UNKNOWN: Run {run_id} outcome is unknown and unreconciled; retry rejected",
        )

    try:
        new_run = RunService.retry_new_attempt(
            db,
            tenant_id=principal.tenant_id,
            task_id=run.task_id,
            binding_id=run.binding_id,
        )
        return {
            "task_id": new_run.task_id,
            "run_id": new_run.run_id,
            "attempt": new_run.attempt,
            "binding_id": new_run.binding_id,
            "state": new_run.state,
            "created_at": new_run.created_at.isoformat(),
        }
    except RemoteOutcomeUnknownError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"REMOTE_OUTCOME_UNKNOWN: {exc}",
        )
    except RunServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

