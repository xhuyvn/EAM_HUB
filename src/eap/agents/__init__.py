"""EAP Agent Inventory package (P3a-WP5, ISS-037)."""

from eap.agents.service import (
    AgentNotFoundError,
    AgentServiceError,
    AgentValidationError,
    AgentVersionImmutableError,
    InlineSecretError,
    derive_agent_health,
    get_agent_detail,
    list_agents,
    register_agent_and_binding,
    revoke_binding,
    validate_agent_manifest,
    validate_binding_payload,
    validate_no_inline_secrets,
)

__all__ = [
    "AgentNotFoundError",
    "AgentServiceError",
    "AgentValidationError",
    "AgentVersionImmutableError",
    "InlineSecretError",
    "derive_agent_health",
    "get_agent_detail",
    "list_agents",
    "register_agent_and_binding",
    "revoke_binding",
    "validate_agent_manifest",
    "validate_binding_payload",
    "validate_no_inline_secrets",
]
