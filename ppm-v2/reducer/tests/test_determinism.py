"""Determinism and purity tests for ppm_reducer.

Verifies that two runs with identical inputs, or with shuffled claims, evidence,
waivers, or criteria, produce identical RFC 8785 / JCS SHA-256 hashes for both
the full single-project assessment and domain findings.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from ppm_reducer import assemble_assessment


def get_fixtures_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "ppm-v2" / "evals" / "fixtures"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "s01-claim-done-test-failed",
        "s02a-pass-old-revision-no-fail",
        "s02b-pass-old-revision-fail-current",
        "s03-arch-drift-no-adr",
        "s07-inferred-dependency",
        "s08-shared-component",
        "s16-concurrent-updates",
        "s21-forged-junit",
        "s22-waiver-expiry",
        "s23-copied-repo",
        "s24-draft-only",
        "s25-active-waiver",
        "s27-in-repo-fail",
        "s28-ci-attested-pass",
    ],
)
def test_determinism_shuffled_inputs(fixture_name: str) -> None:
    fpath = get_fixtures_dir() / fixture_name
    with (fpath / "inputs.json").open("r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    # Run 1: original inputs
    res1 = assemble_assessment(inputs=data, assessment_id="asmt-det-001")
    asmt_dict1 = res1.to_dict(include_explanation=False)
    findings_list1 = [f.to_dict() for f in res1.findings]

    jcs_asmt1 = rfc8785.dumps(asmt_dict1)
    jcs_findings1 = rfc8785.dumps(findings_list1)
    hash_asmt1 = hashlib.sha256(jcs_asmt1).hexdigest()
    hash_findings1 = hashlib.sha256(jcs_findings1).hexdigest()

    # Run 2: shuffled claims, evidence, waivers, criteria order, dependency_refs
    rng = random.Random(42)
    claims_shuffled = list(data.get("claims", []))
    rng.shuffle(claims_shuffled)

    evidence_shuffled = list(data.get("evidence", []))
    rng.shuffle(evidence_shuffled)

    waivers_shuffled = list(data.get("waivers", []))
    rng.shuffle(waivers_shuffled)

    dependency_refs_shuffled = list(data.get("dependency_refs", []))
    rng.shuffle(dependency_refs_shuffled)

    baseline_shuffled = None
    if data.get("baseline"):
        baseline_shuffled = dict(data["baseline"])
        crit_shuffled = list(data["baseline"]["criteria"])
        rng.shuffle(crit_shuffled)
        baseline_shuffled["criteria"] = crit_shuffled

    shuffled_inputs = dict(data)
    shuffled_inputs["claims"] = claims_shuffled
    shuffled_inputs["evidence"] = evidence_shuffled
    shuffled_inputs["waivers"] = waivers_shuffled
    shuffled_inputs["baseline"] = baseline_shuffled
    shuffled_inputs["dependency_refs"] = dependency_refs_shuffled

    res2 = assemble_assessment(inputs=shuffled_inputs, assessment_id="asmt-det-001")
    asmt_dict2 = res2.to_dict(include_explanation=False)
    findings_list2 = [f.to_dict() for f in res2.findings]

    jcs_asmt2 = rfc8785.dumps(asmt_dict2)
    jcs_findings2 = rfc8785.dumps(findings_list2)
    hash_asmt2 = hashlib.sha256(jcs_asmt2).hexdigest()
    hash_findings2 = hashlib.sha256(jcs_findings2).hexdigest()

    assert hash_asmt1 == hash_asmt2, (
        f"[{fixture_name}] Non-deterministic assessment SHA-256 hash across runs with shuffled inputs!"
    )
    assert hash_findings1 == hash_findings2, (
        f"[{fixture_name}] Non-deterministic findings SHA-256 hash across runs with shuffled inputs!"
    )
