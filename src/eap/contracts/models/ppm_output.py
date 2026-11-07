"""PPM assessment output contract model (ppm-output.schema.json)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from eap.contracts.models.base import (
    ContractModel,
    DateTimeStr,
    NonEmptyStr,
    NullableDateTimeStr,
)


class PpmCriterion(ContractModel):
    criterion_id: Annotated[str, Field(min_length=1)]
    deliverable_id: Annotated[str, Field(min_length=1)]
    owner_project_id: Annotated[str, Field(min_length=1)]
    weight: Annotated[float, Field(gt=0)]
    required: bool
    status: Literal[
        "verified",
        "unverified",
        "contradicted",
        "stale",
        "unknown",
        "waived",
    ]
    reason_code: Literal[
        "evidence_current_pass",
        "evidence_missing",
        "evidence_stale_revision",
        "evidence_refuted",
        "claim_only",
        "mixed_snapshot",
        "out_of_scope_environment",
        "source_unavailable",
        "coverage_not_mapped",
        "evidence_wrong_producer",
        "reported_artifact_only",
        "waiver_approved",
    ]
    supporting_evidence_refs: list[NonEmptyStr]
    refuting_evidence_refs: list[NonEmptyStr]
    missing_evidence: list[NonEmptyStr]
    explanation: Annotated[str, Field(max_length=1000)]
    reported_status: Literal[
        "none",
        "claimed_done",
        "claimed_in_progress",
        "claimed_blocked",
    ]
    reported_claim_refs: list[NonEmptyStr]
    evidence_revision: Annotated[str | None, Field(min_length=1)] = Field(...)
    covered_test_ids: list[NonEmptyStr]
    previous_status: (
        Literal[
            "verified",
            "unverified",
            "contradicted",
            "stale",
            "unknown",
            "waived",
        ]
        | None
    ) = Field(...)
    evidence_trust: Literal[
        "verifier_observed",
        "ci_attested",
        "reported_artifact",
        "none",
    ]
    waiver_ref: Annotated[str | None, Field(min_length=1)] = Field(...)


class PpmDependencyRef(ContractModel):
    deliverable_id: Annotated[str, Field(min_length=1)]
    owner_project_id: Annotated[str, Field(min_length=1)]
    observed_status: Literal[
        "verified",
        "unverified",
        "contradicted",
        "stale",
        "unknown",
    ]


class PpmProgress(ContractModel):
    basis: Literal["baseline_weighted", "none"]
    verified_weight: Annotated[float | None, Field(ge=0)] = Field(...)
    total_weight: Annotated[float | None, Field(gt=0)] = Field(...)
    criteria_total: Annotated[int, Field(ge=0)]
    criteria_with_current_evidence: Annotated[int, Field(ge=0)]


class PpmOutput(ContractModel):
    """Assessment proposal produced by PPM reducer."""

    contract_version: Literal["1.1"]
    assessment_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    baseline_revision: Annotated[int | None, Field(ge=1)] = Field(...)
    snapshot_refs: Annotated[list[NonEmptyStr], Field(min_length=1)]
    validity: Literal["current", "stale", "unknown", "conflicted"]
    verification: Literal[
        "unknown",
        "claimed",
        "partially_verified",
        "verified",
        "contradicted",
        "stale",
    ]
    acceptance_readiness: Literal["not_ready", "ready_for_review"]
    criteria: list[PpmCriterion]
    dependency_refs: list[PpmDependencyRef]
    progress: PpmProgress
    reported_progress_percent: Annotated[float | None, Field(ge=0, le=100)] = Field(...)
    evidence_refs: list[NonEmptyStr]
    missing_evidence: list[NonEmptyStr]
    as_of: DateTimeStr
    source_as_of: NullableDateTimeStr = Field(...)
    assessed_revision: Annotated[str | None, Field(min_length=1)] = Field(...)
    previous_assessment_id: Annotated[str | None, Field(min_length=1)] = Field(...)
    snapshot_seq: Annotated[int, Field(ge=1)]
    analysis_status: Literal["complete", "model_failed", "skipped"]
    basis_seq: Annotated[int, Field(ge=1)]
    trigger: Literal[
        "snapshot",
        "baseline_changed",
        "evidence_ttl_expired",
        "model_recovered",
        "manual",
    ]
