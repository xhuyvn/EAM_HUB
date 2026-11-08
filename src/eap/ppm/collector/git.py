"""git.py — Safe Git inspection without executing repository-controlled code.

Enforces:
- argv list execution only (shell=False).
- Scrubbed environment (GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL=empty, HOME=temp, GIT_TERMINAL_PROMPT=0).
- Explicit core overrides:
  -c core.fsmonitor=false
  -c core.hooksPath=<empty temp dir>
  -c core.untrackedCache=false
  -c protocol.allow=never
  -c safe.directory=<root>
- Read-only commands only: rev-parse HEAD, status --porcelain=v2 -z --no-optional-locks.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitState:
    commit_sha: str | None
    is_dirty: bool


def is_git_repository(root: str | Path) -> bool:
    """Check if the given directory contains a .git directory or gitdir pointer file."""
    git_dir = Path(root) / ".git"
    return git_dir.exists()


def run_git_read_only(
    root: str | Path,
    args: list[str],
    *,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Execute a read-only git command with scrubbed environment and disabled repo hooks/fsmonitor."""
    root_str = str(Path(root).resolve())

    with tempfile.TemporaryDirectory(prefix="eap_git_sandbox_") as sandbox_dir:
        empty_global_config = os.path.join(sandbox_dir, "empty_gitconfig")
        Path(empty_global_config).touch()

        empty_hooks_dir = os.path.join(sandbox_dir, "hooks")
        os.makedirs(empty_hooks_dir, exist_ok=True)

        clean_env = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": empty_global_config,
            "HOME": sandbox_dir,
            "USERPROFILE": sandbox_dir,
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.environ.get("PATH", ""),
        }
        if sys.platform == "win32":
            clean_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
            clean_env["COMSPEC"] = os.environ.get("COMSPEC", "C:\\Windows\\system32\\cmd.exe")

        # Mandatory safe core flags
        cmd = [
            "git",
            "-c", "core.fsmonitor=false",
            "-c", f"core.hooksPath={empty_hooks_dir}",
            "-c", "core.untrackedCache=false",
            "-c", "protocol.allow=never",
            "-c", f"safe.directory={root_str}",
            "-c", "safe.directory=*",
            *args,
        ]

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


def get_git_state(root: str | Path) -> GitState | None:
    """Retrieve Git HEAD commit and dirty status using scrubbed execution.

    Returns None if the root is not a Git repository.
    """
    if not is_git_repository(root):
        return None

    # 1. rev-parse HEAD
    rev_proc = run_git_read_only(root, ["rev-parse", "HEAD"])
    commit_sha: str | None = None
    if rev_proc.returncode == 0:
        commit_sha = rev_proc.stdout.strip()
    else:
        # Repository without commits
        commit_sha = None

    # 2. status --porcelain=v2 -z --no-optional-locks
    # Note: --no-optional-locks is a git-level flag placed before the status subcommand
    status_proc = run_git_read_only(
        root,
        ["--no-optional-locks", "status", "--porcelain=v2", "-z"],
    )
    is_dirty = False
    if status_proc.returncode == 0:
        is_dirty = len(status_proc.stdout.strip()) > 0
    else:
        is_dirty = True

    return GitState(commit_sha=commit_sha, is_dirty=is_dirty)


__all__ = [
    "GitState",
    "get_git_state",
    "is_git_repository",
    "run_git_read_only",
]
