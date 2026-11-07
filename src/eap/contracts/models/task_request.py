"""Task request contract model (task-request.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel


class TaskRequest(ContractModel):
    """External client request submitting a task for execution."""

    contract_version: Literal["1.0"]
    agent_id: Annotated[str, Field(min_length=1)]
    task_type: Annotated[str, Field(min_length=1)]
    objective: Annotated[str, Field(min_length=1)]
    input_refs: list[str]
    resource_refs: list[str]
    idempotency_key: Annotated[str, Field(min_length=8)]
