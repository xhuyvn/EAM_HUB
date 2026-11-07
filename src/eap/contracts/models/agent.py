"""Agent declaration contract model (agent.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel


class AgentCapabilities(ContractModel):
    """Capabilities requested by an agent manifest."""

    submit: bool
    status: bool
    events: bool
    collect_result: bool
    cancel: bool
    resume: bool
    approve_tool: bool


class Agent(ContractModel):
    """Declared agent manifest specifying interface, runtime, and requested capabilities."""

    contract_version: Literal["1.0"]
    agent_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    domain: Annotated[str, Field(min_length=1)]
    business_owner_ref: Annotated[str, Field(min_length=1)]
    technical_owner_ref: Annotated[str, Field(min_length=1)]
    runtime_kind: Literal["native", "http", "cli", "vendor_sdk"]
    task_types: list[str]
    input_schema_ref: Annotated[str, Field(min_length=1)]
    output_schema_ref: Annotated[str, Field(min_length=1)]
    desired_capabilities: AgentCapabilities
    desired_tools: list[str]
    harness_version: Annotated[str, Field(min_length=1)]
    eval_profile_ref: Annotated[str, Field(min_length=1)]
