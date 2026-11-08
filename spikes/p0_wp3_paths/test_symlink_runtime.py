"""
test_symlink_runtime.py — Empirical runtime verification of Windows 11 symlink containment.

Addresses ISS-026 for DG-3 (P0-WP3b):
Evaluates runtime rejection by safe_open (ctypes and pywin32) on native Windows 11
with Developer Mode enabled.

Vectors tested:
1. File symlink pointing outside root (absolute target)
2. File symlink pointing outside root (relative target)
3. Directory symlink pointing outside root (absolute target)
4. Directory symlink pointing outside root (relative target)
5. Directory symlink opened directly as file
6. File symlink chain pointing outside (chain -> file symlink -> outside)
7. Directory symlink chain pointing outside (chain -> dir symlink -> outside)
8. File symlink pointing inside root (absolute target, follow_symlinks=false)
9. File symlink pointing inside root (relative target, follow_symlinks=false)
10. Git clone with core.symlinks=true containing symlink pointing outside
11. Git clone with core.symlinks=true containing symlink pointing inside
12. TOCTOU swap race with directory symlink swap (1,000 iterations, ctypes & pywin32)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    HardlinkNotAllowedError,
    PathTraversalError,
    SafeOpenError,
    safe_open,
)


def run_symlink_matrix(engine: str = "ctypes") -> list[dict[str, Any]]:
    """Execute all symlink attack vectors against safe_open under the specified engine."""
    results: list[dict[str, Any]] = []

    def record(
        vector_id: str,
        name: str,
        expected: str,
        observed: str,
        passed: bool,
        raw_output: str = "",
    ) -> None:
        results.append({
            "vector_id": vector_id,
            "name": name,
            "engine": engine,
            "platform": sys.platform,
            "expected": expected,
            "observed": observed,
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "raw_output": raw_output,
        })

    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        root = base / "root"
        root.mkdir()
        outside = base / "outside"
        outside.mkdir()

        # Target files
        secret_file = outside / "secret.txt"
        secret_file.write_text("CONFIDENTIAL_OUTSIDE_DATA", encoding="utf-8")
        inside_file = root / "inside.txt"
        inside_file.write_text("INSIDE_ROOT_DATA", encoding="utf-8")

        # 1. File symlink to outside (absolute target)
        sym_file_abs = root / "sym_file_abs.txt"
        os.symlink(str(secret_file), str(sym_file_abs))

        # 2. File symlink to outside (relative target)
        sym_file_rel = root / "sym_file_rel.txt"
        rel_to_outside = os.path.join("..", "outside", "secret.txt")
        os.symlink(rel_to_outside, str(sym_file_rel))

        # 3. Directory symlink to outside (absolute target)
        sym_dir_abs = root / "sym_dir_abs"
        os.symlink(str(outside), str(sym_dir_abs), target_is_directory=True)

        # 4. Directory symlink to outside (relative target)
        sym_dir_rel = root / "sym_dir_rel"
        rel_dir_outside = os.path.join("..", "outside")
        os.symlink(rel_dir_outside, str(sym_dir_rel), target_is_directory=True)

        # 5. File symlink chain pointing outside (chain -> sym_file_rel -> outside)
        sym_chain_file = root / "sym_chain_file.txt"
        os.symlink(str(sym_file_rel), str(sym_chain_file))

        # 6. Directory symlink chain pointing outside (chain -> sym_dir_rel -> outside)
        sym_chain_dir = root / "sym_chain_dir"
        os.symlink(str(sym_dir_rel), str(sym_chain_dir), target_is_directory=True)

        # 7. Symlink pointing inside root (absolute target)
        sym_inside_abs = root / "sym_inside_abs.txt"
        os.symlink(str(inside_file), str(sym_inside_abs))

        # 8. Symlink pointing inside root (relative target)
        sym_inside_rel = root / "sym_inside_rel.txt"
        os.symlink("inside.txt", str(sym_inside_rel))

        # --- Test 1: File symlink outside (abs) ---
        try:
            with safe_open(root, "sym_file_abs.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-A1",
                "File symlink outside root (absolute target)",
                "Reject: FileTypeNotAllowedError (follow_symlinks=false)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-A1",
                "File symlink outside root (absolute target)",
                "Reject: FileTypeNotAllowedError (follow_symlinks=false)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-A1",
                "File symlink outside root (absolute target)",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 2: File symlink outside (rel) ---
        try:
            with safe_open(root, "sym_file_rel.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-A2",
                "File symlink outside root (relative target)",
                "Reject: FileTypeNotAllowedError (follow_symlinks=false)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-A2",
                "File symlink outside root (relative target)",
                "Reject: FileTypeNotAllowedError (follow_symlinks=false)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-A2",
                "File symlink outside root (relative target)",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 3: Directory symlink outside (abs) -> read nested file ---
        try:
            with safe_open(root, "sym_dir_abs/secret.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-B1",
                "Directory symlink outside root (absolute target, read child)",
                "Reject: PathTraversalError (escapes root boundary)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except PathTraversalError as e:
            record(
                "V01-B1",
                "Directory symlink outside root (absolute target, read child)",
                "Reject: PathTraversalError (escapes root boundary)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-B1",
                "Directory symlink outside root (absolute target, read child)",
                "Reject: PathTraversalError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 4: Directory symlink outside (rel) -> read nested file ---
        try:
            with safe_open(root, "sym_dir_rel/secret.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-B2",
                "Directory symlink outside root (relative target, read child)",
                "Reject: PathTraversalError (escapes root boundary)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except PathTraversalError as e:
            record(
                "V01-B2",
                "Directory symlink outside root (relative target, read child)",
                "Reject: PathTraversalError (escapes root boundary)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-B2",
                "Directory symlink outside root (relative target, read child)",
                "Reject: PathTraversalError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 5: Directory symlink opened directly as file ---
        try:
            with safe_open(root, "sym_dir_rel", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-B3",
                "Directory symlink opened directly as file",
                "Reject: FileTypeNotAllowedError (directory)",
                f"UNEXPECTED SUCCESS: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-B3",
                "Directory symlink opened directly as file",
                "Reject: FileTypeNotAllowedError (directory)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-B3",
                "Directory symlink opened directly as file",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 6: File symlink chain pointing outside ---
        try:
            with safe_open(root, "sym_chain_file.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-C1",
                "File symlink chain pointing outside root",
                "Reject: FileTypeNotAllowedError (reparse point)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-C1",
                "File symlink chain pointing outside root",
                "Reject: FileTypeNotAllowedError (reparse point)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-C1",
                "File symlink chain pointing outside root",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 7: Directory symlink chain pointing outside ---
        try:
            with safe_open(root, "sym_chain_dir/secret.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-C2",
                "Directory symlink chain pointing outside root",
                "Reject: PathTraversalError (escapes root boundary)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except PathTraversalError as e:
            record(
                "V01-C2",
                "Directory symlink chain pointing outside root",
                "Reject: PathTraversalError (escapes root boundary)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-C2",
                "Directory symlink chain pointing outside root",
                "Reject: PathTraversalError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 8: Symlink to file inside root (absolute target, follow_symlinks=false) ---
        try:
            with safe_open(root, "sym_inside_abs.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-D1",
                "Symlink to file inside root (absolute, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"UNEXPECTED READ: '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-D1",
                "Symlink to file inside root (absolute, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-D1",
                "Symlink to file inside root (absolute, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 9: Symlink to file inside root (relative target, follow_symlinks=false) ---
        try:
            with safe_open(root, "sym_inside_rel.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-D2",
                "Symlink to file inside root (relative, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"UNEXPECTED READ: '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-D2",
                "Symlink to file inside root (relative, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"Rejected: {type(e).__name__}: {e}",
                True,
                raw_output=str(e),
            )
        except Exception as e:
            record(
                "V01-D2",
                "Symlink to file inside root (relative, follow_symlinks=false)",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # --- Test 10 & 11: Git clone with core.symlinks=true ---
        src_repo = base / "git_source_repo"
        src_repo.mkdir()
        env = os.environ.copy()
        # Suppress git interactive prompts
        env["GIT_TERMINAL_PROMPT"] = "0"

        subprocess.run(["git", "init", "-q"], cwd=src_repo, check=True, env=env)
        subprocess.run(["git", "config", "user.name", "P0-WP3b Tester"], cwd=src_repo, check=True, env=env)
        subprocess.run(["git", "config", "user.email", "tester@eam-hub.local"], cwd=src_repo, check=True, env=env)
        subprocess.run(["git", "config", "core.symlinks", "true"], cwd=src_repo, check=True, env=env)

        (src_repo / "repo_target.txt").write_text("LEGITIMATE_REPO_FILE", encoding="utf-8")
        # Create symlinks in source repo
        os.symlink("repo_target.txt", str(src_repo / "git_sym_internal.txt"))
        os.symlink("../secret.txt", str(src_repo / "git_sym_escape.txt"))

        subprocess.run(["git", "add", "."], cwd=src_repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "add symlinks"], cwd=src_repo, check=True, env=env)

        clone_repo = base / "git_cloned_repo"
        subprocess.run(
            ["git", "clone", "-q", "-c", "core.symlinks=true", str(src_repo), str(clone_repo)],
            check=True,
            env=env,
        )

        # Verify git materialized real symlinks
        is_clone_escape_symlink = os.path.islink(clone_repo / "git_sym_escape.txt")
        is_clone_internal_symlink = os.path.islink(clone_repo / "git_sym_internal.txt")

        # Test git clone symlink pointing outside
        try:
            with safe_open(clone_repo, "git_sym_escape.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-E1",
                "Git clone (core.symlinks=true) symlink pointing outside",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"UNEXPECTED ESCAPE: read '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-E1",
                "Git clone (core.symlinks=true) symlink pointing outside",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"Rejected: {type(e).__name__}: {e} (real_symlink={is_clone_escape_symlink})",
                True,
                raw_output=f"islink={is_clone_escape_symlink}; {e}",
            )
        except Exception as e:
            record(
                "V01-E1",
                "Git clone (core.symlinks=true) symlink pointing outside",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

        # Test git clone symlink pointing inside
        try:
            with safe_open(clone_repo, "git_sym_internal.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V01-E2",
                "Git clone (core.symlinks=true) symlink pointing inside",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"UNEXPECTED READ: '{data[:30]}'",
                False,
                raw_output=data,
            )
        except FileTypeNotAllowedError as e:
            record(
                "V01-E2",
                "Git clone (core.symlinks=true) symlink pointing inside",
                "Reject: FileTypeNotAllowedError (reparse point disallowed)",
                f"Rejected: {type(e).__name__}: {e} (real_symlink={is_clone_internal_symlink})",
                True,
                raw_output=f"islink={is_clone_internal_symlink}; {e}",
            )
        except Exception as e:
            record(
                "V01-E2",
                "Git clone (core.symlinks=true) symlink pointing inside",
                "Reject: FileTypeNotAllowedError",
                f"Unexpected Exception: {type(e).__name__}: {e}",
                False,
                raw_output=str(e),
            )

    return results


def run_toctou_symlink_race(
    iterations: int = 1000,
    engine: str = "ctypes",
) -> dict[str, Any]:
    """Execute TOCTOU directory swap race with a directory symlink instead of junction."""
    with tempfile.TemporaryDirectory() as td:
        base = Path(td).resolve()
        root = base / "root"
        root.mkdir()
        outside = base / "outside"
        outside.mkdir()

        secret_file = outside / "target.txt"
        secret_file.write_text("CONFIDENTIAL_SECRET_ESCAPED_DATA", encoding="utf-8")

        safe_dir = base / "safe_dir"
        safe_dir.mkdir()
        (safe_dir / "target.txt").write_text("SAFE_LEGITIMATE_TARGET_DATA", encoding="utf-8")

        swap_dir = root / "swap_dir"
        shutil.copytree(str(safe_dir), str(swap_dir))

        stop_event = threading.Event()
        stats: dict[str, Any] = {
            "iterations": iterations,
            "engine": engine,
            "swap_type": "directory_symlink",
            "safe_reads": 0,
            "blocked_traversals": 0,
            "file_not_found": 0,
            "escapes": 0,
            "unhandled_errors": 0,
        }

        def swapper_thread() -> None:
            is_symlink = False
            temp_swap = root / "swap_temp"
            while not stop_event.is_set():
                try:
                    if is_symlink:
                        # Replace symlink with legitimate directory
                        shutil.copytree(str(safe_dir), str(temp_swap))
                        if swap_dir.exists() or os.path.islink(swap_dir):
                            if sys.platform == "win32":
                                os.system(f'rmdir "{swap_dir}" >nul 2>&1')
                            else:
                                swap_dir.unlink(missing_ok=True)
                        if temp_swap.exists():
                            temp_swap.rename(swap_dir)
                        is_symlink = False
                    else:
                        # Replace directory with directory symlink pointing outside
                        if swap_dir.exists():
                            shutil.rmtree(str(swap_dir), ignore_errors=True)
                        os.symlink(str(outside), str(swap_dir), target_is_directory=True)
                        is_symlink = True
                except Exception:
                    pass
                time.sleep(0.0001)

        t = threading.Thread(target=swapper_thread, daemon=True)
        t.start()

        try:
            for _ in range(iterations):
                try:
                    with safe_open(root, "swap_dir/target.txt", engine=engine) as fp:
                        content = fp.read()
                        if "CONFIDENTIAL_SECRET" in content:
                            stats["escapes"] += 1
                        elif "SAFE_LEGITIMATE" in content:
                            stats["safe_reads"] += 1
                        else:
                            stats["unhandled_errors"] += 1
                except (PathTraversalError, FileTypeNotAllowedError):
                    stats["blocked_traversals"] += 1
                except (FileNotFoundError, OSError):
                    stats["file_not_found"] += 1
                except Exception:
                    stats["unhandled_errors"] += 1
        finally:
            stop_event.set()
            t.join(timeout=3.0)
            if swap_dir.exists() or os.path.islink(swap_dir):
                if sys.platform == "win32":
                    os.system(f'rmdir "{swap_dir}" >nul 2>&1')
                else:
                    swap_dir.unlink(missing_ok=True)

        return stats


def main() -> None:
    print("=" * 80)
    print("P0-WP3b: WINDOWS 11 SYMLINK RUNTIME VERIFICATION (ISS-026 / DG-3)")
    print("=" * 80)

    # 1. Environment info
    import ctypes
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if sys.platform == "win32" else False
    print(f"Platform: {sys.platform}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"User is_admin: {is_admin}")
    print("Prerequisite: Developer Mode enabled in Settings > System > For developers.")

    all_results: dict[str, Any] = {
        "metadata": {
            "work_package": "P0-WP3b",
            "issue": "ISS-026",
            "gate": "DG-3",
            "date": "2026-11-08",
            "platform": "Windows 11 (AMD64)",
            "is_admin": is_admin,
            "prerequisite_mode": "Developer Mode enabled (Settings > System > For developers)",
        },
        "matrix_ctypes": [],
        "matrix_pywin32": [],
        "toctou_ctypes": {},
        "toctou_pywin32": {},
    }

    # 2. Run Attack Matrix
    for engine in ["ctypes", "pywin32"]:
        print(f"\n--- Running Symlink Attack Matrix ({engine}) ---")
        matrix = run_symlink_matrix(engine=engine)
        all_results[f"matrix_{engine}"] = matrix
        for item in matrix:
            print(f"  [{item['status']}] {item['vector_id']}: {item['name']}")
            print(f"         Observed: {item['observed']}")

    # 3. Run TOCTOU Symlink Race
    for engine in ["ctypes", "pywin32"]:
        print(f"\n--- Running TOCTOU Symlink Swap Race ({engine}, 1,000 iterations) ---")
        toctou_res = run_toctou_symlink_race(iterations=1000, engine=engine)
        all_results[f"toctou_{engine}"] = toctou_res
        print(f"  Iterations: {toctou_res['iterations']}")
        print(f"  Safe reads: {toctou_res['safe_reads']}")
        print(f"  Blocked traversals: {toctou_res['blocked_traversals']}")
        print(f"  File not found / transient: {toctou_res['file_not_found']}")
        print(f"  Unhandled errors: {toctou_res['unhandled_errors']}")
        print(f"  Escapes: {toctou_res['escapes']}")
        status = "PASS" if toctou_res["escapes"] == 0 else "FAIL"
        print(f"  Verdict: {status}")

    # Write output to json
    out_path = Path(__file__).parent / "symlink_verification_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults successfully written to {out_path}")


if __name__ == "__main__":
    main()
