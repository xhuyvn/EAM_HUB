"""git.py — Safe Git inspection without executing repository-controlled code or reading outside root.

Protects against arbitrary command execution from untrusted repositories (C2-1 / ISS-030)
and repository-controlled containment escapes (ISS-031):
- argv list execution only (shell=False).
- Minimum Git version enforcement (2.41.0, required for `--attr-source`).
- Handle-based gitdir containment via `safe_open` / `safe_resolve_dir` / `resolve_contained_dir`:
  - `.git` directory -> `gitdir = <root>/.git`.
  - `.git` gitfile -> parsed and verified via handle-based checks to resolve inside `<root>`;
    otherwise returns `GitState(is_dirty=True, is_unknown=True, reason="gitdir_outside_root")`
    without invoking git.
  - Rejects `commondir` (`git_commondir`), `objects/info/alternates` / `http-alternates`
    (`git_alternates`), and `config.worktree` / `extensions.worktreeConfig` (`git_worktree_config`)
    without invoking git.
- Explicit `--git-dir=<gitdir> --work-tree=<root>` and `-c core.worktree=<root>` on every git call.
- Scrubbed execution environment:
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=empty`, `GIT_CONFIG_SYSTEM=empty`,
  `GIT_ATTR_NOSYSTEM=1`, `HOME=temp`, `USERPROFILE=temp`, `GIT_TERMINAL_PROMPT=0`,
  `GIT_CEILING_DIRECTORIES=<parent_of_root>`.
  Explicitly scrubs `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_INDEX_FILE`, `GIT_NAMESPACE`.
- Repository-level code execution vectors strictly neutralized:
  - Worktree `.gitattributes`: neutralized via top-level `--attr-source=<empty_tree>`.
  - Filter drivers (clean, smudge, process): all discovered filter names overridden to empty.
  - Diff drivers (textconv, command): all discovered diff drivers overridden to empty.
  - Submodules: `--ignore-submodules=all`, `submodule.recurse=false`, `status.submodulesummary=false`.
  - Include directives: system/global config wiped; filter names parsed from all config/includes.
  - Reparse & monitoring hooks: `core.fsmonitor=false`, `core.hooksPath=<empty_temp_dir>`,
    `core.untrackedCache=false`, `protocol.allow=never`, `core.attributesFile=`.
- Uncertainty principle:
  Any failure or uncertainty in determining git status returns `is_dirty=True` and `is_unknown=True`
  (causing the collector to flag `git_state_unknown` or the specific containment reason and produce
  a partial snapshot).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from eap.ppm.collector.safe_open import (
    SafeOpenError,
    resolve_contained_dir,
    safe_open,
    safe_resolve_dir,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitState:
    commit_sha: str | None
    is_dirty: bool
    is_unknown: bool = False
    reason: str | None = None


EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EMPTY_TREE_SHA256 = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939ec74decc5310"

# Git 2.41.0 introduced top-level `--attr-source=<tree>` (see Documentation/RelNotes/2.41.0.adoc).
MIN_GIT_VERSION: tuple[int, int, int] = (2, 41, 0)

_SCRUBBED_GIT_ENV_VARS = frozenset({
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES",
})

_CACHED_GIT_VERSION: tuple[int, int, int] | None = None
_GIT_VERSION_PROBED: bool = False
_WARNED_UNSUPPORTED_GIT_VERSION: bool = False


def _parse_git_version(version_output: str) -> tuple[int, int, int] | None:
    """Parse `git --version` stdout into a (major, minor, patch) tuple."""
    m = re.search(r"git version (\d+)\.(\d+)(?:\.(\d+))?", version_output)
    if not m:
        return None
    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3)) if m.group(3) is not None else 0
    return (major, minor, patch)


def _probe_git_version() -> tuple[int, int, int] | None:
    """Execute `git --version` once and return the parsed version tuple."""
    try:
        res = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
            shell=False,
        )
        if res.returncode != 0:
            return None
        return _parse_git_version(res.stdout)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git --version probe failed: %s", e)
        return None


def get_git_version() -> tuple[int, int, int] | None:
    """Return cached Git version tuple, probing `git --version` once per process."""
    global _CACHED_GIT_VERSION, _GIT_VERSION_PROBED
    if not _GIT_VERSION_PROBED:
        _CACHED_GIT_VERSION = _probe_git_version()
        _GIT_VERSION_PROBED = True
    return _CACHED_GIT_VERSION


def reset_git_version_cache() -> None:
    """Reset the cached Git version and warning flag (intended for tests)."""
    global _CACHED_GIT_VERSION, _GIT_VERSION_PROBED, _WARNED_UNSUPPORTED_GIT_VERSION
    _CACHED_GIT_VERSION = None
    _GIT_VERSION_PROBED = False
    _WARNED_UNSUPPORTED_GIT_VERSION = False


def _build_clean_git_env(
    root_path: Path,
    sandbox_dir: str,
    empty_global_config: str,
) -> dict[str, str]:
    """Construct a minimal scrubbed environment for Git child processes."""
    clean_env: dict[str, str] = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": empty_global_config,
        "GIT_CONFIG_SYSTEM": empty_global_config,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CEILING_DIRECTORIES": str(root_path.parent),
        "HOME": sandbox_dir,
        "USERPROFILE": sandbox_dir,
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": os.environ.get("PATH", ""),
    }
    if sys.platform == "win32":
        clean_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
        clean_env["COMSPEC"] = os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe")

    # Defense-in-depth: guarantee none of the redirectable git env vars exist except GIT_CEILING_DIRECTORIES.
    for key in _SCRUBBED_GIT_ENV_VARS - {"GIT_CEILING_DIRECTORIES"}:
        clean_env.pop(key, None)

    return clean_env


def is_git_repository(root: str | Path) -> bool:
    """Check if the given directory contains a .git directory or gitdir pointer file."""
    git_entry = Path(root) / ".git"
    return os.path.lexists(git_entry)


def _safe_read_text(root_path: Path, rel_path: str) -> str | None:
    """Read a text file under root_path using safe_open; return None if missing."""
    try:
        with safe_open(
            root_path,
            rel_path,
            mode="r",
            encoding="utf-8",
            follow_symlinks=False,
            allow_hardlinked_files=False,
        ) as f:
            data = f.read()
            return data if isinstance(data, str) else data.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None


def _resolve_gitdir(root_path: Path) -> tuple[Path, str] | GitState | None:
    """Resolve and verify gitdir containment inside root_path using handle-based checks.

    Returns:
        - None if `<root>/.git` does not exist.
        - `(gitdir_path, gitdir_rel)` if gitdir is safely contained inside `root_path`.
        - `GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason=...)` on any
          containment violation or unsafe gitdir configuration.
    """
    if not os.path.lexists(root_path / ".git"):
        return None

    # 1. Try `.git` as a contained directory (rejects symlinks/junctions via handle checks).
    try:
        return safe_resolve_dir(root_path, ".git")
    except FileNotFoundError:
        return None
    except SafeOpenError:
        # Either `.git` is a regular file (gitfile) OR `.git` is a symlink/reparse point.
        pass
    except OSError:
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")

    # 2. Try `.git` as a regular gitfile via safe_open (rejects symlinks, junctions, hardlinks).
    try:
        content = _safe_read_text(root_path, ".git")
    except SafeOpenError:
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")
    except (OSError, UnicodeDecodeError):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")

    if content is None:
        return None

    first_line = ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break

    if not first_line.lower().startswith("gitdir:"):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")

    raw_target = first_line[len("gitdir:"):].strip()
    if not raw_target:
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")

    try:
        return resolve_contained_dir(root_path, raw_target)
    except (SafeOpenError, OSError):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")


def _check_gitdir_containment_metadata(
    root_path: Path,
    gitdir_path: Path,
    gitdir_rel: str,
) -> GitState | None:
    """Check for gitdir metadata that could redirect reads outside root without running git."""
    if os.path.lexists(gitdir_path / "commondir"):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_commondir")

    if (
        os.path.lexists(gitdir_path / "objects" / "info" / "alternates")
        or os.path.lexists(gitdir_path / "objects" / "info" / "http-alternates")
    ):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_alternates")

    if os.path.lexists(gitdir_path / "config.worktree"):
        return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_worktree_config")

    config_rel = f"{gitdir_rel}/config"
    if os.path.lexists(gitdir_path / "config"):
        try:
            config_text = _safe_read_text(root_path, config_rel)
        except SafeOpenError:
            return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")
        except (OSError, UnicodeDecodeError):
            return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_state_unknown")

        if config_text is not None and re.search(
            r"(?:^|\s)worktreeconfig\s*=",
            config_text,
            re.IGNORECASE | re.MULTILINE,
        ):
            return GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_worktree_config")

    return None


def _detect_empty_tree(root_path: Path, gitdir_rel: str = ".git") -> str:
    """Detect object format (sha1 vs sha256) and return corresponding empty tree hash."""
    try:
        content = _safe_read_text(root_path, f"{gitdir_rel}/config")
        if content and "objectformat = sha256" in content.lower():
            return EMPTY_TREE_SHA256
    except (SafeOpenError, OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read git config for objectformat: %s", e)
    return EMPTY_TREE_SHA1


def _discover_filter_and_diff_names(
    root: Path,
    sandbox_dir: str,
    *,
    gitdir_path: Path | None = None,
    gitdir_rel: str = ".git",
) -> tuple[set[str], set[str]]:
    """Discover all filter names and diff driver names declared across the repository.

    Inspects:
    1. Repository <gitdir>/config (including include/includeIf sections)
    2. Repository <gitdir>/info/attributes
    3. Worktree root .gitattributes
    4. Submodule <gitdir>/modules/** configs and attributes
    5. git config --list query executed in scrubbed environment with explicit --git-dir / --work-tree
    """
    resolved_gitdir = gitdir_path if gitdir_path is not None else (root / ".git")
    filter_names: set[str] = {
        "lfs",
        "evil",
        "evilproc",
        "subevil",
        "filter",
        "incfilter",
        "inciffilter",
    }
    diff_names: set[str] = {"astextplain", "evildiff", "diff"}

    # 1. Inspect <gitdir>/config
    try:
        content = _safe_read_text(root, f"{gitdir_rel}/config")
        if content:
            for m in re.finditer(r'\[filter\s+"([^"]+)"\]', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'\[diff\s+"([^"]+)"\]', content, re.IGNORECASE):
                diff_names.add(m.group(1))
            for m in re.finditer(r'filter\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                diff_names.add(m.group(1))
    except (SafeOpenError, OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read %s/config for filter/diff discovery: %s", gitdir_rel, e)

    # 2. Inspect <gitdir>/info/attributes
    try:
        content = _safe_read_text(root, f"{gitdir_rel}/info/attributes")
        if content:
            for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                diff_names.add(m.group(1))
    except (SafeOpenError, OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read %s/info/attributes: %s", gitdir_rel, e)

    # 3. Inspect .gitattributes in worktree root
    try:
        content = _safe_read_text(root, ".gitattributes")
        if content:
            for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                diff_names.add(m.group(1))
    except (SafeOpenError, OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read root .gitattributes: %s", e)

    # 4. Inspect <gitdir>/modules/**
    modules_dir = resolved_gitdir / "modules"
    if modules_dir.exists():
        try:
            for cfg in modules_dir.rglob("config"):
                try:
                    rel_cfg = cfg.relative_to(root).as_posix()
                    content = _safe_read_text(root, rel_cfg)
                    if content:
                        for m in re.finditer(r'\[filter\s+"([^"]+)"\]', content, re.IGNORECASE):
                            filter_names.add(m.group(1))
                        for m in re.finditer(r'filter\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                            filter_names.add(m.group(1))
                except (SafeOpenError, OSError, UnicodeDecodeError, ValueError) as e:
                    logger.debug("Failed to read submodule config %s: %s", cfg, e)
            for attr in modules_dir.rglob("attributes"):
                try:
                    rel_attr = attr.relative_to(root).as_posix()
                    content = _safe_read_text(root, rel_attr)
                    if content:
                        for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                            filter_names.add(m.group(1))
                        for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                            diff_names.add(m.group(1))
                except (SafeOpenError, OSError, UnicodeDecodeError, ValueError) as e:
                    logger.debug("Failed to read submodule attributes %s: %s", attr, e)
        except OSError as e:
            logger.debug("Failed to iterate submodule modules directory: %s", e)

    # 5. Query git config --list with scrubbed env and explicit --git-dir / --work-tree
    try:
        empty_cfg = os.path.join(sandbox_dir, "empty_gitconfig")
        clean_env = _build_clean_git_env(root, sandbox_dir, empty_cfg)
        root_str = str(root)
        gitdir_str = str(resolved_gitdir)
        res = subprocess.run(
            [
                "git",
                f"--git-dir={gitdir_str}",
                f"--work-tree={root_str}",
                "-c",
                f"core.worktree={root_str}",
                "config",
                "--list",
            ],
            cwd=root_str,
            env=clean_env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("filter."):
                    parts = line.split(".", 2)
                    if len(parts) >= 2:
                        filter_names.add(parts[1])
                elif line.startswith("diff."):
                    parts = line.split(".", 2)
                    if len(parts) >= 2:
                        diff_names.add(parts[1])
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git config --list query failed: %s", e)

    return filter_names, diff_names


def run_git_read_only(
    root: str | Path,
    args: list[str],
    *,
    gitdir: str | Path | None = None,
    gitdir_rel: str = ".git",
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a read-only git command with scrubbed environment and all execution vectors neutralized."""
    root_path = Path(root).resolve()
    root_str = str(root_path)
    resolved_gitdir = Path(gitdir).resolve() if gitdir is not None else (root_path / ".git")
    gitdir_str = str(resolved_gitdir)

    with tempfile.TemporaryDirectory(prefix="eap_git_sandbox_") as sandbox_dir:
        empty_global_config = os.path.join(sandbox_dir, "empty_gitconfig")
        Path(empty_global_config).touch()

        empty_hooks_dir = os.path.join(sandbox_dir, "hooks")
        os.makedirs(empty_hooks_dir, exist_ok=True)

        clean_env = _build_clean_git_env(root_path, sandbox_dir, empty_global_config)

        empty_tree = _detect_empty_tree(root_path, gitdir_rel=gitdir_rel)
        filter_names, diff_names = _discover_filter_and_diff_names(
            root_path,
            sandbox_dir,
            gitdir_path=resolved_gitdir,
            gitdir_rel=gitdir_rel,
        )

        # Mandatory safe core flags neutralizing all execution vectors and containment escapes
        overrides: list[str] = [
            f"--git-dir={gitdir_str}",
            f"--work-tree={root_str}",
            f"--attr-source={empty_tree}",
            "-c", f"core.worktree={root_str}",
            "-c", "core.fsmonitor=false",
            "-c", f"core.hooksPath={empty_hooks_dir}",
            "-c", "core.untrackedCache=false",
            "-c", "protocol.allow=never",
            "-c", f"safe.directory={root_str}",
            "-c", "safe.directory=*",
            "-c", "submodule.recurse=false",
            "-c", "status.submodulesummary=false",
            "-c", "core.attributesFile=",
        ]

        # Neutralize all discovered filters
        for fname in sorted(filter_names):
            overrides.extend([
                "-c", f"filter.{fname}.clean=",
                "-c", f"filter.{fname}.smudge=",
                "-c", f"filter.{fname}.process=",
                "-c", f"filter.{fname}.required=false",
            ])

        # Neutralize all discovered diff drivers
        for dname in sorted(diff_names):
            overrides.extend([
                "-c", f"diff.{dname}.textconv=",
                "-c", f"diff.{dname}.command=",
            ])

        cmd = ["git", *overrides, *args]

        return subprocess.run(
            cmd,
            cwd=root_str,
            env=clean_env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )


def _read_head_direct(root: Path, gitdir_rel: str = ".git") -> str | None:
    """Read commit SHA directly from <gitdir>/HEAD and refs via safe_open without invoking subprocess."""
    try:
        raw_head = _safe_read_text(root, f"{gitdir_rel}/HEAD")
        if raw_head is None:
            return None
        content = raw_head.strip()
        if not content:
            return None
        if not content.startswith("ref:"):
            # Direct SHA (detached HEAD)
            if len(content) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in content):
                return content
            return None

        # Symbolic ref: e.g. ref: refs/heads/main
        ref_path = content[4:].strip()
        if not ref_path:
            return None
        raw_ref = _safe_read_text(root, f"{gitdir_rel}/{ref_path}")
        if raw_ref is not None:
            sha = raw_ref.strip()
            if len(sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in sha):
                return sha

        # Packed refs fallback
        raw_packed = _safe_read_text(root, f"{gitdir_rel}/packed-refs")
        if raw_packed is not None:
            for line in raw_packed.splitlines():
                line = line.strip()
                if line.startswith(("#", "^")) or not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1] == ref_path:
                    sha = parts[0]
                    if len(sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in sha):
                        return sha
    except (SafeOpenError, OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read head/refs directly: %s", e)
    return None


def get_git_state(root: str | Path) -> GitState | None:
    """Retrieve Git HEAD commit and dirty status using strictly contained, neutralized execution.

    Returns None if the root is not a Git repository.
    Any failure, containment violation, unsupported git version, or uncertainty is treated as
    dirty/unknown (is_dirty=True, is_unknown=True) with an explicit reason code.
    """
    global _WARNED_UNSUPPORTED_GIT_VERSION

    root_path = Path(root).resolve()
    resolved = _resolve_gitdir(root_path)
    if resolved is None:
        return None
    if isinstance(resolved, GitState):
        return resolved

    gitdir_path, gitdir_rel = resolved

    # Check gitdir metadata for containment-bypassing constructs before running git
    metadata_err = _check_gitdir_containment_metadata(root_path, gitdir_path, gitdir_rel)
    if metadata_err is not None:
        return metadata_err

    # Enforce minimum Git version (probed once per process)
    git_version = get_git_version()
    if git_version is None or git_version < MIN_GIT_VERSION:
        if not _WARNED_UNSUPPORTED_GIT_VERSION:
            logger.warning(
                "Git version %s is below minimum supported %s (--attr-source required); "
                "treating repository state as unknown.",
                git_version,
                ".".join(str(x) for x in MIN_GIT_VERSION),
            )
            _WARNED_UNSUPPORTED_GIT_VERSION = True
        return GitState(
            commit_sha=None,
            is_dirty=True,
            is_unknown=True,
            reason="git_version_unsupported",
        )

    # 1. Resolve HEAD commit SHA (direct safe_open parsing with fallback to rev-parse)
    commit_sha = _read_head_direct(root_path, gitdir_rel=gitdir_rel)
    if commit_sha is None:
        try:
            rev_proc = run_git_read_only(
                root_path,
                ["rev-parse", "HEAD"],
                gitdir=gitdir_path,
                gitdir_rel=gitdir_rel,
            )
            if rev_proc.returncode == 0:
                out = rev_proc.stdout.strip()
                if len(out) in (40, 64):
                    commit_sha = out
            else:
                commit_sha = None
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("rev-parse HEAD fallback failed: %s", e)
            commit_sha = None

    # 2. Status inspection: --no-optional-locks status --ignore-submodules=all --porcelain=v2 -z
    try:
        status_proc = run_git_read_only(
            root_path,
            ["--no-optional-locks", "status", "--ignore-submodules=all", "--porcelain=v2", "-z"],
            gitdir=gitdir_path,
            gitdir_rel=gitdir_rel,
        )
        if status_proc.returncode == 0:
            is_dirty = len(status_proc.stdout.strip()) > 0
            return GitState(commit_sha=commit_sha, is_dirty=is_dirty, is_unknown=False)
        else:
            # Uncertain / failure status is treated as dirty and unknown
            return GitState(
                commit_sha=commit_sha,
                is_dirty=True,
                is_unknown=True,
                reason="git_state_unknown",
            )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git status inspection failed: %s", e)
        return GitState(
            commit_sha=commit_sha,
            is_dirty=True,
            is_unknown=True,
            reason="git_state_unknown",
        )


__all__ = [
    "MIN_GIT_VERSION",
    "GitState",
    "get_git_state",
    "get_git_version",
    "is_git_repository",
    "reset_git_version_cache",
    "run_git_read_only",
]
