"""EAP configuration and environment settings."""

from __future__ import annotations

import ipaddress
import json

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class InsecureBindError(ValueError):
    """Raised when EAP_BIND is configured to a non-loopback host."""



def parse_bind_host(bind_str: str) -> str:
    """Extract host part from EAP_BIND (e.g. '127.0.0.1:8000', '[::1]:8000', 'localhost', '::1')."""
    s = bind_str.strip()
    if s.startswith("["):
        end_bracket = s.find("]")
        if end_bracket != -1:
            return s[1:end_bracket]
    if ":" in s:
        # Check if entire string is an IPv6 address without port
        try:
            ipaddress.IPv6Address(s)
            return s
        except ValueError:
            pass
        parts = s.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0]
        return s
    return s


def is_loopback_host(host: str) -> bool:
    """Check if host is a loopback address (127.0.0.1, ::1, localhost)."""
    host_clean = host.strip().lower()
    if host_clean == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host_clean)
        return ip.is_loopback
    except ValueError:
        return False


def validate_loopback_bind(bind_str: str) -> None:
    """Verify that bind host is loopback only, refusing startup otherwise."""
    host = parse_bind_host(bind_str)
    if not is_loopback_host(host):
        raise InsecureBindError(
            f"EAP_BIND host '{host}' is not a loopback address. "
            f"EAP refuses to run on non-loopback interfaces for security "
            f"(loopback only: 127.0.0.1, ::1, localhost)."
        )


class ConfiguredPrincipal(BaseModel):
    """Local authenticated principal configuration."""

    tenant_id: str
    principal_id: str
    token_hash: SecretStr
    roles: list[str] = ["admin"]


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

    # Host and environment configuration (L2)
    eap_host_id: str = Field(default="host-local-001", alias="EAP_HOST_ID")
    eap_host_os: str | None = Field(default=None, alias="EAP_HOST_OS")

    # Local pilot principals (token hashes only; sha256 hex; tokens/hashes never logged)
    eap_owner_tenant_id: str = Field(default="tenant-owner", alias="EAP_OWNER_TENANT_ID")
    eap_owner_principal_id: str = Field(default="principal-owner", alias="EAP_OWNER_PRINCIPAL_ID")
    eap_owner_token_hash: SecretStr | None = Field(default=None, alias="EAP_OWNER_TOKEN_HASH")

    eap_second_tenant_id: str = Field(default="tenant-second", alias="EAP_SECOND_TENANT_ID")
    eap_second_principal_id: str = Field(default="principal-second", alias="EAP_SECOND_PRINCIPAL_ID")
    eap_second_token_hash: SecretStr | None = Field(default=None, alias="EAP_SECOND_TOKEN_HASH")

    eap_principals_json: SecretStr | None = Field(default=None, alias="EAP_PRINCIPALS_JSON")
    local_principals: list[ConfiguredPrincipal] | None = Field(default=None, exclude=True)

    # Collector safety boundary (P2a-WP2 / DG-3)
    eap_collector_allow_real_roots: bool = Field(default=False, alias="EAP_COLLECTOR_ALLOW_REAL_ROOTS")
    eap_collector_fixtures_root: str | None = Field(default=None, alias="EAP_COLLECTOR_FIXTURES_ROOT")

    @field_validator(
        "redis_url",
        "qdrant_url",
        "database_url",
        "test_database_url",
        "postgres_password",
        "redis_password",
        "qdrant_api_key",
        "eap_secret_canary",
        "eap_owner_token_hash",
        "eap_second_token_hash",
        "eap_principals_json",
        "eap_collector_fixtures_root",
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

    def get_server_host_os(self) -> str:
        """Derive host OS server-side from configuration or local connector platform (L2)."""
        import sys

        if self.eap_host_os and self.eap_host_os.strip():
            return self.eap_host_os.strip().lower()
        return "windows" if sys.platform == "win32" else "linux"

    def get_configured_principals(self) -> list[ConfiguredPrincipal]:
        principals: list[ConfiguredPrincipal] = []
        if self.local_principals:
            principals.extend(self.local_principals)

        if self.eap_owner_token_hash and self.eap_owner_token_hash.get_secret_value().strip():
            principals.append(
                ConfiguredPrincipal(
                    tenant_id=self.eap_owner_tenant_id,
                    principal_id=self.eap_owner_principal_id,
                    token_hash=self.eap_owner_token_hash,
                    roles=["owner", "admin"],
                )
            )
        if self.eap_second_token_hash and self.eap_second_token_hash.get_secret_value().strip():
            principals.append(
                ConfiguredPrincipal(
                    tenant_id=self.eap_second_tenant_id,
                    principal_id=self.eap_second_principal_id,
                    token_hash=self.eap_second_token_hash,
                    roles=["admin"],
                )
            )
        if self.eap_principals_json and self.eap_principals_json.get_secret_value().strip():
            try:
                raw_json = json.loads(self.eap_principals_json.get_secret_value())
                if isinstance(raw_json, list):
                    for item in raw_json:
                        if isinstance(item, dict):
                            principals.append(ConfiguredPrincipal.model_validate(item))
            except (json.JSONDecodeError, ValueError, TypeError):
                # Silently ignore malformed principals JSON; never log secret payload (SC-04)
                pass
        return principals


def get_settings() -> Settings:
    return Settings()
