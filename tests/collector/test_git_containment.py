"""Containment and minimum-version tests for Git state extraction (P2a-WP2b / ISS-031).

Covers:
1. core.worktree -> outside dir containing sentinel file: result does not depend on outside dir,
   and subprocess spy verifies explicit --git-dir, --work-tree=<root>, and -c core.worktree=<root>.
2. objects/info/alternates and http-alternates -> is_unknown=True, reason="git_alternates", git not invoked.
3. .git gitfile pointing outside root -> is_unknown=True, reason="gitdir_outside_root", git not invoked.
4. .git gitfile pointing inside root (valid) -> normal clean/dirty result.
5. Linked worktree (`git worktree add`) scanned as root -> is_unknown=True (gitdir_outside_root, git not invoked),
   and contained gitdir with `commondir` -> is_unknown=True, reason="git_commondir", git not invoked.
6. extensions.worktreeConfig / config.worktree -> is_unknown=True, reason="git_worktree_config", git not invoked.
7. Environment injection (GIT_DIR, GIT_WORK_TREE, GIT_ALTERNATE_OBJECT_DIRECTORIES, etc.)
   set in parent process are not seen by child; GIT_CEILING_DIRECTORIES is set to parent of root.
8. Unsupported git version (monkeypatched version probe) -> is_unknown=True, reason="git_version_unsupported",
   status not invoked, one warning logged, version probe cached once per process.
9. Collector integration: reason codes appear in snapshot partial_reasons.
10. Regression: full ISS-030 attack set still neutralized with containment flags active.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

import eap.ppm.collector.git as git_mod
from eap.db import location_registrations, project_scopes, sources
from eap.ppm.collector import PpmCollector
from eap.ppm.collector.git import (
    MIN_GIT_VERSION,
    get_git_state,
    get_git_version,
    reset_git_version_cache,
)


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


def _commit_file(repo_dir: Path, filename: str = "tracked.txt", content: bytes = b"hello\n") -> None:
    (repo_dir / filename).write_bytes(content)
    subprocess.run(["git", "add", filename], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "commit"], cwd=str(repo_dir), check=True, capture_output=True)


def test_core_worktree_outside_dir_ignored_and_verified_by_spy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """core.worktree pointing outside root must not affect is_dirty; explicit --work-tree=<root> passed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo)

    outside = tmp_path / "outside_worktree"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside dirty file\n", encoding="utf-8")

    # Configure repository-controlled core.worktree to point outside root
    subprocess.run(
        ["git", "config", "core.worktree", str(outside)],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    captured_calls: list[dict[str, Any]] = []
    real_run = subprocess.run

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        captured_calls.append({"cmd": list(cmd), "env": dict(kwargs.get("env") or {})})
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    state1 = get_git_state(repo)
    assert state1 is not None
    assert state1.is_unknown is False
    assert state1.is_dirty is False

    # Mutate outside directory: add another file and modify sentinel
    (outside / "sentinel.txt").write_text("mutated outside\n", encoding="utf-8")
    (outside / "extra_outside.txt").write_text("another outside file\n", encoding="utf-8")

    state2 = get_git_state(repo)
    assert state2 is not None
    assert state2.is_unknown is False
    assert state2.is_dirty is False
    assert state1 == state2

    # Verify spy captured explicit --git-dir, --work-tree=<root>, and -c core.worktree=<root>
    root_resolved = str(repo.resolve())
    gitdir_resolved = str((repo / ".git").resolve())
    status_calls = [c for c in captured_calls if "status" in c["cmd"]]
    assert len(status_calls) >= 2
    for call in status_calls:
        cmd = call["cmd"]
        assert f"--git-dir={gitdir_resolved}" in cmd
        assert f"--work-tree={root_resolved}" in cmd
        assert f"core.worktree={root_resolved}" in cmd


def test_alternates_returns_unknown_without_invoking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """objects/info/alternates present -> is_unknown=True, reason='git_alternates', git not invoked."""
    repo = tmp_path / "repo_alt"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo)

    foreign = tmp_path / "foreign_objects"
    foreign.mkdir()
    alt_file = repo / ".git" / "objects" / "info" / "alternates"
    alt_file.parent.mkdir(parents=True, exist_ok=True)
    alt_file.write_text(str(foreign) + "\n", encoding="utf-8")

    invoked: list[list[str]] = []

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        invoked.append(list(cmd))
        raise AssertionError(f"git subprocess should not have been invoked: {cmd}")

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    state = get_git_state(repo)
    assert state is not None
    assert state.is_dirty is True
    assert state.is_unknown is True
    assert state.reason == "git_alternates"
    assert invoked == []


