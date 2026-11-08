"""safe_open.py — Secure file opening with strict path containment, handle-based
verification, link count, and file type enforcement.

Implements P0-WP3 ctypes architecture (ISS-027, ISS-028):
- Windows: ctypes CreateFileW with FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
  GetFinalPathNameByHandleW containment check, BY_HANDLE_FILE_INFORMATION link count and
  attributes check, msvcrt.open_osfhandle transfer with single-close safety.
- Linux: openat2 syscall with RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS, with fallback to
  O_NOFOLLOW per component + /proc/self/fd inspection.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO, TextIO, cast

# --- Exception Hierarchy ---

class SafeOpenError(Exception):
    """Base exception for all safe_open errors."""


class InvalidPathError(SafeOpenError):
    """Raised when the path format or characters are disallowed."""


class PathTraversalError(SafeOpenError):
    """Raised when path resolution escapes the allowed root directory."""


class HardlinkNotAllowedError(SafeOpenError):
    """Raised when a file has hardlinks (st_nlink > 1) and hardlinks are disallowed."""


class FileTypeNotAllowedError(SafeOpenError):
    """Raised when the target is not a regular disk file (directory, symlink, device, pipe, etc.)."""


class FileTooLargeError(SafeOpenError):
    """Raised when file size exceeds max_file_bytes."""


# --- Common Path Validation ---

def validate_relative_path(rel: str | Path) -> str:
    """Validate relative path syntax against escape vectors and illegal constructs.

    Adheres to contracts/location-registration.schema.json:
    - No empty paths or null bytes
    - No alternate data streams (':')
    - No UNC prefixes (\\\\ or //)
    - No absolute paths or leading slashes (/ or \\)
    - No drive specifiers (e.g. C:)
    - No device/NT namespaces (\\\\?\\, \\\\.\\, //?/, //./)
    - No parent traversal ('..')
    """
    rel_str = str(rel)
    if not rel_str:
        raise InvalidPathError("Empty relative path is not permitted.")
    if "\0" in rel_str:
        raise InvalidPathError("Null bytes are not permitted in path.")
    if ":" in rel_str:
        raise PathTraversalError("Colons (alternate data streams or drive specifiers) are not permitted.")
    if rel_str.startswith(("\\\\", "//")):
        raise PathTraversalError("UNC paths are not permitted.")
    if rel_str.startswith(("/", "\\")):
        raise PathTraversalError("Absolute paths are not permitted in relative component.")
    if len(rel_str) >= 2 and rel_str[1] == ":" and rel_str[0].isalpha():
        raise PathTraversalError("Drive specifiers are not permitted.")
    if rel_str.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise PathTraversalError("Device and extended paths are not permitted.")

    # Split components by both / and \\
    normalized = rel_str.replace("/", "\\")
    parts = normalized.split("\\")
    for part in parts:
        if part == "..":
            raise PathTraversalError("Parent directory traversal ('..') is not permitted.")
    return rel_str


# --- Windows Implementation (ctypes stdlib) ---

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32

    # Win32 Constants
    GENERIC_READ = 0x80000000
    FILE_READ_ATTRIBUTES = 0x0080
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3

    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000

    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_DEVICE = 0x00000040
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400

    FILE_TYPE_DISK = 0x0001

    VOLUME_NAME_DOS = 0x00000000
    FILE_NAME_NORMALIZED = 0x00000000

    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD

    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(BY_HANDLE_FILE_INFORMATION),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL

    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def _win_get_final_path_ctypes(handle: int) -> str:
        buf = ctypes.create_unicode_buffer(32768)
        ret = kernel32.GetFinalPathNameByHandleW(handle, buf, 32768, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS)
        if ret == 0:
            err = ctypes.GetLastError()
            raise OSError(err, f"GetFinalPathNameByHandleW failed with error {err}")
        return buf.value

    def canonicalize_root_windows(root: str | Path) -> str:
        """Resolve canonical NT/DOS path for root directory with trailing backslash."""
        root_str = os.path.abspath(str(root))
        win_path = root_str if root_str.startswith("\\\\?\\") else f"\\\\?\\{root_str}"

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
            raise OSError(err, f"Failed to open root directory '{root_str}': error {err}")
        try:
            final = _win_get_final_path_ctypes(h)
        finally:
            kernel32.CloseHandle(h)

        if not final.endswith("\\"):
            final += "\\"
        return final

    def _safe_open_windows(
        root: str | Path,
        rel: str | Path,
        mode: str = "r",
        encoding: str | None = "utf-8",
        follow_symlinks: bool = False,
        allow_hardlinked_files: bool = False,
        max_file_bytes: int = 16777216,
    ) -> TextIO | BinaryIO:
        validate_relative_path(rel)
        canon_root = canonicalize_root_windows(root)

        norm_rel = str(rel).replace("/", "\\").lstrip("\\")
        target_path = canon_root + norm_rel

        h = kernel32.CreateFileW(
            target_path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if h == INVALID_HANDLE_VALUE or h == 0:
            err = ctypes.GetLastError()
            raise FileNotFoundError(f"Cannot open '{norm_rel}': Win32 error {err}")

        # Handle is acquired. Enforce handle-level containment and inspection.
        # ISS-027: Ensure handle is closed exactly once. If error occurs before open_osfhandle,
        # kernel32.CloseHandle(h) is called. Once open_osfhandle succeeds, the CRT fd owns
        # the handle and Python's stream close manages it.
        handle_transferred = False
        try:
            # 1. Final Path containment check
            final_path = _win_get_final_path_ctypes(h)
            if not final_path.casefold().startswith(canon_root.casefold()):
                raise PathTraversalError(
                    f"Final path '{final_path}' escapes root '{canon_root}'."
                )
            sub_path = final_path[len(canon_root):]
            if ":" in sub_path:
                raise PathTraversalError(f"Alternate data stream detected in final path: '{final_path}'")

            # 2. File Type check
            ftype = kernel32.GetFileType(h)
            if ftype != FILE_TYPE_DISK:
                raise FileTypeNotAllowedError(f"Handle type {ftype} is not a disk file.")

            info = BY_HANDLE_FILE_INFORMATION()
            if not kernel32.GetFileInformationByHandle(h, ctypes.byref(info)):
                err = ctypes.GetLastError()
                raise OSError(err, f"GetFileInformationByHandle failed: error {err}")

            attrs = info.dwFileAttributes
            n_links = info.nNumberOfLinks
            file_size = (info.nFileSizeHigh << 32) | info.nFileSizeLow

            if attrs & FILE_ATTRIBUTE_DIRECTORY:
                raise FileTypeNotAllowedError("Target is a directory.")
            if (not follow_symlinks) and (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
                raise FileTypeNotAllowedError("Target is a reparse point / symlink.")
            if attrs & FILE_ATTRIBUTE_DEVICE:
                raise FileTypeNotAllowedError("Target is a device file.")

            # 3. Hardlink check
            if (not allow_hardlinked_files) and n_links > 1:
                raise HardlinkNotAllowedError(
                    f"Hardlinked files disallowed: link count {n_links} > 1."
                )

            # 4. Size check
            if file_size > max_file_bytes:
                raise FileTooLargeError(
                    f"File size {file_size} exceeds max_file_bytes {max_file_bytes}."
                )

            # 5. Convert handle to CRT descriptor (ISS-027)
            fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
            handle_transferred = True
            try:
                stream = open(  # noqa: SIM115
                    fd,
                    mode=mode,
                    encoding=encoding if "b" not in mode else None,
                )
                return cast(TextIO | BinaryIO, stream)
            except Exception:
                os.close(fd)
                raise
        finally:
            if not handle_transferred:
                kernel32.CloseHandle(h)


# --- Linux Implementation (openat2 & /proc/self/fd) ---

if sys.platform != "win32":
    import ctypes
    import errno

    SYS_openat2 = 437

    RESOLVE_NO_XDEV = 0x01
    RESOLVE_NO_MAGICLINKS = 0x02
    RESOLVE_NO_SYMLINKS = 0x04
    RESOLVE_BENEATH = 0x08
    RESOLVE_IN_ROOT = 0x10

    class open_how(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint64),
            ("mode", ctypes.c_uint64),
            ("resolve", ctypes.c_uint64),
        ]

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        libc.syscall.argtypes = [
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        HAS_LIBC = True
    except (OSError, AttributeError):
        HAS_LIBC = False

    def canonicalize_root_linux(root: str | Path) -> str:
        r = os.path.realpath(str(root))
        if not r.endswith("/"):
            r += "/"
        return r

    def _safe_open_linux(
        root: str | Path,
        rel: str | Path,
        mode: str = "r",
        encoding: str | None = "utf-8",
        follow_symlinks: bool = False,
        allow_hardlinked_files: bool = False,
        max_file_bytes: int = 16777216,
    ) -> TextIO | BinaryIO:
        validate_relative_path(rel)
        canon_root = canonicalize_root_linux(root)
        norm_rel = str(rel).lstrip("/")

        fd = -1
        dir_fd = -1
        used_openat2 = False

        if HAS_LIBC:
            try:
                dir_fd = os.open(canon_root, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
                resolve_flags = RESOLVE_BENEATH
                if not follow_symlinks:
                    resolve_flags |= RESOLVE_NO_SYMLINKS

                how = open_how(
                    flags=os.O_RDONLY | os.O_CLOEXEC,
                    mode=0,
                    resolve=resolve_flags,
                )
                res = libc.syscall(
                    SYS_openat2,
                    ctypes.c_int(dir_fd),
                    norm_rel.encode("utf-8"),
                    ctypes.byref(how),
                    ctypes.c_size_t(ctypes.sizeof(how)),
                )
                if res >= 0:
                    fd = int(res)
                    used_openat2 = True
                else:
                    err = ctypes.get_errno()
                    if err == errno.EXDEV:
                        raise PathTraversalError(f"openat2: Path '{norm_rel}' escapes beneath root boundary.")
                    elif err == errno.ELOOP:
                        raise FileTypeNotAllowedError(f"openat2: Symlink rejected in '{norm_rel}'.")
                    elif err == errno.ENOENT:
                        raise FileNotFoundError(f"openat2: File not found: '{norm_rel}'")
                    elif err == errno.ENOTDIR:
                        raise FileTypeNotAllowedError(f"openat2: Component is not a directory: '{norm_rel}'")
                    elif err != errno.ENOSYS:
                        raise OSError(err, f"openat2 syscall failed with errno {err}")
            finally:
                if dir_fd >= 0:
                    try:
                        os.close(dir_fd)
                    except OSError:
                        pass

        # Fallback if openat2 is unsupported (e.g. older kernels or container restrictions)
        if not used_openat2:
            current_fd = os.open(canon_root, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
            parts = norm_rel.split("/")
            try:
                for idx, part in enumerate(parts):
                    is_last = idx == len(parts) - 1
                    flags = os.O_CLOEXEC
                    if not follow_symlinks:
                        flags |= os.O_NOFOLLOW
                    if is_last:
                        flags |= os.O_RDONLY
                    else:
                        flags |= os.O_PATH | os.O_DIRECTORY

                    try:
                        next_fd = os.open(part, flags, dir_fd=current_fd)
                    except OSError as e:
                        if e.errno == errno.ELOOP:
                            raise FileTypeNotAllowedError(f"Symlink rejected: '{part}'") from e
                        elif e.errno == errno.ENOENT:
                            raise FileNotFoundError(f"Not found: '{part}'") from e
                        elif e.errno == errno.ENOTDIR:
                            raise FileTypeNotAllowedError(f"Not a directory: '{part}'") from e
                        raise

                    os.close(current_fd)
                    current_fd = next_fd
                fd = current_fd
            except BaseException:
                if current_fd >= 0:
                    try:
                        os.close(current_fd)
                    except OSError:
                        pass
                raise

        # Enforce handle attributes on the open descriptor
        try:
            # 1. Procfs containment verification
            try:
                proc_target = os.readlink(f"/proc/self/fd/{fd}")
                if not proc_target.startswith(canon_root):
                    raise PathTraversalError(
                        f"Target path '{proc_target}' escapes root '{canon_root}'"
                    )
            except OSError:
                pass  # procfs unavailable in sandbox

            # 2. File stat inspection
            st = os.fstat(fd)
            if stat.S_ISDIR(st.st_mode):
                raise FileTypeNotAllowedError("Target is a directory.")
            if (not follow_symlinks) and stat.S_ISLNK(st.st_mode):
                raise FileTypeNotAllowedError("Target is a symbolic link.")
            if not stat.S_ISREG(st.st_mode):
                raise FileTypeNotAllowedError("Target is not a regular file.")

            # 3. Hardlink check
            if (not allow_hardlinked_files) and st.st_nlink > 1:
                raise HardlinkNotAllowedError(
                    f"Hardlinked files disallowed: link count {st.st_nlink} > 1."
                )

            # 4. Size check
            if st.st_size > max_file_bytes:
                raise FileTooLargeError(
                    f"File size {st.st_size} exceeds max_file_bytes {max_file_bytes}."
                )

            stream = open(  # noqa: SIM115
                fd,
                mode=mode,
                encoding=encoding if "b" not in mode else None,
            )
            return cast(TextIO | BinaryIO, stream)
        except BaseException:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise


# --- Unified safe_open Entrypoint ---

def safe_open(
    root: str | Path,
    rel: str | Path,
    mode: str = "r",
    encoding: str | None = "utf-8",
    follow_symlinks: bool = False,
    allow_hardlinked_files: bool = False,
    max_file_bytes: int = 16777216,
) -> TextIO | BinaryIO:
    """Securely open a file with strict path containment, handle-based verification,

    link count, and file type enforcement.

    Args:
        root: The root boundary directory.
        rel: Relative path from the root.
        mode: File open mode ('r', 'rb').
        encoding: Text encoding (default 'utf-8', ignored for binary modes).
        follow_symlinks: Whether to allow symlinks (default False).
        allow_hardlinked_files: Whether to allow files with st_nlink > 1 (default False).
        max_file_bytes: Maximum permitted file size in bytes (default 16 MiB).

    Returns:
        An open file stream (TextIO or BinaryIO).
    """
    if sys.platform == "win32":
        return _safe_open_windows(
            root=root,
            rel=rel,
            mode=mode,
            encoding=encoding,
            follow_symlinks=follow_symlinks,
            allow_hardlinked_files=allow_hardlinked_files,
            max_file_bytes=max_file_bytes,
        )
    else:
        return _safe_open_linux(
            root=root,
            rel=rel,
            mode=mode,
            encoding=encoding,
            follow_symlinks=follow_symlinks,
            allow_hardlinked_files=allow_hardlinked_files,
            max_file_bytes=max_file_bytes,
        )


__all__ = [
    "FileTooLargeError",
    "FileTypeNotAllowedError",
    "HardlinkNotAllowedError",
    "InvalidPathError",
    "PathTraversalError",
    "SafeOpenError",
    "canonicalize_root_windows",
    "safe_open",
    "validate_relative_path",
]
