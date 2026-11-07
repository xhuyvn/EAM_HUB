"""FastAPI dependencies for identity, database session, and input validation."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from eap.config import get_settings
from eap.db.session import get_session_factory

# Single source of truth for forbidden identity field tokens (P2-1)
FORBIDDEN_IDENTITY_TOKENS: tuple[str, ...] = ("tenant", "principal")


def is_forbidden_identity_name(name: str) -> bool:
    """Check if any token ('tenant', 'principal') is present in the identifier/header/query name."""
    normalized = name.lower().replace("-", "_")
    return any(token in normalized for token in FORBIDDEN_IDENTITY_TOKENS)


def check_forbidden_headers_and_query(request: Request) -> None:
    """Inspect request headers and query parameters for forbidden tenant or principal inputs."""
    # Enforce: No client-supplied tenant or principal in headers (P2-1)
    for header_name in request.headers:
        if is_forbidden_identity_name(header_name):
            raise HTTPException(
                status_code=422,
                detail=f"Header '{header_name}' is forbidden: tenant/principal is server-side resolved",
            )

    # Enforce: No client-supplied tenant or principal in query parameters
    for param_name in request.query_params:
        if is_forbidden_identity_name(param_name):
            raise HTTPException(
                status_code=422,
                detail=f"Query parameter '{param_name}' is forbidden: tenant/principal is server-side resolved",
            )


class Principal(BaseModel):
    """Server-side principal identity."""

    tenant_id: str
    principal_id: str
    roles: list[str] = ["admin"]


def get_principal(request: Request) -> Principal:
    """Resolve principal strictly on the server side (local bootstrap until P1a-WP5).

    Tenant identity NEVER comes from the client (P2-1). Reads local bootstrap
    principal from settings (EAP_BOOTSTRAP_TENANT_ID, EAP_BOOTSTRAP_PRINCIPAL_ID).
    P1a-WP5 will replace this bootstrap resolution with authenticated principal.
    """
    check_forbidden_headers_and_query(request)
    settings = get_settings()
    return Principal(
        tenant_id=settings.eap_bootstrap_tenant_id,
        principal_id=settings.eap_bootstrap_principal_id,
    )


async def check_forbidden_body_fields(request: Request) -> dict[str, Any]:
    """Inspect request body for forbidden client-declared tenant or principal fields."""
    check_forbidden_headers_and_query(request)

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body: Any = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}") from e

        if isinstance(body, dict):
            for field in body:
                if is_forbidden_identity_name(field):
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
