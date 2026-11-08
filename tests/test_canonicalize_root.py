"""Tests for canonicalize_root and alias validation (P2a-WP1).

Covers:
- canonicalize_root negatives (Linux + Windows):
  - relative paths
  - '..' segments
  - device namespaces (\\\\?\\, \\\\.\\, //?/, //./)
  - UNC paths (\\\\server\\share) without allow_unc
  - NUL and control characters
  - mixed separators ('/' and '\\' mixed)
  - Linux backslashes
- canonicalize_root positives:
  - Windows normalization (lowercase drive and path, forward slashes)
  - Linux normalization (case preserved, redundant slashes collapsed)
  - UNC paths with allow_unc=True
  - Root drive preservation
- validate_aliases negatives and positives:
  - absolute paths rejected
  - '..' segments rejected
  - backslashes rejected
  - duplicate aliases rejected
  - valid relative aliases accepted
"""

from __future__ import annotations

import pytest

from eap.ppm.path import (
    PathValidationError,
    canonicalize_root,
    validate_aliases,
)


class TestCanonicalizeRootNegatives:
    """Negative tests for pure root canonicalization across Linux and Windows."""

    @pytest.mark.parametrize(
        "bad_linux_path",
        [
            "relative/path",
            "./relative/path",
            "projects/eap",
            "foo",
        ],
    )
    def test_linux_relative_paths_rejected(self, bad_linux_path: str) -> None:
        with pytest.raises(PathValidationError, match="must be absolute"):
            canonicalize_root(bad_linux_path, host_os="linux")

    @pytest.mark.parametrize(
        "bad_win_path",
        [
            "relative\\path",
            ".\\relative\\path",
            "projects\\eap",
            "C:relative_no_slash",
            "\\relative_no_drive",
            "/relative_no_drive",
        ],
    )
    def test_windows_relative_paths_rejected(self, bad_win_path: str) -> None:
        with pytest.raises(PathValidationError, match="must start with drive letter|too short|not absolute"):
            canonicalize_root(bad_win_path, host_os="windows")

    @pytest.mark.parametrize(
        "path_with_dots,os_name",
        [
            ("/home/user/../other", "linux"),
            ("/../root", "linux"),
            ("/a/b/c/..", "linux"),
            ("C:\\Projects\\..\\Other", "windows"),
            ("C:/Projects/../Other", "windows"),
            ("D:\\..\\Root", "windows"),
        ],
    )
    def test_parent_traversal_segments_rejected(self, path_with_dots: str, os_name: str) -> None:
        with pytest.raises(PathValidationError, match="'\\.\\.' segments"):
            canonicalize_root(path_with_dots, host_os=os_name)

    @pytest.mark.parametrize(
        "device_ns",
        [
            "\\\\?\\C:\\Projects\\repo",
            "\\\\.\\COM1",
            "//?/C:/Projects/repo",
            "//./COM1",
        ],
    )
    def test_windows_device_namespaces_rejected(self, device_ns: str) -> None:
        with pytest.raises(PathValidationError, match="device namespaces"):
            canonicalize_root(device_ns, host_os="windows")

    @pytest.mark.parametrize(
        "unc_path",
        [
            "\\\\server\\share\\project",
            "//server/share/project",
        ],
    )
    def test_unc_paths_rejected_without_allow_unc(self, unc_path: str) -> None:
        with pytest.raises(PathValidationError, match="UNC paths"):
            canonicalize_root(unc_path, host_os="windows", allow_unc=False)

    @pytest.mark.parametrize(
        "path_with_ctrl",
        [
            "/home/user\x00/project",
            "/home/user\n/project",
            "/home/user\r/project",
            "/home/user\t/project",
            "C:\\Projects\\Repo\x00\\Sub",
            "C:\\Projects\\Repo\x1f\\Sub",
        ],
    )
    def test_nul_and_control_characters_rejected(self, path_with_ctrl: str) -> None:
        with pytest.raises(PathValidationError, match="control or NUL character"):
            canonicalize_root(path_with_ctrl, host_os="linux")
        with pytest.raises(PathValidationError, match="control or NUL character"):
            canonicalize_root(path_with_ctrl, host_os="windows")

    @pytest.mark.parametrize(
        "mixed_path",
        [
            "C:\\Projects/Repo",
            "C:/Projects\\Repo",
            "/home/user\\project",
            "D:\\repos/app/src",
        ],
    )
    def test_mixed_separators_rejected(self, mixed_path: str) -> None:
        with pytest.raises(PathValidationError, match="mixed path separators"):
            canonicalize_root(mixed_path, host_os="windows")

    def test_linux_backslash_rejected(self) -> None:
        with pytest.raises(PathValidationError, match="POSIX path must not contain backslashes|mixed path separators"):
            canonicalize_root("/home/user\\dir", host_os="linux")
        with pytest.raises(PathValidationError, match="POSIX path must not contain backslashes"):
            canonicalize_root("\\home\\user\\dir", host_os="linux")

    @pytest.mark.parametrize(
        "bad_win_segment",
        [
            "C:\\Repo\\App.",
            "C:\\Repo\\App ",
            "C:\\Repo\\Subdir.\\Folder",
            "C:\\Repo\\Subdir \\Folder",
            "\\\\server\\share\\App.",
            "\\\\server\\share\\App ",
        ],
    )
    def test_windows_trailing_dot_or_space_rejected(self, bad_win_segment: str) -> None:
        with pytest.raises(PathValidationError, match="must not end with '\\.' or ' '"):
            canonicalize_root(bad_win_segment, host_os="windows", allow_unc=True)

    @pytest.mark.parametrize(
        "reserved_device_path",
        [
            "C:\\Repo\\CON",
            "C:\\Repo\\con.txt",
            "C:\\Repo\\AUX",
            "C:\\Repo\\aux.json",
            "C:\\Repo\\PRN",
            "C:\\Repo\\prn.log",
            "C:\\Repo\\NUL",
            "C:\\Repo\\nul.dat",
            "C:\\Repo\\COM1",
            "C:\\Repo\\com9.txt",
            "C:\\Repo\\LPT1",
            "C:\\Repo\\lpt8.bin",
            "\\\\server\\share\\aux.json",
        ],
    )
    def test_windows_reserved_device_names_rejected(self, reserved_device_path: str) -> None:
        with pytest.raises(PathValidationError, match="reserved device name"):
            canonicalize_root(reserved_device_path, host_os="windows", allow_unc=True)

    @pytest.mark.parametrize(
        "trailing_ctrl_path",
        [
            "/home/user/project\n",
            "/home/user/project\r",
            "/home/user/project\t",
            "C:\\Projects\\repo\n",
        ],
    )
    def test_trailing_control_characters_rejected_without_strip(self, trailing_ctrl_path: str) -> None:
        os_name = "linux" if trailing_ctrl_path.startswith("/") else "windows"
        with pytest.raises(PathValidationError, match="control or NUL character"):
            canonicalize_root(trailing_ctrl_path, host_os=os_name)

    def test_unicode_nfc_normalization_and_casefold(self) -> None:
        # C:\Répo (NFC: \xe9) vs C:\Re\u0301po (NFD: e + combining acute accent)
        nfc_path = "C:\\Répo\\App"
        nfd_path = "C:\\Re\u0301po\\App"
        res_nfc = canonicalize_root(nfc_path, host_os="windows")
        res_nfd = canonicalize_root(nfd_path, host_os="windows")
        assert res_nfc == "c:/répo/app"
        assert res_nfd == "c:/répo/app"
        assert res_nfc == res_nfd

    def test_windows_ads_colon_rejected(self) -> None:
        with pytest.raises(PathValidationError, match="alternate data stream"):
            canonicalize_root("C:\\Projects\\file:hidden", host_os="windows")


