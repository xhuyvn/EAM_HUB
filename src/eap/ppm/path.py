"""Pure path canonicalization and alias validation for EAP PPM (P2a-WP1).

Strictly pure functions without touching the filesystem.
Real filesystem containment checks (symlink/junction/hardlink/ADS inspection)
belong to P2a-WP2 per spike P0-WP3.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

SAFE_RELATIVE_PATTERN = re.compile(r"^(?!/)(?!.*\\)(?!.*:)(?!.*(?:^|/)\.\.(?:/|$)).+$")


class PathValidationError(ValueError):
    """Raised when a path fails pure canonicalization or containment rules."""


def canonicalize_root(path: str, host_os: str, allow_unc: bool = False) -> str:
    """Canonicalize a physical root path purely in memory without touching the filesystem.

    Enforces:
    - Rejection of relative paths.
    - Rejection of '..' segments.
    - Rejection of NUL and control characters (ord < 32, ord == 127).
    - Rejection of Windows device namespaces (\\\\?\\, \\\\.\\, //?/, //./).
    - Rejection of UNC paths (\\\\server\\share, //server/share) unless allow_unc=True.
    - Rejection of mixed separators (e.g. mixing '/' and '\\' in the same path).
    - Rejection of Alternate Data Streams (':' in filename/path on Windows).
    - Normalization of case on Windows (entire path lowercased for case-insensitive uniqueness).
    - Normalization of separators to forward slashes ('/').
    - Collapsing of redundant slashes and stripping trailing slashes (except root).
    """
    if not isinstance(path, str):
        raise PathValidationError(f"Path must be a string, got {type(path).__name__}")

    raw = path.strip()
    if not raw:
        raise PathValidationError("Path must not be empty or whitespace only")

    # Reject NUL and control characters
    for ch in raw:
        code = ord(ch)
        if code < 32 or code == 127:
            raise PathValidationError(
                f"Path contains invalid control or NUL character (ASCII {code}): {raw!r}"
            )

    os_clean = host_os.strip().lower()
    is_windows = os_clean in ("windows", "win", "win32")
    is_posix = os_clean in ("linux", "posix", "darwin", "macos", "unix")

    if not is_windows and not is_posix:
        raise PathValidationError(f"Unsupported host_os '{host_os}'; expected 'windows' or 'linux'")

    # Reject mixed separators (cannot contain both '/' and '\\')
    has_fwd = "/" in raw
    has_bwd = "\\" in raw
    if has_fwd and has_bwd:
        raise PathValidationError(f"Path contains mixed path separators ('/' and '\\'): {raw}")

    # Check Windows device namespaces
    if raw.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
        raise PathValidationError(
            f"Windows device namespaces ('\\\\?\\', '\\\\.\\') are strictly forbidden: {raw}"
        )

    # Check UNC paths
    is_unc = raw.startswith(("\\\\", "//"))
    if is_unc:
        if not allow_unc:
            raise PathValidationError(
                f"UNC paths ('\\\\server\\share') are not permitted unless allow_unc=True: {raw}"
            )
        # Parse UNC path
        sep = "\\" if has_bwd else "/"
        unc_body = raw[2:]
        parts = [p for p in unc_body.split(sep) if p and p != "."]
        if any(p == ".." for p in parts):
            raise PathValidationError(f"UNC path must not contain '..' segments: {raw}")
        if len(parts) < 2:
            raise PathValidationError(f"UNC path must include both server and share: {raw}")
        if is_windows:
            parts = [p.lower() for p in parts]
        return "//" + "/".join(parts)

    if is_posix:
        if has_bwd:
            raise PathValidationError(f"POSIX path must not contain backslashes: {raw}")
        if not raw.startswith("/"):
            raise PathValidationError(f"POSIX path must be absolute (must start with '/'): {raw}")

        parts = raw.split("/")
        if any(p == ".." for p in parts):
            raise PathValidationError(f"POSIX path must not contain '..' segments: {raw}")

        normalized_parts = [p for p in parts if p and p != "."]
        if not normalized_parts:
            return "/"
        return "/" + "/".join(normalized_parts)

    # Windows path validation
    # Must start with drive letter e.g. "C:\" or "C:/"
    if len(raw) < 3:
        raise PathValidationError(
            f"Windows path '{raw}' is too short to be an absolute path (needs drive letter like 'C:/')"
        )

    drive_letter = raw[0]
    colon = raw[1]
    drive_sep = raw[2]

    if not (drive_letter.isalpha() and colon == ":" and drive_sep in ("/", "\\")):
        raise PathValidationError(
            f"Windows path '{raw}' is not absolute (must start with drive letter and separator like 'C:\\' or 'C:/')"
        )

    # Check Alternate Data Streams (ADS): colon after drive letter
    if ":" in raw[2:]:
        raise PathValidationError(
            f"Windows path contains alternate data stream or invalid colon: {raw}"
        )

    sep_used = drive_sep
    tail = raw[3:]
    parts = tail.split(sep_used)

    if any(p == ".." for p in parts):
        raise PathValidationError(f"Windows path must not contain '..' segments: {raw}")

    # Case normalization on Windows: lowercase drive and all path segments
    clean_parts = [p.lower() for p in parts if p and p != "."]
    norm_drive = drive_letter.lower() + ":"

    if not clean_parts:
        return norm_drive + "/"
    return norm_drive + "/" + "/".join(clean_parts)


def validate_aliases(
    artifact_aliases: Mapping[str, Sequence[str]],
    notes_globs: Sequence[str] | None = None,
) -> None:
    """Validate artifact aliases and notes globs per docs/04 rules.

    Rejects absolute paths, '..' segments, backslashes, duplicate aliases within
    a category, and invalid path characters.
    """
    required_categories = ("architecture", "blueprint", "implementation_plan")
    for cat in required_categories:
        if cat not in artifact_aliases:
            raise PathValidationError(f"Missing required artifact alias category: '{cat}'")

    for cat, aliases in artifact_aliases.items():
        if not isinstance(aliases, (list, tuple)):
            raise PathValidationError(f"Aliases for '{cat}' must be a sequence of strings")
        seen: set[str] = set()
        for alias in aliases:
            if not isinstance(alias, str):
                raise PathValidationError(f"Alias in '{cat}' must be a string, got {type(alias).__name__}")
            alias_clean = alias.strip()
            if not alias_clean:
                raise PathValidationError(f"Alias in '{cat}' must not be empty")
            if alias_clean in seen:
                raise PathValidationError(f"Duplicate alias '{alias_clean}' in category '{cat}'")
            seen.add(alias_clean)

            if not SAFE_RELATIVE_PATTERN.match(alias_clean):
                raise PathValidationError(
                    f"Alias '{alias_clean}' in '{cat}' violates safe relative path rules "
                    f"(must be relative, no backslashes, no colons, no '..' segments)"
                )

    if notes_globs is not None:
        if not isinstance(notes_globs, (list, tuple)):
            raise PathValidationError("notes_globs must be a sequence of strings")
        for glob_pattern in notes_globs:
            if not isinstance(glob_pattern, str):
                raise PathValidationError(
                    f"notes_glob must be a string, got {type(glob_pattern).__name__}"
                )
            clean_glob = glob_pattern.strip()
            if not clean_glob:
                raise PathValidationError("notes_glob must not be empty")
            if not SAFE_RELATIVE_PATTERN.match(clean_glob):
                raise PathValidationError(
                    f"notes_glob '{clean_glob}' violates safe relative path rules "
                    f"(must be relative, no backslashes, no colons, no '..' segments)"
                )
