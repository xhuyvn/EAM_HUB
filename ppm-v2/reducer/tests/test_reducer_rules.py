"""Targeted unit tests for specific edge cases and rules.

Covers:
1. TTL boundary strictly greater than, tested at the boundary second.
2. Dangling supersession reference raising InputRejected(reason="dangling_supersession").
3. Unmapped passing test producing status="unverified", reason_code="coverage_not_mapped".
4. Conflicting concurrent claims exposure on CriterionResult.conflicting_claim_refs.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ppm_reducer import (
    CriterionStatus,
    InputRejected,
    ReasonCode,
    ReportedStatus,
    assess_criteria,
)
from ppm_reducer.trust import is_evidence_ttl_expired


def test_ttl_boundary_second() -> None:
    """Test the evidence TTL boundary at the exact boundary second.

    Boundary rule: Strictly greater than (age > ttl_days).
    At age == ttl_days: fresh.
    At age == ttl_days + 1 second: stale.
    """
    now_dt = dt.datetime(2026, 11, 5, 12, 0, 0, tzinfo=dt.UTC)
    ttl_days = 7.0
    ttl_delta = dt.timedelta(days=ttl_days)

    # 1. Exact boundary: now - 7 days (exact second)
    exact_boundary = now_dt - ttl_delta
    assert not is_evidence_ttl_expired(exact_boundary, now_dt, ttl_days), "Exact boundary must be fresh"

    # 2. 1 second older than boundary: now - 7 days - 1s
    stale_boundary = exact_boundary - dt.timedelta(seconds=1)
    assert is_evidence_ttl_expired(stale_boundary, now_dt, ttl_days), "Boundary second past TTL must be stale"

    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-TTL-001",
                "deliverable_id": "DEL-TTL",
                "owner_project_id": "proj-ttl",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_ttl"}],
                "previous_status": None,
            }
        ],
    }

    # Test exact boundary in assess_criteria -> verified
    ev_fresh = [
        {
            "evidence_id": "ev-fresh",
            "trust_class": "verifier_observed",
            "revision": "rev-1",
            "observed_at": exact_boundary.isoformat().replace("+00:00", "Z"),
            "test_id": "test_ttl",
            "covered_test_ids": ["test_ttl"],
            "result": "passed",
            "artifact_path": None,
        }
    ]
    res_fresh = assess_criteria(
        baseline=baseline,
        claims=[],
        evidence=ev_fresh,
        waivers=[],
        assessed_revision="rev-1",
        now=now_dt.isoformat().replace("+00:00", "Z"),
        evidence_ttl_days=ttl_days,
    )
    assert len(res_fresh) == 1
    assert res_fresh[0].status == CriterionStatus.VERIFIED
    assert res_fresh[0].reason_code == ReasonCode.EVIDENCE_CURRENT_PASS

    # Test stale by 1 second in assess_criteria -> stale
    ev_stale = [
        {
            "evidence_id": "ev-stale",
            "trust_class": "verifier_observed",
            "revision": "rev-1",
            "observed_at": stale_boundary.isoformat().replace("+00:00", "Z"),
            "test_id": "test_ttl",
            "covered_test_ids": ["test_ttl"],
            "result": "passed",
            "artifact_path": None,
        }
    ]
    res_stale = assess_criteria(
        baseline=baseline,
        claims=[],
        evidence=ev_stale,
        waivers=[],
        assessed_revision="rev-1",
        now=now_dt.isoformat().replace("+00:00", "Z"),
        evidence_ttl_days=ttl_days,
    )
    assert len(res_stale) == 1
    assert res_stale[0].status == CriterionStatus.STALE
    assert res_stale[0].reason_code == ReasonCode.EVIDENCE_STALE_REVISION


def test_dangling_supersession() -> None:
    """Claims referencing nonexistent supersedes_update_id must raise InputRejected(dangling_supersession)."""
    claims = [
        {
            "claim_id": "claim-001",
            "source_file": ".ppm/updates/upd-001.yaml",
            "criterion_ids": ["AC-001"],
            "reported_status": "claimed_done",
            "supersedes_update_id": "upd-nonexistent-999",
        }
    ]
    with pytest.raises(InputRejected) as exc_info:
        assess_criteria(
            baseline={"baseline_revision": 1, "criteria": []},
            claims=claims,
            evidence=[],
            waivers=[],
            assessed_revision="rev-1",
            now="2026-11-05T12:00:00Z",
        )
    assert exc_info.value.reason == "dangling_supersession"


def test_unmapped_pass_coverage_not_mapped() -> None:
    """A test that passed but is not in baseline verification_refs produces coverage_not_mapped."""
    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-MAP-001",
                "deliverable_id": "DEL-MAP",
                "owner_project_id": "proj-map",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_approved_suite"}],
                "previous_status": None,
            }
        ],
    }
    # Claim points to criterion and specifies unmapped test
    claims = [
        {
            "claim_id": "claim-unmapped",
            "source_file": ".ppm/updates/upd-001.yaml",
            "criterion_ids": ["AC-MAP-001"],
            "test_ids": ["test_unapproved_random"],
            "reported_status": "claimed_done",
        }
    ]
    # Evidence ran unapproved test and passed
    evidence = [
        {
            "evidence_id": "ev-unapproved",
            "trust_class": "verifier_observed",
            "revision": "rev-map-1",
            "observed_at": "2026-11-05T10:00:00Z",
            "test_id": "test_unapproved_random",
            "covered_test_ids": ["test_unapproved_random"],
            "result": "passed",
        }
    ]

    res = assess_criteria(
        baseline=baseline,
        claims=claims,
        evidence=evidence,
        waivers=[],
        assessed_revision="rev-map-1",
        now="2026-11-05T12:00:00Z",
    )
    assert len(res) == 1
    assert res[0].status == CriterionStatus.UNVERIFIED
    assert res[0].reason_code == ReasonCode.COVERAGE_NOT_MAPPED


def test_conflicting_claims_exposed() -> None:
    """Conflicting concurrent claims expose conflicting_claim_refs for WP3 findings."""
    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-CONF-001",
                "deliverable_id": "DEL-CONF",
                "owner_project_id": "proj-conf",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_conf"}],
                "previous_status": None,
            }
        ],
    }
    claims = [
        {
            "claim_id": "claim-a",
            "source_file": ".ppm/updates/upd-a.yaml",
            "criterion_ids": ["AC-CONF-001"],
            "test_ids": ["test_conf"],
            "reported_status": "claimed_done",
        },
        {
            "claim_id": "claim-b",
            "source_file": ".ppm/updates/upd-b.yaml",
            "criterion_ids": ["AC-CONF-001"],
            "test_ids": ["test_conf"],
            "reported_status": "claimed_blocked",
        },
    ]

    res = assess_criteria(
        baseline=baseline,
        claims=claims,
        evidence=[],
        waivers=[],
        assessed_revision="rev-conf-1",
        now="2026-11-05T12:00:00Z",
    )
    assert len(res) == 1
    assert res[0].status == CriterionStatus.UNVERIFIED
    assert res[0].reason_code == ReasonCode.CLAIM_ONLY
    # Highest priority reported status
    assert res[0].reported_status == ReportedStatus.CLAIMED_DONE
    # Exposed conflicting claims
    assert "claim-a" in res[0].conflicting_claim_refs
    assert "claim-b" in res[0].conflicting_claim_refs
