"""Run control command contract model (run-command.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr


class RunCommand(ContractModel):
    """Control command (cancel, reconcile, retry) targeted at an active or unknown run."""

    contract_version: Literal["1.1"]
    command_id: Annotated[str, Field(min_length=1)]
    run_id: Annotated[str, Field(min_length=1)]
    kind: Literal["cancel", "reconcile", "retry_new_attempt"]
    expected_revision: Annotated[int, Field(ge=1)]
    idempotency_key: Annotated[str, Field(min_length=8)]
    requested_by_ref: Annotated[str, Field(min_length=1)]
    requested_at: DateTimeStr
    state: Literal["accepted", "acknowledged", "completed", "rejected"]
    rejection_code: (
        Literal[
            "FORBIDDEN",
            "STALE_REVISION",
            "CAPABILITY_UNSUPPORTED",
            "REMOTE_OUTCOME_UNKNOWN",
            "TERMINAL_STATE",
        ]
        | None
    ) = Field(...)
    outcome_note: Annotated[str | None, Field(max_length=500)] = Field(...)
