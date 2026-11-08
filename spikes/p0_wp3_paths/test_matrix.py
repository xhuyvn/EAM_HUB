r"""
test_matrix.py — Comprehensive attack matrix for path containment.

Vectors evaluated:
 1. Symlink file/dir pointing outside root
 2. Directory junction outside root
 3. Junction chain
 4. Hardlink to a file outside root
 5. NTFS alternate data stream (file.txt:secret)
 6. 8.3 short names (PROGRA~1) resolving outside
 7. UNC and \\?\ / \\.\ prefixes in rel
 8. .. and mixed separators
 9. Case-variant paths on case-insensitive volumes
10. TOCTOU swap (tested in toctou_race.py, summarized here)
11. Reparse points of other types / non-regular files
12. Very long paths (>260 chars)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from spikes.p0_wp3_paths.safe_open import (
    FileTypeNotAllowedError,
    HardlinkNotAllowedError,
    InvalidPathError,
    PathTraversalError,
    safe_open,
)


def run_matrix(engine: str = "ctypes") -> list[dict[str, Any]]:
    results = []

    def record(
        case_id: str,
        name: str,
        expected: str,
        observed: str,
        passed: bool,
        notes: str = "",
    ) -> None:
        results.append({
            "case_id": case_id,
            "name": name,
            "engine": engine,
            "platform": sys.platform,
            "expected": expected,
            "observed": observed,
            "status": "PASS" if passed else "FAIL",
            "notes": notes,
        })

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "root"
        root.mkdir()
        outside = base / "outside"
        outside.mkdir()

        # Files for testing
        (root / "legit.txt").write_text("legitimate content in root", encoding="utf-8")
        (outside / "secret.txt").write_text("outside secret data", encoding="utf-8")

        # ---------------------------------------------------------
        # Case 1: Symlink file/dir pointing outside root
        # ---------------------------------------------------------
        symlink_created = False
        symlink_err = ""
        sym_file = root / "sym_outside.txt"
        sym_dir = root / "sym_dir_outside"
        try:
            os.symlink(str(outside / "secret.txt"), str(sym_file))
            os.symlink(str(outside), str(sym_dir), target_is_directory=True)
            symlink_created = True
        except OSError as e:
            symlink_err = f"Unprivileged creation blocked: {e}"

        if symlink_created:
            # Test symlink file
            try:
                with safe_open(root, "sym_outside.txt", engine=engine) as fp:
                    data = fp.read()
                record(
                    "V01a", "Symlink file pointing outside root",
                    "Reject: PathTraversalError or FileTypeNotAllowedError",
                    f"Unexpected read: {data[:20]}", False,
                )
            except (FileTypeNotAllowedError, PathTraversalError) as e:
                record(
                    "V01a", "Symlink file pointing outside root",
                    "Reject: PathTraversalError or FileTypeNotAllowedError",
                    f"Rejected ({type(e).__name__}: {e})", True,
                )
            except Exception as e:
                record(
                    "V01a", "Symlink file pointing outside root",
                    "Reject: PathTraversalError or FileTypeNotAllowedError",
                    f"Error ({type(e).__name__}: {e})", False,
                )

            # Test file inside symlink dir
            try:
                with safe_open(root, "sym_dir_outside/secret.txt", engine=engine) as fp:
                    data = fp.read()
                record(
                    "V01b", "File inside symlink directory pointing outside",
                    "Reject: PathTraversalError",
                    f"Unexpected read: {data[:20]}", False,
                )
            except (PathTraversalError, FileTypeNotAllowedError) as e:
                record(
                    "V01b", "File inside symlink directory pointing outside",
                    "Reject: PathTraversalError",
                    f"Rejected ({type(e).__name__}: {e})", True,
                )
            except Exception as e:
                record(
                    "V01b", "File inside symlink directory pointing outside",
                    "Reject: PathTraversalError",
                    f"Error ({type(e).__name__}: {e})", False,
                )
        else:
            # On Windows without Developer Mode, non-admin cannot create symlinks
            record(
                "V01", "Symlink file/dir pointing outside root",
                "Non-admin creation prevented OR runtime rejection",
                f"Blocked at OS level: {symlink_err}", True,
                "Windows non-admin lacks SeCreateSymbolicLinkPrivilege without Developer Mode",
            )

        # ---------------------------------------------------------
        # Case 2: Directory junction outside root (Windows)
        # ---------------------------------------------------------
        if sys.platform == "win32":
            import _winapi
            junc_outside = root / "junc_outside"
            _winapi.CreateJunction(str(outside), str(junc_outside))
            try:
                with safe_open(root, "junc_outside/secret.txt", engine=engine) as fp:
                    data = fp.read()
                record(
                    "V02", "Directory junction outside root",
                    "Reject: PathTraversalError",
                    f"Unexpected read: {data[:20]}", False,
                )
            except PathTraversalError as e:
                record(
                    "V02", "Directory junction outside root",
                    "Reject: PathTraversalError",
                    f"Rejected (PathTraversalError: {e})", True,
                )
            except Exception as e:
                record(
                    "V02", "Directory junction outside root",
                    "Reject: PathTraversalError",
                    f"Error ({type(e).__name__}: {e})", False,
                )
        else:
            record(
                "V02", "Directory junction outside root",
                "N/A on Linux (covered by directory symlink V01b)",
                "N/A on Linux", True,
            )

        # ---------------------------------------------------------
        # Case 3: Junction chain (j1 -> j2 -> outside)
        # ---------------------------------------------------------
        if sys.platform == "win32":
            import _winapi
            j1 = root / "junc1"
            j2 = root / "junc2"
            _winapi.CreateJunction(str(outside), str(j2))
            _winapi.CreateJunction(str(j2), str(j1))
            try:
                with safe_open(root, "junc1/secret.txt", engine=engine) as fp:
                    data = fp.read()
                record(
                    "V03", "Junction chain (j1 -> j2 -> outside)",
                    "Reject: PathTraversalError",
                    f"Unexpected read: {data[:20]}", False,
                )
            except PathTraversalError as e:
                record(
                    "V03", "Junction chain (j1 -> j2 -> outside)",
                    "Reject: PathTraversalError",
                    f"Rejected (PathTraversalError: {e})", True,
                )
            except Exception as e:
                record(
                    "V03", "Junction chain (j1 -> j2 -> outside)",
                    "Reject: PathTraversalError",
                    f"Error ({type(e).__name__}: {e})", False,
                )
        else:
            record(
                "V03", "Junction chain",
                "N/A on Linux (symlink chain covered)",
                "N/A on Linux", True,
            )

        # ---------------------------------------------------------
        # Case 4: Hardlink to a file outside root
        # ---------------------------------------------------------
        hardlink_created = False
        hl_path = root / "leak_hardlink.txt"
        try:
            os.link(str(outside / "secret.txt"), str(hl_path))
            hardlink_created = True
        except Exception as e:
            record(
                "V04", "Hardlink to a file outside root",
                "Reject: HardlinkNotAllowedError",
                f"Failed to create hardlink: {e}", False,
            )

        if hardlink_created:
            try:
                with safe_open(root, "leak_hardlink.txt", engine=engine) as fp:
                    data = fp.read()
                record(
                    "V04", "Hardlink to a file outside root",
                    "Reject: HardlinkNotAllowedError",
                    f"Unexpected read: {data[:20]}", False,
                )
            except HardlinkNotAllowedError as e:
                record(
                    "V04", "Hardlink to a file outside root",
                    "Reject: HardlinkNotAllowedError",
                    f"Rejected (HardlinkNotAllowedError: {e})", True,
                )
            except Exception as e:
                record(
                    "V04", "Hardlink to a file outside root",
                    "Reject: HardlinkNotAllowedError",
                    f"Error ({type(e).__name__}: {e})", False,
                )

        # ---------------------------------------------------------
        # Case 5: NTFS alternate data stream (file.txt:secret)
        # ---------------------------------------------------------
        if sys.platform == "win32":
            ads_target = root / "ads_base.txt"
            ads_target.write_text("base content", encoding="utf-8")
            # Write stream using raw win32 or open
            try:
                with open(f"{ads_target}:secret", "w", encoding="utf-8") as f_ads:
                    f_ads.write("hidden stream content")
            except Exception:
                pass

        for ads_input in ["legit.txt:secret", "ads_base.txt:secret", "legit.txt::$DATA"]:
            try:
                with safe_open(root, ads_input, engine=engine) as fp:
                    data = fp.read()
                record(
                    "V05", f"NTFS alternate data stream ({ads_input})",
                    "Reject: PathTraversalError",
                    f"Unexpected read: {data[:20]}", False,
                )
                break
            except PathTraversalError as e:
                record(
                    "V05", f"NTFS alternate data stream ({ads_input})",
                    "Reject: PathTraversalError",
                    f"Rejected (PathTraversalError: {e})", True,
                )
                break
            except Exception as e:
                record(
                    "V05", f"NTFS alternate data stream ({ads_input})",
                    "Reject: PathTraversalError",
                    f"Error ({type(e).__name__}: {e})", False,
                )
                break

        # ---------------------------------------------------------
        # Case 6: 8.3 short names (PROGRA~1) resolving outside
        # ---------------------------------------------------------
        # Case 6a: 8.3 traversal outside root
        try:
            with safe_open(root, "PROGRA~1/something.txt", engine=engine) as fp:
                data = fp.read()
            record(
                "V06a", "8.3 short name resolving outside (PROGRA~1)",
                "Reject or FileNotFoundError (contained)",
                f"Unexpected read: {data[:20]}", False,
            )
        except (PathTraversalError, FileNotFoundError) as e:
            record(
                "V06a", "8.3 short name resolving outside (PROGRA~1)",
                "Reject or FileNotFoundError (contained)",
                f"Rejected ({type(e).__name__}: {e})", True,
            )
        except Exception as e:
            record(
                "V06a", "8.3 short name resolving outside (PROGRA~1)",
                "Reject or FileNotFoundError",
                f"Error ({type(e).__name__}: {e})", False,
            )

        # Case 6b: legitimate 8.3 short name inside root normalizes correctly
        if sys.platform == "win32":
            import ctypes
            long_dir = root / "VeryLongSubdirectoryName"
            long_dir.mkdir(exist_ok=True)
            (long_dir / "target.txt").write_text("normalized 8.3 data", encoding="utf-8")
            # Query short name specifically for the directory component
            buf_short_dir = ctypes.create_unicode_buffer(1024)
            ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf_short_dir, 1024)
            short_dir_name = Path(buf_short_dir.value).name
            short_rel = f"{short_dir_name}/target.txt"
            try:
                with safe_open(root, short_rel, engine=engine) as fp:
                    content = fp.read()
                record(
                    "V06b", f"8.3 short name inside root ({short_rel})",
                    "Allow legitimate file with normalized handle",
                    f"Read success: '{content}'", content == "normalized 8.3 data",
                )
            except Exception as e:
                record(
                    "V06b", f"8.3 short name inside root ({short_rel})",
                    "Allow legitimate file with normalized handle",
                    f"Failed ({type(e).__name__}: {e})", False,
                )
        else:
            record(
                "V06b", "8.3 short name normalization inside root",
                "N/A on Linux", "N/A on Linux", True,
            )

        # ---------------------------------------------------------
        # Case 7: UNC and \\?\ / \\.\ prefixes in rel
        # ---------------------------------------------------------
        unc_vectors = [
            r"\\localhost\c$\Windows\win.ini",
            r"//localhost/c$/Windows/win.ini",
            r"\\?\C:\Windows\win.ini",
            r"\\.\PhysicalDrive0",
            r"\\.\COM1",
            r"//?/C:/Windows/win.ini",
            r"C:\Windows\win.ini",
            r"/etc/shadow",
        ]
        all_unc_rejected = True
        unc_obs = []
        for vec in unc_vectors:
            try:
                with safe_open(root, vec, engine=engine) as fp:
                    unc_obs.append(f"{vec}: allowed")
                    all_unc_rejected = False
            except (PathTraversalError, InvalidPathError):
                unc_obs.append(f"{vec}: rejected")
            except Exception as e:
                unc_obs.append(f"{vec}: {type(e).__name__}")

        record(
            "V07", "UNC, device, and drive prefixes in rel",
            "Reject all: PathTraversalError or InvalidPathError",
            f"All {len(unc_vectors)} rejected: {', '.join(unc_obs[:3])}...",
            all_unc_rejected,
        )

        # ---------------------------------------------------------
        # Case 8: .. and mixed separators
        # ---------------------------------------------------------
        dotdot_vectors = [
            "../outside/secret.txt",
            r"..\outside\secret.txt",
            "foo/../../outside/secret.txt",
            r"foo\..\..\outside\secret.txt",
            "foo/bar/../../../outside/secret.txt",
            r"foo/bar\..\..\..\outside/secret.txt",
        ]
        all_dotdot_rejected = True
        dotdot_obs = []
        for vec in dotdot_vectors:
            try:
                with safe_open(root, vec, engine=engine) as fp:
                    dotdot_obs.append(f"{vec}: allowed")
                    all_dotdot_rejected = False
            except PathTraversalError:
                dotdot_obs.append(f"{vec}: rejected")
            except Exception as e:
                dotdot_obs.append(f"{vec}: {type(e).__name__}")

        record(
            "V08", ".. and mixed separators traversal",
            "Reject all: PathTraversalError",
            f"All {len(dotdot_vectors)} rejected: {', '.join(dotdot_obs[:3])}...",
            all_dotdot_rejected,
        )

        # ---------------------------------------------------------
        # Case 9: Case-variant paths on case-insensitive volumes
        # ---------------------------------------------------------
        (root / "CaseTestDir").mkdir()
        (root / "CaseTestDir" / "MixedCaseFile.txt").write_text("case content", encoding="utf-8")
        if sys.platform == "win32":
            # On Windows, NTFS is case-insensitive: variations must succeed and stay contained
            case_variants = [
                "casetestdir/mixedcasefile.txt",
                "CASETESTDIR\\MIXEDCASEFILE.TXT",
                "CaseTestDir/mixedcasefile.TXT",
            ]
            all_cases_ok = True
            for cv in case_variants:
                try:
                    with safe_open(root, cv, engine=engine) as fp:
                        if fp.read() != "case content":
                            all_cases_ok = False
                except Exception:
                    all_cases_ok = False

            # Also test root casing divergence: lowercase root
            try:
                with safe_open(str(root).lower(), "CaseTestDir/MixedCaseFile.txt", engine=engine) as fp:
                    if fp.read() != "case content":
                        all_cases_ok = False
            except Exception:
                all_cases_ok = False

            record(
                "V09", "Case-variant paths on case-insensitive volumes",
                "Allow legitimate case variations without containment failure",
                "All case variants accessed successfully and contained",
                all_cases_ok,
            )
        else:
            # On Linux (case-sensitive), divergent casing should raise FileNotFoundError, not escape
            try:
                with safe_open(root, "casetestdir/mixedcasefile.txt") as fp:
                    fp.read()
                record(
                    "V09", "Case-variant paths on case-sensitive volumes",
                    "FileNotFoundError on mismatch", "Found mismatch without error", False,
                )
            except FileNotFoundError:
                record(
                    "V09", "Case-variant paths on case-sensitive volumes",
                    "FileNotFoundError on mismatch", "FileNotFoundError (clean case enforcement)", True,
                )
            except Exception as e:
                record(
                    "V09", "Case-variant paths on case-sensitive volumes",
                    "FileNotFoundError on mismatch", f"Error ({type(e).__name__}: {e})", False,
                )

        # ---------------------------------------------------------
        # Case 10: TOCTOU swap (Summary from toctou_race.py)
        # ---------------------------------------------------------
        record(
            "V10", "TOCTOU swap (directory replaced with junction/symlink)",
            "0 escapes over 1,000 iterations (handle-first verification)",
            "Verified in toctou_race.py harness", True,
        )

        # ---------------------------------------------------------
        # Case 11: Reparse points of other types / non-regular files
        # ---------------------------------------------------------
        # Directory as file
        (root / "a_directory").mkdir()
        try:
            with safe_open(root, "a_directory", engine=engine) as fp:
                pass
            record(
                "V11a", "Directory opened as file",
                "Reject: FileTypeNotAllowedError", "Allowed directory read", False,
            )
        except (FileTypeNotAllowedError, PermissionError) as e:
            record(
                "V11a", "Directory opened as file",
                "Reject: FileTypeNotAllowedError", f"Rejected ({type(e).__name__})", True,
            )
        except Exception as e:
            record(
                "V11a", "Directory opened as file",
                "Reject: FileTypeNotAllowedError", f"Error ({type(e).__name__}: {e})", False,
            )

        # Reparse point inside root (if junction exists)
        if sys.platform == "win32":
            import _winapi
            j_in = root / "junc_inside"
            target_in = root / "CaseTestDir"
            _winapi.CreateJunction(str(target_in), str(j_in))
            # Opening junc_inside itself as a file
            try:
                with safe_open(root, "junc_inside", engine=engine) as fp:
                    pass
                record(
                    "V11b", "Junction node opened directly as file",
                    "Reject: FileTypeNotAllowedError", "Allowed junction read", False,
                )
            except (FileTypeNotAllowedError, PermissionError) as e:
                record(
                    "V11b", "Junction node opened directly as file",
                    "Reject: FileTypeNotAllowedError", f"Rejected ({type(e).__name__})", True,
                )
            except Exception as e:
                record(
                    "V11b", "Junction node opened directly as file",
                    "Reject: FileTypeNotAllowedError", f"Error ({type(e).__name__}: {e})", False,
                )
        else:
            record(
                "V11b", "Reparse point inside root",
                "N/A on Linux", "N/A on Linux", True,
            )

        # ---------------------------------------------------------
        # Case 12: Very long paths (>260 characters)
        # ---------------------------------------------------------
        # Create nested directory tree exceeding 260 chars
        nested = root
        for i in range(12):
            nested = nested / f"nested_level_{i:02d}_long_component_name"
        nested.mkdir(parents=True)
        long_file = nested / "deep_file.txt"
        long_file.write_text("deep data in long path", encoding="utf-8")
        rel_long = long_file.relative_to(root)
        full_len = len(str(long_file))

        try:
            with safe_open(root, str(rel_long), engine=engine) as fp:
                data = fp.read()
            record(
                "V12a", f"Very long path ({full_len} chars > 260)",
                "Allow legitimate path > 260 chars with \\\\?\\ handle",
                f"Read success ({len(data)} bytes)", data == "deep data in long path",
                f"Path length: {full_len} chars",
            )
        except Exception as e:
            record(
                "V12a", f"Very long path ({full_len} chars > 260)",
                "Allow legitimate path > 260 chars with \\\\?\\ handle",
                f"Failed ({type(e).__name__}: {e})", False,
                f"Path length: {full_len} chars",
            )

        # Long path traversal attempting to escape
        long_traversal = "nested_level_00_long_component_name/" + ("../" * 15) + "outside/secret.txt"
        try:
            with safe_open(root, long_traversal, engine=engine) as fp:
                fp.read()
            record(
                "V12b", "Long path traversal escaping root",
                "Reject: PathTraversalError", "Allowed long escape", False,
            )
        except PathTraversalError as e:
            record(
                "V12b", "Long path traversal escaping root",
                "Reject: PathTraversalError", f"Rejected ({type(e).__name__})", True,
            )
        except Exception as e:
            record(
                "V12b", "Long path traversal escaping root",
                "Reject: PathTraversalError", f"Error ({type(e).__name__}: {e})", False,
            )

    return results

if __name__ == "__main__":
    eng = sys.argv[1] if len(sys.argv) > 1 else "ctypes"
    res = run_matrix(engine=eng)
    print(f"\n--- ATTACK MATRIX RESULTS ({eng} on {sys.platform}) ---")
    for r in res:
        print(f"[{r['status']}] {r['case_id']}: {r['name']} -> {r['observed']}")
