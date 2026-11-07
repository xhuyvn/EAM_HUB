"""Experiment contract model (experiment.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import ContractModel, Sha256Str


class ExperimentPrimaryMetric(ContractModel):
    name: Annotated[str, Field(min_length=1)]
    direction: Literal["higher_is_better", "lower_is_better"]
    non_inferiority_margin: Annotated[float, Field(ge=0)]


class ExperimentGuardrailMetric(ContractModel):
    name: Annotated[str, Field(min_length=1)]
    max_regression: Annotated[float, Field(ge=0)]
    scope: Literal["overall", "per_stratum"]


class ExperimentRollout(ContractModel):
    stage: Literal["none", "shadow", "canary"]
    max_traffic_percent: Annotated[float, Field(ge=0, le=100)]
    rollback_trigger_refs: list[str]


class Experiment(ContractModel):
    """Experiment specification for agent/prompt/retrieval candidate comparison."""

    contract_version: Literal["1.1"]
    experiment_id: Annotated[str, Field(min_length=1)]
    base_release_digest: Sha256Str
    candidate_release_digest: Sha256Str
    dataset_ref: Annotated[str, Field(min_length=1)]
    holdout_ref: Annotated[str, Field(min_length=1)]
    evaluator_version: Annotated[str, Field(min_length=1)]
    model_profile_ref: Annotated[str, Field(min_length=1)]
    trials_per_case: Annotated[int, Field(ge=1)]
    budget_ref: Annotated[str, Field(min_length=1)]
    objective: Annotated[str, Field(min_length=1)]
    promotion_policy_ref: Annotated[str, Field(min_length=1)]
    reviewer_ref: Annotated[str, Field(min_length=1)]
    state: Literal[
        "proposed",
        "evaluating",
        "review_required",
        "approved",
        "canary",
        "promoted",
        "reverted",
        "rejected",
    ]
    proposer_ref: Annotated[str, Field(min_length=1)]
    change_kind: Literal[
        "prompt",
        "tools",
        "retrieval",
        "context",
        "model_routing",
        "planning",
        "recovery",
        "verification",
    ]
    pairing: Literal["paired_same_case_same_environment"]
    holdout_access: Literal["evaluator_only"]
    primary_metric: ExperimentPrimaryMetric
    guardrail_metrics: Annotated[list[ExperimentGuardrailMetric], Field(min_length=1)]
    rollout: ExperimentRollout
