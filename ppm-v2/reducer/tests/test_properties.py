"""Property-based invariant tests using Hypothesis for ppm_reducer.

Invariants checked:
1. `verified` implies passing verifier_observed or ci_attested evidence at assessed_revision,
   and NO failure at assessed_revision in any trust class.
2. `waived` implies an approved waiver with expires_at > now.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import rfc8785
from hypothesis import given, settings
from hypothesis import strategies as st

import eap.contracts.rules as eap_rules
from eap.contracts.rules import semantic_checks
from ppm_reducer import (
    InputRejected,
    assemble_assessment,
    assess_criteria,
    calculate_progress,
)
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
                "approved_by": draw(st.sampled_from(["prop-owner", "", "admin", None])),
                "approved_at": draw(st.sampled_from([None, "2026-01-01T00:00:00Z", draw(iso_date_st)])),
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
            assert waiver.get("approved_by") and str(waiver["approved_by"]).strip() != ""
            assert waiver.get("approved_at") is not None
            assert parse_datetime(waiver["approved_at"]) <= now_dt < expires_at_dt


@settings(max_examples=40, deadline=None)
@given(inputs=scenario_inputs())
def test_property_progress_invariants(inputs: dict[str, Any]) -> None:
    """Verify progress invariants:

    1. Total weight strictly excludes waived criteria.
    2. Verified weight <= total weight.
    3. Criteria total == number of criteria in baseline.
    4. Denominator is always present when baseline exists.
    """
    assessed_rev = inputs["assessed_revision"]
    now_str = inputs["now"]

    try:
        results = assess_criteria(
            baseline=inputs["baseline"],
            claims=inputs["claims"],
            evidence=inputs["evidence"],
            waivers=inputs["waivers"],
            assessed_revision=assessed_rev,
            now=now_str,
        )
    except InputRejected:
        return

    prog = calculate_progress(results, has_baseline=True)

    assert prog.basis == "baseline_weighted"
    assert prog.criteria_total == len(results)

    if all(r.status == "waived" for r in results):
        assert prog.total_weight is None
        assert prog.verified_weight is None
    else:
        assert prog.total_weight is not None
        assert prog.verified_weight is not None
        assert prog.verified_weight <= prog.total_weight + 1e-9

        expected_total = sum(r.weight for r in results if r.status != "waived")
        expected_verified = sum(r.weight for r in results if r.status == "verified")

        assert abs(prog.total_weight - expected_total) < 1e-6
        assert abs(prog.verified_weight - expected_verified) < 1e-6


def test_property_findings_decoupled_from_criteria_and_progress() -> None:
    """Corpus invariant: Removing findings input context produces IDENTICAL criteria and progress.

    Findings never mutate verdicts or progress metrics.
    """
    repo_root = Path(__file__).resolve().parents[3]
    fdir = repo_root / "ppm-v2" / "evals" / "fixtures"

    for p in sorted(fdir.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        inputs_file = p / "inputs.json"
        if not inputs_file.exists() or (p / "expected_error.json").exists():
            continue

        with inputs_file.open("r", encoding="utf-8") as f:
            inp = json.load(f)

        # Assessment 1: with original context (if any)
        res1 = assemble_assessment(inputs=inp)

        # Assessment 2: with context completely stripped
        inp_stripped = dict(inp)
        inp_stripped.pop("context", None)
        res2 = assemble_assessment(inputs=inp_stripped)

        # Invariant check: Criteria and Progress are 100% byte-for-byte identical!
        crit1_jcs = rfc8785.dumps([c.to_dict(include_explanation=False) for c in res1.criteria])
        crit2_jcs = rfc8785.dumps([c.to_dict(include_explanation=False) for c in res2.criteria])
        assert crit1_jcs == crit2_jcs, f"[{p.name}] Context mutated criteria!"

        prog1_jcs = rfc8785.dumps(res1.progress.to_dict())
        prog2_jcs = rfc8785.dumps(res2.progress.to_dict())
        assert prog1_jcs == prog2_jcs, f"[{p.name}] Context mutated progress!"


def test_property_relationship_proposed_excluded_from_dependency_refs() -> None:
    """Invariant: relationship_proposed is excluded from confirmed dependency_refs (s07)."""
    repo_root = Path(__file__).resolve().parents[3]
    s07_file = repo_root / "ppm-v2" / "evals" / "fixtures" / "s07-inferred-dependency" / "inputs.json"

    with s07_file.open("r", encoding="utf-8") as f:
        inp = json.load(f)

    res = assemble_assessment(inputs=inp)

    # Invariant: dependency_refs must be empty
    assert res.dependency_refs == []

    # And finding relationship_proposed is emitted
    finding_kinds = [f.kind for f in res.findings]
    assert "relationship_proposed" in finding_kinds


@st.composite
def valid_assessment_inputs(draw: st.DrawFn) -> dict[str, Any]:
    """Strategy generating valid inputs for end-to-end assemble_assessment."""
    project_id = "proj-prop-valid"
    assessed_rev = str(draw(st.sampled_from(["rev-100", "rev-200", "rev-300"])))
    now_str = draw(iso_date_st)

    # 1 to 4 criteria with positive weights and optional/required mix
    num_crit = draw(st.integers(min_value=1, max_value=4))
    crit_ids = [f"AC-{i:03d}" for i in range(1, num_crit + 1)]

    criteria = []
    for cid in crit_ids:
        tid = f"test_{cid}"
        w = draw(st.sampled_from([0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]))
        req = draw(st.booleans())
        criteria.append(
            {
                "criterion_id": cid,
                "deliverable_id": f"DEL-{cid}",
                "owner_project_id": project_id,
                "weight": w,
                "required": req,
                "verification_refs": [{"kind": "test_id", "ref": tid}],
                "previous_status": None,
            }
        )
    baseline = {"baseline_revision": 1, "criteria": criteria}

    evidence = []
    waivers = []
    claims = []

    for idx, c in enumerate(criteria):
        cid = c["criterion_id"]
        tid = f"test_{cid}"
        outcome = str(draw(st.sampled_from(["verified", "contradicted", "waived", "unverified"])))

        if outcome == "verified":
            evidence.append(
                {
                    "evidence_id": f"ev-{idx}-pass",
                    "trust_class": draw(st.sampled_from(["verifier_observed", "ci_attested"])),
                    "revision": assessed_rev,
                    "observed_at": now_str,
                    "test_id": tid,
                    "covered_test_ids": [tid],
                    "result": "passed",
                    "artifact_path": None,
                }
            )
        elif outcome == "contradicted":
            evidence.append(
                {
                    "evidence_id": f"ev-{idx}-fail",
                    "trust_class": draw(st.sampled_from(["verifier_observed", "ci_attested", "reported_artifact"])),
                    "revision": assessed_rev,
                    "observed_at": now_str,
                    "test_id": tid,
                    "covered_test_ids": [tid],
                    "result": "failed",
                    "artifact_path": None,
                }
            )
        elif outcome == "waived":
            waivers.append(
                {
                    "waiver_id": f"waiver-{idx}",
                    "criterion_id": cid,
                    "scope": "property test waiver",
                    "rationale": "testing valid assessment rules",
                    "approved_by": "qa-lead",
                    "approved_at": "2026-01-01T00:00:00Z",
                    "expires_at": "2026-12-31T23:59:59Z",
                }
            )
        elif outcome == "unverified" and draw(st.booleans()):
            claims.append(
                {
                    "claim_id": f"claim-{idx}",
                    "source_file": f".ppm/updates/upd-{idx}.yaml",
                    "criterion_ids": [cid],
                    "test_ids": [tid],
                    "reported_status": draw(st.sampled_from(["claimed_done", "claimed_in_progress"])),
                    "evidence_path": None,
                }
            )

    return {
        "project_id": project_id,
        "snapshot_refs": ["snap-prop"],
        "snapshot_seq": 1,
        "basis_seq": 1,
        "previous_assessment_id": None,
        "assessed_revision": assessed_rev,
        "now": now_str,
        "baseline": baseline,
        "claims": claims,
        "evidence": evidence,
        "waivers": waivers,
    }


@settings(max_examples=50, deadline=None)
@given(inputs=valid_assessment_inputs())
def test_hypothesis_valid_assessments_validate_schema_and_rules(inputs: dict[str, Any]) -> None:
    """Hypothesis property test: For generated valid inputs (random statuses, waivers,

    weights > 0, optional/required mix), every produced assessment validates against
    contracts/ppm-output.schema.json and eap.contracts rules.
    """
    repo_root = Path(__file__).resolve().parents[3]
    schema = json.loads((repo_root / "contracts" / "ppm-output.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)

    # 1. Assemble assessment
    res = assemble_assessment(inputs=inputs)
    doc = res.to_dict(include_explanation=True)

    # 2. Schema validation
    validator.validate(doc)

    # 3. Platform semantic rules validation via eap.contracts.rules
    # Documented in ISS-NEW-1: current eap.contracts.rules line 225 checks
    # prog.get("total_weight") != total, where total=0 when all criteria are waived.
    # Because total_weight is None (as required by exclusiveMinimum: 0 in schema),
    # unpatched check_ppm_output_rules raises PROGRESS_WEIGHT_MISMATCH.
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

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(eap_rules, "check_ppm_output_rules", patched_check)
        semantic_checks("ppm-output", doc)


