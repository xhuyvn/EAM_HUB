"""Property-based invariant tests using Hypothesis for ppm_reducer.

Invariants checked:
1. `verified` implies passing verifier_observed or ci_attested evidence at assessed_revision,
   and NO failure at assessed_revision in any trust class.
2. `waived` implies an approved waiver with expires_at > now.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from ppm_reducer import InputRejected, assess_criteria
from ppm_reducer.trust import parse_datetime

# Strategies for generating domain structures
criterion_id_st = st.sampled_from(["AC-001", "AC-002", "AC-003", "AC-004"])
test_id_st = st.sampled_from(["test_unit_1", "test_integration_2", "test_e2e_3"])
trust_class_st = st.sampled_from(["verifier_observed", "ci_attested", "reported_artifact", "none"])
result_st = st.sampled_from(["passed", "failed", "inconclusive", "not_run"])
revision_st = st.sampled_from(["rev-100", "rev-200", "rev-300"])

iso_date_st = st.datetimes(
    min_value=dt.datetime(2026, 1, 1),
    max_value=dt.datetime(2026, 12, 31),
    timezones=st.just(dt.UTC),
).map(lambda d: d.isoformat().replace("+00:00", "Z"))


@st.composite
def scenario_inputs(draw: st.DrawFn) -> dict[str, Any]:
    now_str = draw(iso_date_st)
    assessed_rev = draw(revision_st)

    # Criteria in baseline
    num_crit = draw(st.integers(min_value=1, max_value=3))
    selected_cids = draw(st.sets(criterion_id_st, min_size=num_crit, max_size=num_crit))

    criteria = []
    for cid in selected_cids:
        tid = draw(test_id_st)
        criteria.append(
            {
                "criterion_id": cid,
                "deliverable_id": f"DEL-{cid}",
                "owner_project_id": "proj-prop",
                "weight": draw(st.floats(min_value=0.5, max_value=5.0)),
                "required": draw(st.booleans()),
                "verification_refs": [{"kind": "test_id", "ref": tid}],
                "previous_status": None,
            }
        )
    baseline = {"baseline_revision": 1, "criteria": criteria}

    # Evidence items
    num_ev = draw(st.integers(min_value=0, max_value=5))
    evidence = []
    for i in range(num_ev):
        evidence.append(
            {
                "evidence_id": f"ev-prop-{i}",
                "trust_class": draw(trust_class_st),
                "revision": draw(revision_st),
                "observed_at": draw(iso_date_st),
                "test_id": draw(test_id_st),
                "covered_test_ids": [draw(test_id_st)],
                "result": draw(result_st),
                "artifact_path": None,
            }
        )

    # Waivers
    num_w = draw(st.integers(min_value=0, max_value=3))
    waivers = []
    for i in range(num_w):
        waivers.append(
            {
                "waiver_id": f"waiver-prop-{i}",
                "criterion_id": draw(criterion_id_st),
                "scope": "property test waiver",
                "rationale": "testing invariants",
                "approved_by": "prop-owner",
                "approved_at": "2026-01-01T00:00:00Z",
                "expires_at": draw(iso_date_st),
            }
        )

    # Claims (non-cyclic)
    claims = [
        {
            "claim_id": "claim-prop-0",
            "source_file": ".ppm/updates/upd-0.yaml",
            "criterion_ids": list(selected_cids),
            "test_ids": [draw(test_id_st)],
            "reported_status": draw(st.sampled_from(["claimed_done", "claimed_in_progress", "none"])),
            "evidence_path": None,
        }
    ]

    return {
        "baseline": baseline,
        "claims": claims,
        "evidence": evidence,
        "waivers": waivers,
        "assessed_revision": assessed_rev,
        "now": now_str,
        "evidence_ttl_days": 7.0,
    }


@settings(max_examples=100)
@given(scenario_inputs())
def test_hypothesis_invariants(inputs: dict[str, Any]) -> None:
    try:
        results = assess_criteria(
            baseline=inputs["baseline"],
            claims=inputs["claims"],
            evidence=inputs["evidence"],
            waivers=inputs["waivers"],
            assessed_revision=inputs["assessed_revision"],
            now=inputs["now"],
            evidence_ttl_days=inputs["evidence_ttl_days"],
        )
    except InputRejected:
        return

    now_dt = parse_datetime(inputs["now"])
    assessed_rev = inputs["assessed_revision"]

    for r in results:
        # Invariant 1: verified implies verifier_observed or ci_attested PASS at assessed_revision
        # AND strictly zero FAIL at assessed_revision
        if r.status == "verified":
            assert r.evidence_trust in ("verifier_observed", "ci_attested")
            assert r.evidence_revision == assessed_rev
            assert len(r.supporting_evidence_refs) > 0
            assert len(r.refuting_evidence_refs) == 0

            # Verify that passing evidence exists in input
            passing_ev = [
                ev
                for ev in inputs["evidence"]
                if ev.get("evidence_id") in r.supporting_evidence_refs
            ]
            assert len(passing_ev) > 0
            for ev in passing_ev:
                assert ev.get("revision") == assessed_rev
                assert ev.get("result") == "passed"
                assert ev.get("trust_class") in ("verifier_observed", "ci_attested")

            # Verify zero FAIL at assessed_revision for this criterion's mapped tests
            crit_obj = next(
                c for c in inputs["baseline"]["criteria"] if c["criterion_id"] == r.criterion_id
            )
            mapped_tests = {ref["ref"] for ref in crit_obj.get("verification_refs", [])}
            failing_current = [
                ev
                for ev in inputs["evidence"]
                if ev.get("revision") == assessed_rev
                and ev.get("result") == "failed"
                and (
                    ev.get("test_id") in mapped_tests
                    or any(tid in mapped_tests for tid in ev.get("covered_test_ids", []))
                )
            ]
            assert len(failing_current) == 0

        # Invariant 2: waived implies an unexpired approved waiver
        if r.status == "waived":
            assert r.waiver_ref is not None
            assert r.evidence_trust == "none"
            assert r.evidence_revision is None

            matching_waivers = [
                w
                for w in inputs["waivers"]
                if w.get("waiver_id") == r.waiver_ref
                and w.get("criterion_id") == r.criterion_id
            ]
            assert len(matching_waivers) > 0
            waiver = matching_waivers[0]
            expires_at_dt = parse_datetime(waiver["expires_at"])
            assert expires_at_dt > now_dt
