"""Task execution result contract model (result.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr


class ResultUsage(ContractModel):
    cost_status: Literal["actual", "estimated", "partial", "unknown"]
    cost_usd: Annotated[float | None, Field(ge=0)] = Field(...)
    provider_usage_ref: str | None = Field(...)
    as_of: DateTimeStr
    usage_scope: Literal["run", "task_tree"]


class Result(ContractModel):
    """Terminal result and summary produced by run execution."""

    contract_version: Literal["1.1"]
    task_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    execution_ref: Annotated[str, Field(min_length=1)]
    outcome: Literal["succeeded", "partial", "failed", "cancelled"]
    summary: Annotated[str, Field(min_length=1)]
    output_refs: list[str]
    evidence_refs: list[str]
    missing_evidence: list[str]
    verification_required: bool
    usage: ResultUsage
    attempt: Annotated[int, Field(ge=1)]
