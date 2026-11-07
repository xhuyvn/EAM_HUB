"""Producer event contract model (event.schema.json)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from eap.contracts.models.base import ContractModel, DateTimeStr, Sha256Str

EventType = Literal[
    "run.started",
    "run.progress",
    "run.state_changed",
    "run.waiting_approval",
    "run.finished",
    "worker.heartbeat",
    "artifact.created",
    "approval.decided",
    "usage.reported",
    "ppm.assessment.updated",
    "connector.status",
]

RUN_SCOPED_EVENT_TYPES: set[str] = {
    "run.started",
    "run.progress",
    "run.state_changed",
    "run.waiting_approval",
    "run.finished",
    "worker.heartbeat",
    "artifact.created",
    "usage.reported",
}


class RunStartedPayload(ContractModel):
    execution_ref: Annotated[str, Field(min_length=1)]
    agent_version: Annotated[str, Field(min_length=1)]


class RunProgressPayload(ContractModel):
    stage: Annotated[str, Field(min_length=1, max_length=200)]
    completed_units: Annotated[int | None, Field(ge=0)] = Field(...)
    total_units: Annotated[int | None, Field(ge=1)] = Field(...)
    unit: Annotated[str | None, Field(max_length=40)] = Field(...)


class RunWaitingApprovalPayload(ContractModel):
    approval_id: Annotated[str, Field(min_length=1)]
    action_digest: Sha256Str
    expires_at: DateTimeStr


class RunFinishedPayload(ContractModel):
    outcome: Literal["succeeded", "failed", "cancelled", "timed_out"]
    result_ref: str | None = Field(...)


class WorkerHeartbeatPayload(ContractModel):
    worker_id: Annotated[str, Field(min_length=1)]
    observed_state: Literal["idle", "busy", "draining", "stopped"]


class ArtifactCreatedPayload(ContractModel):
    artifact_id: Annotated[str, Field(min_length=1)]
    content_sha256: Sha256Str
    classification: Literal["public", "internal", "confidential", "restricted"]


class RunStateChangedPayload(ContractModel):
    from_state: str
    to_state: str
    reason_code: Annotated[str, Field(min_length=1)]
    evidence_ref: str | None = Field(...)


class ApprovalDecidedPayload(ContractModel):
    approval_id: Annotated[str, Field(min_length=1)]
    action_digest: Sha256Str
    decision: Literal["approved", "denied"]
    decided_by_ref: Annotated[str, Field(min_length=1)]


class UsageReportedPayload(ContractModel):
    provider_usage_id: Annotated[str, Field(min_length=1)]
    usage_scope: Literal["call", "run_cumulative"]
    cost_status: Literal["actual", "estimated", "unknown"]
    cost_usd: Annotated[float | None, Field(ge=0)] = Field(...)
    input_tokens: Annotated[int | None, Field(ge=0)] = Field(...)
    output_tokens: Annotated[int | None, Field(ge=0)] = Field(...)


class PpmAssessmentUpdatedPayload(ContractModel):
    project_id: Annotated[str, Field(min_length=1)]
    assessment_id: Annotated[str, Field(min_length=1)]
    baseline_revision: Annotated[int, Field(ge=1)]
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


class ConnectorStatusPayload(ContractModel):
    connector_id: Annotated[str, Field(min_length=1)]
    connectivity: Literal["healthy", "stale", "offline"]
    last_observed_at: DateTimeStr


PAYLOAD_MODELS: dict[str, type[ContractModel]] = {
    "run.started": RunStartedPayload,
    "run.progress": RunProgressPayload,
    "run.waiting_approval": RunWaitingApprovalPayload,
    "run.finished": RunFinishedPayload,
    "worker.heartbeat": WorkerHeartbeatPayload,
    "artifact.created": ArtifactCreatedPayload,
    "run.state_changed": RunStateChangedPayload,
    "approval.decided": ApprovalDecidedPayload,
    "usage.reported": UsageReportedPayload,
    "ppm.assessment.updated": PpmAssessmentUpdatedPayload,
    "connector.status": ConnectorStatusPayload,
}


class Event(ContractModel):
    """Producer event 1.1 streamed to outbox and relayed to event log."""

    contract_version: Literal["1.1"]
    event_id: Annotated[str, Field(min_length=1)]
    tenant_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str | None, Field(min_length=1)] = Field(...)
    run_id: Annotated[str | None, Field(min_length=1)] = Field(...)
    attempt: Annotated[int | None, Field(ge=1)] = Field(...)
    producer_id: Annotated[str, Field(min_length=1)]
    producer_seq: Annotated[int, Field(ge=1)]
    occurred_at: DateTimeStr
    event_type: EventType
    payload: dict[str, Any]

    @model_validator(mode="after")
    def validate_event_envelope_and_payload(self) -> Event:
        if self.event_type in RUN_SCOPED_EVENT_TYPES:
            if self.task_id is None:
                raise ValueError(f"task_id required for {self.event_type}")
            if self.run_id is None:
                raise ValueError(f"run_id required for {self.event_type}")
            if self.attempt is None:
                raise ValueError(f"attempt required for {self.event_type}")

        payload_cls = PAYLOAD_MODELS.get(self.event_type)
        if payload_cls is not None:
            # Validate payload structure against the typed payload model
            validated_payload = payload_cls.model_validate(self.payload)
            self.payload = validated_payload.model_dump(mode="json")
        return self
