"""FastAPI application factory and routes."""

from fastapi import FastAPI

from eap.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    app = FastAPI(
        title="Enterprise Agent Platform",
        version="0.1.0",
        description="EAP modular monolith platform API",
    )

    @app.get("/healthz")
    async def healthz():
        return {
            "status": "ok",
            "env": settings.eap_env,
            "redis_enabled": settings.has_redis,
            "qdrant_enabled": settings.has_qdrant,
        }

    return app


app = create_app()
