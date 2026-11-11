"""Tests for safe_open path containment, handle inspection, and attack matrix (P2a-WP2, ISS-027).

Ported from P0-WP3 spike attack matrix:
- V01: Symlink file/dir pointing outside root
- V02: Directory junction outside root
- V03: Junction chain (j1 -> j2 -> outside)
- V04: Hardlink to a file outside root (nNumberOfLinks / st_nlink > 1)
- V05: NTFS alternate data stream (file.txt:secret, ::$DATA)
- V06: 8.3 short names (PROGRA~1 vs internal short names)
- V07: UNC and NT device namespaces (\\\\, //, \\\\?\\, \\\\.\\)
- V08: Parent directory traversal (..) and mixed separators (/ and \\)
- V09: Case-variant paths on case-insensitive volumes
- V10: TOCTOU directory swap race
- V11: Directory opened as file, junction opened as file
- V12: Long paths (>260 characters)
- ISS-027: Handle closed exactly once (no handle leak or double-close)
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from eap.ppm.collector.safe_open import (
    FileTooLargeError,
    FileTypeNotAllowedError,
    HardlinkNotAllowedError,
    PathTraversalError,
    safe_open,
)


@pytest.fixture
def test_dirs(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    (root / "legit.txt").write_text("legitimate content in root", encoding="utf-8")
    (outside / "secret.txt").write_text("outside secret data", encoding="utf-8")
    return root, outside


def test_v01_symlink_rejection(test_dirs):
    """V01: Symlink pointing outside root is blocked or raises FileTypeNotAllowedError/PathTraversalError."""
    root, outside = test_dirs
    sym_file = root / "sym_outside.txt"

    try:
        os.symlink(str(outside / "secret.txt"), str(sym_file))
    except OSError:
        # Non-admin Windows without developer mode blocks symlink creation
        pytest.skip("Symlink creation not permitted for unprivileged user on Windows")

    with (
        pytest.raises((FileTypeNotAllowedError, PathTraversalError)),
        safe_open(root, "sym_outside.txt") as f,
    ):
        f.read()


def test_v02_junction_outside(test_dirs):
    """V02: Directory junction outside root raises PathTraversalError."""
    if sys.platform != "win32":
        pytest.skip("Directory junctions are Windows-specific")

    import _winapi
    root, outside = test_dirs
    junc = root / "junc_outside"
    _winapi.CreateJunction(str(outside), str(junc))

    with pytest.raises(PathTraversalError), safe_open(root, "junc_outside/secret.txt") as f:
        f.read()


def test_v03_junction_chain(test_dirs):
    """V03: Junction chain (j1 -> j2 -> outside) raises PathTraversalError."""
    if sys.platform != "win32":
        pytest.skip("Directory junctions are Windows-specific")

    import _winapi
    root, outside = test_dirs
    j1 = root / "junc1"
    j2 = root / "junc2"
    _winapi.CreateJunction(str(outside), str(j2))
    _winapi.CreateJunction(str(j2), str(j1))

    with pytest.raises(PathTraversalError), safe_open(root, "junc1/secret.txt") as f:
        f.read()


def test_v04_hardlink_outside(test_dirs):
    """V04: Hardlink to file outside root raises HardlinkNotAllowedError (link count > 1)."""
    root, outside = test_dirs
    hl = root / "hardlink_leak.txt"

    try:
        os.link(str(outside / "secret.txt"), str(hl))
    except (OSError, NotImplementedError):
        pytest.skip("Hardlinks not supported on this filesystem")

    with pytest.raises(HardlinkNotAllowedError), safe_open(root, "hardlink_leak.txt") as f:
        f.read()


def test_v05_ntfs_alternate_data_stream(test_dirs):
    """V05: NTFS alternate data streams (:stream) are rejected with PathTraversalError."""
    root, _ = test_dirs
    for stream_target in ["legit.txt:secret", "legit.txt::$DATA", "nested/file.txt:stream"]:
        with pytest.raises(PathTraversalError):
            safe_open(root, stream_target)


def test_v06a_short_name_traversal_rejected(test_dirs):
    """V06a: 8.3 short name resolving outside raises PathTraversalError or FileNotFoundError."""
    root, _ = test_dirs
    with (
        pytest.raises((PathTraversalError, FileNotFoundError)),
        safe_open(root, "PROGRA~1/something.txt") as f,
    ):
        f.read()


def test_v06b_short_name_inside_normalized(test_dirs):
    """V06b: Legitimate 8.3 short name inside root normalizes and reads successfully on Windows."""
    if sys.platform != "win32":
        pytest.skip("8.3 short names are Windows-specific")

    import ctypes
    root, _ = test_dirs
    long_dir = root / "VeryLongSubdirectoryName"
    long_dir.mkdir(exist_ok=True)
    (long_dir / "target.txt").write_text("normalized 8.3 content", encoding="utf-8")

    buf = ctypes.create_unicode_buffer(1024)
    ret = ctypes.windll.kernel32.GetShortPathNameW(str(long_dir), buf, 1024)
    if ret == 0 or not buf.value:
        pytest.skip("8.3 generation disabled on this volume")

    short_name = Path(buf.value).name
    short_rel = f"{short_name}/target.txt"

    with safe_open(root, short_rel) as f:
        data = f.read()
    assert data == "normalized 8.3 content"


def test_v07_unc_and_nt_prefixes_rejected(test_dirs):
    """V07: UNC and NT device namespaces in relative path are rejected pre-open."""
    root, _ = test_dirs
    bad_prefixes = [
        r"\\localhost\c$\Windows\win.ini",
        r"//localhost/c$/Windows/win.ini",
        r"\\?\C:\Windows\win.ini",
        r"\\.\PhysicalDrive0",
        r"//?/C:/Windows/win.ini",
        r"//./COM1",
        r"\Windows\system32\calc.exe",
        r"/etc/passwd",
        r"C:\boot.ini",
    ]
    for bad_rel in bad_prefixes:
        with pytest.raises(PathTraversalError):
            safe_open(root, bad_rel)


def test_v08_parent_traversal_and_mixed_separators(test_dirs):
    """V08: Parent directory traversal (..) and mixed separators are rejected."""
    root, _ = test_dirs
    traversals = [
        "../outside/secret.txt",
        r"..\outside\secret.txt",
        "sub/../../secret.txt",
        r"sub\..\..\secret.txt",
        "sub/../legit.txt",
        r"sub/..\legit.txt",
    ]
    for bad_rel in traversals:
        with pytest.raises(PathTraversalError):
            safe_open(root, bad_rel)


def test_v09_case_sensitivity_containment(test_dirs):
    """V09: Case-variant paths succeed when legitimate on Windows or fail with FileNotFoundError."""
    root, _ = test_dirs
    sub = root / "SubDir"
    sub.mkdir()
    (sub / "file.txt").write_text("case test data", encoding="utf-8")

    if sys.platform == "win32":
        with safe_open(root, "subdir/file.txt") as f:
            assert f.read() == "case test data"
        with safe_open(root, "SUBDIR/FILE.TXT") as f:
            assert f.read() == "case test data"
    else:
        # Case mismatch on Linux raises FileNotFoundError without escaping
        with pytest.raises(FileNotFoundError), safe_open(root, "SUBDIR/FILE.TXT") as f:
            f.read()


def test_v10_toctou_directory_swap_race():
    """V10: TOCTOU race where an intermediate directory is swapped with an outside link/junction

    results in 0 containment escapes across rapid operations.
    """
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "root"
        root.mkdir()
        outside = base / "outside"
        outside.mkdir()

        (outside / "secret.txt").write_text("PWNED_SECRET_DATA", encoding="utf-8")

        safe_target_dir = base / "safe_dir"
        safe_target_dir.mkdir()
        (safe_target_dir / "target.txt").write_text("SAFE_DATA", encoding="utf-8")

        swap_point = root / "swap"
        # Initial link to safe dir
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(safe_target_dir), str(swap_point))
        else:
            try:
                os.symlink(str(safe_target_dir), str(swap_point))
            except OSError:
                pytest.skip("Symlink creation not supported")

        stop_event = threading.Event()

        def swapper():
            is_outside = False
            while not stop_event.is_set():
                try:
                    if sys.platform == "win32":
                        import _winapi
                        os.rmdir(str(swap_point))
                        target = str(outside if not is_outside else safe_target_dir)
                        _winapi.CreateJunction(target, str(swap_point))
                    else:
                        os.unlink(str(swap_point))
                        target = str(outside if not is_outside else safe_target_dir)
                        os.symlink(target, str(swap_point))
                    is_outside = not is_outside
                except OSError:
                    pass

        t = threading.Thread(target=swapper, daemon=True)
        t.start()

        escapes = 0
        try:
            for _ in range(200):
                try:
                    with safe_open(root, "swap/target.txt") as f:
                        content = f.read()
                        if "PWNED" in content:
                            escapes += 1
                except (PathTraversalError, FileTypeNotAllowedError, FileNotFoundError, OSError):
                    pass
        finally:
            stop_event.set()
            t.join(timeout=2.0)

        assert escapes == 0, f"TOCTOU escape detected! {escapes} unauthorized reads leaked secret data."


def test_v11_directory_opened_as_file_rejected(test_dirs):
    """V11: Opening a directory as a file raises FileTypeNotAllowedError."""
    root, _ = test_dirs
    sub = root / "subfolder"
    sub.mkdir()

    with pytest.raises(FileTypeNotAllowedError), safe_open(root, "subfolder") as f:
        f.read()


def test_v12_long_paths_containment(test_dirs):
    """V12: Deep paths (>260 chars) are accessible when legitimate and contained."""
    root, _ = test_dirs
    deep = root
    rel_parts = []
    # Build a path exceeding 260 characters
    for i in range(12):
        seg = f"deep_nested_segment_{i:02d}"
        rel_parts.append(seg)
        deep = deep / seg
    deep.mkdir(parents=True)
    target = deep / "payload.txt"
    target.write_text("deep payload", encoding="utf-8")

    rel_path = "/".join(rel_parts) + "/payload.txt"
    assert len(str(target)) > 260 or len(rel_path) > 100

    with safe_open(root, rel_path) as f:
        assert f.read() == "deep payload"


def test_iss027_handle_closed_exactly_once(test_dirs):
    """ISS-027: Handle is closed exactly once upon stream exit without leaking or double-closing."""
    root, _ = test_dirs
    file_path = root / "handle_test.txt"
    file_path.write_text("handle close safety verification", encoding="utf-8")

    # Open and close 100 times in sequence; if handles leak on Windows, file deletion or table limits fail
    for _ in range(100):
        with safe_open(root, "handle_test.txt") as f:
            content = f.read()
            assert content == "handle close safety verification"

    # Immediately modify / delete the file; if a handle remained open, Windows raises PermissionError
    file_path.write_text("updated content after handles closed", encoding="utf-8")
    assert file_path.read_text(encoding="utf-8") == "updated content after handles closed"


def test_max_file_bytes_limit(test_dirs):
    """safe_open strictly enforces max_file_bytes attribute check."""
    root, _ = test_dirs
    big_file = root / "oversized.bin"
    big_file.write_bytes(b"x" * 1024)

    with (
        pytest.raises(FileTooLargeError),
        safe_open(root, "oversized.bin", max_file_bytes=512) as f,
    ):
        f.read()
