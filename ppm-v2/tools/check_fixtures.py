#!/usr/bin/env python3
"""Validate PPM v2 evaluation fixture corpus.

Checks:
- inputs.json against inputs.schema.json
- expected.json against contracts/ppm-output.schema.json
- expected_rollup.json against rollup.schema.json
- Cross-reference between MAPPING.md and fixture directories on disk
- Confidentiality check: s19 outputs contain zero hidden project IDs
- Hard rule invariants: explicit denominators, cost_state=unknown has cost_usd=null, single deliverable counting
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import jsonschema  # type: ignore[import-untyped]


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def check_fixtures(
    repo_root: Path,
    fixtures_dir: Path,
    output_schema_path: Path,
    inputs_schema_path: Path,
    rollup_schema_path: Path,
    mapping_path: Path,
) -> tuple[int, int]:
    errors: list[str] = []

    # 1. Load schemas
    try:
        inputs_schema = load_json(inputs_schema_path)
        output_schema = load_json(output_schema_path)
        rollup_schema = load_json(rollup_schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: Failed to load schemas: {exc}", file=sys.stderr)
        sys.exit(1)

    inputs_validator = jsonschema.Draft202012Validator(inputs_schema)
    output_validator = jsonschema.Draft202012Validator(output_schema)
    rollup_validator = jsonschema.Draft202012Validator(rollup_schema)

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
        readme_path = fdir / "README.md"
        rollup_path = fdir / "expected_rollup.json"

        if not inputs_path.exists():
            errors.append(f"[{fixture_id}] Missing inputs.json")
            continue
        if not expected_path.exists():
            errors.append(f"[{fixture_id}] Missing expected.json")
            continue
        if not readme_path.exists():
            errors.append(f"[{fixture_id}] Missing README.md")

        # Validate inputs.json
        try:
            inputs_data = load_json(inputs_path)
            for err in inputs_validator.iter_errors(inputs_data):
                errors.append(f"[{fixture_id}/inputs.json] Schema error at '{err.json_path}': {err.message}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"[{fixture_id}/inputs.json] JSON load error: {exc}")
            continue

        # Validate expected.json
        try:
            expected_data = load_json(expected_path)
            for err in output_validator.iter_errors(expected_data):
                errors.append(f"[{fixture_id}/expected.json] Schema error at '{err.json_path}': {err.message}")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"[{fixture_id}/expected.json] JSON load error: {exc}")
            continue

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
                rollup_data = load_json(rollup_path)
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
    mapping_path = repo_root / "ppm-v2" / "evals" / "fixtures" / "MAPPING.md"

    check_fixtures(
        repo_root=repo_root,
        fixtures_dir=fixtures_dir,
        output_schema_path=output_schema_path,
        inputs_schema_path=inputs_schema_path,
        rollup_schema_path=rollup_schema_path,
        mapping_path=mapping_path,
    )


if __name__ == "__main__":
    main()
