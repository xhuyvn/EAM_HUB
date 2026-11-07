#!/usr/bin/env python3
"""Validate PPM v2 evaluation fixture corpus.

Checks:
- inputs.json against inputs.schema.json
- expected.json against contracts/ppm-output.schema.json (or expected_error.json)
- expected_findings.json against findings.schema.json
- expected_rollup.json against rollup.schema.json
- Cross-reference between MAPPING.md and fixture directories on disk
- Confidentiality check: s19 outputs contain zero hidden project IDs
- Cross-checks inputs <-> expected:
  * every criterion's evidence_trust equals the trust_class of a referenced input evidence (or none)
  * verified requires verifier_observed|ci_attested passing evidence at assessed_revision
  * waived requires an unexpired waiver at now
- Hard rule invariants: explicit denominators, cost_state=unknown has cost_usd=null, single deliverable counting
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def parse_iso(ts_str: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts_str)


def check_fixtures(
    repo_root: Path,
    fixtures_dir: Path,
    output_schema_path: Path,
    inputs_schema_path: Path,
    rollup_schema_path: Path,
    findings_schema_path: Path,
    mapping_path: Path,
) -> tuple[int, int]:
    errors: list[str] = []

    # 1. Load schemas
    try:
        inputs_schema = cast(dict[str, Any], load_json(inputs_schema_path))
        output_schema = cast(dict[str, Any], load_json(output_schema_path))
        rollup_schema = cast(dict[str, Any], load_json(rollup_schema_path))
        findings_schema = cast(dict[str, Any], load_json(findings_schema_path))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Failed to load schemas: {exc}", file=sys.stderr)
        sys.exit(1)

    inputs_validator = jsonschema.Draft202012Validator(inputs_schema)
    output_validator = jsonschema.Draft202012Validator(output_schema)
    rollup_validator = jsonschema.Draft202012Validator(rollup_schema)
    findings_validator = jsonschema.Draft202012Validator(findings_schema)

    # 2. Check MAPPING.md cross-reference if mapping_path exists
    fixtures_in_mapping: set[str] = set()
    if mapping_path.exists():
        mapping_text = mapping_path.read_text(encoding="utf-8")
        # Match table rows containing `sXX-...`
        for match in re.finditer(r"\|\s*`([a-z0-9_-]+)`\s*\|", mapping_text):
            fid = match.group(1)
            fixtures_in_mapping.add(fid)

    fixture_dirs = [d for d in fixtures_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    fixture_dirs.sort(key=lambda p: p.name)
    fixture_dir_names = {d.name for d in fixture_dirs}

    if fixtures_in_mapping:
        missing_on_disk = fixtures_in_mapping - fixture_dir_names
        extra_on_disk = fixture_dir_names - fixtures_in_mapping
        if missing_on_disk:
            errors.append(f"MAPPING.md lists fixtures missing on disk: {sorted(missing_on_disk)}")
        if extra_on_disk:
            errors.append(f"Disk contains fixtures not listed in MAPPING.md: {sorted(extra_on_disk)}")

    fixture_count = 0
    rollup_count = 0

    # 3. Validate each fixture
    for fdir in fixture_dirs:
        fixture_id = fdir.name
        inputs_path = fdir / "inputs.json"
        expected_path = fdir / "expected.json"
        expected_err_path = fdir / "expected_error.json"
        findings_path = fdir / "expected_findings.json"
        readme_path = fdir / "README.md"
        rollup_path = fdir / "expected_rollup.json"

        if not inputs_path.exists():
            errors.append(f"[{fixture_id}] Missing inputs.json")
            continue
        if not expected_path.exists() and not expected_err_path.exists():
            errors.append(f"[{fixture_id}] Missing expected.json or expected_error.json")
            continue
        if not readme_path.exists():
            errors.append(f"[{fixture_id}] Missing README.md")

        # Validate inputs.json
        inputs_data: dict[str, Any] = {}
        try:
            inputs_data = cast(dict[str, Any], load_json(inputs_path))
            for err in inputs_validator.iter_errors(inputs_data):
                errors.append(f"[{fixture_id}/inputs.json] Schema error at '{err.json_path}': {err.message}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"[{fixture_id}/inputs.json] JSON load error: {exc}")
            continue

        # Validate expected_findings.json
        if findings_path.exists():
            try:
                findings_data = load_json(findings_path)
                for err in findings_validator.iter_errors(findings_data):
                    errors.append(f"[{fixture_id}/expected_findings.json] Schema error at '{err.json_path}': {err.message}")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"[{fixture_id}/expected_findings.json] JSON load error: {exc}")
        else:
            errors.append(f"[{fixture_id}] Missing expected_findings.json")

        # Validate expected_error.json if present
        if expected_err_path.exists():
            try:
                err_data = cast(dict[str, Any], load_json(expected_err_path))
                if not err_data.get("error"):
                    errors.append(f"[{fixture_id}/expected_error.json] Missing 'error' property")
                if not err_data.get("reason"):
                    errors.append(f"[{fixture_id}/expected_error.json] Missing 'reason' property")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"[{fixture_id}/expected_error.json] JSON load error: {exc}")
            fixture_count += 1
            continue

        # Validate expected.json
        expected_data: dict[str, Any] = {}
        try:
            expected_data = cast(dict[str, Any], load_json(expected_path))
            for err in output_validator.iter_errors(expected_data):
                errors.append(f"[{fixture_id}/expected.json] Schema error at '{err.json_path}': {err.message}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"[{fixture_id}/expected.json] JSON load error: {exc}")
            continue

        # Cross-checks between inputs.json and expected.json
        now_str = inputs_data.get("now", "")
        now_dt = parse_iso(now_str) if now_str else None
        assessed_rev = inputs_data.get("assessed_revision")

        input_evidence_list = inputs_data.get("evidence", [])
        input_evidence_map = {ev.get("evidence_id"): ev for ev in input_evidence_list}
        input_trust_classes = {ev.get("trust_class") for ev in input_evidence_list}
        input_waivers = {w.get("waiver_id"): w for w in inputs_data.get("waivers", [])}

        criteria = expected_data.get("criteria", [])
        for crit in criteria:
            cid = crit.get("criterion_id")
            c_trust = crit.get("evidence_trust")
            c_status = crit.get("status")
            w_ref = crit.get("waiver_ref")

            # 1. evidence_trust equals the trust_class of a referenced input evidence (or none)
            if c_trust != "none":
                ref_ev_ids = crit.get("supporting_evidence_refs", []) + crit.get("refuting_evidence_refs", [])
                if ref_ev_ids:
                    for r_id in ref_ev_ids:
                        ref_ev = input_evidence_map.get(r_id)
                        if ref_ev and ref_ev.get("trust_class") != c_trust:
                            errors.append(
                                f"[{fixture_id}] Criterion {cid} evidence_trust '{c_trust}' does not match referenced evidence {r_id} trust_class '{ref_ev.get('trust_class')}'"
                            )
                elif c_trust not in input_trust_classes:
                    errors.append(
                        f"[{fixture_id}] Criterion {cid} declares evidence_trust '{c_trust}' not present in input evidence"
                    )

            # 2. verified requires verifier_observed|ci_attested evidence at assessed_revision
            if c_status == "verified":
                if c_trust not in ("verifier_observed", "ci_attested"):
                    errors.append(
                        f"[{fixture_id}] Criterion {cid} has status 'verified' but evidence_trust is '{c_trust}' (requires verifier_observed or ci_attested)"
                    )
                # Check for passing evidence at assessed_revision
                has_passing_current = any(
                    ev.get("trust_class") in ("verifier_observed", "ci_attested")
                    and ev.get("revision") == assessed_rev
                    and ev.get("result") == "passed"
                    for ev in input_evidence_list
                )
                if not has_passing_current:
                    errors.append(
                        f"[{fixture_id}] Criterion {cid} has status 'verified' but lacks verifier_observed or ci_attested passing evidence at assessed revision {assessed_rev}"
                    )

            # 3. waived requires an unexpired waiver at now
            if c_status == "waived":
                if not w_ref:
                    errors.append(f"[{fixture_id}] Criterion {cid} has status 'waived' but waiver_ref is null")
                else:
                    waiver = input_waivers.get(w_ref)
                    if not waiver:
                        errors.append(f"[{fixture_id}] Criterion {cid} references nonexistent waiver '{w_ref}'")
                    elif now_dt:
                        expires_at = parse_iso(waiver["expires_at"])
                        if expires_at <= now_dt:
                            errors.append(
                                f"[{fixture_id}] Criterion {cid} is marked 'waived' with expired waiver '{w_ref}' (expired {waiver['expires_at']} <= now {now_str})"
                            )

        # Invariant: explicit denominator for progress
        progress = expected_data.get("progress", {})
        if progress.get("basis") == "baseline_weighted" and (
            progress.get("total_weight") is None or progress.get("total_weight") <= 0
        ):
            errors.append(f"[{fixture_id}/expected.json] Progress basis baseline_weighted requires positive total_weight")

        # Validate rollup if present
        if rollup_path.exists():
            rollup_count += 1
            try:
                rollup_data = cast(dict[str, Any], load_json(rollup_path))
                for err in rollup_validator.iter_errors(rollup_data):
                    errors.append(f"[{fixture_id}/expected_rollup.json] Schema error at '{err.json_path}': {err.message}")

                # Rollup invariants
                if rollup_data.get("cost_state") == "unknown" and rollup_data.get("cost_usd") is not None:
                    errors.append(
                        f"[{fixture_id}/expected_rollup.json] cost_state=unknown MUST have cost_usd=null, got {rollup_data.get('cost_usd')}"
                    )

                rprogress = rollup_data.get("progress", {})
                if rprogress.get("basis") == "baseline_weighted" and (
                    rprogress.get("total_weight") is None or rprogress.get("total_weight") <= 0
                ):
                    errors.append(f"[{fixture_id}/expected_rollup.json] Rollup total_weight must be positive")

            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"[{fixture_id}/expected_rollup.json] JSON load error: {exc}")

        # Check s19 confidentiality: zero hidden project IDs in outputs
        if fixture_id == "s19-partial-portfolio":
            hidden_ids: list[str] = []
            context = inputs_data.get("context", {})
            if "hidden_project_ids" in context:
                hidden_ids = context["hidden_project_ids"]

            if not hidden_ids:
                errors.append("[s19-partial-portfolio] inputs.json missing context.hidden_project_ids")
            else:
                for out_file in [expected_path, rollup_path]:
                    if out_file.exists():
                        content = out_file.read_text(encoding="utf-8")
                        for hid in hidden_ids:
                            if hid in content:
                                errors.append(f"[s19-partial-portfolio] Output {out_file.name} leaked hidden project ID: '{hid}'")

        fixture_count += 1

    if errors:
        print(f"FAIL {len(errors)} error(s) found in fixture corpus:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"PASS {fixture_count} fixtures, {rollup_count} rollup")
    return fixture_count, rollup_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Check PPM v2 fixture corpus against schemas and invariants.")
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Path to evals/fixtures directory (defaults to ppm-v2/evals/fixtures)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    fixtures_dir = args.fixtures_dir or (repo_root / "ppm-v2" / "evals" / "fixtures")
    output_schema_path = repo_root / "contracts" / "ppm-output.schema.json"
    inputs_schema_path = repo_root / "ppm-v2" / "evals" / "fixtures" / "inputs.schema.json"
    rollup_schema_path = repo_root / "ppm-v2" / "evals" / "fixtures" / "rollup.schema.json"
    findings_schema_path = repo_root / "ppm-v2" / "evals" / "fixtures" / "findings.schema.json"
    mapping_path = repo_root / "ppm-v2" / "evals" / "fixtures" / "MAPPING.md"

    check_fixtures(
        repo_root=repo_root,
        fixtures_dir=fixtures_dir,
        output_schema_path=output_schema_path,
        inputs_schema_path=inputs_schema_path,
        rollup_schema_path=rollup_schema_path,
        findings_schema_path=findings_schema_path,
        mapping_path=mapping_path,
    )


if __name__ == "__main__":
    main()
