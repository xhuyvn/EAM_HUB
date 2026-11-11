"""Collector module for EAP PPM (P2a-WP2)."""

from eap.ppm.collector.collector import (
    CollectorScanResult,
    PpmCollector,
    ScopeScanResult,
)
from eap.ppm.collector.git import GitState, get_git_state, is_git_repository, run_git_read_only
from eap.ppm.collector.identity import (
    RootNotPermittedSecurityError,
    RootReplacedSecurityError,
    check_root_permission,
    get_fs_identity,
)
from eap.ppm.collector.parser import (
    DepthLimitExceededError,
    DuplicateKeyError,
    SafeParseError,
    safe_load_json,
    safe_load_yaml,
)
from eap.ppm.collector.safe_open import (
    FileTooLargeError,
    FileTypeNotAllowedError,
    HardlinkNotAllowedError,
    InvalidPathError,
    PathTraversalError,
    SafeOpenError,
    safe_open,
    validate_relative_path,
)

__all__ = [
    "CollectorScanResult",
    "DepthLimitExceededError",
    "DuplicateKeyError",
    "FileTooLargeError",
    "FileTypeNotAllowedError",
    "GitState",
    "HardlinkNotAllowedError",
    "InvalidPathError",
    "PathTraversalError",
    "PpmCollector",
    "RootNotPermittedSecurityError",
    "RootReplacedSecurityError",
    "SafeOpenError",
    "SafeParseError",
    "ScopeScanResult",
    "check_root_permission",
    "get_fs_identity",
    "get_git_state",
    "is_git_repository",
    "run_git_read_only",
    "safe_load_json",
    "safe_load_yaml",
    "safe_open",
    "validate_relative_path",
]
