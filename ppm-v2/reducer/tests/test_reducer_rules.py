"""Targeted unit tests for specific edge cases and rules.

Covers:
1. TTL boundary strictly greater than, tested at the boundary second.
2. Dangling supersession reference raising InputRejected(reason="dangling_supersession").
3. Unmapped passing test producing status="unverified", reason_code="coverage_not_mapped".
4. Conflicting concurrent claims exposure on CriterionResult.conflicting_claim_refs.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

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


def test_waiver_inactive_conditions() -> None:
    """M2-1: Waiver is active only if approved_by is non-empty AND approved_at is set AND approved_at <= now < expires_at.

    Otherwise ignored (criterion falls back to evidence rules).
    Tested cases:
    1. missing / empty approved_by
    2. null / missing approved_at
    3. approved_at after now (approved_at > now)
    4. expires_at == now (boundary second: inactive)
    """
    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-WV-001",
                "deliverable_id": "DEL-WV",
                "owner_project_id": "proj-wv",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_wv"}],
                "previous_status": None,
            }
        ],
    }
    now_str = "2026-11-05T12:00:00Z"

    # Case 0: Baseline active waiver -> waived
    w_active = [
        {
            "waiver_id": "w-valid",
            "criterion_id": "AC-WV-001",
            "approved_by": "lead-reviewer",
            "approved_at": "2026-11-01T00:00:00Z",
            "expires_at": "2026-11-10T00:00:00Z",
        }
    ]
    res_act = assess_criteria(
        baseline=baseline, claims=[], evidence=[], waivers=w_active, assessed_revision="rev-1", now=now_str
    )
    assert len(res_act) == 1
    assert res_act[0].status == CriterionStatus.WAIVED
    assert res_act[0].reason_code == ReasonCode.WAIVER_APPROVED

    # Case 1: missing / empty approved_by -> inactive -> falls back to evidence_missing
    for bad_by in [None, "", "   "]:
        w_bad_by = [
            {
                "waiver_id": "w-bad-by",
                "criterion_id": "AC-WV-001",
                "approved_by": bad_by,
                "approved_at": "2026-11-01T00:00:00Z",
                "expires_at": "2026-11-10T00:00:00Z",
            }
        ]
        res = assess_criteria(
            baseline=baseline, claims=[], evidence=[], waivers=w_bad_by, assessed_revision="rev-1", now=now_str
        )
        assert len(res) == 1
        assert res[0].status == CriterionStatus.UNVERIFIED
        assert res[0].reason_code == ReasonCode.EVIDENCE_MISSING
        assert res[0].waiver_ref is None

    # Case 2: null / missing approved_at -> inactive -> falls back to evidence_missing
    w_null_app = [
        {
            "waiver_id": "w-null-app",
            "criterion_id": "AC-WV-001",
            "approved_by": "lead-reviewer",
            "approved_at": None,
            "expires_at": "2026-11-10T00:00:00Z",
        }
    ]
    res = assess_criteria(
        baseline=baseline, claims=[], evidence=[], waivers=w_null_app, assessed_revision="rev-1", now=now_str
    )
    assert len(res) == 1
    assert res[0].status == CriterionStatus.UNVERIFIED
    assert res[0].reason_code == ReasonCode.EVIDENCE_MISSING
    assert res[0].waiver_ref is None

    # Case 3: approved_at after now (approved_at > now) -> inactive
    w_future_app = [
        {
            "waiver_id": "w-future-app",
            "criterion_id": "AC-WV-001",
            "approved_by": "lead-reviewer",
            "approved_at": "2026-11-06T00:00:00Z",
            "expires_at": "2026-11-10T00:00:00Z",
        }
    ]
    res = assess_criteria(
        baseline=baseline, claims=[], evidence=[], waivers=w_future_app, assessed_revision="rev-1", now=now_str
    )
    assert len(res) == 1
    assert res[0].status == CriterionStatus.UNVERIFIED
    assert res[0].reason_code == ReasonCode.EVIDENCE_MISSING
    assert res[0].waiver_ref is None

    # Case 4: expires_at == now (boundary second: inactive) -> falls back to evidence_missing
    w_exact_exp = [
        {
            "waiver_id": "w-exact-exp",
            "criterion_id": "AC-WV-001",
            "approved_by": "lead-reviewer",
            "approved_at": "2026-11-01T00:00:00Z",
            "expires_at": now_str,
        }
    ]
    res = assess_criteria(
        baseline=baseline, claims=[], evidence=[], waivers=w_exact_exp, assessed_revision="rev-1", now=now_str
    )
    assert len(res) == 1
    assert res[0].status == CriterionStatus.UNVERIFIED
    assert res[0].reason_code == ReasonCode.EVIDENCE_MISSING
    assert res[0].waiver_ref is None


def test_evidence_from_future() -> None:
    """M2-2: Evidence with observed_at > now raises InputRejected(reason="evidence_from_future")."""
    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-FUT-001",
                "deliverable_id": "DEL-FUT",
                "owner_project_id": "proj-fut",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_fut"}],
                "previous_status": None,
            }
        ],
    }
    now_str = "2026-11-05T12:00:00Z"
    # Evidence 1 second in the future
    future_ev = [
        {
            "evidence_id": "ev-future-1",
            "trust_class": "verifier_observed",
            "revision": "rev-fut-1",
            "observed_at": "2026-11-05T12:00:01Z",
            "test_id": "test_fut",
            "result": "passed",
        }
    ]

    with pytest.raises(InputRejected) as exc_info:
        assess_criteria(
            baseline=baseline,
            claims=[],
            evidence=future_ev,
            waivers=[],
            assessed_revision="rev-fut-1",
            now=now_str,
        )
    assert exc_info.value.reason == "evidence_from_future"


def test_missing_assessed_revision() -> None:
    """M2-3: Baseline present and assessed_revision is None or empty raises InputRejected(reason="missing_assessed_revision")."""
    baseline = {
        "baseline_revision": 1,
        "criteria": [
            {
                "criterion_id": "AC-REV-001",
                "deliverable_id": "DEL-REV",
                "owner_project_id": "proj-rev",
                "weight": 1.0,
                "required": True,
                "verification_refs": [{"kind": "test_id", "ref": "test_rev"}],
                "previous_status": None,
            }
        ],
    }
    now_str = "2026-11-05T12:00:00Z"

    # None revision with baseline present
    with pytest.raises(InputRejected) as exc_none:
        assess_criteria(
            baseline=baseline,
            claims=[],
            evidence=[],
            waivers=[],
            assessed_revision=None,
            now=now_str,
        )
    assert exc_none.value.reason == "missing_assessed_revision"

    # Empty string revision with baseline present
    with pytest.raises(InputRejected) as exc_empty:
        assess_criteria(
            baseline=baseline,
            claims=[],
            evidence=[],
            waivers=[],
            assessed_revision="",
            now=now_str,
        )
    assert exc_empty.value.reason == "missing_assessed_revision"

    # Whitespace-only revision with baseline present
    with pytest.raises(InputRejected) as exc_ws:
        assess_criteria(
            baseline=baseline,
            claims=[],
            evidence=[],
            waivers=[],
            assessed_revision="   ",
            now=now_str,
        )
    assert exc_ws.value.reason == "missing_assessed_revision"

    # Draft-only (no baseline) does NOT raise missing_assessed_revision
    res_draft = assess_criteria(
        baseline=None,
        claims=[],
        evidence=[],
        waivers=[],
        assessed_revision=None,
        now=now_str,
    )
    assert res_draft == []


def test_s24_draft_only_progress_and_readiness() -> None:
    """M0-WP3 s24 rule: absent a baseline, progress basis is none, weights null, readiness not_ready."""
    from ppm_reducer import assemble_assessment

    inputs = {
        "fixture_id": "test-s24",
        "project_id": "proj-draft",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "now": "2026-11-05T12:00:00Z",
        "baseline": None,
        "claims": [],
        "evidence": [],
        "waivers": [],
    }

    res = assemble_assessment(inputs=inputs)
    assert res.progress.basis == "none"
    assert res.progress.verified_weight is None
    assert res.progress.total_weight is None
    assert res.progress.criteria_total == 0
    assert res.progress.criteria_with_current_evidence == 0
    assert res.acceptance_readiness == "not_ready"
    assert res.verification == "unknown"
    assert res.validity == "unknown"
    assert res.missing_evidence == ["No approved baseline revision in PPM domain database"]


def test_s25_active_waiver_excluded_from_denominator() -> None:
    """M0-WP3 s25 rule: total_weight and verified_weight strictly exclude waived criteria."""
    from ppm_reducer import assemble_assessment

    inputs = {
        "fixture_id": "test-s25",
        "project_id": "proj-waiver",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "now": "2026-11-05T12:00:00Z",
        "baseline": {
            "baseline_revision": 1,
            "criteria": [
                {
                    "criterion_id": "AC-001",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": "proj-waiver",
                    "weight": 2.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "t1"}],
                    "previous_status": None,
                },
                {
                    "criterion_id": "AC-002",
                    "deliverable_id": "DEL-2",
                    "owner_project_id": "proj-waiver",
                    "weight": 3.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "t2"}],
                    "previous_status": None,
                },
            ],
        },
        "claims": [],
        "evidence": [
            {
                "evidence_id": "ev-1",
                "trust_class": "verifier_observed",
                "revision": "rev-1",
                "observed_at": "2026-11-05T11:00:00Z",
                "test_id": "t1",
                "covered_test_ids": ["t1"],
                "result": "passed",
                "artifact_path": None,
            }
        ],
        "waivers": [
            {
                "waiver_id": "w-2",
                "criterion_id": "AC-002",
                "scope": "test waiver",
                "rationale": "hardware unavailable",
                "approved_by": "lead",
                "approved_at": "2026-11-01T00:00:00Z",
                "expires_at": "2026-11-10T00:00:00Z",
            }
        ],
    }

    res = assemble_assessment(inputs=inputs)
    # AC-001 is verified (weight 2.0)
    # AC-002 is waived (weight 3.0) -> excluded from total_weight and verified_weight
    assert res.progress.total_weight == 2.0
    assert res.progress.verified_weight == 2.0
    assert res.progress.criteria_total == 2
    assert res.progress.criteria_with_current_evidence == 1
    assert res.verification == "verified"
    assert res.acceptance_readiness == "ready_for_review"


def test_domain_findings_types_and_severities() -> None:
    """Test all 4 domain findings: architecture_drift, relationship_proposed, identity_conflict, conflicting_claims."""
    from ppm_reducer import (
        FindingKind,
        FindingSeverity,
        assemble_assessment,
    )

    inputs = {
        "fixture_id": "test-findings",
        "project_id": "proj-multi-findings",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "now": "2026-11-05T12:00:00Z",
        "baseline": {
            "baseline_revision": 1,
            "criteria": [
                {
                    "criterion_id": "AC-001",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": "proj-multi-findings",
                    "weight": 1.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "t1"}],
                    "previous_status": None,
                }
            ],
        },
        "claims": [
            {
                "claim_id": "c1",
                "source_file": ".ppm/updates/u1.yaml",
                "criterion_ids": ["AC-001"],
                "test_ids": ["t1"],
                "reported_status": "claimed_done",
                "evidence_path": None,
                "working_tree_state": "clean",
                "recorded_at": "2026-11-05T11:00:00Z",
                "summary": "done",
            },
            {
                "claim_id": "c2",
                "source_file": ".ppm/updates/u2.yaml",
                "criterion_ids": ["AC-001"],
                "test_ids": ["t1"],
                "reported_status": "claimed_blocked",
                "evidence_path": None,
                "working_tree_state": "clean",
                "recorded_at": "2026-11-05T11:01:00Z",
                "summary": "blocked",
            },
        ],
        "evidence": [],
        "waivers": [],
        "context": {
            "architecture_drift": {
                "component": "auth_service",
                "implemented_pattern": "plain_jwt",
                "prescribed_pattern": "oauth2_pkce",
                "has_approved_adr": False,
            },
            "relationship_proposals": [
                {
                    "source_project_id": "proj-multi-findings",
                    "target_project_id": "proj-core",
                    "target_deliverable_id": "DEL-CORE",
                    "relation_type": "depends_on",
                    "status": "proposed",
                }
            ],
            "repo_identity": {
                "registered": "git@github.com:org/primary.git",
                "observed": "git@github.com:org/mirror.git",
            },
        },
    }

    res = assemble_assessment(inputs=inputs)
    finding_map = {f.kind: f for f in res.findings}

    # 1. architecture_drift
    assert FindingKind.ARCHITECTURE_DRIFT in finding_map
    ad = finding_map[FindingKind.ARCHITECTURE_DRIFT]
    assert ad.severity == FindingSeverity.REVIEW_REQUIRED
    assert ad.target_ref == "component:auth_service"

    # 2. relationship_proposed
    assert FindingKind.RELATIONSHIP_PROPOSED in finding_map
    rp = finding_map[FindingKind.RELATIONSHIP_PROPOSED]
    assert rp.severity == FindingSeverity.INFO
    assert rp.target_ref == "deliverable:DEL-CORE"

    # 3. identity_conflict
    assert FindingKind.IDENTITY_CONFLICT in finding_map
    ic = finding_map[FindingKind.IDENTITY_CONFLICT]
    assert ic.severity == FindingSeverity.BLOCKER
    assert ic.target_ref == "project:proj-multi-findings"

    # 4. conflicting_claims
    assert FindingKind.CONFLICTING_CLAIMS in finding_map
    cc = finding_map[FindingKind.CONFLICTING_CLAIMS]
    assert cc.severity == FindingSeverity.WARNING
    assert cc.target_ref == "criterion:AC-001"
    assert cc.details["conflicting_updates"] == ["c1", "c2"]
    assert cc.details["claimed_statuses"] == ["claimed_done", "claimed_blocked"]


def test_m3_1_all_waived_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    """M3-1: When every criterion is waived (non-waived denominator empty),

    reducer emits verified_weight=null, total_weight=null, basis='baseline_weighted',
    verification='unknown', acceptance_readiness='not_ready'.
    Validates against contracts/ppm-output.schema.json and eap.contracts rules.
    """
    import json
    from pathlib import Path

    import jsonschema  # type: ignore[import-untyped]

    import eap.contracts.rules as eap_rules
    from eap.contracts.rules import ContractViolation, semantic_checks
    from ppm_reducer import assemble_assessment

    repo_root = Path(__file__).resolve().parents[3]
    schema = json.loads((repo_root / "contracts" / "ppm-output.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    inputs = {
        "project_id": "proj-all-waived",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "now": "2026-11-05T12:00:00Z",
        "baseline": {
            "baseline_revision": 1,
            "criteria": [
                {
                    "criterion_id": "AC-001",
                    "deliverable_id": "DEL-1",
                    "owner_project_id": "proj-all-waived",
                    "weight": 2.0,
                    "required": True,
                    "verification_refs": [{"kind": "test_id", "ref": "t1"}],
                    "previous_status": None,
                },
                {
                    "criterion_id": "AC-002",
                    "deliverable_id": "DEL-2",
                    "owner_project_id": "proj-all-waived",
                    "weight": 3.0,
                    "required": False,
                    "verification_refs": [{"kind": "test_id", "ref": "t2"}],
                    "previous_status": None,
                },
            ],
        },
        "claims": [],
        "evidence": [],
        "waivers": [
            {
                "waiver_id": "w-1",
                "criterion_id": "AC-001",
                "scope": "test waiver 1",
                "rationale": "hardware unavailable",
                "approved_by": "lead",
                "approved_at": "2026-11-01T00:00:00Z",
                "expires_at": "2026-11-10T00:00:00Z",
            },
            {
                "waiver_id": "w-2",
                "criterion_id": "AC-002",
                "scope": "test waiver 2",
                "rationale": "scope postponed",
                "approved_by": "lead",
                "approved_at": "2026-11-01T00:00:00Z",
                "expires_at": "2026-11-10T00:00:00Z",
            },
        ],
    }

    res = assemble_assessment(inputs=inputs)

    # 1. Reducer contract assertions
    assert res.progress.basis == "baseline_weighted"
    assert res.progress.verified_weight is None
    assert res.progress.total_weight is None
    assert res.progress.criteria_total == 2
    assert res.progress.criteria_with_current_evidence == 0
    assert res.verification == "unknown"
    assert res.acceptance_readiness == "not_ready"

    # 2. Schema validation against contracts/ppm-output.schema.json
    doc = res.to_dict(include_explanation=True)
    validator.validate(doc)

    # 3. Platform semantic rules validation:
    # Documented in ISS-NEW-1: current eap.contracts.rules line 225 checks
    # prog.get("total_weight") != total, where total=0 when all criteria are waived.
    # Because total_weight is None (as required by exclusiveMinimum: 0 in schema),
    # unpatched check_ppm_output_rules raises PROGRESS_WEIGHT_MISMATCH.
    with pytest.raises(ContractViolation) as exc_info:
        semantic_checks("ppm-output", doc)
    assert exc_info.value.code == "PROGRESS_WEIGHT_MISMATCH"

    # With the proposed platform rule fix for ISS-NEW-1 (allowing total_weight=None when non-waived total is 0):
    orig_check = eap_rules.check_ppm_output_rules

    def patched_check(output: dict[str, Any]) -> None:
        val = eap_rules._to_dict(output)
        prog = val.get("progress", {})
        crits = val.get("criteria", [])
        total = sum(c.get("weight", 0) for c in crits if c.get("status") != "waived")
        if total == 0 and prog.get("total_weight") is None and prog.get("verified_weight") is None:
            prog_copy = dict(prog)
            prog_copy["total_weight"] = 0
            prog_copy["verified_weight"] = 0
            val_copy = dict(val)
            val_copy["progress"] = prog_copy
            orig_check(val_copy)
            return
        orig_check(val)

    monkeypatch.setattr(eap_rules, "check_ppm_output_rules", patched_check)
    semantic_checks("ppm-output", doc)  # Passes cleanly!


def test_m3_2_invalid_baseline_weight() -> None:
    """M3-2: Reject invalid baseline input: weight missing, non-numeric, or <= 0

    raises InputRejected(reason="invalid_weight").
    """
    from ppm_reducer import assemble_assessment, assess_criteria

    base_inputs = {
        "project_id": "proj-invalid-weight",
        "snapshot_refs": ["snap-1"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": "rev-1",
        "now": "2026-11-05T12:00:00Z",
        "claims": [],
        "evidence": [],
        "waivers": [],
    }

    invalid_weights: list[Any] = [
        None,            # missing / None
        "high",          # string
        "1.0",           # string numeric
        True,            # boolean True
        False,           # boolean False
        [1.0],           # list
        {"w": 1},        # dict
        0,               # zero int
        0.0,             # zero float
        -1,              # negative int
        -0.001,          # negative float
        float("nan"),    # NaN
    ]

    for bad_w in invalid_weights:
        crit: dict[str, Any] = {
            "criterion_id": "AC-BAD-WEIGHT",
            "deliverable_id": "DEL-1",
            "owner_project_id": "proj-invalid-weight",
            "required": True,
            "verification_refs": [{"kind": "test_id", "ref": "t1"}],
        }
        crit["weight"] = bad_w

        # 1. Test assess_criteria
        with pytest.raises(InputRejected) as exc_info:
            assess_criteria(
                baseline={"baseline_revision": 1, "criteria": [crit]},
                claims=[],
                evidence=[],
                waivers=[],
                assessed_revision="rev-1",
                now="2026-11-05T12:00:00Z",
            )
        assert exc_info.value.reason == "invalid_weight", f"Failed for {bad_w}: {exc_info.value.reason}"

        # 2. Test assemble_assessment
        inp = dict(base_inputs)
        inp["baseline"] = {"baseline_revision": 1, "criteria": [crit]}
        with pytest.raises(InputRejected) as exc_info:
            assemble_assessment(inputs=inp)
        assert exc_info.value.reason == "invalid_weight", f"Failed for {bad_w}: {exc_info.value.reason}"

    # Also test completely omitted "weight" key
    crit_omitted: dict[str, Any] = {
        "criterion_id": "AC-OMITTED-WEIGHT",
        "deliverable_id": "DEL-1",
        "owner_project_id": "proj-invalid-weight",
        "required": True,
        "verification_refs": [{"kind": "test_id", "ref": "t1"}],
    }
    with pytest.raises(InputRejected) as exc_info:
        assemble_assessment(inputs={**base_inputs, "baseline": {"baseline_revision": 1, "criteria": [crit_omitted]}})
    assert exc_info.value.reason == "invalid_weight"



