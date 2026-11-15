"""HUD API routes: GET /v1/hud/snapshot (P3a-WP1)."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from eap.api.dependencies import (
    Principal,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)
from eap.config import Settings, get_settings
from eap.hud.snapshot import (
    CursorError,
    HudContractViolationError,
    HudSnapshotBuilder,
    get_default_snapshot_builder,
    take_hud_snapshot,
    verify_snapshot_cursor,
    viewer_scope_ref_for_principal,
)

logger = logging.getLogger("eap.api.hud_routes")

hud_router = APIRouter(
    prefix="/v1/hud",
    tags=["hud"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)


def get_hud_builder() -> HudSnapshotBuilder:
    """FastAPI dependency returning the HUD snapshot builder (overridable in tests)."""
    return get_default_snapshot_builder()


@hud_router.get("/snapshot")
def get_hud_snapshot(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    builder: Annotated[HudSnapshotBuilder, Depends(get_hud_builder)],
    project_id: Annotated[str | None, Query(min_length=1)] = None,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> Any:
    """Return atomic REPEATABLE READ HUD projection (hud-projection 1.1) for caller scope."""
    expected_scope = viewer_scope_ref_for_principal(principal, project_id=project_id)

    if cursor is not None:
        try:
            verify_snapshot_cursor(
                cursor,
                expected_tenant_id=principal.tenant_id,
                expected_scope=expected_scope,
                expected_grant_epoch=0,
                settings=settings,
            )
        except CursorError as exc:
            logger.warning("HUD cursor rejected (status=%s code=%s)", exc.status_code, exc.code)
            return JSONResponse(status_code=exc.status_code, content=exc.to_response_dict())

    try:
        snapshot = take_hud_snapshot(
            db,
            principal=principal,
            project_id=project_id,
            grant_epoch=0,
            settings=settings,
            builder=builder,
        )
    except HudContractViolationError as exc:
        logger.error("HUD snapshot contract validation blocked response: %s", exc.reason)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "HUD projection contract validation failed",
                "code": "HUD_CONTRACT_VIOLATION",
            },
        )

    return JSONResponse(status_code=200, content=snapshot)