def test_http_alternates_returns_unknown_without_invoking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """objects/info/http-alternates present -> is_unknown=True, reason='git_alternates', git not invoked."""
    repo = tmp_path / "repo_http_alt"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo)

    alt_file = repo / ".git" / "objects" / "info" / "http-alternates"
    alt_file.parent.mkdir(parents=True, exist_ok=True)
    alt_file.write_text("https://example.invalid/objects\n", encoding="utf-8")

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"git subprocess should not have been invoked: {cmd}")

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    state = get_git_state(repo)
    assert state is not None
    assert state.is_dirty is True
    assert state.is_unknown is True
    assert state.reason == "git_alternates"


def test_gitfile_outside_root_returns_unknown_without_invoking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .git gitfile pointing outside root -> is_unknown=True, reason='gitdir_outside_root', git not invoked."""
    outside_repo = tmp_path / "outside_repo"
    outside_repo.mkdir()
    _init_git_repo(outside_repo)
    _commit_file(outside_repo)

    scanned_root = tmp_path / "scanned_root"
    scanned_root.mkdir()
    (scanned_root / ".git").write_text(f"gitdir: {outside_repo / '.git'}\n", encoding="utf-8")

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"git subprocess should not have been invoked: {cmd}")

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    state = get_git_state(scanned_root)
    assert state is not None
    assert state.is_dirty is True
    assert state.is_unknown is True
    assert state.reason == "gitdir_outside_root"


def test_gitfile_inside_root_valid_returns_normal_result(tmp_path: Path) -> None:
    """A .git gitfile pointing to a valid directory inside root returns normal clean/dirty state."""
    repo = tmp_path / "repo_gitfile_inside"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo, ".gitignore", b".git_internal/\n")
    _commit_file(repo)

    # Move .git directory to .git_internal inside the same root and replace .git with a gitfile
    internal_gitdir = repo / ".git_internal"
    (repo / ".git").rename(internal_gitdir)
    (repo / ".git").write_text("gitdir: .git_internal\n", encoding="utf-8")

    clean_state = get_git_state(repo)
    assert clean_state is not None
    assert clean_state.is_unknown is False
    assert clean_state.is_dirty is False
    assert clean_state.commit_sha is not None

    # Dirty the working tree
    (repo / "tracked.txt").write_bytes(b"modified\n")
    dirty_state = get_git_state(repo)
    assert dirty_state is not None
    assert dirty_state.is_unknown is False
    assert dirty_state.is_dirty is True


def test_linked_worktree_and_commondir_return_unknown_without_invoking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked worktree (`git worktree add`) and contained `commondir` both fail safe without invoking git."""
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    _init_git_repo(main_repo)
    _commit_file(main_repo)

    linked_wt = tmp_path / "linked_wt"
    subprocess.run(
        ["git", "worktree", "add", str(linked_wt)],
        cwd=str(main_repo),
        check=True,
        capture_output=True,
    )

    contained_repo = tmp_path / "contained_commondir"
    contained_repo.mkdir()
    _init_git_repo(contained_repo)
    _commit_file(contained_repo)
    (contained_repo / ".git" / "commondir").write_text("../.git\n", encoding="utf-8")

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"git subprocess should not have been invoked: {cmd}")

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    # 1. Scanning the `git worktree add` directory as root: its .git gitfile points to main_repo/.git/worktrees/...
    wt_state = get_git_state(linked_wt)
    assert wt_state is not None
    assert wt_state.is_dirty is True
    assert wt_state.is_unknown is True
    assert wt_state.reason == "gitdir_outside_root"

    # 2. A contained gitdir containing a `commondir` file returns `git_commondir`
    cd_state = get_git_state(contained_repo)
    assert cd_state is not None
    assert cd_state.is_dirty is True
    assert cd_state.is_unknown is True
    assert cd_state.reason == "git_commondir"


