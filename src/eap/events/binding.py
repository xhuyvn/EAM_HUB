"""Producer binding and authorization definitions for event ingestion (ADR-020, C23)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from eap.contracts.models.binding import Binding, BindingCertifiedCapabilities


@dataclass(frozen=True)
class ProducerBinding:
    """Authoritative binding for an event producer.

    Per C23 and docs/02: tenant identity and event authorization are ALWAYS
    bound from this record, NEVER inferred or trusted from the event body.
    """

    tenant_id: str
    producer_id: str
    binding_id: str
    certified_capabilities: BindingCertifiedCapabilities | dict[str, bool] | set[str] = field(
        default_factory=lambda: {"events": True}
    )

    def can_emit_events(self) -> bool:
        """Check if the binding authorizes event publication."""
        caps = self.certified_capabilities
        if isinstance(caps, BindingCertifiedCapabilities):
            return bool(caps.events)
        if isinstance(caps, dict):
            return bool(caps.get("events", False))
        if isinstance(caps, (set, list, tuple)):
            return "events" in caps
        return False

    @classmethod
    def from_contract_binding(cls, tenant_id: str, producer_id: str, binding: Binding) -> ProducerBinding:
        """Construct from a published Binding contract model."""
        return cls(
            tenant_id=tenant_id,
            producer_id=producer_id,
            binding_id=binding.binding_id,
            certified_capabilities=binding.certified_capabilities,
        )


class BindingProvider(Protocol):
    """Protocol for resolving producer bindings during ingestion."""

    def __call__(self, tenant_id: str, producer_id: str) -> ProducerBinding | None:
        """Lookup binding for tenant and producer."""
        ...


class StaticBindingProvider:
    """In-memory binding provider for tests and configured deployments."""

    def __init__(self, bindings: Sequence[ProducerBinding] | None = None) -> None:
        self._bindings: dict[tuple[str, str], ProducerBinding] = {}
        if bindings:
            for b in bindings:
                self.register(b)

    def register(self, binding: ProducerBinding) -> None:
        self._bindings[(binding.tenant_id, binding.producer_id)] = binding

    def __call__(self, tenant_id: str, producer_id: str) -> ProducerBinding | None:
        return self._bindings.get((tenant_id, producer_id))


__all__ = [
    "BindingProvider",
    "ProducerBinding",
    "StaticBindingProvider",
]
