"""identity.py — Physical filesystem identity inspection and safety boundary checks (ISS-028).

Enforces:
- Physical filesystem identity resolution (Windows volume serial + file index; Linux st_dev + st_ino).
- Safety boundary: When EAP_COLLECTOR_ALLOW_REAL_ROOTS is false, refuses any root outside
  the OS temp directory or configured fixtures root.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


class RootNotPermittedSecurityError(PermissionError):
    """Raised when a directory root is outside permitted temp or fixture directories."""


class RootReplacedSecurityError(PermissionError):
    """Raised when the physical filesystem identity of a source root changed between scans."""


def get_fs_identity(path: str | Path) -> str:
    """Derive unique physical filesystem identity from the underlying storage inode/file index.

    Windows: volume serial number + 64-bit file index (nFileIndexHigh:nFileIndexLow).
    Linux: st_dev + st_ino.
    """
    path_str = str(path)
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32

        FILE_READ_ATTRIBUTES = 0x0080
        FILE_SHARE_READ = 0x00000001
        FILE_SHARE_WRITE = 0x00000002
        FILE_SHARE_DELETE = 0x00000004
        OPEN_EXISTING = 3
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        from .safe_open import BY_HANDLE_FILE_INFORMATION

        abs_path = os.path.abspath(path_str)
        win_path = abs_path if abs_path.startswith("\\\\?\\") else f"\\\\?\\{abs_path}"

        h = kernel32.CreateFileW(
            win_path,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if h == INVALID_HANDLE_VALUE or h == 0:
            err = ctypes.GetLastError()
            raise OSError(err, f"Failed to open directory '{path_str}' for identity: Win32 error {err}")

        try:
            info = BY_HANDLE_FILE_INFORMATION()
            if not kernel32.GetFileInformationByHandle(h, ctypes.byref(info)):
                err = ctypes.GetLastError()
                raise OSError(err, f"GetFileInformationByHandle failed: error {err}")

            vol = info.dwVolumeSerialNumber
            idx = (info.nFileIndexHigh << 32) | info.nFileIndexLow
            return f"win:{vol:08x}:{idx:016x}"
        finally:
            kernel32.CloseHandle(h)
    else:
        st = os.stat(path_str)
        return f"posix:{st.st_dev}:{st.st_ino}"


def check_root_permission(
    root_path: str | Path,
    *,
    allow_real_roots: bool = False,
    fixtures_root: str | Path | None = None,
) -> None:
    """Enforce the non-negotiable safety boundary on location roots.

    When allow_real_roots is false, the collector strictly refuses any root
    outside the OS temp directory or the configured fixtures root.
    """
    if allow_real_roots:
        return

    real_root = os.path.realpath(str(root_path))
    real_temp = os.path.realpath(tempfile.gettempdir())

    # Case-insensitive comparison on Windows
    if sys.platform == "win32":
        norm_root = real_root.casefold()
        norm_temp = real_temp.casefold()
        is_in_temp = norm_root.startswith(norm_temp)
    else:
        is_in_temp = real_root.startswith(real_temp)

    if is_in_temp:
        return

    if fixtures_root is not None:
        real_fixtures = os.path.realpath(str(fixtures_root))
        if sys.platform == "win32":
            norm_fixtures = real_fixtures.casefold()
            if norm_root.startswith(norm_fixtures):
                return
        else:
            if real_root.startswith(real_fixtures):
                return

    raise RootNotPermittedSecurityError(
        f"Collector refused root '{root_path}': path is outside OS temp directory '{real_temp}' "
        "while EAP_COLLECTOR_ALLOW_REAL_ROOTS is False."
    )


__all__ = [
    "RootNotPermittedSecurityError",
    "RootReplacedSecurityError",
    "check_root_permission",
    "get_fs_identity",
]
