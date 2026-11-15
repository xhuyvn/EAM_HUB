"""FastAPI read-only Agent Inventory routes (`/v1/agents`, `/v1/agents/{agent_id}`, P3a-WP5)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from eap.agents.service import get_agent_detail, list_agents
from eap.api.dependencies import (
    Principal,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)

agent_router = APIRouter(
    prefix="/v1",
    tags=["agents"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)


@agent_router.get("/agents", status_code=status.HTTP_200_OK)
def get_agent_inventory(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    window_hours: Annotated[
        int | None,
        Query(
            ge=1,
            le=2160,
            description="Optional rolling health lookback window in hours; omits filter to use all recorded runs.",
        ),
    ] = None,
) -> dict[str, Any]:
    """Read-only list of registered agents, bindings, capabilities, and derived health for caller's tenant."""
    return list_agents(
        db,
        tenant_id=principal.tenant_id,
        window_hours=window_hours,
    )


@agent_router.get("/agents/{agent_id}", status_code=status.HTTP_200_OK)
def get_agent_inventory_detail(
    agent_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    window_hours: Annotated[
        int | None,
        Query(
            ge=1,
            le=2160,
            description="Optional rolling health lookback window in hours; omits filter to use all recorded runs.",
        ),
    ] = None,
    recent_limit: Annotated[
        int,
        Query(
            ge=1,
            le=100,
            description="Maximum number of recent attributed runs to include.",
        ),
    ] = 20,
) -> dict[str, Any]:
    """Read-only agent detail (manifest summary, bindings, capability evidence refs, health, recent runs).

    Returns 404 both for unknown agent IDs and agents owned by another tenant.
    """
    detail = get_agent_detail(
        db,
        tenant_id=principal.tenant_id,
        agent_id=agent_id,
        window_hours=window_hours,
        recent_limit=recent_limit,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found",
        )
    return detail
