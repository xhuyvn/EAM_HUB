"""Typed errors for the EAP Model Gateway."""

from __future__ import annotations


class ModelGatewayError(RuntimeError):
    """Base error for Model Gateway failures."""

    code: str = "model_gateway_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class EgressDeniedError(ModelGatewayError):
    """Raised when a profile's endpoint class is forbidden by the binding's egress policy."""

    code = "egress_denied"

    def __init__(
        self,
        message: str,
        *,
        profile_ref: str,
        endpoint_class: str,
        egress_policy_ref: str,
    ) -> None:
        super().__init__(message, code="egress_denied")
        self.profile_ref = profile_ref
        self.endpoint_class = endpoint_class
        self.egress_policy_ref = egress_policy_ref


class ProfileNotAuthorizedError(ModelGatewayError):
    """Raised when a profile_ref is not authorized in the deployment binding."""

    code = "profile_not_authorized"

    def __init__(self, message: str, *, profile_ref: str, binding_id: str) -> None:
        super().__init__(message, code="profile_not_authorized")
        self.profile_ref = profile_ref
        self.binding_id = binding_id


class ProfileNotFoundError(ModelGatewayError):
    """Raised when a profile_ref is not registered in the ProfileRegistry (fail closed)."""

    code = "profile_not_found"

    def __init__(self, message: str, *, profile_ref: str) -> None:
        super().__init__(message, code="profile_not_found")
        self.profile_ref = profile_ref


class ModelTimeoutError(ModelGatewayError):
    """Raised when a model provider call times out."""

    code = "timeout"


class ModelRefusalError(ModelGatewayError):
    """Raised when a model provider refuses a request."""

    code = "refusal"


class InvalidModelOutputError(ModelGatewayError):
    """Raised when model output is not valid JSON or fails schema validation."""

    code = "invalid_json"
