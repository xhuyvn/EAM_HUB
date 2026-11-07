"""Task envelope contract model (task-envelope.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr, NonEmptyStr


class TaskEnvelopeExecutionContext(ContractModel):
    tenant_id: Annotated[str, Field(min_length=1)]
    principal_id: Annotated[str, Field(min_length=1)]
    grant_refs: list[NonEmptyStr]
    resource_refs: list[NonEmptyStr]
    policy_version: Annotated[str, Field(min_length=1)]
    authorization_ref: Annotated[str, Field(min_length=1)]


class TaskEnvelope(ContractModel):
    """Execution envelope delivered to agent runner containing context and fences."""

    contract_version: Literal["1.1"]
    task_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    parent_task_id: str | None = Field(...)
    agent_id: Annotated[str, Field(min_length=1)]
    agent_version: Annotated[str, Field(min_length=1)]
    binding_id: Annotated[str, Field(min_length=1)]
    task_type: Annotated[str, Field(min_length=1)]
    objective: Annotated[str, Field(min_length=1)]
    input_refs: list[NonEmptyStr]
    output_schema_ref: Annotated[str, Field(min_length=1)]
    attempt: Annotated[int, Field(ge=1)]
    execution_context: TaskEnvelopeExecutionContext
    budget_reservation_ref: Annotated[str, Field(min_length=1)]
    deadline: DateTimeStr
    idempotency_key: Annotated[str, Field(min_length=8)]
    completion_policy_ref: Annotated[str, Field(min_length=1)]
    lease_fencing_token: Annotated[int, Field(ge=1)]
    task_intent_key: Annotated[str, Field(min_length=8)]
