"""Progress, validity, verification, and readiness evaluation for PPM reducer.

Pure module: zero I/O, stdlib allowlist only (dataclasses, typing).
Rule: Denominator is always present; waived criteria excluded from weights.
"""

from collections.abc import Sequence
from typing import Any

from .types import (
    AcceptanceReadiness,
    CriterionResult,
    CriterionStatus,
    ProgressBasis,
    ProgressResult,
    ReasonCode,
    ReportedStatus,
    Validity,
    Verification,
)


def calculate_progress(
    criteria: Sequence[CriterionResult | dict[str, Any]],
    has_baseline: bool,
) -> ProgressResult:
    """Calculate baseline-weighted progress and criteria metrics.

    Rule: Waived criteria (status='waived') are strictly excluded from both
    verified_weight and total_weight. When no baseline exists (s24), basis is
    'none', weights are null, and criteria counts are 0.
    When all criteria are waived (empty non-waived denominator), verified_weight
    and total_weight are null, while basis stays 'baseline_weighted' (M3-1).
    """
    if not has_baseline:
        return ProgressResult(
            basis=ProgressBasis.NONE,
            verified_weight=None,
            total_weight=None,
            criteria_total=0,
            criteria_with_current_evidence=0,
        )

    criteria_total = len(criteria)
    total_weight = 0.0
    verified_weight = 0.0
    cwce = 0
    non_waived_count = 0

    for c in criteria:
        status = c["status"] if isinstance(c, dict) else c.status
        weight = float(c["weight"] if isinstance(c, dict) else c.weight)
        reason = c["reason_code"] if isinstance(c, dict) else c.reason_code

        if status != CriterionStatus.WAIVED:
            total_weight += weight
            non_waived_count += 1

        if status == CriterionStatus.VERIFIED:
            verified_weight += weight

        if reason in (ReasonCode.EVIDENCE_CURRENT_PASS, ReasonCode.EVIDENCE_REFUTED):
            cwce += 1

    if non_waived_count == 0:
        return ProgressResult(
            basis=ProgressBasis.BASELINE_WEIGHTED,
            verified_weight=None,
            total_weight=None,
            criteria_total=criteria_total,
            criteria_with_current_evidence=cwce,
        )

    return ProgressResult(
        basis=ProgressBasis.BASELINE_WEIGHTED,
        verified_weight=float(round(verified_weight, 6)),
        total_weight=float(round(total_weight, 6)),
        criteria_total=criteria_total,
        criteria_with_current_evidence=cwce,
    )


def calculate_validity(
    criteria: Sequence[CriterionResult | dict[str, Any]],
    has_baseline: bool,
) -> str:
    """Determine assessment validity ('current', 'stale', 'unknown')."""
    if not has_baseline:
        return Validity.UNKNOWN
    if any((c["status"] if isinstance(c, dict) else c.status) == CriterionStatus.STALE for c in criteria):
        return Validity.STALE
    return Validity.CURRENT


def calculate_verification(
    criteria: Sequence[CriterionResult | dict[str, Any]],
    has_baseline: bool,
) -> str:
    """Determine top-level verification state across all criteria."""
    if not has_baseline:
        return Verification.UNKNOWN

    statuses = [(c["status"] if isinstance(c, dict) else c.status) for c in criteria]
    if CriterionStatus.CONTRADICTED in statuses:
        return Verification.CONTRADICTED
    if CriterionStatus.STALE in statuses:
        return Verification.STALE
    if len(criteria) > 0 and all(s in (CriterionStatus.VERIFIED, CriterionStatus.WAIVED) for s in statuses):
        # M3-1: If every criterion is waived, nothing is verifiable -> verification is 'unknown'
        if all(s == CriterionStatus.WAIVED for s in statuses):
            return Verification.UNKNOWN
        return Verification.VERIFIED
    if CriterionStatus.VERIFIED in statuses:
        return Verification.PARTIALLY_VERIFIED

    rep_statuses = [(c["reported_status"] if isinstance(c, dict) else c.reported_status) for c in criteria]
    if any(
        r in (ReportedStatus.CLAIMED_DONE, ReportedStatus.CLAIMED_IN_PROGRESS, ReportedStatus.CLAIMED_BLOCKED)
        for r in rep_statuses
    ):
        return Verification.CLAIMED

    return Verification.UNKNOWN


def calculate_readiness(
    criteria: Sequence[CriterionResult | dict[str, Any]],
    verification: str,
    has_baseline: bool,
) -> str:
    """Determine acceptance readiness.

    Rule: 'ready_for_review' only when verification is 'verified' and every
    required non-waived criterion is verified. Otherwise 'not_ready'.
    """
    if not has_baseline or len(criteria) == 0:
        return AcceptanceReadiness.NOT_READY

    if verification == Verification.VERIFIED:
        req_non_waived = [
            c
            for c in criteria
            if (c["required"] if isinstance(c, dict) else c.required)
            and (c["status"] if isinstance(c, dict) else c.status) != CriterionStatus.WAIVED
        ]
        if all((c["status"] if isinstance(c, dict) else c.status) == CriterionStatus.VERIFIED for c in req_non_waived):
            return AcceptanceReadiness.READY_FOR_REVIEW

    return AcceptanceReadiness.NOT_READY


def calculate_reported_progress_percent(
    claims: Sequence[dict[str, Any]],
    has_baseline: bool,
) -> float | None:
    """Calculate self-reported progress percentage from un-superseded claims."""
    if not has_baseline:
        return None
    if not claims:
        return 0.0

    done_count = sum(1 for c in claims if c.get("reported_status") == ReportedStatus.CLAIMED_DONE)
    return round((done_count / len(claims)) * 100.0, 1)
