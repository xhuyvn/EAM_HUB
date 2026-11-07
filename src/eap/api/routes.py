"""Task and Run platform API routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from eap.api.dependencies import Principal, check_forbidden_body_fields, get_db, get_principal
from eap.contracts.models.task_request import TaskRequest
from eap.task.service import TaskService

router = APIRouter(prefix="/v1", tags=["tasks"])


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
