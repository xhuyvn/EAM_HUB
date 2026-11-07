#!/usr/bin/env python3
"""Run PPM v2 deterministic reducer across the fixture corpus.

Runs every fixture in ppm-v2/evals/fixtures through the reducer:
- Single-project assessment assembly (criteria, progress, validity, verification, readiness)
- Domain findings detection
- Portfolio rollup aggregation (s08, s10, s19)
- Input rejection / expected errors (s29, s31, s32)

Prints "PASS n fixtures" or precise unified diffs on any failure.
Returns non-zero exit code on failure.
Supports --json report option and --benchmark for multi-run timing.
"""

from __future__ import annotations

import argparse
import difflib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from ppm_reducer import (
    InputRejected,
    assemble_assessment,
    rollup_portfolio,
)


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _diff_dicts(expected: Any, produced: Any, label: str) -> str:
    exp_lines = json.dumps(expected, indent=2, sort_keys=True).splitlines(keepends=True)
    prod_lines = json.dumps(produced, indent=2, sort_keys=True).splitlines(keepends=True)
    diff = difflib.unified_diff(
        exp_lines,
        prod_lines,
        fromfile=f"expected_{label}",
        tofile=f"produced_{label}",
    )
    return "".join(diff)


def run_fixture(fixture_dir: Path) -> dict[str, Any]:
    """Execute a single fixture through the reducer and verify output against expectations."""
    fixture_id = fixture_dir.name
    inputs_path = fixture_dir / "inputs.json"
    expected_path = fixture_dir / "expected.json"
    expected_err_path = fixture_dir / "expected_error.json"
    findings_path = fixture_dir / "expected_findings.json"
    rollup_path = fixture_dir / "expected_rollup.json"

    if not inputs_path.exists():
        return {
            "fixture_id": fixture_id,
            "status": "FAIL",
            "error": "Missing inputs.json",
        }

    inputs_data = _load_json(inputs_path)
    t0 = time.perf_counter()

    # Case 1: Expected error fixture
    if expected_err_path.exists():
        expected_err = _load_json(expected_err_path)
        expected_reason = expected_err.get("reason")
        try:
            assemble_assessment(inputs=inputs_data)
            t1 = time.perf_counter()
            return {
                "fixture_id": fixture_id,
                "status": "FAIL",
                "duration_ms": round((t1 - t0) * 1000, 3),
                "error": f"Expected InputRejected(reason={expected_reason!r}) but assessment succeeded",
            }
        except InputRejected as exc:
            t1 = time.perf_counter()
            if exc.reason != expected_reason:
                return {
                    "fixture_id": fixture_id,
                    "status": "FAIL",
                    "duration_ms": round((t1 - t0) * 1000, 3),
                    "error": f"InputRejected reason mismatch: expected {expected_reason!r}, got {exc.reason!r}",
                }
            return {
                "fixture_id": fixture_id,
                "status": "PASS",
                "kind": "expected_error",
                "reason": exc.reason,
                "duration_ms": round((t1 - t0) * 1000, 3),
            }
        except Exception as exc:  # noqa: BLE001
            t1 = time.perf_counter()
            return {
                "fixture_id": fixture_id,
                "status": "FAIL",
                "duration_ms": round((t1 - t0) * 1000, 3),
                "error": f"Unexpected exception: {type(exc).__name__}: {exc}",
            }

    # Case 2: Expected valid assessment
    if not expected_path.exists():
        return {
            "fixture_id": fixture_id,
            "status": "FAIL",
            "error": "Missing expected.json or expected_error.json",
        }

    expected_data = _load_json(expected_path)
    try:
        result = assemble_assessment(
            inputs=inputs_data,
            assessment_id=expected_data.get("assessment_id"),
        )
    except Exception as exc:  # noqa: BLE001
        t1 = time.perf_counter()
        return {
            "fixture_id": fixture_id,
            "status": "FAIL",
            "duration_ms": round((t1 - t0) * 1000, 3),
            "error": f"Reducer raised unexpected exception: {type(exc).__name__}: {exc}",
        }

    produced_dict = result.to_dict(include_explanation=False)

    # Normalize expected criteria: exclude explanation for deterministic comparison
    exp_copy = dict(expected_data)
    exp_copy["criteria"] = [dict(c) for c in exp_copy.get("criteria", [])]
    for c in exp_copy["criteria"]:
        c.pop("explanation", None)

    # Check assessment match
    diffs: list[str] = []
    if produced_dict != exp_copy:
        diffs.append(_diff_dicts(exp_copy, produced_dict, f"{fixture_id}_assessment"))

    # Check findings match
    if findings_path.exists():
        expected_findings = _load_json(findings_path)
        produced_findings = [f.to_dict() for f in result.findings]
        if produced_findings != expected_findings:
            diffs.append(_diff_dicts(expected_findings, produced_findings, f"{fixture_id}_findings"))

    # Check rollup match if present
    if rollup_path.exists():
        expected_rollup = _load_json(rollup_path)
        try:
            produced_rollup = rollup_portfolio(inputs=inputs_data).to_dict()
            if produced_rollup != expected_rollup:
                diffs.append(_diff_dicts(expected_rollup, produced_rollup, f"{fixture_id}_rollup"))
        except Exception as exc:  # noqa: BLE001
            diffs.append(f"Rollup raised unexpected exception: {type(exc).__name__}: {exc}")

    t1 = time.perf_counter()
    duration_ms = round((t1 - t0) * 1000, 3)

    if diffs:
        return {
            "fixture_id": fixture_id,
            "status": "FAIL",
            "duration_ms": duration_ms,
            "diff": "\n".join(diffs),
        }

    return {
        "fixture_id": fixture_id,
        "status": "PASS",
        "kind": "assessment",
        "criteria_count": len(result.criteria),
        "has_rollup": rollup_path.exists(),
        "duration_ms": duration_ms,
    }


