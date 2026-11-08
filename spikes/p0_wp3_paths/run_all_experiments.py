"""
run_all_experiments.py — Automated runner for P0-WP3 spike experiments.

Collects empirical results from native Windows 11 and Linux (Docker python:3.12-slim),
recording:
- Attack matrix (12 cases each)
- TOCTOU swap race outcomes (1,000 iterations each)
- Benchmark overhead on 10,000 files
- Unprivileged / non-admin user behavior
Outputs to spikes/p0_wp3_paths/results.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Ensure project root is on sys.path
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from spikes.p0_wp3_paths.bench_overhead import run_benchmark
from spikes.p0_wp3_paths.test_matrix import run_matrix
from spikes.p0_wp3_paths.test_symlink_runtime import (
    run_symlink_matrix,
    run_toctou_symlink_race,
)
from spikes.p0_wp3_paths.toctou_race import run_toctou_race


def main() -> None:
    print("=" * 70)
    print("P0-WP3 SPIKE: WINDOWS & LINUX PATH CONTAINMENT EMPIRICAL RUNNER")
    print("=" * 70)

    report_data = {
        "timestamp": datetime.now(UTC).isoformat(),
        "host": {
            "platform": sys.platform,
            "python_version": sys.version,
            "is_windows": sys.platform == "win32",
        },
        "windows": {},
        "linux_docker": {},
    }

    # 1. Native Windows Experiments
    if sys.platform == "win32":
        import ctypes
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        report_data["host"]["is_admin"] = is_admin
        print(f"\n[1/2] Running Native Windows Experiments (is_admin={is_admin})...")

        # Ctypes matrix
        print("  -> Running attack matrix (ctypes)...")
        win_matrix_ctypes = run_matrix(engine="ctypes")

        # Pywin32 matrix
        print("  -> Running attack matrix (pywin32)...")
        win_matrix_pywin32 = run_matrix(engine="pywin32")

        # TOCTOU ctypes
        print("  -> Running TOCTOU swap race (ctypes, 1,000 iterations)...")
        toctou_ctypes = run_toctou_race(iterations=1000, engine="ctypes")

        # TOCTOU pywin32
        print("  -> Running TOCTOU swap race (pywin32, 1,000 iterations)...")
        toctou_pywin32 = run_toctou_race(iterations=1000, engine="pywin32")

        # Symlink runtime verification (P0-WP3b / ISS-026)
        print("  -> Running symlink runtime verification matrix (ctypes & pywin32)...")
        symlink_matrix_ctypes = run_symlink_matrix(engine="ctypes")
        symlink_matrix_pywin32 = run_symlink_matrix(engine="pywin32")
        print("  -> Running TOCTOU symlink swap race (ctypes & pywin32, 1,000 iterations)...")
        symlink_toctou_ctypes = run_toctou_symlink_race(iterations=1000, engine="ctypes")
        symlink_toctou_pywin32 = run_toctou_symlink_race(iterations=1000, engine="pywin32")

        # Benchmark 10,000 files
        print("  -> Running 10,000 files overhead benchmark...")
        bench_win = run_benchmark(file_count=10000)

        report_data["windows"] = {
            "is_admin": is_admin,
            "developer_mode": True,
            "matrix_ctypes": win_matrix_ctypes,
            "matrix_pywin32": win_matrix_pywin32,
            "toctou_ctypes": toctou_ctypes,
            "toctou_pywin32": toctou_pywin32,
            "symlink_verification": {
                "matrix_ctypes": symlink_matrix_ctypes,
                "matrix_pywin32": symlink_matrix_pywin32,
                "toctou_symlink_ctypes": symlink_toctou_ctypes,
                "toctou_symlink_pywin32": symlink_toctou_pywin32,
            },
            "benchmark_10k": bench_win,
        }

    # 2. Linux Experiments via Docker
    print("\n[2/2] Running Linux Experiments via Docker (python:3.12-slim)...")
    try:
        # Check docker
        docker_version = subprocess.check_output(["docker", "--version"], text=True).strip()
        report_data["linux_docker"]["docker_version"] = docker_version

        # Run test_matrix
        print("  -> Running attack matrix in Linux container...")
        cmd_matrix = [
            "docker", "run", "--rm",
            "-v", f"{_repo_root}:/workspace",
            "-w", "/workspace",
            "python:3.12-slim",
            "python3", "-c",
            "from spikes.p0_wp3_paths.test_matrix import run_matrix; import json; print(json.dumps(run_matrix('ctypes')))",
        ]
        out_matrix = subprocess.check_output(cmd_matrix, text=True)
        # Parse last line as JSON
        matrix_linux = json.loads(out_matrix.strip().splitlines()[-1])
        report_data["linux_docker"]["matrix"] = matrix_linux

        # Run toctou
        print("  -> Running TOCTOU race in Linux container (1,000 iterations)...")
        cmd_toctou = [
            "docker", "run", "--rm",
            "-v", f"{_repo_root}:/workspace",
            "-w", "/workspace",
            "python:3.12-slim",
            "python3", "-c",
            "from spikes.p0_wp3_paths.toctou_race import run_toctou_race; import json; print(json.dumps(run_toctou_race(1000, 'ctypes')))",
        ]
        out_toctou = subprocess.check_output(cmd_toctou, text=True)
        toctou_linux = json.loads(out_toctou.strip().splitlines()[-1])
        report_data["linux_docker"]["toctou"] = toctou_linux

        # Run benchmark
        print("  -> Running 10,000 files benchmark in Linux container...")
        cmd_bench = [
            "docker", "run", "--rm",
            "-v", f"{_repo_root}:/workspace",
            "-w", "/workspace",
            "python:3.12-slim",
            "python3", "-c",
            "from spikes.p0_wp3_paths.bench_overhead import run_benchmark; import json; print(json.dumps(run_benchmark(10000)))",
        ]
        out_bench = subprocess.check_output(cmd_bench, text=True)
        bench_linux = json.loads(out_bench.strip().splitlines()[-1])
        report_data["linux_docker"]["benchmark_10k"] = bench_linux

    except Exception as e:
        print(f"  -> Docker run failed: {e}")
        report_data["linux_docker"]["error"] = str(e)

    # Save results.json
    out_file = Path(__file__).parent / "results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print("\n" + "=" * 70)
    print(f"All experiments finished. Raw results saved to: {out_file}")
    print("=" * 70)

if __name__ == "__main__":
    main()
