"""Request logging middleware (SC-04)."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from eap.observability.logging import principal_id_var, request_id_var, tenant_id_var

logger = logging.getLogger("eap.api.request")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs HTTP method, path, status, duration, request_id, and authenticated identity (L4).

    Strict security invariant (SC-04):
    NEVER logs request/response headers or request/response bodies.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Extract or generate request_id
        incoming_req_id = request.headers.get("x-request-id")
        if incoming_req_id and incoming_req_id.strip() and len(incoming_req_id) <= 64:
            req_id = incoming_req_id.strip()
        else:
            req_id = f"req-{uuid.uuid4().hex[:16]}"

        request_id_var.set(req_id)
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            # Attach correlation id to response
            response.headers["x-request-id"] = req_id

            # Extract tenant_id and principal_id from request.state set during authentication (L4)
            tenant_id = getattr(request.state, "tenant_id", None) or tenant_id_var.get()
            principal_id = getattr(request.state, "principal_id", None) or principal_id_var.get()
            if tenant_id is not None:
                tenant_id_var.set(tenant_id)
            if principal_id is not None:
                principal_id_var.set(principal_id)

            # Structured request completion log (never headers, never bodies)
            logger.info(
                "HTTP %s %s %d (%.2fms)",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "request_id": req_id,
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                },
            )
            return response

        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            tenant_id = getattr(request.state, "tenant_id", None) or tenant_id_var.get()
            principal_id = getattr(request.state, "principal_id", None) or principal_id_var.get()
            if tenant_id is not None:
                tenant_id_var.set(tenant_id)
            if principal_id is not None:
                principal_id_var.set(principal_id)

            logger.exception(
                "HTTP %s %s error (%.2fms)",
                request.method,
                request.url.path,
                duration_ms,
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": round(duration_ms, 2),
                    "request_id": req_id,
                    "tenant_id": tenant_id,
                    "principal_id": principal_id,
                },
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error", "code": "INTERNAL_SERVER_ERROR"},
                headers={"x-request-id": req_id},
            )
