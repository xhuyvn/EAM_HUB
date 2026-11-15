"""PPM Sources and Locations API routes (P2a-WP1)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from eap.api.dependencies import (
    Principal,
    check_forbidden_body_fields,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)
from eap.config import Settings, get_settings
from eap.ppm.assessments import (
    AssessmentNotFoundError,
    AssessmentProjectMismatchError,
    diff_assessments,
    get_assessment_detail,
    list_project_assessments,
)
from eap.ppm.path import PathValidationError
from eap.ppm.scheduler import PpmScanScheduler, validate_schedule_config
from eap.ppm.service import (
    DuplicateLocationError,
    InvalidLocationStateError,
    LocationNotFoundError,
    LocationService,
    LocationServiceError,
    MetadataRootConflictError,
    RegistrantApprovalForbiddenError,
    ShareApprovalForbiddenError,
    ShareNotFoundError,
    SourceAlreadyRegisteredError,
    UnapprovedSourceLocationError,
    UnauthorizedApprovalError,
)

ppm_router = APIRouter(
    prefix="/v1/ppm",
    tags=["ppm-locations"],
    dependencies=[Depends(check_forbidden_headers_and_query)],
)


class SourceRegistrationRequest(BaseModel):
    source_id: Annotated[str, Field(min_length=1)]
    canonical_root: Annotated[str, Field(min_length=1)]
    repo_identity: Annotated[str, Field(min_length=1)]


@ppm_router.post(
    "/sources",
    summary="Register a physical source root (201 active or 202 pending_share)",
)
async def register_source(
    request: Request,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> dict[str, Any]:
    try:
        req = SourceRegistrationRequest.model_validate(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid source registration request: {exc}",
        ) from exc

    server_host_os = settings.get_server_host_os()
    server_host_id = settings.eap_host_id

    try:
        result, status_code = LocationService.register_source(
            db,
            tenant_id=principal.tenant_id,
            source_id=req.source_id,
            canonical_root=req.canonical_root,
            repo_identity=req.repo_identity,
            principal_id=principal.principal_id,
            host_id=server_host_id,
            host_os=server_host_os,
        )
        response.status_code = status_code
        return result
    except PathValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except SourceAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc


@ppm_router.get(
    "/sources",
    summary="List all sources for the authenticated tenant",
)
async def list_sources(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    return LocationService.list_sources(db, tenant_id=principal.tenant_id)


@ppm_router.get(
    "/sources/{source_id}",
    summary="Get source details (strictly scoped to tenant)",
)
async def get_source(
    source_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    src = LocationService.get_source(db, tenant_id=principal.tenant_id, source_id=source_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found",
        )
    return src


class ScheduleUpdateRequest(BaseModel):
    interval_seconds: int = Field(default=300)
    debounce_seconds: int = Field(default=5)


@ppm_router.get(
    "/sources/{source_id}/schedule",
    summary="Get scan scheduler state and observability for a source (HUD-readable)",
)
async def get_source_schedule(
    source_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    obs = PpmScanScheduler.get_source_observability(
        db,
        tenant_id=principal.tenant_id,
        source_id=source_id,
    )
    if obs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found",
        )
    return obs.to_dict()


@ppm_router.put(
    "/sources/{source_id}/schedule",
    summary="Configure scan interval (>= 60s) and debounce for a source",
)
async def update_source_schedule(
    source_id: str,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    src = LocationService.get_source(db, tenant_id=principal.tenant_id, source_id=source_id)
    if src is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found",
        )
    try:
        req = ScheduleUpdateRequest.model_validate(body)
        validate_schedule_config(req.interval_seconds, req.debounce_seconds)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid schedule configuration: {exc}",
        ) from exc

    scheduler = PpmScanScheduler()
    scheduler.ensure_schedule(
        db,
        tenant_id=principal.tenant_id,
        source_id=source_id,
        interval_seconds=req.interval_seconds,
        debounce_seconds=req.debounce_seconds,
    )
    obs = PpmScanScheduler.get_source_observability(
        db,
        tenant_id=principal.tenant_id,
        source_id=source_id,
    )
    assert obs is not None
    return obs.to_dict()


@ppm_router.post(
    "/sources/{source_id}/scan",
    summary="Trigger a manual scan for a source (works even when scheduler is disabled)",
)
async def trigger_manual_source_scan(
    source_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    scheduler = PpmScanScheduler()
    try:
        res = scheduler.trigger_manual(
            db,
            tenant_id=principal.tenant_id,
            source_id=source_id,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source '{source_id}' not found",
        ) from exc
    return res.to_dict()


@ppm_router.post(
    "/locations",
    status_code=status.HTTP_201_CREATED,
    summary="Register a location (proposed state; or suspended on identity_conflict)",
)
async def register_location(
    request: Request,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    server_host_os = settings.get_server_host_os()
    try:
        return LocationService.register_location(
            db,
            tenant_id=principal.tenant_id,
            data=body,
            principal_id=principal.principal_id,
            host_os=server_host_os,
        )
    except PathValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except (SourceAlreadyRegisteredError, DuplicateLocationError, MetadataRootConflictError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc
    except LocationServiceError as exc:
        if exc.code in ("LOCATION_ACTIVE_REQUIRES_APPROVAL", "CONTRACT_VIOLATION"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=exc.message,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.message,
        ) from exc


@ppm_router.get(
    "/locations",
    summary="List locations for authenticated tenant",
)
async def list_locations(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
    project_id: str | None = None,
    source_id: str | None = None,
    state: str | None = None,
) -> list[dict[str, Any]]:
    return LocationService.list_locations(
        db,
        tenant_id=principal.tenant_id,
        project_id=project_id,
        source_id=source_id,
        state=state,
    )


@ppm_router.get(
    "/locations/{location_id}",
    summary="Get location details (strictly scoped to tenant)",
)
async def get_location(
    location_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    loc = LocationService.get_location(db, tenant_id=principal.tenant_id, location_id=location_id)
    if loc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location '{location_id}' not found",
        )
    return loc


@ppm_router.post(
    "/locations/{location_id}/approve",
    summary="Approve location registration (proposed -> active)",
)
async def approve_location(
    location_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.approve_location(
            db,
            tenant_id=principal.tenant_id,
            location_id=location_id,
            principal=principal,
        )
    except LocationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except (UnauthorizedApprovalError, RegistrantApprovalForbiddenError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exc.message,
        ) from exc
    except (InvalidLocationStateError, UnapprovedSourceLocationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.message,
        ) from exc


@ppm_router.post(
    "/locations/{location_id}/approval",
    summary="Alias for approving location registration",
)
async def approve_location_alias(
    location_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    return await approve_location(location_id=location_id, principal=principal, db=db)


@ppm_router.post(
    "/shares/{share_id}/approve",
    summary="Approve cross-tenant source share request (L3)",
)
async def approve_share(
    share_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.approve_share(db, share_id=share_id, principal=principal)
    except ShareNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except ShareApprovalForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exc.message,
        ) from exc


@ppm_router.post(
    "/shares/{share_id}/reject",
    summary="Reject cross-tenant source share request (L3)",
)
async def reject_share(
    share_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.reject_share(db, share_id=share_id, principal=principal)
    except ShareNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except ShareApprovalForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exc.message,
        ) from exc


@ppm_router.post(
    "/shares/{share_id}/revoke",
    summary="Revoke cross-tenant source share, suspending active locations (L3)",
)
async def revoke_share(
    share_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.revoke_share(db, share_id=share_id, principal=principal)
    except ShareNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc
    except ShareApprovalForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=exc.message,
        ) from exc


@ppm_router.get(
    "/shares/{share_id}",
    summary="Get cross-tenant source share request details",
)
async def get_share(
    share_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.get_share(db, share_id=share_id, principal=principal)
    except ShareNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.message,
        ) from exc


@ppm_router.get(
    "/shares",
    summary="List cross-tenant source share requests visible to tenant",
)
async def list_shares(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    return LocationService.list_shares(db, principal=principal)


@ppm_router.get(
    "/assessments/{assessment_id}",
    summary="Get assessment output with Reported and Verified per-criterion explanations",
)
async def get_assessment(
    assessment_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return get_assessment_detail(db, principal.tenant_id, assessment_id)
    except AssessmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assessment not found",
        ) from exc


@ppm_router.get(
    "/assessments/{assessment_id}/diff",
    summary="Diff per-criterion status between two assessments of the same tenant and project",
)
async def get_assessment_diff(
    assessment_id: str,
    against: Annotated[str, Query(min_length=1)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return diff_assessments(db, principal.tenant_id, assessment_id, against)
    except AssessmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assessment not found",
        ) from exc
    except AssessmentProjectMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@ppm_router.get(
    "/projects/{project_id}/assessments",
    summary="List assessments for a project (id, basis_seq, created_at, is_current)",
)
async def list_assessments_for_project(
    project_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict[str, Any]]:
    return list_project_assessments(db, principal.tenant_id, project_id)
