"""PPM Sources and Locations API routes (P2a-WP1)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from eap.api.dependencies import (
    Principal,
    check_forbidden_body_fields,
    check_forbidden_headers_and_query,
    get_db,
    get_principal,
)
from eap.ppm.path import PathValidationError
from eap.ppm.service import (
    DuplicateLocationError,
    InvalidLocationStateError,
    LocationNotFoundError,
    LocationService,
    LocationServiceError,
    MetadataRootConflictError,
    RegistrantApprovalForbiddenError,
    SourceAlreadyRegisteredError,
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
    host_os: str = "windows"


@ppm_router.post(
    "/sources",
    status_code=status.HTTP_201_CREATED,
    summary="Register a physical source root (409 on duplicate folder or repo identity)",
)
async def register_source(
    request: Request,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        req = SourceRegistrationRequest.model_validate(body)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid source registration request: {exc}",
        ) from exc

    try:
        return LocationService.register_source(
            db,
            tenant_id=principal.tenant_id,
            source_id=req.source_id,
            canonical_root=req.canonical_root,
            repo_identity=req.repo_identity,
            host_os=req.host_os,
        )
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


@ppm_router.post(
    "/locations",
    status_code=status.HTTP_201_CREATED,
    summary="Register a project location scope (starts in state proposed)",
)
async def register_location(
    request: Request,
    body: Annotated[dict[str, Any], Depends(check_forbidden_body_fields)],
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return LocationService.register_location(
            db,
            tenant_id=principal.tenant_id,
            data=body,
            principal_id=principal.principal_id,
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
    except InvalidLocationStateError as exc:
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