def test_worktree_config_returns_unknown_without_invoking_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config.worktree or extensions.worktreeConfig -> is_unknown=True, reason='git_worktree_config'."""
    repo1 = tmp_path / "repo_cfg_wt"
    repo1.mkdir()
    _init_git_repo(repo1)
    _commit_file(repo1)
    (repo1 / ".git" / "config.worktree").write_text("[core]\n\tworktree = /tmp\n", encoding="utf-8")

    repo2 = tmp_path / "repo_ext_wt"
    repo2.mkdir()
    _init_git_repo(repo2)
    _commit_file(repo2)
    subprocess.run(
        ["git", "config", "extensions.worktreeConfig", "true"],
        cwd=str(repo2),
        check=True,
        capture_output=True,
    )

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"git subprocess should not have been invoked: {cmd}")

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    s1 = get_git_state(repo1)
    assert s1 is not None and s1.is_unknown is True and s1.reason == "git_worktree_config"

    s2 = get_git_state(repo2)
    assert s2 is not None and s2.is_unknown is True and s2.reason == "git_worktree_config"


def test_environment_injection_scrubbed_and_ceiling_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GIT_DIR, GIT_WORK_TREE, GIT_ALTERNATE_OBJECT_DIRECTORIES etc. in parent env are scrubbed."""
    repo = tmp_path / "repo_env"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo)

    foreign = tmp_path / "foreign"
    foreign.mkdir()

    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
    ):
        monkeypatch.setenv(var, str(foreign))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))

    captured_envs: list[dict[str, str]] = []
    real_run = subprocess.run

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if "env" in kwargs and kwargs["env"] is not None:
            captured_envs.append(dict(kwargs["env"]))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    state = get_git_state(repo)
    assert state is not None
    assert state.is_unknown is False
    assert state.is_dirty is False

    assert len(captured_envs) >= 1
    expected_ceiling = str(repo.resolve().parent)
    for child_env in captured_envs:
        for scrubbed_var in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
        ):
            assert scrubbed_var not in child_env
        assert child_env.get("GIT_CEILING_DIRECTORIES") == expected_ceiling


