"""FastAPI dependencies for identity, database session, and input validation."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Generator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from eap.config import ConfiguredPrincipal, Settings, get_settings
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


def hash_token(token: str) -> str:
    """Compute sha256 hex digest of a bearer token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_principal(
    request: Request,
    settings: Annotated[Settings | None, Depends(get_settings)] = None,
) -> Principal:
    """Resolve principal strictly on the server side from configured bearer tokens.

    Tenant identity NEVER comes from the client (P2-1). Authenticates the
    request via Authorization: Bearer <token> using constant-time comparison
    against configured token hashes from settings.
    Missing, malformed, or invalid tokens raise HTTP 401 Unauthorized with
    no distinction or detail about which part failed.
    """
    check_forbidden_headers_and_query(request)

    if settings is None:
        settings = get_settings()

    auth_header = request.headers.get("authorization")
    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1].strip()
    token_digest = hash_token(token)

    configured_principals = settings.get_configured_principals()
    matched_principal: ConfiguredPrincipal | None = None

    for candidate in configured_principals:
        candidate_hash = candidate.token_hash.get_secret_value().strip().lower()
        candidate_hash = candidate_hash.removeprefix("sha256:")
        if secrets.compare_digest(token_digest, candidate_hash):
            matched_principal = candidate

    if matched_principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return Principal(
        tenant_id=matched_principal.tenant_id,
        principal_id=matched_principal.principal_id,
        roles=matched_principal.roles,
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
