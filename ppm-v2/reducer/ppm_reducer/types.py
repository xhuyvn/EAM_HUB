"""Types and domain models for the PPM reducer.

Pure module: only allowed stdlib imports (dataclasses, enum, typing).
"""

import dataclasses
import enum
from typing import Any


class InputRejected(Exception):
    """Raised when reducer inputs are rejected (e.g. cyclic or dangling supersession)."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


class TrustClass(enum.StrEnum):
    VERIFIER_OBSERVED = "verifier_observed"
    CI_ATTESTED = "ci_attested"
    REPORTED_ARTIFACT = "reported_artifact"
    NONE = "none"


class CriterionStatus(enum.StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    UNKNOWN = "unknown"
    WAIVED = "waived"


class ReasonCode(enum.StrEnum):
    EVIDENCE_CURRENT_PASS = "evidence_current_pass"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_STALE_REVISION = "evidence_stale_revision"
    EVIDENCE_REFUTED = "evidence_refuted"
    CLAIM_ONLY = "claim_only"
    MIXED_SNAPSHOT = "mixed_snapshot"
    OUT_OF_SCOPE_ENVIRONMENT = "out_of_scope_environment"
    SOURCE_UNAVAILABLE = "source_unavailable"
    COVERAGE_NOT_MAPPED = "coverage_not_mapped"
    EVIDENCE_WRONG_PRODUCER = "evidence_wrong_producer"
    REPORTED_ARTIFACT_ONLY = "reported_artifact_only"
    WAIVER_APPROVED = "waiver_approved"


class ReportedStatus(enum.StrEnum):
    NONE = "none"
    CLAIMED_DONE = "claimed_done"
    CLAIMED_IN_PROGRESS = "claimed_in_progress"
    CLAIMED_BLOCKED = "claimed_blocked"


@dataclasses.dataclass(frozen=True)
class CriterionResult:
    """Outcome of evaluating a single deliverable criterion against claims, evidence, and waivers."""

    criterion_id: str
    deliverable_id: str
    owner_project_id: str
    weight: float
    required: bool
    status: str
    reason_code: str
    supporting_evidence_refs: list[str]
    refuting_evidence_refs: list[str]
    missing_evidence: list[str]
    explanation: str
    reported_status: str
    reported_claim_refs: list[str]
    evidence_revision: str | None
    covered_test_ids: list[str]
    previous_status: str | None
    evidence_trust: str
    waiver_ref: str | None
    conflicting_claim_refs: list[str] = dataclasses.field(default_factory=list)

    def to_dict(self, include_explanation: bool = True) -> dict[str, Any]:
        """Convert to dictionary matching contracts/ppm-output.schema.json criteria[] format."""
        data: dict[str, Any] = {
            "criterion_id": self.criterion_id,
            "deliverable_id": self.deliverable_id,
            "owner_project_id": self.owner_project_id,
            "weight": self.weight,
            "required": self.required,
            "status": self.status,
            "reason_code": self.reason_code,
            "supporting_evidence_refs": list(self.supporting_evidence_refs),
            "refuting_evidence_refs": list(self.refuting_evidence_refs),
            "missing_evidence": list(self.missing_evidence),
            "reported_status": self.reported_status,
            "reported_claim_refs": list(self.reported_claim_refs),
            "evidence_revision": self.evidence_revision,
            "covered_test_ids": list(self.covered_test_ids),
            "previous_status": self.previous_status,
            "evidence_trust": self.evidence_trust,
            "waiver_ref": self.waiver_ref,
        }
        if include_explanation:
            data["explanation"] = self.explanation
        return data


class FindingKind(enum.StrEnum):
    ARCHITECTURE_DRIFT = "architecture_drift"
    RELATIONSHIP_PROPOSED = "relationship_proposed"
    IDENTITY_CONFLICT = "identity_conflict"
    CONFLICTING_CLAIMS = "conflicting_claims"


class FindingSeverity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    REVIEW_REQUIRED = "review_required"
    BLOCKER = "blocker"


class ProgressBasis(enum.StrEnum):
    BASELINE_WEIGHTED = "baseline_weighted"
    NONE = "none"


class AcceptanceReadiness(enum.StrEnum):
    NOT_READY = "not_ready"
    READY_FOR_REVIEW = "ready_for_review"


class Validity(enum.StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"
    CONFLICTED = "conflicted"


class Verification(enum.StrEnum):
    UNKNOWN = "unknown"
    CLAIMED = "claimed"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    STALE = "stale"


@dataclasses.dataclass(frozen=True)
class ProgressResult:
    """Progress ratio and criterion counts adhering to contracts/ppm-output.schema.json."""

    basis: str
    verified_weight: float | None
    total_weight: float | None
    criteria_total: int
    criteria_with_current_evidence: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "verified_weight": self.verified_weight,
            "total_weight": self.total_weight,
            "criteria_total": self.criteria_total,
            "criteria_with_current_evidence": self.criteria_with_current_evidence,
        }


@dataclasses.dataclass(frozen=True)
class FindingResult:
    """Domain finding emitted by the reducer (conforms to findings.schema.json)."""

    kind: str
    severity: str
    target_ref: str
    criterion_ids: list[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "target_ref": self.target_ref,
            "criterion_ids": list(self.criterion_ids),
            "details": dict(self.details),
        }


@dataclasses.dataclass(frozen=True)
class AssessmentResult:
    """Full single-project assessment proposal produced by the PPM reducer."""

    contract_version: str
    assessment_id: str
    project_id: str
    baseline_revision: int | None
    snapshot_refs: list[str]
    validity: str
    verification: str
    acceptance_readiness: str
    criteria: list[CriterionResult]
    dependency_refs: list[dict[str, Any]]
    progress: ProgressResult
    reported_progress_percent: float | None
    evidence_refs: list[str]
    missing_evidence: list[str]
    as_of: str
    source_as_of: str | None
    assessed_revision: str | None
    previous_assessment_id: str | None
    snapshot_seq: int
    analysis_status: str
    basis_seq: int
    trigger: str
    findings: list[FindingResult] = dataclasses.field(default_factory=list)

    def to_dict(self, include_explanation: bool = True) -> dict[str, Any]:
        """Convert to dict conforming strictly to contracts/ppm-output.schema.json."""
        return {
            "contract_version": self.contract_version,
            "assessment_id": self.assessment_id,
            "project_id": self.project_id,
            "baseline_revision": self.baseline_revision,
            "snapshot_refs": list(self.snapshot_refs),
            "validity": self.validity,
            "verification": self.verification,
            "acceptance_readiness": self.acceptance_readiness,
            "criteria": [c.to_dict(include_explanation=include_explanation) for c in self.criteria],
            "dependency_refs": [dict(d) for d in self.dependency_refs],
            "progress": self.progress.to_dict(),
            "reported_progress_percent": self.reported_progress_percent,
            "evidence_refs": list(self.evidence_refs),
            "missing_evidence": list(self.missing_evidence),
            "as_of": self.as_of,
            "source_as_of": self.source_as_of,
            "assessed_revision": self.assessed_revision,
            "previous_assessment_id": self.previous_assessment_id,
            "snapshot_seq": self.snapshot_seq,
            "analysis_status": self.analysis_status,
            "basis_seq": self.basis_seq,
            "trigger": self.trigger,
        }

