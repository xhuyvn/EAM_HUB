"""HUD projection contract model (hud-projection.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, DateTimeStr, NullableDateTimeStr


class HudRowProgress(ContractModel):
    kind: Literal["units", "stage_only", "none"]
    completed_units: Annotated[int | None, Field(ge=0)] = Field(...)
    total_units: Annotated[int | None, Field(ge=1)] = Field(...)
    unit: str | None = Field(...)
    stage: Annotated[str | None, Field(max_length=200)] = Field(...)


class HudRowCost(ContractModel):
    status: Literal["actual", "estimated", "partial", "unknown"]
    amount_usd: Annotated[float | None, Field(ge=0)] = Field(...)
    as_of: NullableDateTimeStr = Field(...)
    scope: Literal["run", "task_tree"]


class HudRowProject(ContractModel):
    project_id: Annotated[str, Field(min_length=1)]
    assessment_id: Annotated[str | None, Field(min_length=1)] = Field(...)
    verification: Literal[
        "unknown",
        "claimed",
        "partially_verified",
        "verified",
        "contradicted",
        "stale",
    ]
    validity: Literal["current", "stale", "unknown", "conflicted"]
    acceptance: Literal[
        "not_requested",
        "pending",
        "accepted",
        "rejected",
        "superseded",
    ]
    assessment_as_of: NullableDateTimeStr = Field(...)
    verified_weight: Annotated[float | None, Field(ge=0)] = Field(...)
    total_weight: Annotated[float | None, Field(gt=0)] = Field(...)


class HudRowPendingCommand(ContractModel):
    command_id: Annotated[str, Field(min_length=1)]
    kind: Literal["cancel", "reconcile", "retry_new_attempt"]
    state: Literal["accepted", "acknowledged"]


class HudRow(ContractModel):
    task_id: Annotated[str, Field(min_length=1)]
    task_state: Literal[
        "draft",
        "ready",
        "active",
        "blocked",
        "review_required",
        "completed",
        "failed",
        "cancelled",
    ]
    run_id: Annotated[str | None, Field(min_length=1)] = Field(...)
    attempt: Annotated[int | None, Field(ge=1)] = Field(...)
    run_state: (
        Literal[
            "queued",
            "dispatching",
            "running",
            "waiting_approval",
            "cancelling",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "unknown",
        ]
        | None
    ) = Field(...)
    remote_outcome: Literal["known", "unknown", "not_applicable"]
    connectivity: Literal["healthy", "stale", "offline", "unknown"]
    last_observed_at: NullableDateTimeStr = Field(...)
    observation_source: Annotated[str, Field(min_length=1)]
    progress: HudRowProgress
    project: HudRowProject | None = Field(...)
    cost: HudRowCost
    pending_command: HudRowPendingCommand | None = Field(...)


class HudProjection(ContractModel):
    """Projection snapshot served to HUD web client."""

    contract_version: Literal["1.1"]
    projection_revision: Annotated[int, Field(ge=1)]
    snapshot_cursor: Annotated[str, Field(min_length=1)]
    as_of: DateTimeStr
    viewer_scope_ref: Annotated[str, Field(min_length=1)]
    rows: list[HudRow]
