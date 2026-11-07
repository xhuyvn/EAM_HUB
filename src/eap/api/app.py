"""FastAPI application factory and routes."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from eap.api.routes import router
from eap.config import Settings, get_settings
from eap.run.service import (
    IllegalRunTransitionError,
    LateTerminalEventError,
    RemoteOutcomeUnknownError,
    RunNotFoundError,
)
from eap.task.service import TaskNotFoundError


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Enterprise Agent Platform",
        version="0.1.0",
        description="EAP modular monolith platform API",
    )

    @app.exception_handler(IllegalRunTransitionError)
    async def illegal_transition_handler(request: Request, exc: IllegalRunTransitionError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "ILLEGAL_RUN_TRANSITION"},
        )

    @app.exception_handler(LateTerminalEventError)
    async def late_terminal_handler(request: Request, exc: LateTerminalEventError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "RUN_ALREADY_TERMINAL"},
        )

    @app.exception_handler(RemoteOutcomeUnknownError)
    async def remote_outcome_unknown_handler(request: Request, exc: RemoteOutcomeUnknownError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc), "code": "REMOTE_OUTCOME_UNKNOWN"},
        )

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found_handler(request: Request, exc: TaskNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "code": "TASK_NOT_FOUND"},
        )

    @app.exception_handler(RunNotFoundError)
    async def run_not_found_handler(request: Request, exc: RunNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "code": "RUN_NOT_FOUND"},
        )

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "env": settings.eap_env,
            "redis_enabled": settings.has_redis,
            "qdrant_enabled": settings.has_qdrant,
        }

    app.include_router(router)
    return app


app = create_app()
