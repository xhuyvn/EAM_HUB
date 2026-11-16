"""Egress policy and binding authorization enforcement for Model Gateway."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from eap.contracts.models.binding import Binding
from eap.model_gateway.errors import EgressDeniedError, ProfileNotAuthorizedError
from eap.model_gateway.profiles import ModelProfile

EGRESS_ALLOWED_CLASSES: dict[str, frozenset[str]] = {
    "egress:local-only": frozenset({"local"}),
    "local_only": frozenset({"local"}),
    "egress:vpc-only": frozenset({"local", "approved_vpc"}),
    "egress:approved-vpc": frozenset({"local", "approved_vpc"}),
    "egress:cloud-allowed": frozenset({"local", "approved_vpc", "cloud"}),
    "egress:approved-cloud": frozenset({"local", "approved_vpc", "cloud"}),
    "egress:deny-all": frozenset(),
}


def normalize_binding(binding: Binding | Mapping[str, Any]) -> Binding:
    """Validate and normalize a binding mapping or Binding model."""
    if isinstance(binding, Binding):
        return binding
    return Binding.model_validate(dict(binding))


def is_egress_allowed(egress_policy_ref: str, endpoint_class: str) -> bool:
    """Return True iff endpoint_class is explicitly allowed by egress_policy_ref (fail-closed)."""
    allowed = EGRESS_ALLOWED_CLASSES.get(egress_policy_ref)
    if allowed is None:
        return False
    return endpoint_class in allowed


def enforce_egress(egress_policy_ref: str, profile: ModelProfile) -> None:
    """Refuse any model or embedding call whose endpoint class violates egress_policy_ref."""
    if not is_egress_allowed(egress_policy_ref, profile.endpoint_class):
        raise EgressDeniedError(
            f"egress_denied: profile '{profile.profile_ref}' (endpoint_class='{profile.endpoint_class}') "
            f"is forbidden by egress policy '{egress_policy_ref}'",
            profile_ref=profile.profile_ref,
            endpoint_class=profile.endpoint_class,
            egress_policy_ref=egress_policy_ref,
        )


def check_binding_profile_authorized(
    binding: Binding,
    profile_ref: str,
    *,
    for_embedding: bool = False,
) -> None:
    """Verify that profile_ref is explicitly authorized by the deployment binding."""
    if for_embedding:
        if binding.embedding_profile_ref is None or profile_ref != binding.embedding_profile_ref:
            raise ProfileNotAuthorizedError(
                f"Embedding profile '{profile_ref}' is not authorized by binding '{binding.binding_id}'",
                profile_ref=profile_ref,
                binding_id=binding.binding_id,
            )
        return

    allowed_profiles = {binding.model_profile_ref, *binding.fallback_model_profile_refs}
    if profile_ref not in allowed_profiles:
        raise ProfileNotAuthorizedError(
            f"Model profile '{profile_ref}' is not authorized by binding '{binding.binding_id}'",
            profile_ref=profile_ref,
            binding_id=binding.binding_id,
        )
