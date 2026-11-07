"""Test configuration settings and empty optional services."""

import pathlib
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient

from eap.api.app import create_app
from eap.config import Settings


def test_settings_empty_optional_services():
    settings = Settings(
        _env_file=None,
        EAP_ENV="test",
        REDIS_URL="",
        QDRANT_URL="",
    )
    assert settings.eap_env == "test"
    assert settings.redis_url is None
    assert settings.qdrant_url is None
    assert not settings.has_redis
    assert not settings.has_qdrant


def test_settings_with_optional_services():
    settings = Settings(
        _env_file=None,
        EAP_ENV="test",
        REDIS_URL="redis://127.0.0.1:6379/0",
        QDRANT_URL="http://127.0.0.1:6333",
    )
    assert settings.has_redis
    assert settings.has_qdrant


def test_env_example_interpolation():
    """R2: loads a temp copy of .env.example with POSTGRES_PASSWORD=x and asserts database_url contains ':x@'."""
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    env_example_text = (repo_root / ".env.example").read_text(encoding="utf-8")

    # Replace POSTGRES_PASSWORD= with POSTGRES_PASSWORD=x
    assert "POSTGRES_PASSWORD=" in env_example_text
    env_modified = env_example_text.replace("POSTGRES_PASSWORD=", "POSTGRES_PASSWORD=x", 1)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
        tmp.write(env_modified)
        tmp_path = tmp.name

    try:
        settings = Settings(_env_file=tmp_path)
        assert settings.database_url is not None, "DATABASE_URL failed to load from interpolated .env"
        db_url_val = settings.database_url.get_secret_value()
        assert ":x@" in db_url_val, f"Expected ':x@' in database_url, got: {db_url_val}"
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_create_app_empty_optional_services_serves_healthz():
    """R6: create_app() with REDIS_URL='' and QDRANT_URL='' serves /healthz 200."""
    settings = Settings(
        _env_file=None,
        EAP_ENV="test",
        REDIS_URL="",
        QDRANT_URL="",
    )
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["redis_enabled"] is False
        assert data["qdrant_enabled"] is False
