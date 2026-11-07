"""Contract models and schema registry for EAP."""

from __future__ import annotations

from eap.contracts.models.action_proposal import ActionProposal
from eap.contracts.models.agent import Agent, AgentCapabilities
from eap.contracts.models.approval_decision import ApprovalConsumption, ApprovalDecision
from eap.contracts.models.base import ContractModel
from eap.contracts.models.binding import Binding, BindingCertifiedCapabilities
from eap.contracts.models.event import (
    ApprovalDecidedPayload,
    ArtifactCreatedPayload,
    ConnectorStatusPayload,
    Event,
    EventType,
    PpmAssessmentUpdatedPayload,
    RunFinishedPayload,
    RunProgressPayload,
    RunStartedPayload,
    RunStateChangedPayload,
    RunWaitingApprovalPayload,
    UsageReportedPayload,
    WorkerHeartbeatPayload,
)
from eap.contracts.models.experiment import (
    Experiment,
    ExperimentGuardrailMetric,
    ExperimentPrimaryMetric,
    ExperimentRollout,
)
from eap.contracts.models.hud_projection import (
    HudProjection,
    HudRow,
    HudRowCost,
    HudRowPendingCommand,
    HudRowProgress,
    HudRowProject,
)
from eap.contracts.models.location_registration import (
    LocationArtifactAliases,
    LocationIdentity,
    LocationRegistration,
)
from eap.contracts.models.ppm_input import PpmInput
from eap.contracts.models.ppm_output import (
    PpmCriterion,
    PpmDependencyRef,
    PpmOutput,
    PpmProgress,
)
from eap.contracts.models.research_input import ResearchInput
from eap.contracts.models.research_output import ResearchOutput
from eap.contracts.models.result import Result, ResultUsage
from eap.contracts.models.run_command import RunCommand
from eap.contracts.models.task_envelope import TaskEnvelope, TaskEnvelopeExecutionContext
from eap.contracts.models.task_request import TaskRequest

# Canonical mapping from schema name (with and without extension) to root model
SCHEMA_NAME_TO_MODEL: dict[str, type[ContractModel]] = {
    "action-proposal": ActionProposal,
    "agent": Agent,
    "approval-decision": ApprovalDecision,
    "binding": Binding,
    "event": Event,
    "experiment": Experiment,
    "hud-projection": HudProjection,
    "location-registration": LocationRegistration,
    "ppm-input": PpmInput,
    "ppm-output": PpmOutput,
    "research-input": ResearchInput,
    "research-output": ResearchOutput,
    "result": Result,
    "run-command": RunCommand,
    "task-envelope": TaskEnvelope,
    "task-request": TaskRequest,
}

SCHEMA_REGISTRY: dict[str, type[ContractModel]] = {
    f"{name}.schema.json": model for name, model in SCHEMA_NAME_TO_MODEL.items()
}
SCHEMA_REGISTRY.update(SCHEMA_NAME_TO_MODEL)


def get_model_for_schema(schema_name: str) -> type[ContractModel]:
    """Retrieve the root contract model registered for a given schema filename or name."""
    clean_name = schema_name.removesuffix(".schema.json")
    if clean_name in SCHEMA_NAME_TO_MODEL:
        return SCHEMA_NAME_TO_MODEL[clean_name]
    raise KeyError(f"No contract model registered for schema: {schema_name}")


__all__ = [
    "SCHEMA_NAME_TO_MODEL",
    "SCHEMA_REGISTRY",
    "ActionProposal",
    "Agent",
    "AgentCapabilities",
    "ApprovalConsumption",
    "ApprovalDecidedPayload",
    "ApprovalDecision",
    "ArtifactCreatedPayload",
    "Binding",
    "BindingCertifiedCapabilities",
    "ConnectorStatusPayload",
    "ContractModel",
    "Event",
    "EventType",
    "Experiment",
    "ExperimentGuardrailMetric",
    "ExperimentPrimaryMetric",
    "ExperimentRollout",
    "HudProjection",
    "HudRow",
    "HudRowCost",
    "HudRowPendingCommand",
    "HudRowProgress",
    "HudRowProject",
    "LocationArtifactAliases",
    "LocationIdentity",
    "LocationRegistration",
    "PpmAssessmentUpdatedPayload",
    "PpmCriterion",
    "PpmDependencyRef",
    "PpmInput",
    "PpmOutput",
    "PpmProgress",
    "ResearchInput",
    "ResearchOutput",
    "Result",
    "ResultUsage",
    "RunCommand",
    "RunFinishedPayload",
    "RunProgressPayload",
    "RunStartedPayload",
    "RunStateChangedPayload",
    "RunWaitingApprovalPayload",
    "TaskEnvelope",
    "TaskEnvelopeExecutionContext",
    "TaskRequest",
    "UsageReportedPayload",
    "WorkerHeartbeatPayload",
    "get_model_for_schema",
]
