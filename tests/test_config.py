"""Test configuration settings and empty optional services."""

from eap.config import Settings


def test_settings_empty_optional_services():
    settings = Settings(
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
        EAP_ENV="test",
        REDIS_URL="redis://127.0.0.1:6379/0",
        QDRANT_URL="http://127.0.0.1:6333",
    )
    assert settings.has_redis
    assert settings.has_qdrant