def run_corpus(fixtures_dir: Path) -> tuple[bool, list[dict[str, Any]], float]:
    """Execute all fixtures in the directory, returning overall pass status and report."""
    fixture_dirs = sorted([d for d in fixtures_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    t0 = time.perf_counter()
    results = [run_fixture(fdir) for fdir in fixture_dirs]
    t1 = time.perf_counter()
    all_passed = all(r["status"] == "PASS" for r in results)
    return all_passed, results, t1 - t0


def benchmark_corpus(fixtures_dir: Path, runs: int = 5) -> dict[str, Any]:
    """Run corpus multiple times to measure median execution time."""
    durations: list[float] = []
    fixture_count = 0
    for _ in range(runs):
        all_passed, results, dur = run_corpus(fixtures_dir)
        if not all_passed:
            raise RuntimeError("Benchmark aborted due to fixture failure")
        durations.append(dur)
        fixture_count = len(results)

    median_dur = statistics.median(durations)
    return {
        "runs": runs,
        "fixture_count": fixture_count,
        "durations_seconds": [round(d, 5) for d in durations],
        "median_seconds": round(median_dur, 5),
        "median_ms": round(median_dur * 1000, 2),
        "mean_seconds": round(statistics.mean(durations), 5),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PPM v2 reducer against evaluation fixture corpus.")
    parser.add_argument(
        "--fixtures-dir",
        type=Path,
        default=None,
        help="Path to fixture corpus directory (defaults to ppm-v2/evals/fixtures)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output structured JSON report to stdout",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run 5 passes and report timing statistics (median of 5 runs)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print per-fixture details",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent.parent
    fixtures_dir = args.fixtures_dir or (repo_root / "ppm-v2" / "evals" / "fixtures")

    if not fixtures_dir.exists():
        print(f"ERROR: Fixtures directory not found: {fixtures_dir}", file=sys.stderr)
        sys.exit(1)

    if args.benchmark:
        bench = benchmark_corpus(fixtures_dir, runs=5)
        if args.json_output:
            print(json.dumps(bench, indent=2))
        else:
            print(f"PASS {bench['fixture_count']} fixtures")
            print(f"Benchmark: 5 runs, median = {bench['median_ms']} ms ({bench['median_seconds']} s)")
            for i, d in enumerate(bench["durations_seconds"], 1):
                print(f"  Run {i}: {round(d * 1000, 2)} ms")
        sys.exit(0)

    all_passed, results, total_time = run_corpus(fixtures_dir)

    if args.json_output:
        report = {
            "passed": all_passed,
            "fixture_count": len(results),
            "total_time_seconds": round(total_time, 5),
            "results": results,
        }
        print(json.dumps(report, indent=2))
        sys.exit(0 if all_passed else 1)

    if args.verbose:
        for r in results:
            fid = r["fixture_id"]
            st = r["status"]
            dur = r.get("duration_ms", 0.0)
            kind = r.get("kind", "")
            print(f"[{st}] {fid} ({kind}, {dur} ms)")
            if st != "PASS":
                if "diff" in r:
                    print(r["diff"])
                if "error" in r:
                    print(f"  Error: {r['error']}")

    if not all_passed:
        print(f"FAIL: {sum(1 for r in results if r['status'] != 'PASS')} failing fixture(s):", file=sys.stderr)
        for r in results:
            if r["status"] != "PASS":
                print(f"\n--- Failing fixture: {r['fixture_id']} ---", file=sys.stderr)
                if "error" in r:
                    print(f"Error: {r['error']}", file=sys.stderr)
                if "diff" in r:
                    print(r["diff"], file=sys.stderr)
        sys.exit(1)

    print(f"PASS {len(results)} fixtures")
    sys.exit(0)


if __name__ == "__main__":
    main()
