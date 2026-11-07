"""Corpus validation tests for ppm_reducer.

Verifies that assess_criteria matches handwritten and signed expected.json criteria[]
byte-for-byte post RFC 8785/JCS normalization for every fixture in the corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785

from ppm_reducer import InputRejected, assess_criteria


def get_fixtures_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "ppm-v2" / "evals" / "fixtures"


def get_all_fixture_dirs() -> list[Path]:
    fdir = get_fixtures_dir()
    return sorted([d for d in fdir.iterdir() if d.is_dir() and not d.name.startswith(".")])


@pytest.mark.parametrize("fpath", get_all_fixture_dirs(), ids=lambda p: p.name)
def test_fixture_corpus(fpath: Path) -> None:
    fid = fpath.name
    inputs_file = fpath / "inputs.json"
    assert inputs_file.exists(), f"[{fid}] Missing inputs.json"

    with inputs_file.open("r", encoding="utf-8") as f:
        inputs_data: dict[str, Any] = json.load(f)

    expected_error_file = fpath / "expected_error.json"
    if expected_error_file.exists():
        with expected_error_file.open("r", encoding="utf-8") as f:
            err_data = json.load(f)
        exp_reason = err_data.get("reason")
        with pytest.raises(InputRejected) as exc_info:
            assess_criteria(
                baseline=inputs_data.get("baseline"),
                claims=inputs_data.get("claims", []),
                evidence=inputs_data.get("evidence", []),
                waivers=inputs_data.get("waivers", []),
                assessed_revision=inputs_data.get("assessed_revision"),
                now=inputs_data["now"],
                evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
            )
        assert exc_info.value.reason == exp_reason, (
            f"[{fid}] InputRejected reason {exc_info.value.reason} != expected {exp_reason}"
        )
        return

    expected_file = fpath / "expected.json"
    assert expected_file.exists(), f"[{fid}] Missing expected.json"
    with expected_file.open("r", encoding="utf-8") as f:
        expected_data: dict[str, Any] = json.load(f)

    exp_criteria = [dict(c) for c in expected_data.get("criteria", [])]
    for c in exp_criteria:
        c.pop("explanation", None)

    produced_results = assess_criteria(
        baseline=inputs_data.get("baseline"),
        claims=inputs_data.get("claims", []),
        evidence=inputs_data.get("evidence", []),
        waivers=inputs_data.get("waivers", []),
        assessed_revision=inputs_data.get("assessed_revision"),
        now=inputs_data["now"],
        evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
    )

    produced_criteria = [r.to_dict(include_explanation=False) for r in produced_results]

    if fid == "s24-draft-only":
        assert produced_results == []
        assert exp_criteria == []

    jcs_produced = rfc8785.dumps(produced_criteria)
    jcs_expected = rfc8785.dumps(exp_criteria)

    assert jcs_produced == jcs_expected, (
        f"[{fid}] JCS byte mismatch!\n"
        f"PRODUCED: {json.dumps(produced_criteria, indent=2)}\n"
        f"EXPECTED: {json.dumps(exp_criteria, indent=2)}"
    )


def test_corpus_summary_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Print per-fixture PASS table required by acceptance suite."""
    fixture_dirs = get_all_fixture_dirs()
    print("\n" + "=" * 70)
    print(f"{'Fixture ID':<40} | {'Criteria':<10} | {'Status'}")
    print("-" * 70)

    for fpath in fixture_dirs:
        fid = fpath.name
        with (fpath / "inputs.json").open("r", encoding="utf-8") as f:
            inputs_data = json.load(f)

        if (fpath / "expected_error.json").exists():
            with (fpath / "expected_error.json").open("r", encoding="utf-8") as f:
                err_data = json.load(f)
            try:
                assess_criteria(
                    baseline=inputs_data.get("baseline"),
                    claims=inputs_data.get("claims", []),
                    evidence=inputs_data.get("evidence", []),
                    waivers=inputs_data.get("waivers", []),
                    assessed_revision=inputs_data.get("assessed_revision"),
                    now=inputs_data["now"],
                    evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
                )
                print(f"{fid:<40} | {'N/A':<10} | FAIL (no error)")
            except InputRejected as exc:
                if exc.reason == err_data.get("reason"):
                    print(f"{fid:<40} | {'N/A':<10} | PASS (InputRejected: {exc.reason})")
                else:
                    print(f"{fid:<40} | {'N/A':<10} | FAIL (wrong reason)")
            continue

        with (fpath / "expected.json").open("r", encoding="utf-8") as f:
            exp_data = json.load(f)
        exp_criteria = [dict(c) for c in exp_data.get("criteria", [])]
        for c in exp_criteria:
            c.pop("explanation", None)

        res = assess_criteria(
            baseline=inputs_data.get("baseline"),
            claims=inputs_data.get("claims", []),
            evidence=inputs_data.get("evidence", []),
            waivers=inputs_data.get("waivers", []),
            assessed_revision=inputs_data.get("assessed_revision"),
            now=inputs_data["now"],
            evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
        )
        res_dicts = [r.to_dict(include_explanation=False) for r in res]
        if rfc8785.dumps(res_dicts) == rfc8785.dumps(exp_criteria):
            print(f"{fid:<40} | {len(res):<10} | PASS (JCS match)")
        else:
            print(f"{fid:<40} | {len(res):<10} | FAIL (mismatch)")

    print("=" * 70)
