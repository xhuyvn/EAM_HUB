"""EAP Model Gateway, provider adapters, egress enforcement, and profile registry."""

from eap.model_gateway.egress import (
    EGRESS_ALLOWED_CLASSES,
    check_binding_profile_authorized,
    enforce_egress,
    is_egress_allowed,
)
from eap.model_gateway.errors import (
    EgressDeniedError,
    InvalidModelOutputError,
    ModelGatewayError,
    ModelRefusalError,
    ModelTimeoutError,
    ProfileNotAuthorizedError,
    ProfileNotFoundError,
)
from eap.model_gateway.gateway import (
    EmbeddingResult,
    GatewayResult,
    ModelGateway,
    TelemetryExporter,
    generate_embeddings,
    generate_structured,
)
from eap.model_gateway.profiles import (
    EndpointClass,
    ModelProfile,
    ProfileRegistry,
    default_profile_registry,
)
from eap.model_gateway.providers import (
    EmbeddingResponse,
    FakeInvalidJson,
    FakeModelProvider,
    FakeOutcome,
    FakeProviderCall,
    FakeRefusal,
    FakeSuccess,
    FakeTimeout,
    ModelProvider,
    OllamaProvider,
    ProviderResponse,
)

__all__ = [
    "EGRESS_ALLOWED_CLASSES",
    "EgressDeniedError",
    "EmbeddingResponse",
    "EmbeddingResult",
    "EndpointClass",
    "FakeInvalidJson",
    "FakeModelProvider",
    "FakeOutcome",
    "FakeProviderCall",
    "FakeRefusal",
    "FakeSuccess",
    "FakeTimeout",
    "GatewayResult",
    "InvalidModelOutputError",
    "ModelGateway",
    "ModelGatewayError",
    "ModelProfile",
    "ModelProvider",
    "ModelRefusalError",
    "ModelTimeoutError",
    "OllamaProvider",
    "ProfileNotAuthorizedError",
    "ProfileNotFoundError",
    "ProfileRegistry",
    "ProviderResponse",
    "TelemetryExporter",
    "check_binding_profile_authorized",
    "default_profile_registry",
    "enforce_egress",
    "generate_embeddings",
    "generate_structured",
    "is_egress_allowed",
]
