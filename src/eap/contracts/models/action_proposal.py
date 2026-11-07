"""Action proposal contract model (action-proposal.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr, Sha256Str


class ActionProposal(ContractModel):
    """Proposal for a governed side-effecting action requiring approval or audit."""

    contract_version: Literal["1.1"]
    proposal_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    tool_id: Annotated[str, Field(min_length=1)]
    arguments_ref: Annotated[str, Field(min_length=1)]
    action_digest: Sha256Str
    resource_ref: Annotated[str, Field(min_length=1)]
    expected_resource_revision: Annotated[str, Field(min_length=1)]
    policy_version: Annotated[str, Field(min_length=1)]
    expires_at: DateTimeStr
    required_role: Annotated[str, Field(min_length=1)]
    binding_id: Annotated[str, Field(min_length=1)]
    arguments_sha256: Sha256Str
    operation_key: Annotated[str, Field(min_length=8)]
    nonce: Annotated[str, Field(min_length=16)]
    digest_algorithm: Literal["sha256-jcs-v1"]
