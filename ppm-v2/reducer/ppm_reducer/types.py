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
