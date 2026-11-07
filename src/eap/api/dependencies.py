"""FastAPI dependencies for identity, database session, and input validation."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from eap.db.session import get_session_factory

FORBIDDEN_IDENTITY_FIELDS: set[str] = {
    "tenant_id",
    "tenant",
    "principal",
    "principal_id",
}


class Principal(BaseModel):
    """Server-side principal identity."""

    tenant_id: str = "tenant-default"
    principal_id: str = "principal-default"
    roles: list[str] = ["admin"]


def get_principal(request: Request) -> Principal:
    """Resolve principal strictly on the server side (local bootstrap until P1a-WP5)."""
    # Reject forbidden principal / tenant fields in query params
    for field in FORBIDDEN_IDENTITY_FIELDS:
        if field in request.query_params:
            raise HTTPException(
                status_code=422,
                detail=f"Query parameter '{field}' is forbidden: tenant/principal is server-side resolved",
            )

    # Server-side resolution (e.g. from headers in local test/bootstrap harness)
    tenant_id = request.headers.get("X-Tenant-Id", "tenant-default")
    principal_id = request.headers.get("X-Principal-Id", "principal-default")
    return Principal(tenant_id=tenant_id, principal_id=principal_id)


async def check_forbidden_body_fields(request: Request) -> dict[str, Any]:
    """Inspect request body for forbidden client-declared tenant or principal fields."""
    # Check content type
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body: Any = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}") from e

        if isinstance(body, dict):
            for field in FORBIDDEN_IDENTITY_FIELDS:
                if field in body:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Field '{field}' in request body is forbidden: tenant/principal is server-side resolved",
                    )
            return body
    return {}


def get_db() -> Generator[Session, None, None]:
    """Yield database session from pool."""
    session_factory = get_session_factory()
    db = session_factory()
    try:
        yield db
    finally:
        db.close()
