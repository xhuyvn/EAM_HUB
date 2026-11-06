"""Integration interface sketch. Implementations supply authentication and durable behavior."""
from typing import Any, Mapping, Protocol

class AgentAdapter(Protocol):
    async def capabilities(self, binding_id: str) -> Mapping[str, bool]:
        """Return capabilities backed by the stored certification evidence."""
        ...

    async def submit(
        self,
        task_envelope: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        """Return an execution_ref or an explicit ambiguous-submission result."""
        ...

    async def status(self, execution_ref: str) -> Mapping[str, Any]:
        """Return normalized state, raw provider status and observation time."""
        ...

    async def collect_result(self, execution_ref: str) -> Mapping[str, Any]:
        """Return a result envelope validated against contracts/result.schema.json."""
        ...

    async def cancel(self, execution_ref: str, command_id: str) -> Mapping[str, Any]:
        """Support only when certified; acknowledgement and completion are distinct."""
        ...
