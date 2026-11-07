"""Determinism and purity tests for ppm_reducer.

Verifies that two runs with identical inputs, or with shuffled claims, evidence,
waivers, or criteria, produce identical RFC 8785 / JCS SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from ppm_reducer import assess_criteria


def get_fixtures_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "ppm-v2" / "evals" / "fixtures"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "s01-claim-done-test-failed",
        "s02a-pass-old-revision-no-fail",
        "s02b-pass-old-revision-fail-current",
        "s08-shared-component",
        "s16-concurrent-updates",
        "s21-forged-junit",
        "s22-waiver-expiry",
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
    res1 = assess_criteria(
        baseline=data.get("baseline"),
        claims=data.get("claims", []),
        evidence=data.get("evidence", []),
        waivers=data.get("waivers", []),
        assessed_revision=data.get("assessed_revision"),
        now=data["now"],
        evidence_ttl_days=data.get("evidence_ttl_days", 7.0),
    )
    jcs1 = rfc8785.dumps([r.to_dict(include_explanation=False) for r in res1])
    hash1 = hashlib.sha256(jcs1).hexdigest()

    # Run 2: shuffled claims, evidence, waivers, criteria order
    rng = random.Random(42)
    claims_shuffled = list(data.get("claims", []))
    rng.shuffle(claims_shuffled)

    evidence_shuffled = list(data.get("evidence", []))
    rng.shuffle(evidence_shuffled)

    waivers_shuffled = list(data.get("waivers", []))
    rng.shuffle(waivers_shuffled)

    baseline_shuffled = None
    if data.get("baseline"):
        baseline_shuffled = dict(data["baseline"])
        crit_shuffled = list(data["baseline"]["criteria"])
        rng.shuffle(crit_shuffled)
        baseline_shuffled["criteria"] = crit_shuffled

    res2 = assess_criteria(
        baseline=baseline_shuffled,
        claims=claims_shuffled,
        evidence=evidence_shuffled,
        waivers=waivers_shuffled,
        assessed_revision=data.get("assessed_revision"),
        now=data["now"],
        evidence_ttl_days=data.get("evidence_ttl_days", 7.0),
    )
    jcs2 = rfc8785.dumps([r.to_dict(include_explanation=False) for r in res2])
    hash2 = hashlib.sha256(jcs2).hexdigest()

    assert hash1 == hash2, f"[{fixture_name}] Non-deterministic SHA-256 hash across runs with shuffled inputs!"
