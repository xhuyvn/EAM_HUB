"""Tests for ISS-019 semantic rules fix in eap.contracts.rules.

Enforces:
1. All-waived assessment with total_weight=None and verified_weight=None passes rules.
2. verified and ready_for_review require at least one verified criterion (VERIFIED_PROJECT_UNMET_CRITERIA,
   ACCEPTANCE_READINESS_UNVERIFIED).
3. Non-waived total=0 with numeric weights rejected (PROGRESS_WEIGHT_MISMATCH).
"""

from __future__ import annotations

import pytest

from eap.contracts.rules import ContractViolation, check_ppm_output_rules


def _base_all_waived_doc() -> dict:
    return {
        "contract_version": "1.1",
        "project_id": "proj-all-waived",
        "assessment_id": "assess-waived-001",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "baseline_revision": 1,
        "as_of": "2026-11-05T12:00:00Z",
        "verification": "unknown",
        "acceptance_readiness": "not_ready",
        "validity": "current",
        "trigger": "snapshot",
        "missing_evidence": [],
        "criteria": [
            {
                "criterion_id": "AC-001",
                "deliverable_id": "DEL-1",
                "owner_project_id": "proj-all-waived",
                "weight": 1.0,
                "required": True,
                "status": "waived",
                "reason_code": "waiver_approved",
                "supporting_evidence_refs": [],
                "refuting_evidence_refs": [],
                "missing_evidence": [],
                "explanation": "Waived by owner",
                "reported_status": "none",
                "reported_claim_refs": [],
                "evidence_revision": None,
                "covered_test_ids": [],
                "previous_status": None,
                "evidence_trust": "untrusted",
                "waiver_ref": "waiver-001",
            },
            {
                "criterion_id": "AC-002",
                "deliverable_id": "DEL-1",
                "owner_project_id": "proj-all-waived",
                "weight": 2.0,
                "required": True,
                "status": "waived",
                "reason_code": "waiver_approved",
                "supporting_evidence_refs": [],
                "refuting_evidence_refs": [],
                "missing_evidence": [],
                "explanation": "Waived by owner",
                "reported_status": "none",
                "reported_claim_refs": [],
                "evidence_revision": None,
                "covered_test_ids": [],
                "previous_status": None,
                "evidence_trust": "untrusted",
                "waiver_ref": "waiver-002",
            },
        ],
        "dependency_refs": [],
        "progress": {
            "basis": "baseline_weighted",
            "verified_weight": None,
            "total_weight": None,
            "criteria_total": 2,
            "criteria_with_current_evidence": 0,
        },
        "reported_progress_percent": None,
    }


def test_iss019_all_waived_passes_rules() -> None:
    """An all-waived assessment with null weights passes check_ppm_output_rules."""
    doc = _base_all_waived_doc()
    # Must not raise
    check_ppm_output_rules(doc)


def test_iss019_all_waived_cannot_be_verified() -> None:
    """An all-waived assessment cannot be verified if zero criteria are verified."""
    doc = _base_all_waived_doc()
    doc["verification"] = "verified"
    with pytest.raises(ContractViolation) as exc_info:
        check_ppm_output_rules(doc)
    assert exc_info.value.code == "VERIFIED_PROJECT_UNMET_CRITERIA"


def test_iss019_all_waived_cannot_be_ready_for_review() -> None:
    """An all-waived assessment cannot be ready_for_review if zero criteria are verified."""
    doc = _base_all_waived_doc()
    doc["acceptance_readiness"] = "ready_for_review"
    with pytest.raises(ContractViolation) as exc_info:
        check_ppm_output_rules(doc)
    assert exc_info.value.code == "ACCEPTANCE_READINESS_UNVERIFIED"


def test_iss019_all_waived_rejects_mismatched_weights() -> None:
    """When non-waived denominator is empty, mismatched positive weights violate rules."""
    doc = _base_all_waived_doc()
    doc["progress"]["total_weight"] = 5.0
    doc["progress"]["verified_weight"] = 2.0
    with pytest.raises(ContractViolation) as exc_info:
        check_ppm_output_rules(doc)
    assert exc_info.value.code == "PROGRESS_WEIGHT_MISMATCH"
