"""
bench_overhead.py — Microsecond overhead benchmark across 10,000 files.

Compares:
1. Standard Python open() [baseline]
2. safe_open() via ctypes
3. safe_open() via pywin32 (Windows only)
4. safe_open() via openat2 / procfs (Linux)
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from spikes.p0_wp3_paths.safe_open import safe_open


def run_benchmark(file_count: int = 10000) -> dict[str, Any]:
    results = {}
    print(f"Creating {file_count} test files in temporary directory...")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "corpus"
        root.mkdir()

        # Distribute into 100 subdirectories to avoid flat-directory filesystem limits
        dir_count = 100
        per_dir = file_count // dir_count
        rel_paths = []

        content = "0123456789abcdef" * 4  # 64 bytes

        for d in range(dir_count):
            sub = root / f"d_{d:03d}"
            sub.mkdir()
            for f in range(per_dir):
                rel = f"d_{d:03d}/f_{f:04d}.txt"
                p = root / rel
                p.write_text(content, encoding="utf-8")
                rel_paths.append(rel)

        print(f"Created {len(rel_paths)} files. Benchmarking...")

        # 1. Baseline: standard open()
        t0 = time.perf_counter()
        for rel in rel_paths:
            with open(root / rel, "r", encoding="utf-8") as fp:
                _ = fp.read(1)
        t1 = time.perf_counter()
        baseline_sec = t1 - t0
        baseline_us = (baseline_sec / file_count) * 1_000_000
        results["baseline_open"] = {
            "total_sec": round(baseline_sec, 4),
            "us_per_file": round(baseline_us, 2),
            "files_per_sec": round(file_count / baseline_sec, 1),
        }
        print(f"  Baseline open(): {baseline_us:.2f} µs/file ({file_count / baseline_sec:.1f} files/s)")

        # 2. safe_open ctypes
        t0 = time.perf_counter()
        for rel in rel_paths:
            with safe_open(root, rel, engine="ctypes") as fp:
                _ = fp.read(1)
        t1 = time.perf_counter()
        ctypes_sec = t1 - t0
        ctypes_us = (ctypes_sec / file_count) * 1_000_000
        results["safe_open_ctypes"] = {
            "total_sec": round(ctypes_sec, 4),
            "us_per_file": round(ctypes_us, 2),
            "overhead_us_over_baseline": round(ctypes_us - baseline_us, 2),
            "files_per_sec": round(file_count / ctypes_sec, 1),
        }
        print(f"  safe_open (ctypes): {ctypes_us:.2f} µs/file (+{ctypes_us - baseline_us:.2f} µs overhead)")

        # 3. safe_open pywin32 (if Windows)
        if sys.platform == "win32":
            try:
                import win32file
                t0 = time.perf_counter()
                for rel in rel_paths:
                    with safe_open(root, rel, engine="pywin32") as fp:
                        _ = fp.read(1)
                t1 = time.perf_counter()
                pywin32_sec = t1 - t0
                pywin32_us = (pywin32_sec / file_count) * 1_000_000
                results["safe_open_pywin32"] = {
                    "total_sec": round(pywin32_sec, 4),
                    "us_per_file": round(pywin32_us, 2),
                    "overhead_us_over_baseline": round(pywin32_us - baseline_us, 2),
                    "files_per_sec": round(file_count / pywin32_sec, 1),
                }
                print(f"  safe_open (pywin32): {pywin32_us:.2f} µs/file (+{pywin32_us - baseline_us:.2f} µs overhead)")
            except ImportError:
                print("  pywin32 not available")

    return results

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    res = run_benchmark(file_count=n)
    print("\n--- BENCHMARK SUMMARY ---")
    for k, v in res.items():
        print(f"{k}: {v}")
