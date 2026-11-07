"""Deployment binding contract model (binding.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, Sha256Str


class BindingCertifiedCapabilities(ContractModel):
    """Capabilities certified by platform governance for this binding."""

    submit: bool
    status: bool
    events: bool
    collect_result: bool
    cancel: bool
    resume: bool
    approve_tool: bool


class Binding(ContractModel):
    """Deployment binding connecting an agent release to execution environment and policies."""

    contract_version: Literal["1.1"]
    binding_id: Annotated[str, Field(min_length=1)]
    agent_id: Annotated[str, Field(min_length=1)]
    release_version: Annotated[str, Field(min_length=1)]
    release_sha256: Sha256Str
    mode: Literal["observe", "managed", "governed"]
    endpoint_ref: Annotated[str, Field(min_length=1)]
    secret_ref: str | None = Field(...)
    resource_scope_refs: list[str]
    certified_capabilities: BindingCertifiedCapabilities
    capability_evidence_ref: Annotated[str, Field(min_length=1)]
    model_profile_ref: Annotated[str, Field(min_length=1)]
    grant_refs: list[str]
    execution_boundary: Literal["platform_gateway", "sandboxed", "external_trusted"]
    policy_version: Annotated[str, Field(min_length=1)]
    fallback_model_profile_refs: list[str]
    embedding_profile_ref: Annotated[str | None, Field(min_length=1)] = Field(...)
    egress_policy_ref: Annotated[str, Field(min_length=1)]
    telemetry_export: Literal["local_only", "approved_endpoint"]
