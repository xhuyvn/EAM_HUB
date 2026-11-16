"""Model profile definitions and registry for the EAP Model Gateway."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EndpointClass = Literal["local", "approved_vpc", "cloud"]


@dataclass(frozen=True)
class ModelProfile:
    """Metadata describing a model profile and its network egress classification."""

    profile_ref: str
    endpoint_class: EndpointClass
    provider_kind: str
    model_name: str
    supports_structured: bool = True
    supports_embeddings: bool = False


class ProfileRegistry:
    """In-memory registry of known model profiles (fail-closed on unknown refs)."""

    def __init__(self, profiles: list[ModelProfile] | None = None) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        if profiles:
            for p in profiles:
                self.register(p)

    def register(self, profile: ModelProfile) -> None:
        """Register or update a model profile by profile_ref."""
        self._profiles[profile.profile_ref] = profile

    def get(self, profile_ref: str) -> ModelProfile | None:
        """Return the ModelProfile for profile_ref, or None if unknown."""
        return self._profiles.get(profile_ref)

    def list_profiles(self) -> list[ModelProfile]:
        """Return all registered profiles sorted by profile_ref."""
        return [self._profiles[k] for k in sorted(self._profiles)]


def default_profile_registry() -> ProfileRegistry:
    """Build a ProfileRegistry with standard local/vpc/cloud fixture profiles."""
    return ProfileRegistry(
        [
            ModelProfile(
                profile_ref="local-scoped-default",
                endpoint_class="local",
                provider_kind="fake",
                model_name="fake-local-v1",
                supports_structured=True,
                supports_embeddings=False,
            ),
            ModelProfile(
                profile_ref="fake-local-default",
                endpoint_class="local",
                provider_kind="fake",
                model_name="fake-local-v1",
                supports_structured=True,
                supports_embeddings=False,
            ),
            ModelProfile(
                profile_ref="ollama-local-default",
                endpoint_class="local",
                provider_kind="ollama",
                model_name="ollama-stub",
                supports_structured=True,
                supports_embeddings=False,
            ),
            ModelProfile(
                profile_ref="local-embed-default",
                endpoint_class="local",
                provider_kind="fake",
                model_name="fake-embed-local-v1",
                supports_structured=False,
                supports_embeddings=True,
            ),
            ModelProfile(
                profile_ref="vpc-default",
                endpoint_class="approved_vpc",
                provider_kind="fake",
                model_name="vpc-stub-v1",
                supports_structured=True,
                supports_embeddings=False,
            ),
            ModelProfile(
                profile_ref="cloud-default",
                endpoint_class="cloud",
                provider_kind="fake",
                model_name="cloud-stub-v1",
                supports_structured=True,
                supports_embeddings=False,
            ),
            ModelProfile(
                profile_ref="cloud-embed-default",
                endpoint_class="cloud",
                provider_kind="fake",
                model_name="cloud-embed-stub-v1",
                supports_structured=False,
                supports_embeddings=True,
            ),
        ]
    )
