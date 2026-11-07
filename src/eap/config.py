"""EAP configuration and environment settings."""

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    eap_env: str = Field(default="dev", alias="EAP_ENV")
    eap_bind: str = Field(default="127.0.0.1:8000", alias="EAP_BIND")

    database_url: SecretStr | None = Field(
        default=None,
        alias="DATABASE_URL",
    )
    test_database_url: SecretStr | None = Field(
        default=None,
        alias="TEST_DATABASE_URL",
    )
    postgres_password: SecretStr | None = Field(default=None, alias="POSTGRES_PASSWORD")

    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    redis_password: SecretStr | None = Field(default=None, alias="REDIS_PASSWORD")

    qdrant_url: str | None = Field(default=None, alias="QDRANT_URL")
    qdrant_api_key: SecretStr | None = Field(default=None, alias="QDRANT_API_KEY")

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    relay_enabled: bool = Field(default=True, alias="RELAY_ENABLED")
    eap_egress_default: str = Field(default="local_only", alias="EAP_EGRESS_DEFAULT")
    eap_telemetry: str = Field(default="local_only", alias="EAP_TELEMETRY")
    eap_secret_canary: SecretStr | None = Field(default=None, alias="EAP_SECRET_CANARY")
    eap_bootstrap_tenant_id: str = Field(default="tenant-default", alias="EAP_BOOTSTRAP_TENANT_ID")
    eap_bootstrap_principal_id: str = Field(default="principal-default", alias="EAP_BOOTSTRAP_PRINCIPAL_ID")

    @field_validator(
        "redis_url",
        "qdrant_url",
        "database_url",
        "test_database_url",
        "postgres_password",
        "redis_password",
        "qdrant_api_key",
        "eap_secret_canary",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if v is not None:
            if isinstance(v, str) and not v.strip():
                return None
            if isinstance(v, SecretStr) and not v.get_secret_value().strip():
                return None
        return v

    @property
    def has_redis(self) -> bool:
        return bool(self.redis_url)

    @property
    def has_qdrant(self) -> bool:
        return bool(self.qdrant_url)


def get_settings() -> Settings:
    return Settings()
