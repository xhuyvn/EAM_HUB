"""Tests for safe Git state extraction without executing repository-controlled code (P2a-WP2 / C2-1 / ISS-030).

Verifies the complete attack matrix neutralizing arbitrary command execution from untrusted repos:
1. Clean filter via .gitattributes (owner reproduction of C2-1).
2. Long-running process filter via .gitattributes (git-lfs style).
3. Attributes defined in .git/info/attributes.
4. Filter defined through [include] path=other.cfg.
5. Filter defined through [includeIf "gitdir:..."].
6. Submodule whose own repository config defines a filter.
7. core.fsmonitor script injection.
8. core.hooksPath script injection.
9. textconv / diff driver command injection.
10. Accurate clean/dirty repository state detection.
11. Corrupt/uncertain repository status fails safely as dirty and unknown.
12. Non-Git repository handling.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from eap.ppm.collector.git import get_git_state, is_git_repository


def _init_git_repo(repo_dir: Path) -> None:
    """Initialize a real Git repository in repo_dir with basic user config."""
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


def _make_py_marker_cmd(marker_path: Path, marker_text: str = "pwned\n") -> str:
    """Generate a platform-neutral python one-liner command to append marker_text and echo stdin to stdout."""
    py_posix = Path(sys.executable).as_posix()
    m_posix = marker_path.as_posix()
    return (
        f'"{py_posix}" -c "import sys; '
        f'open(r\'{m_posix}\', \'a\').write(\'{marker_text}\'); '
        f'sys.stdout.write(sys.stdin.read())"'
    )


def test_clean_filter_gitattributes_never_executed(tmp_path: Path):
    """C2-1 Owner reproduction: clean filter via .gitattributes must never execute."""
    repo = tmp_path / "repo_clean_filter"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    marker = tmp_path / "clean_filter.marker"
    assert not marker.exists()

    (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    py_cmd = _make_py_marker_cmd(marker, "clean_filter_ran\n")
    subprocess.run(["git", "config", "filter.evil.clean", py_cmd], cwd=str(repo), check=True, capture_output=True)

    # Modify tracked file
    (repo / "file.txt").write_text("modified", encoding="utf-8")

    # Safe inspection
    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: clean filter via .gitattributes was executed!"


def test_process_filter_gitattributes_never_executed(tmp_path: Path):
    """Process filter (long-running / git-lfs style) via .gitattributes must never execute."""
    repo = tmp_path / "repo_proc_filter"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    marker = tmp_path / "proc_filter.marker"
    assert not marker.exists()

    (repo / ".gitattributes").write_text("* filter=evilproc\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    py_cmd = _make_py_marker_cmd(marker, "proc_filter_ran\n")
    subprocess.run(["git", "config", "filter.evilproc.process", py_cmd], cwd=str(repo), check=True, capture_output=True)

    (repo / "file.txt").write_text("modified", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: process filter via .gitattributes was executed!"


def test_info_attributes_filter_never_executed(tmp_path: Path):
    """Attributes defined in .git/info/attributes must never execute."""
    repo = tmp_path / "repo_info_attrs"
    repo.mkdir()
    _init_git_repo(repo)

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    marker = tmp_path / "info_attrs.marker"
    assert not marker.exists()

    info_dir = repo / ".git" / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    (info_dir / "attributes").write_text("* filter=evilinfo\n", encoding="utf-8")

    py_cmd = _make_py_marker_cmd(marker, "info_filter_ran\n")
    subprocess.run(["git", "config", "filter.evilinfo.clean", py_cmd], cwd=str(repo), check=True, capture_output=True)

    # Modify to same length to force content hash comparison
    (repo / "file.txt").write_text("modifie", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: filter from .git/info/attributes was executed!"


def test_include_path_filter_never_executed(tmp_path: Path):
    """Filter defined through [include] path=other.cfg must never execute."""
    repo = tmp_path / "repo_inc_path"
    repo.mkdir()
    _init_git_repo(repo)

    marker = tmp_path / "include_path.marker"
    assert not marker.exists()

    other_cfg = repo / "other.cfg"
    other_cfg.touch()
    py_cmd = _make_py_marker_cmd(marker, "include_path_ran\n")
    subprocess.run(
        ["git", "config", "--file", str(other_cfg), "filter.incfilter.clean", py_cmd],
        check=True,
        capture_output=True,
    )

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=incfilter\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    subprocess.run(
        ["git", "config", "include.path", other_cfg.as_posix()],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    (repo / "file.txt").write_text("modifie", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: filter from [include] was executed!"


def test_includeif_gitdir_filter_never_executed(tmp_path: Path):
    """Filter defined through [includeIf "gitdir:..."] must never execute."""
    repo = tmp_path / "repo_incif"
    repo.mkdir()
    _init_git_repo(repo)

    marker = tmp_path / "includeif.marker"
    assert not marker.exists()

    other_cfg = repo / "other.cfg"
    other_cfg.touch()
    py_cmd = _make_py_marker_cmd(marker, "includeif_ran\n")
    subprocess.run(
        ["git", "config", "--file", str(other_cfg), "filter.inciffilter.clean", py_cmd],
        check=True,
        capture_output=True,
    )

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=inciffilter\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    git_config = repo / ".git" / "config"
    content = git_config.read_text(encoding="utf-8")
    content += f'\n[includeIf "gitdir:{repo.as_posix()}/**"]\n\tpath = {other_cfg.as_posix()}\n'
    git_config.write_text(content, encoding="utf-8")

    (repo / "file.txt").write_text("modifie", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: filter from [includeIf] was executed!"


def test_submodule_filter_never_executed(tmp_path: Path):
    """Submodule whose own config defines a filter must never execute."""
    sub_origin = tmp_path / "sub_origin"
    sub_origin.mkdir()
    _init_git_repo(sub_origin)
    (sub_origin / "sub_file.txt").write_text("sub_initial", encoding="utf-8")
    subprocess.run(["git", "add", "sub_file.txt"], cwd=str(sub_origin), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "sub init"], cwd=str(sub_origin), check=True, capture_output=True)

    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    _init_git_repo(main_repo)
    (main_repo / "main_file.txt").write_text("main", encoding="utf-8")
    subprocess.run(["git", "add", "main_file.txt"], cwd=str(main_repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "main init"], cwd=str(main_repo), check=True, capture_output=True)

    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_origin), "mysub"],
        cwd=str(main_repo),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "commit", "-m", "add sub"], cwd=str(main_repo), check=True, capture_output=True)

    marker = tmp_path / "submodule_filter.marker"
    assert not marker.exists()
    py_cmd = _make_py_marker_cmd(marker, "submodule_filter_ran\n")

    sub_worktree = main_repo / "mysub"
    (sub_worktree / ".gitattributes").write_text("* filter=subevil\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitattributes"], cwd=str(sub_worktree), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add sub attr"], cwd=str(sub_worktree), check=True, capture_output=True)

    sub_git_dir = main_repo / ".git" / "modules" / "mysub"
    sub_cfg = sub_git_dir / "config"
    subprocess.run(
        ["git", "config", "--file", str(sub_cfg), "filter.subevil.clean", py_cmd],
        check=True,
        capture_output=True,
    )

    (sub_worktree / "sub_file.txt").write_text("sub_modifie", encoding="utf-8")

    # In main_repo, configure recursive submodules and submodule summary
    subprocess.run(["git", "config", "status.submodulesummary", "true"], cwd=str(main_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "submodule.recurse", "true"], cwd=str(main_repo), check=True, capture_output=True)

    state = get_git_state(main_repo)
    assert state is not None
    assert state.commit_sha is not None
    assert not marker.exists(), "Security failure: submodule filter was executed!"


def test_fsmonitor_injection_script_never_executed(tmp_path: Path):
    """A repository configuring core.fsmonitor to an arbitrary script never executes that script."""
    repo = tmp_path / "malicious_repo"
    repo.mkdir()
    _init_git_repo(repo)

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

    if sys.platform == "win32":
        script_path = tmp_path / "evil_fsmonitor.bat"
        script_path.write_text(f'@echo PWNED > "{marker}"\n', encoding="utf-8")
    else:
        script_path = tmp_path / "evil_fsmonitor.sh"
        script_path.write_text(f'#!/bin/sh\necho PWNED > "{marker}"\n', encoding="utf-8")
        script_path.chmod(0o755)

    git_config = repo / ".git" / "config"
    with open(git_config, "a", encoding="utf-8") as f:
        escaped_script = str(script_path).replace("\\", "/")
        f.write(f'\n[core]\n    fsmonitor = "{escaped_script}"\n')

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is False
    assert not marker.exists(), "Security failure: core.fsmonitor script in .git/config was executed!"


def test_hooks_path_injection_never_executed(tmp_path: Path):
    """A repository configuring core.hooksPath to an arbitrary directory never executes hooks."""
    repo = tmp_path / "hook_repo"
    repo.mkdir()
    _init_git_repo(repo)

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

    git_config = repo / ".git" / "config"
    with open(git_config, "a", encoding="utf-8") as f:
        escaped_hooks = str(malicious_hooks).replace("\\", "/")
        f.write(f'\n[core]\n    hooksPath = "{escaped_hooks}"\n')

    state = get_git_state(repo)
    assert state is not None
    assert not marker.exists(), "Security failure: malicious hook script was executed!"


def test_textconv_diff_driver_never_executed(tmp_path: Path):
    """A repository defining a textconv / diff driver command must never execute."""
    repo = tmp_path / "repo_textconv"
    repo.mkdir()
    _init_git_repo(repo)

    marker = tmp_path / "textconv.marker"
    assert not marker.exists()

    py_cmd = _make_py_marker_cmd(marker, "textconv_ran\n")

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    (repo / ".gitattributes").write_text("* diff=evildiff\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    subprocess.run(["git", "config", "diff.evildiff.textconv", py_cmd], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "diff.evildiff.command", py_cmd], cwd=str(repo), check=True, capture_output=True)

    (repo / "file.txt").write_text("modified", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.commit_sha is not None
    assert state.is_dirty is True
    assert not marker.exists(), "Security failure: textconv / diff driver was executed!"


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
    assert state1.is_unknown is False

    # Modify file without committing
    (repo / "file.txt").write_text("modified content", encoding="utf-8")

    state2 = get_git_state(repo)
    assert state2 is not None
    assert state2.is_dirty is True
    assert state2.is_unknown is False


def test_uncertain_corrupt_repo_yields_unknown_dirty(tmp_path: Path):
    """Corrupt or invalid git configuration yields is_dirty=True and is_unknown=True."""
    repo = tmp_path / "corrupt_repo"
    repo.mkdir()
    _init_git_repo(repo)

    # Corrupt .git/config syntax
    git_config = repo / ".git" / "config"
    git_config.write_text("THIS IS INVALID SYNTAX [][][[\n", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.is_dirty is True
    assert state.is_unknown is True


def test_non_git_repository_handling(tmp_path: Path):
    """Non-Git directories are identified and return None without error."""
    non_git = tmp_path / "plain_dir"
    non_git.mkdir()
    (non_git / "code.py").write_text("print('hello')", encoding="utf-8")

    assert is_git_repository(non_git) is False
    assert get_git_state(non_git) is None