class TestCanonicalizeRootPositives:
    """Positive normalization tests for Windows and Linux."""

    def test_windows_case_and_slash_normalization(self) -> None:
        result = canonicalize_root("C:\\Projects\\EAM_HUB", host_os="windows")
        assert result == "c:/projects/eam_hub"

    def test_windows_forward_slashes_and_casing(self) -> None:
        result = canonicalize_root("D:/Projects/Example-Monorepo", host_os="windows")
        assert result == "d:/projects/example-monorepo"

    def test_windows_trailing_slashes_stripped(self) -> None:
        result = canonicalize_root("C:\\Repos\\Project\\\\", host_os="windows")
        assert result == "c:/repos/project"

    def test_windows_root_drive_preserved(self) -> None:
        assert canonicalize_root("C:\\", host_os="windows") == "c:/"
        assert canonicalize_root("d:/", host_os="windows") == "d:/"

    def test_linux_case_preserved(self) -> None:
        result = canonicalize_root("/home/User/EAM_HUB", host_os="linux")
        assert result == "/home/User/EAM_HUB"

    def test_linux_redundant_slashes_collapsed(self) -> None:
        result = canonicalize_root("/var//log///audit/", host_os="linux")
        assert result == "/var/log/audit"

    def test_linux_root_preserved(self) -> None:
        assert canonicalize_root("/", host_os="linux") == "/"

    def test_unc_allowed_when_explicit(self) -> None:
        result = canonicalize_root("\\\\server\\share\\repo", host_os="windows", allow_unc=True)
        assert result == "//server/share/repo"


