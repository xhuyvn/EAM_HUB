"""Approval decision contract model (approval-decision.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr, NullableDateTimeStr, Sha256Str


class ApprovalConsumption(ContractModel):
    """Tracking single-use consumption of an approval decision."""

    state: Literal["unconsumed", "consumed", "expired", "revoked"]
    consumed_at: NullableDateTimeStr = Field(...)
    consumed_by_operation_key: Annotated[str | None, Field(min_length=1)] = Field(...)


class ApprovalDecision(ContractModel):
    """Operator decision on an action proposal binding exact digest and nonce."""

    contract_version: Literal["1.1"]
    approval_id: Annotated[str, Field(min_length=1)]
    proposal_id: Annotated[str, Field(min_length=1)]
    action_digest: Sha256Str
    nonce: Annotated[str, Field(min_length=16)]
    decision: Literal["approved", "denied"]
    approver_ref: Annotated[str, Field(min_length=1)]
    approver_role: Annotated[str, Field(min_length=1)]
    decided_at: DateTimeStr
    valid_until: DateTimeStr
    consumption: ApprovalConsumption
