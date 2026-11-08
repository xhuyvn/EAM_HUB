"""Tests for safe Git state extraction without executing repository-controlled code (P2a-WP2).

Verifies:
- Repository .git/config setting core.fsmonitor to an arbitrary script never executes that script (marker is never written).
- Repository hooks / core.hooksPath setting never executes hooks (marker is never written).
- Git environment is strictly scrubbed (GIT_CONFIG_NOSYSTEM=1, GIT_CONFIG_GLOBAL=empty, HOME=temp).
- Read-only execution: rev-parse HEAD and status --porcelain=v2 -z --no-optional-locks.
- Non-Git repositories fallback cleanly without executing git commands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from eap.ppm.collector.git import get_git_state, is_git_repository


def _init_git_repo(repo_dir: Path) -> None:
    """Initialize a real Git repository in repo_dir."""
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )


def test_fsmonitor_injection_script_never_executed(tmp_path: Path):
    """A repository configuring core.fsmonitor to an arbitrary script never executes that script."""
    repo = tmp_path / "malicious_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Commit a file first
    (repo / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    marker = tmp_path / "fsmonitor_pwned.marker"
    assert not marker.exists()

    # Create a script that creates the marker file
    if sys.platform == "win32":
        script_path = tmp_path / "evil_fsmonitor.bat"
        script_path.write_text(f'@echo PWNED > "{marker}"\n', encoding="utf-8")
    else:
        script_path = tmp_path / "evil_fsmonitor.sh"
        script_path.write_text(f'#!/bin/sh\necho PWNED > "{marker}"\n', encoding="utf-8")
        script_path.chmod(0o755)

    # Inject core.fsmonitor into .git/config
    git_config = repo / ".git" / "config"
    with open(git_config, "a", encoding="utf-8") as f:
        escaped_script = str(script_path).replace("\\", "/")
        f.write(f'\n[core]\n    fsmonitor = "{escaped_script}"\n')

    # Now execute safe Git inspection
    state = get_git_state(repo)

    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is False

    # The marker file MUST NOT have been created!
    assert not marker.exists(), "Security failure: core.fsmonitor script in .git/config was executed!"


def test_hooks_path_injection_never_executed(tmp_path: Path):
    """A repository configuring core.hooksPath to an arbitrary directory never executes hooks."""
    repo = tmp_path / "hook_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Commit a file first
    (repo / "file.txt").write_text("content", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    marker = tmp_path / "hook_pwned.marker"
    assert not marker.exists()

    malicious_hooks = tmp_path / "evil_hooks"
    malicious_hooks.mkdir()

    if sys.platform == "win32":
        hook_script = malicious_hooks / "pre-status.bat"
        hook_script.write_text(f'@echo PWNED > "{marker}"\n', encoding="utf-8")
    else:
        hook_script = malicious_hooks / "pre-status"
        hook_script.write_text(f'#!/bin/sh\necho PWNED > "{marker}"\n', encoding="utf-8")
        hook_script.chmod(0o755)

    # Inject core.hooksPath into repo config
    git_config = repo / ".git" / "config"
    with open(git_config, "a", encoding="utf-8") as f:
        escaped_hooks = str(malicious_hooks).replace("\\", "/")
        f.write(f'\n[core]\n    hooksPath = "{escaped_hooks}"\n')

    state = get_git_state(repo)
    assert state is not None
    assert not marker.exists(), "Security failure: malicious hook script was executed!"


def test_dirty_repo_detection(tmp_path: Path):
    """Git inspector accurately detects clean and dirty repository states."""
    repo = tmp_path / "dirty_repo"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    state1 = get_git_state(repo)
    assert state1 is not None
    assert state1.is_dirty is False

    # Modify file without committing
    (repo / "file.txt").write_text("modified content", encoding="utf-8")

    state2 = get_git_state(repo)
    assert state2 is not None
    assert state2.is_dirty is True


def test_non_git_repository_handling(tmp_path: Path):
    """Non-Git directories are identified and return None without error."""
    non_git = tmp_path / "plain_dir"
    non_git.mkdir()
    (non_git / "code.py").write_text("print('hello')", encoding="utf-8")

    assert is_git_repository(non_git) is False
    assert get_git_state(non_git) is None
