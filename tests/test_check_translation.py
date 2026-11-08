"""
tests/test_check_translation.py — Tests for tools/check_translation.py SC-08 staleness checker.
Verifies:
  1. Archive-like copy without .git -> not_run (exit non-zero in strict mode, never claims up-to-date).
  2. Untouched English file -> reported up-to-date.
  3. Touched English file -> reported stale.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from check_translation import has_git_history, run_staleness_report  # type: ignore[import-not-found]


def _run_git(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_archive_like_copy_without_git_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An archive export without .git reports not_run: no git history and fails in --strict."""
    archive = tmp_path / "archive_export"
    archive.mkdir()

    en_dir = archive / "docs"
    en_dir.mkdir()
    (en_dir / "sample.md").write_text("# Sample doc\nEnglish canonical.\n", encoding="utf-8")

    vi_dir = archive / "docs" / "vi" / "docs"
    vi_dir.mkdir(parents=True)
    (vi_dir / "sample.md").write_text(
        "> Translated from docs/sample.md @ a36d44e\n\n# Tài liệu mẫu\nBản dịch tiếng Việt.\n",
        encoding="utf-8",
    )

    # 1. has_git_history must return False
    assert not has_git_history(archive)

    # 2. Non-strict mode: returns 0, prints not_run: no git history, never claims up-to-date
    code = run_staleness_report(archive, strict=False)
    assert code == 0
    captured = capsys.readouterr().out
    assert "SC-08 Report Summary: not_run: no git history." in captured
    assert "up-to-date" not in captured.lower()

    # 3. Strict mode: returns non-zero, prints not_run: no git history, never claims up-to-date
    strict_code = run_staleness_report(archive, strict=True)
    assert strict_code != 0
    captured_strict = capsys.readouterr().out
    assert "SC-08 Report Summary: not_run: no git history." in captured_strict
    assert "up-to-date" not in captured_strict.lower()


def test_untouched_up_to_date_and_touched_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify that an untouched file reports up-to-date and a touched file reports stale."""
    repo = tmp_path / "repo"
    repo.mkdir()

    _run_git(["git", "init"], repo)
    _run_git(["git", "config", "user.name", "Test Runner"], repo)
    _run_git(["git", "config", "user.email", "test@example.com"], repo)

    en_dir = repo / "docs"
    en_dir.mkdir()
    en_file = en_dir / "sample.md"
    en_file.write_text("# Sample\nLine 1\n", encoding="utf-8")

    _run_git(["git", "add", "docs/sample.md"], repo)
    _run_git(["git", "commit", "-m", "Commit 1"], repo)

    sha1 = _run_git(["git", "rev-parse", "HEAD"], repo).stdout.strip()

    vi_dir = repo / "docs" / "vi" / "docs"
    vi_dir.mkdir(parents=True)
    vi_file = vi_dir / "sample.md"
    vi_file.write_text(
        f"> Translated from docs/sample.md @ {sha1}\n\n# Mẫu\nDòng 1\n",
        encoding="utf-8",
    )

    # Untouched English canonical -> up-to-date
    code = run_staleness_report(repo, strict=True)
    assert code == 0
    captured = capsys.readouterr().out
    assert f"OK: docs/sample.md @ {sha1}" in captured
    assert "1 up-to-date, 0 stale" in captured

    # Now touch the English file and commit
    en_file.write_text("# Sample\nLine 1\nLine 2 changed\n", encoding="utf-8")
    _run_git(["git", "add", "docs/sample.md"], repo)
    _run_git(["git", "commit", "-m", "Commit 2"], repo)

    # Touched English canonical -> stale
    code_stale = run_staleness_report(repo, strict=False)
    assert code_stale == 0
    captured_stale = capsys.readouterr().out
    assert "STALE: docs/sample.md" in captured_stale
    assert "1 commits ahead" in captured_stale
    assert "0 up-to-date, 1 stale" in captured_stale

    # Strict mode on stale -> non-zero exit code
    strict_stale = run_staleness_report(repo, strict=True)
    assert strict_stale != 0
