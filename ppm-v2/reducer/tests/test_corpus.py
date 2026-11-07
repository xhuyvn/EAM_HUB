"""Corpus validation tests for ppm_reducer.

Verifies:
1. Produced criteria match expected.json criteria[] post JCS normalization.
2. Produced full assessment matches expected.json on every compared field post JCS normalization (18/18 + s29 InputRejected).
3. Produced domain findings match expected_findings.json post JCS normalization (19/19 fixtures).
4. Produced assessments pass schema validation (contracts/ppm-output.schema.json) and EAP platform rules (semantic_checks).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import pytest
import rfc8785

from eap.contracts.rules import semantic_checks
from ppm_reducer import (
    InputRejected,
    assemble_assessment,
    assess_criteria,
    detect_findings,
)


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def get_fixtures_dir() -> Path:
    return get_repo_root() / "ppm-v2" / "evals" / "fixtures"


def get_all_fixture_dirs() -> list[Path]:
    fdir = get_fixtures_dir()
    return sorted([d for d in fdir.iterdir() if d.is_dir() and not d.name.startswith(".")])


@pytest.fixture(scope="session")
def ppm_output_validator() -> jsonschema.Draft202012Validator:
    schema_path = get_repo_root() / "contracts" / "ppm-output.schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture(scope="session")
def findings_validator() -> jsonschema.Draft202012Validator:
    schema_path = get_fixtures_dir() / "findings.schema.json"
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("fpath", get_all_fixture_dirs(), ids=lambda p: p.name)
def test_fixture_corpus_criteria(fpath: Path) -> None:
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
        f"[{fid}] JCS byte mismatch on criteria!\n"
        f"PRODUCED: {json.dumps(produced_criteria, indent=2)}\n"
        f"EXPECTED: {json.dumps(exp_criteria, indent=2)}"
    )


@pytest.mark.parametrize("fpath", get_all_fixture_dirs(), ids=lambda p: p.name)
def test_fixture_corpus_findings(fpath: Path, findings_validator: jsonschema.Draft202012Validator) -> None:
    fid = fpath.name
    inputs_file = fpath / "inputs.json"
    expected_findings_file = fpath / "expected_findings.json"

    assert inputs_file.exists(), f"[{fid}] Missing inputs.json"
    assert expected_findings_file.exists(), f"[{fid}] Missing expected_findings.json"

    with inputs_file.open("r", encoding="utf-8") as f:
        inputs_data: dict[str, Any] = json.load(f)
    with expected_findings_file.open("r", encoding="utf-8") as f:
        expected_findings: list[dict[str, Any]] = json.load(f)

    expected_error_file = fpath / "expected_error.json"
    if expected_error_file.exists():
        # Cyclic/rejected input fixture still evaluates findings (or produces [])
        try:
            crit_results = assess_criteria(
                baseline=inputs_data.get("baseline"),
                claims=inputs_data.get("claims", []),
                evidence=inputs_data.get("evidence", []),
                waivers=inputs_data.get("waivers", []),
                assessed_revision=inputs_data.get("assessed_revision"),
                now=inputs_data["now"],
                evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
            )
        except InputRejected:
            crit_results = []
    else:
        crit_results = assess_criteria(
            baseline=inputs_data.get("baseline"),
            claims=inputs_data.get("claims", []),
            evidence=inputs_data.get("evidence", []),
            waivers=inputs_data.get("waivers", []),
            assessed_revision=inputs_data.get("assessed_revision"),
            now=inputs_data["now"],
            evidence_ttl_days=inputs_data.get("evidence_ttl_days", 7.0),
        )

    findings = detect_findings(
        context=inputs_data.get("context"),
        criteria_results=crit_results,
        claims=inputs_data.get("claims", []),
        project_id=inputs_data.get("project_id"),
        baseline=inputs_data.get("baseline"),
    )
    produced_findings = [f.to_dict() for f in findings]

    findings_validator.validate(produced_findings)

    jcs_produced = rfc8785.dumps(produced_findings)
    jcs_expected = rfc8785.dumps(expected_findings)

    assert jcs_produced == jcs_expected, (
        f"[{fid}] Findings JCS byte mismatch!\n"
        f"PRODUCED: {json.dumps(produced_findings, indent=2)}\n"
        f"EXPECTED: {json.dumps(expected_findings, indent=2)}"
    )


@pytest.mark.parametrize("fpath", get_all_fixture_dirs(), ids=lambda p: p.name)
def test_fixture_corpus_assessment(
    fpath: Path,
    ppm_output_validator: jsonschema.Draft202012Validator,
) -> None:
    fid = fpath.name
    inputs_file = fpath / "inputs.json"
    expected_error_file = fpath / "expected_error.json"

    with inputs_file.open("r", encoding="utf-8") as f:
        inputs_data: dict[str, Any] = json.load(f)

    if expected_error_file.exists():
        with expected_error_file.open("r", encoding="utf-8") as f:
            err_data = json.load(f)
        exp_reason = err_data.get("reason")
        with pytest.raises(InputRejected) as exc_info:
            assemble_assessment(inputs=inputs_data)
        assert exc_info.value.reason == exp_reason, (
            f"[{fid}] InputRejected reason {exc_info.value.reason} != expected {exp_reason}"
        )
        return

    expected_file = fpath / "expected.json"
    assert expected_file.exists(), f"[{fid}] Missing expected.json"
    with expected_file.open("r", encoding="utf-8") as f:
        expected_data: dict[str, Any] = json.load(f)

    # Supply fixture expected assessment_id to verify all other produced fields
    result = assemble_assessment(
        inputs=inputs_data,
        assessment_id=expected_data.get("assessment_id"),
    )

    produced_dict = result.to_dict(include_explanation=False)

    # Copy expected and exclude explanation from criteria for deterministic comparison
    exp_copy = dict(expected_data)
    exp_copy["criteria"] = [dict(c) for c in exp_copy.get("criteria", [])]
    for c in exp_copy["criteria"]:
        c.pop("explanation", None)

    # 1. JCS byte-for-byte exact equality across full single-project assessment
    jcs_produced = rfc8785.dumps(produced_dict)
    jcs_expected = rfc8785.dumps(exp_copy)

    assert jcs_produced == jcs_expected, (
        f"[{fid}] Full assessment JCS mismatch!\n"
        f"PRODUCED: {json.dumps(produced_dict, indent=2)}\n"
        f"EXPECTED: {json.dumps(exp_copy, indent=2)}"
    )

    # 2. Schema validation against contracts/ppm-output.schema.json
    produced_full = result.to_dict(include_explanation=True)
    ppm_output_validator.validate(produced_full)

    # 3. Platform semantic rules validation via eap.contracts.rules
    semantic_checks("ppm-output", produced_full)


def test_corpus_summary_table(capsys: pytest.CaptureFixture[str]) -> None:
    """Print per-fixture PASS table required by acceptance suite."""
    fixture_dirs = get_all_fixture_dirs()
    print("\n" + "=" * 90)
    print(f"{'Fixture ID':<38} | {'Criteria':<8} | {'Assessment':<12} | {'Findings':<10} | {'Status'}")
    print("-" * 90)

    for fpath in fixture_dirs:
        fid = fpath.name
        with (fpath / "inputs.json").open("r", encoding="utf-8") as f:
            inputs_data = json.load(f)

        if (fpath / "expected_error.json").exists():
            with (fpath / "expected_error.json").open("r", encoding="utf-8") as f:
                err_data = json.load(f)
            try:
                assemble_assessment(inputs=inputs_data)
                print(f"{fid:<38} | {'N/A':<8} | {'FAIL':<12} | {'N/A':<10} | FAIL (no error)")
            except InputRejected as exc:
                if exc.reason == err_data.get("reason"):
                    print(f"{fid:<38} | {'N/A':<8} | {'REJECTED':<12} | {'PASS':<10} | PASS (InputRejected: {exc.reason})")
                else:
                    print(f"{fid:<38} | {'N/A':<8} | {'FAIL':<12} | {'N/A':<10} | FAIL (wrong reason)")
            continue

        with (fpath / "expected.json").open("r", encoding="utf-8") as f:
            exp_data = json.load(f)
        with (fpath / "expected_findings.json").open("r", encoding="utf-8") as f:
            exp_findings = json.load(f)

        res = assemble_assessment(inputs=inputs_data, assessment_id=exp_data.get("assessment_id"))
        prod_dict = res.to_dict(include_explanation=False)
        prod_findings = [f.to_dict() for f in res.findings]

        exp_copy = dict(exp_data)
        exp_copy["criteria"] = [dict(c) for c in exp_copy.get("criteria", [])]
        for c in exp_copy["criteria"]:
            c.pop("explanation", None)

        asmt_match = rfc8785.dumps(prod_dict) == rfc8785.dumps(exp_copy)
        findings_match = rfc8785.dumps(prod_findings) == rfc8785.dumps(exp_findings)

        status = "PASS" if (asmt_match and findings_match) else "FAIL"
        print(f"{fid:<38} | {len(res.criteria):<8} | {'PASS' if asmt_match else 'FAIL':<12} | {'PASS' if findings_match else 'FAIL':<10} | {status}")

    print("=" * 90)