def test_unsupported_git_version_returns_unknown_and_caches_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Below MIN_GIT_VERSION: never run status, return git_version_unsupported, log one warning."""
    repo = tmp_path / "repo_old_git"
    repo.mkdir()
    _init_git_repo(repo)
    _commit_file(repo)

    reset_git_version_cache()
    probe_calls = 0

    def fake_probe() -> tuple[int, int, int]:
        nonlocal probe_calls
        probe_calls += 1
        return (2, 34, 1)

    def spy_run(cmd: list[str], *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"git subprocess should not have been invoked on unsupported version: {cmd}")

    monkeypatch.setattr(git_mod, "_probe_git_version", fake_probe)
    monkeypatch.setattr(git_mod.subprocess, "run", spy_run)

    try:
        with caplog.at_level(logging.WARNING, logger="eap.ppm.collector.git"):
            state1 = get_git_state(repo)
            state2 = get_git_state(repo)

        assert probe_calls == 1, "git --version probe must be cached once per process"
        assert state1 is not None
        assert state1.is_dirty is True
        assert state1.is_unknown is True
        assert state1.reason == "git_version_unsupported"
        assert state2 == state1

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, "unsupported git version must log exactly one warning"
    finally:
        reset_git_version_cache()


def test_real_git_version_probe_meets_minimum() -> None:
    """Verify the local/CI git binary is probed and meets MIN_GIT_VERSION."""
    reset_git_version_cache()
    ver = get_git_version()
    assert ver is not None
    assert ver >= MIN_GIT_VERSION, f"Installed git {ver} is below minimum {MIN_GIT_VERSION}"


def test_collector_records_containment_reason_in_partial_reasons(
    tmp_path: Path,
    db_session: Session,
) -> None:
    """Collector snapshot partial_reasons must record the specific git containment reason code."""
    repo = tmp_path / "collector_alt_repo"
    repo.mkdir()
    _init_git_repo(repo)
    ppm_dir = repo / ".ppm"
    (ppm_dir / "updates").mkdir(parents=True)
    (ppm_dir / "notes").mkdir(parents=True)
    (ppm_dir / "project.yaml").write_text("project_id: proj-containment\n", encoding="utf-8")
    _commit_file(repo, "README.md", b"# test\n")

    # Inject alternates after commit
    alt_file = repo / ".git" / "objects" / "info" / "alternates"
    alt_file.parent.mkdir(parents=True, exist_ok=True)
    alt_file.write_text(str(tmp_path / "outside_objs") + "\n", encoding="utf-8")

    now = dt.datetime.now(dt.UTC)
    root_str = str(repo.resolve())
    db_session.execute(
        sources.insert().values(
            tenant_id="tenant-owner",
            source_id="src-containment",
            canonical_root=root_str,
            repo_identity="repo-containment",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.execute(
        project_scopes.insert().values(
            id="scope-containment",
            tenant_id="tenant-owner",
            project_id="proj-containment",
            source_id="src-containment",
            metadata_root=".ppm",
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    db_session.execute(
        location_registrations.insert().values(
            id="loc-containment",
            tenant_id="tenant-owner",
            source_id="src-containment",
            project_id="proj-containment",
            metadata_root=".ppm",
            state="active",
            revision=1,
            registered_by_ref="principal-test",
            approved_by_ref="principal-owner",
            host_id="host-test-1",
            connector_id="connector-test-1",
            root_path=root_str,
            scan_scope=[".ppm"],
            deny_globs=[],
            notes_globs=[".ppm/notes/*.md"],
            classification="internal",
            egress_policy_ref="policy-local",
            identity={"canonical_root_fingerprint": root_str, "repo_identity": "repo-containment"},
            follow_symlinks=False,
            allow_hardlinked_files=False,
            max_file_bytes=1048576,
            artifact_aliases={"architecture": [], "blueprint": [], "implementation_plan": []},
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    collector = PpmCollector(allow_real_roots=False)
    res = collector.scan_source(db_session, tenant_id="tenant-owner", source_id="src-containment")

    assert res.status == "partial"
    assert res.git_dirty is True
    assert "git_alternates" in res.partial_reasons


def test_iss030_regression_clean_filter_still_not_executed(tmp_path: Path) -> None:
    """Regression check: ISS-030 clean filter attack still never executes under ISS-031 hardening."""
    repo = tmp_path / "repo_iss030_reg"
    repo.mkdir()
    _init_git_repo(repo)

    marker = tmp_path / "iss030_reg.marker"
    py_posix = Path(sys.executable).as_posix()
    m_posix = marker.as_posix()
    py_cmd = (
        f'"{py_posix}" -c "import sys; '
        f'open(r\'{m_posix}\', \'a\').write(\'pwned\\n\'); '
        f'sys.stdout.write(sys.stdin.read())"'
    )

    (repo / "file.txt").write_text("initial", encoding="utf-8")
    (repo / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt", ".gitattributes"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "filter.evil.clean", py_cmd], cwd=str(repo), check=True, capture_output=True)
    (repo / "file.txt").write_text("modified", encoding="utf-8")

    state = get_git_state(repo)
    assert state is not None
    assert state.is_dirty is True
    assert not marker.exists()
