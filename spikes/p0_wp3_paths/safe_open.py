"""
safe_open.py — Secure file opening with strict path containment, handle-based
verification, link count, and file type enforcement.

Supports:
- Windows (via ctypes stdlib and pywin32)
- Linux (via openat2 with RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS, fallback to /proc/self/fd)
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import BinaryIO, TextIO

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
    - No absolute paths or leading slashes
    - No drive letters (e.g. C:)
    - No UNC prefixes (\\ or //)
    - No NT namespaces (\\?\\ or \\.\\)
    - No alternate data streams (':')
    - No parent traversal ('..')
    - No null bytes
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

    # Split components by both / and \
    normalized = rel_str.replace("/", "\\")
    parts = normalized.split("\\")
    for part in parts:
        if part == "..":
            raise PathTraversalError("Parent directory traversal ('..') is not permitted.")
    return rel_str


# --- Windows Implementation (ctypes & pywin32) ---

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

    def canonicalize_root_windows(root: str | Path, engine: str = "ctypes") -> str:
        """Resolve canonical NT/DOS path for root directory with trailing backslash."""
        root_str = os.path.abspath(str(root))
        # Ensure \\?\ prefix for long path handling
        win_path = root_str if root_str.startswith("\\\\?\\") else f"\\\\?\\{root_str}"
        
        if engine == "pywin32":
            import win32con
            import win32file
            h = win32file.CreateFile(
                win_path,
                FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                None,
                win32con.OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                None
            )
            try:
                final = win32file.GetFinalPathNameByHandle(h, 0)
            finally:
                h.Close()
        else:
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
        engine: str = "ctypes",
    ) -> TextIO | BinaryIO:
        validate_relative_path(rel)
        canon_root = canonicalize_root_windows(root, engine=engine)

        # Build full lookup path
        norm_rel = str(rel).replace("/", "\\")
        # Strip any leading slashes/backslashes defensively
        norm_rel = norm_rel.lstrip("\\")
        target_path = canon_root + norm_rel

        if engine == "pywin32":
            import win32con
            import win32file
            try:
                h = win32file.CreateFile(
                    target_path,
                    win32con.GENERIC_READ,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None,
                    win32con.OPEN_EXISTING,
                    FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                    None,
                )
            except Exception as e:
                # Map win32 errors (e.g. file not found, access denied)
                raise FileNotFoundError(f"Cannot open '{norm_rel}': {e}") from e

            try:
                # 1. Final Path containment check
                final_path = win32file.GetFinalPathNameByHandle(h, 0)
                if not final_path.casefold().startswith(canon_root.casefold()):
                    raise PathTraversalError(
                        f"Final path '{final_path}' escapes root '{canon_root}'."
                    )
                # Check for ADS stream suffix
                sub_path = final_path[len(canon_root):]
                if ":" in sub_path:
                    raise PathTraversalError(f"Alternate data stream detected in final path: '{final_path}'")

                # 2. File Type check
                ftype = win32file.GetFileType(h)
                if ftype != FILE_TYPE_DISK:
                    raise FileTypeNotAllowedError(f"Handle type {ftype} is not a disk file.")

                info = win32file.GetFileInformationByHandle(h)
                # info: (attrs, ctime, atime, mtime, vol, fsizehi, fsizelo, links, idxhi, idxlo)
                attrs = info[0]
                n_links = info[7]
                file_size = (info[5] << 32) | info[6]

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

                # Detach handle and convert to Python file descriptor
                raw_handle = int(h.Detach())
                fd = msvcrt.open_osfhandle(raw_handle, os.O_RDONLY)
                return open(
                    fd,
                    mode=mode,
                    encoding=encoding if "b" not in mode else None,
                )
            except SafeOpenError:
                try:
                    h.Close()
                except Exception:
                    pass
                raise
            except Exception as e:
                try:
                    h.Close()
                except Exception:
                    pass
                raise OSError(f"Failed to inspect handle for '{norm_rel}': {e}") from e

        else:
            # ctypes engine
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

                # Convert handle to C runtime fd and Python stream
                fd = msvcrt.open_osfhandle(h, os.O_RDONLY)
                return open(
                    fd,
                    mode=mode,
                    encoding=encoding if "b" not in mode else None,
                )
            except SafeOpenError:
                kernel32.CloseHandle(h)
                raise
            except Exception as e:
                kernel32.CloseHandle(h)
                raise OSError(f"Failed to inspect handle for '{norm_rel}': {e}") from e


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
    except Exception:
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

        # Attempt openat2 with RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS
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
                    dir_fd,
                    norm_rel.encode("utf-8"),
                    ctypes.byref(how),
                    ctypes.sizeof(how),
                )
                if res >= 0:
                    fd = int(res)
                    used_openat2 = True
                else:
                    err = ctypes.get_errno()
                    if err == errno.ENOSYS:
                        # Kernel does not support openat2, fall back
                        pass
                    elif err == errno.EXDEV:
                        raise PathTraversalError(f"openat2: Path '{norm_rel}' escapes root directory.")
                    elif err == errno.ELOOP:
                        raise FileTypeNotAllowedError(f"openat2: Symlink rejected in '{norm_rel}'.")
                    elif err == errno.ENOENT:
                        raise FileNotFoundError(f"File not found: '{norm_rel}'")
                    else:
                        raise OSError(err, f"openat2 failed with errno {err}")
            finally:
                if dir_fd >= 0:
                    os.close(dir_fd)

        if not used_openat2:
            # Fallback path: O_NOFOLLOW on target, intermediate symlink check, and /proc/self/fd verification
            full_path = os.path.join(canon_root, norm_rel)
            
            # Check intermediate directory symlinks
            current = Path(canon_root)
            for part in Path(norm_rel).parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise FileTypeNotAllowedError(f"Intermediate symlink detected in directory '{current}'.")

            flags = os.O_RDONLY | os.O_CLOEXEC
            if not follow_symlinks:
                flags |= os.O_NOFOLLOW

            try:
                fd = os.open(full_path, flags)
            except OSError as e:
                if e.errno == errno.ELOOP:
                    raise FileTypeNotAllowedError(f"O_NOFOLLOW: Symlink rejected in '{norm_rel}'.")
                raise

        try:
            # Final verification via /proc/self/fd
            try:
                final_path = os.readlink(f"/proc/self/fd/{fd}")
                if not final_path.startswith(canon_root):
                    raise PathTraversalError(
                        f"Resolved path '{final_path}' escapes root '{canon_root}'."
                    )
            except (OSError, FileNotFoundError):
                # /proc not mounted or inaccessible
                pass

            # Stat checks
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise FileTypeNotAllowedError("Target is not a regular file.")
            if (not allow_hardlinked_files) and st.st_nlink > 1:
                raise HardlinkNotAllowedError(
                    f"Hardlinked files disallowed: link count {st.st_nlink} > 1."
                )
            if st.st_size > max_file_bytes:
                raise FileTooLargeError(
                    f"File size {st.st_size} exceeds max_file_bytes {max_file_bytes}."
                )

            return open(
                fd,
                mode=mode,
                encoding=encoding if "b" not in mode else None,
            )
        except Exception:
            if fd >= 0:
                os.close(fd)
            raise


# --- Public Entrypoint ---

def safe_open(
    root: str | Path,
    rel: str | Path,
    mode: str = "r",
    encoding: str | None = "utf-8",
    follow_symlinks: bool = False,
    allow_hardlinked_files: bool = False,
    max_file_bytes: int = 16777216,
    engine: str = "ctypes",
) -> TextIO | BinaryIO:
    """Safely open a file relative to a root directory with containment enforcement.
    
    Verifies:
    1. Input syntax (no absolute, UNC, device, '..', or ADS ':')
    2. Opens by handle / file descriptor
    3. Verifies final canonical path (GetFinalPathNameByHandleW on Windows, openat2/procfs on Linux)
    4. Enforces file type (regular disk file only, no directory, no symlink, no device)
    5. Enforces hardlink constraint (nNumberOfLinks == 1 unless allow_hardlinked_files=True)
    6. Enforces file size limit (<= max_file_bytes)
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
            engine=engine,
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
