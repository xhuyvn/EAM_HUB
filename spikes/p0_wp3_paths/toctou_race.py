"""
toctou_race.py — TOCTOU swap race test.

Runs 1,000 iterations attempting to exploit the window between path resolution
and handle acquisition by swapping a directory with a junction to an outside directory.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from spikes.p0_wp3_paths.safe_open import (
    FileTypeNotAllowedError,
    PathTraversalError,
    safe_open,
)


def run_toctou_race(
    iterations: int = 1000,
    engine: str = "ctypes",
    swap_type: str = "symlink",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "root"
        root.mkdir()
        outside = base / "outside"
        outside.mkdir()

        # Secret file outside
        secret_file = outside / "target.txt"
        secret_file.write_text("SECRET_ESCAPED_DATA", encoding="utf-8")

        # Legitimate dir & file inside root
        safe_dir = base / "legit_target_dir"
        safe_dir.mkdir()
        (safe_dir / "target.txt").write_text("SAFE_LEGITIMATE_DATA", encoding="utf-8")

        # Staging outside reference (junction or symlink)
        staging_dir = base / "staging_dir"
        if sys.platform == "win32" and swap_type == "junction":
            import _winapi
            _winapi.CreateJunction(str(outside), str(staging_dir))
        else:
            os.symlink(str(outside), str(staging_dir), target_is_directory=True)

        # We will race on root / "swap_dir"
        swap_dir = root / "swap_dir"
        # Initial state: copy safe directory
        shutil.copytree(str(safe_dir), str(swap_dir))

        stop_event = threading.Event()
        stats = {
            "iterations": iterations,
            "swap_type": swap_type,
            "safe_reads": 0,
            "blocked_traversals": 0,
            "file_not_found": 0,
            "escapes": 0,
            "errors": 0,
            "engine": engine,
            "platform": sys.platform,
        }

        def swapper_thread() -> None:
            # Rapidly swap swap_dir between legitimate dir and junction/symlink
            is_swapped = False
            temp_swap = root / "swap_temp"
            while not stop_event.is_set():
                try:
                    if is_swapped:
                        # Replace with safe dir
                        if temp_swap.exists():
                            if temp_swap.is_dir() and not temp_swap.is_symlink():
                                shutil.rmtree(temp_swap, ignore_errors=True)
                            else:
                                temp_swap.unlink(missing_ok=True)
                        shutil.copytree(str(safe_dir), str(temp_swap))
                        # Atomic or fast replace
                        if swap_dir.exists() or os.path.islink(swap_dir):
                            if sys.platform == "win32":
                                os.system(f'rmdir "{swap_dir}" >nul 2>&1')
                            else:
                                swap_dir.unlink(missing_ok=True)
                        if temp_swap.exists():
                            temp_swap.rename(swap_dir)
                        is_swapped = False
                    else:
                        # Replace with junction / symlink pointing outside
                        if swap_dir.exists():
                            shutil.rmtree(str(swap_dir), ignore_errors=True)
                        if sys.platform == "win32" and swap_type == "junction":
                            import _winapi
                            try:
                                _winapi.CreateJunction(str(outside), str(swap_dir))
                            except Exception:
                                pass
                        else:
                            try:
                                os.symlink(str(outside), str(swap_dir), target_is_directory=True)
                            except Exception:
                                pass
                        is_swapped = True
                except Exception:
                    pass
                # tiny yield
                time.sleep(0.0001)

        t = threading.Thread(target=swapper_thread, daemon=True)
        t.start()

        try:
            for i in range(iterations):
                try:
                    with safe_open(root, "swap_dir/target.txt", engine=engine) as fp:
                        content = fp.read()
                        if "SECRET_ESCAPED" in content:
                            stats["escapes"] += 1
                        elif "SAFE_LEGITIMATE" in content:
                            stats["safe_reads"] += 1
                        else:
                            stats["errors"] += 1
                except (PathTraversalError, FileTypeNotAllowedError):
                    stats["blocked_traversals"] += 1
                except (FileNotFoundError, OSError):
                    stats["file_not_found"] += 1
                except Exception:
                    stats["errors"] += 1
        finally:
            stop_event.set()
            t.join(timeout=2.0)
            # Cleanup any lingering junctions or symlinks
            if sys.platform == "win32":
                os.system(f'rmdir "{staging_dir}" >nul 2>&1')
                os.system(f'rmdir "{swap_dir}" >nul 2>&1')

        return stats

if __name__ == "__main__":
    eng = sys.argv[1] if len(sys.argv) > 1 else "ctypes"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    stype = sys.argv[3] if len(sys.argv) > 3 else "symlink"
    res = run_toctou_race(iterations=n, engine=eng, swap_type=stype)
    print(f"\n--- TOCTOU RACE TEST ({eng} [{stype}] on {sys.platform}, {n} iterations) ---")
    for k, v in res.items():
        print(f"  {k}: {v}")
