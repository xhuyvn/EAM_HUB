"""Test healthz endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

from eap.api.app import app


@pytest.mark.asyncio
async def test_healthz_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "env" in data
        assert "redis_enabled" in data
        assert "qdrant_enabled" in data
