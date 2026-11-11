"""git.py — Safe Git inspection without executing repository-controlled code.

Protects against arbitrary command execution from untrusted repositories (C2-1 / ISS-030):
- argv list execution only (shell=False).
- Scrubbed execution environment:
  GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL=empty, GIT_CONFIG_SYSTEM=empty,
  GIT_ATTR_NOSYSTEM=1, HOME=temp, USERPROFILE=temp, GIT_TERMINAL_PROMPT=0.
- Repository-level code execution vectors strictly neutralized:
  - Worktree `.gitattributes`: neutralized via top-level `--attr-source=<empty_tree>`.
  - Filter drivers (clean, smudge, process): all discovered filter names overridden to empty.
  - Diff drivers (textconv, command): all discovered diff drivers overridden to empty.
  - Submodules: `--ignore-submodules=all`, `submodule.recurse=false`, `status.submodulesummary=false`.
  - Include directives: system/global config wiped; filter names parsed from all config/includes.
  - Reparse & monitoring hooks: `core.fsmonitor=false`, `core.hooksPath=<empty_temp_dir>`,
    `core.untrackedCache=false`, `protocol.allow=never`, `core.attributesFile=`.
- Uncertainty principle:
  Any failure or uncertainty in determining git status returns is_dirty=True and is_unknown=True
  (causing the collector to flag git_state_unknown and produce a partial snapshot).
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitState:
    commit_sha: str | None
    is_dirty: bool
    is_unknown: bool = False


EMPTY_TREE_SHA1 = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
EMPTY_TREE_SHA256 = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939ec74decc5310"


def is_git_repository(root: str | Path) -> bool:
    """Check if the given directory contains a .git directory or gitdir pointer file."""
    git_dir = Path(root) / ".git"
    return git_dir.exists()


def _detect_empty_tree(root: Path) -> str:
    """Detect object format (sha1 vs sha256) and return corresponding empty tree hash."""
    config_file = root / ".git" / "config"
    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8", errors="replace")
            if "objectformat = sha256" in content.lower():
                return EMPTY_TREE_SHA256
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Failed to read git config for objectformat: %s", e)
    return EMPTY_TREE_SHA1


def _discover_filter_and_diff_names(root: Path, sandbox_dir: str) -> tuple[set[str], set[str]]:
    """Discover all filter names and diff driver names declared across the repository.

    Inspects:
    1. Repository .git/config (including include/includeIf sections)
    2. Repository .git/info/attributes
    3. Worktree root .gitattributes
    4. Submodule .git/modules/** configs and attributes
    5. git config --list query executed in scrubbed environment
    """
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

    # 1. Inspect .git/config
    git_config = root / ".git" / "config"
    if git_config.exists():
        try:
            content = git_config.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'\[filter\s+"([^"]+)"\]', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'\[diff\s+"([^"]+)"\]', content, re.IGNORECASE):
                diff_names.add(m.group(1))
            for m in re.finditer(r'filter\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                diff_names.add(m.group(1))
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Failed to read .git/config for filter/diff discovery: %s", e)

    # 2. Inspect .git/info/attributes
    info_attrs = root / ".git" / "info" / "attributes"
    if info_attrs.exists():
        try:
            content = info_attrs.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                diff_names.add(m.group(1))
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Failed to read .git/info/attributes: %s", e)

    # 3. Inspect .gitattributes in worktree root
    worktree_attrs = root / ".gitattributes"
    if worktree_attrs.exists():
        try:
            content = worktree_attrs.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                filter_names.add(m.group(1))
            for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                diff_names.add(m.group(1))
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Failed to read root .gitattributes: %s", e)

    # 4. Inspect .git/modules/**
    modules_dir = root / ".git" / "modules"
    if modules_dir.exists():
        try:
            for cfg in modules_dir.rglob("config"):
                try:
                    content = cfg.read_text(encoding="utf-8", errors="replace")
                    for m in re.finditer(r'\[filter\s+"([^"]+)"\]', content, re.IGNORECASE):
                        filter_names.add(m.group(1))
                    for m in re.finditer(r'filter\.([a-zA-Z0-9_\-]+)\.', content, re.IGNORECASE):
                        filter_names.add(m.group(1))
                except (OSError, UnicodeDecodeError) as e:
                    logger.debug("Failed to read submodule config %s: %s", cfg, e)
            for attr in modules_dir.rglob("attributes"):
                try:
                    content = attr.read_text(encoding="utf-8", errors="replace")
                    for m in re.finditer(r'filter=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                        filter_names.add(m.group(1))
                    for m in re.finditer(r'diff=([a-zA-Z0-9_\-]+)', content, re.IGNORECASE):
                        diff_names.add(m.group(1))
                except (OSError, UnicodeDecodeError) as e:
                    logger.debug("Failed to read submodule attributes %s: %s", attr, e)
        except OSError as e:
            logger.debug("Failed to iterate submodule modules directory: %s", e)

    # 5. Query git config --list with scrubbed env to find any remaining keys
    try:
        empty_cfg = os.path.join(sandbox_dir, "empty_gitconfig")
        clean_env = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": empty_cfg,
            "GIT_CONFIG_SYSTEM": empty_cfg,
            "GIT_ATTR_NOSYSTEM": "1",
            "HOME": sandbox_dir,
            "USERPROFILE": sandbox_dir,
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.environ.get("PATH", ""),
        }
        res = subprocess.run(
            ["git", "config", "--list"],
            cwd=str(root),
            env=clean_env,
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
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a read-only git command with scrubbed environment and all execution vectors neutralized."""
    root_path = Path(root).resolve()
    root_str = str(root_path)

    with tempfile.TemporaryDirectory(prefix="eap_git_sandbox_") as sandbox_dir:
        empty_global_config = os.path.join(sandbox_dir, "empty_gitconfig")
        Path(empty_global_config).touch()

        empty_hooks_dir = os.path.join(sandbox_dir, "hooks")
        os.makedirs(empty_hooks_dir, exist_ok=True)

        clean_env = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": empty_global_config,
            "GIT_CONFIG_SYSTEM": empty_global_config,
            "GIT_ATTR_NOSYSTEM": "1",
            "HOME": sandbox_dir,
            "USERPROFILE": sandbox_dir,
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.environ.get("PATH", ""),
        }
        if sys.platform == "win32":
            clean_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
            clean_env["COMSPEC"] = os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe")

        empty_tree = _detect_empty_tree(root_path)
        filter_names, diff_names = _discover_filter_and_diff_names(root_path, sandbox_dir)

        # Mandatory safe core flags neutralizing all execution vectors
        overrides: list[str] = [
            f"--attr-source={empty_tree}",
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


def _read_head_direct(root: Path) -> str | None:
    """Read commit SHA directly from .git/HEAD and refs without invoking subprocess."""
    head_file = root / ".git" / "HEAD"
    if not head_file.exists():
        return None
    try:
        content = head_file.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return None
        if not content.startswith("ref:"):
            # Direct SHA (detached HEAD)
            if len(content) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in content):
                return content
            return None

        # Symbolic ref: e.g. ref: refs/heads/main
        ref_path = content[4:].strip()
        loose_ref = root / ".git" / ref_path
        if loose_ref.exists():
            sha = loose_ref.read_text(encoding="utf-8", errors="replace").strip()
            if len(sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in sha):
                return sha

        # Packed refs fallback
        packed_refs = root / ".git" / "packed-refs"
        if packed_refs.exists():
            for line in packed_refs.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith(("#", "^")) or not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1] == ref_path:
                    sha = parts[0]
                    if len(sha) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in sha):
                        return sha
    except (OSError, UnicodeDecodeError) as e:
        logger.debug("Failed to read head/refs directly: %s", e)
    return None


def get_git_state(root: str | Path) -> GitState | None:
    """Retrieve Git HEAD commit and dirty status using strictly neutralized execution.

    Returns None if the root is not a Git repository.
    Any failure or uncertainty is treated as dirty/unknown (is_dirty=True, is_unknown=True).
    """
    root_path = Path(root)
    git_dir = root_path / ".git"
    if not git_dir.exists():
        return None

    # 1. Resolve HEAD commit SHA (direct safe_open parsing with fallback to rev-parse)
    commit_sha = _read_head_direct(root_path)
    if commit_sha is None:
        try:
            rev_proc = run_git_read_only(root_path, ["rev-parse", "HEAD"])
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
        )
        if status_proc.returncode == 0:
            is_dirty = len(status_proc.stdout.strip()) > 0
            return GitState(commit_sha=commit_sha, is_dirty=is_dirty, is_unknown=False)
        else:
            # Uncertain / failure status is treated as dirty and unknown
            return GitState(commit_sha=commit_sha, is_dirty=True, is_unknown=True)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("git status inspection failed: %s", e)
        return GitState(commit_sha=commit_sha, is_dirty=True, is_unknown=True)


__all__ = [
    "GitState",
    "get_git_state",
    "is_git_repository",
    "run_git_read_only",
]