class TestValidateAliases:
    """Tests for artifact_aliases and notes_globs validation per docs/04."""

    def test_valid_aliases_accepted(self) -> None:
        aliases = {
            "architecture": ["architecture.md", "docs/ARCHITECTURE.md"],
            "blueprint": ["MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md", "docs/IMPLEMENTATION_PLAN.md"],
        }
        notes_globs = [".ppm/notes/*.md", "notes/**/*.md"]
        # Should not raise
        validate_aliases(aliases, notes_globs)

    def test_absolute_alias_rejected(self) -> None:
        aliases = {
            "architecture": ["/etc/passwd"],
            "blueprint": ["MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md"],
        }
        with pytest.raises(PathValidationError, match="violates safe relative path"):
            validate_aliases(aliases)

    def test_drive_letter_alias_rejected(self) -> None:
        aliases = {
            "architecture": ["C:/Users/x/arch.md"],
            "blueprint": ["MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md"],
        }
        with pytest.raises(PathValidationError, match="violates safe relative path"):
            validate_aliases(aliases)

    def test_dot_dot_alias_rejected(self) -> None:
        aliases = {
            "architecture": ["architecture.md"],
            "blueprint": ["../other/MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md"],
        }
        with pytest.raises(PathValidationError, match="violates safe relative path"):
            validate_aliases(aliases)

    def test_backslash_in_alias_rejected(self) -> None:
        aliases = {
            "architecture": ["docs\\architecture.md"],
            "blueprint": ["MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md"],
        }
        with pytest.raises(PathValidationError, match="violates safe relative path"):
            validate_aliases(aliases)

    def test_duplicate_alias_rejected(self) -> None:
        aliases = {
            "architecture": ["arch.md", "arch.md"],
            "blueprint": ["MASTER_BLUEPRINT.md"],
            "implementation_plan": ["implement_plan.md"],
        }
        with pytest.raises(PathValidationError, match="Duplicate alias 'arch.md'"):
            validate_aliases(aliases)

    def test_missing_category_rejected(self) -> None:
        aliases = {
            "architecture": ["arch.md"],
            "blueprint": ["blueprint.md"],
        }
        with pytest.raises(PathValidationError, match="Missing required artifact alias category: 'implementation_plan'"):
            validate_aliases(aliases)

    def test_notes_glob_dot_dot_rejected(self) -> None:
        aliases = {
            "architecture": ["arch.md"],
            "blueprint": ["blueprint.md"],
            "implementation_plan": ["plan.md"],
        }
        with pytest.raises(PathValidationError, match="violates safe relative path"):
            validate_aliases(aliases, notes_globs=["../escape/*.md"])
