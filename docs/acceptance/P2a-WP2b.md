# Acceptance Packet: P2a-WP2b Git Containment & Minimum Version (ISS-031)

## 1. Milestone and Scope
- **Work Package**: `P2a-WP2b` (close `ISS-031`, small follow-up to `P2a-WP2`, blocks `EAP_COLLECTOR_ALLOW_REAL_ROOTS=true` / `DG-3`).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P2a-WP2b` (branched from `main` at `db54218`)
- **Base Commit**: `db54218`
- **Safety & Governance**:
  - `EAP_COLLECTOR_ALLOW_REAL_ROOTS` remains `false`; all tests use `tmp_path` fixtures only.
  - No network calls, no new migrations, no contract (`contracts/**`, `examples/**`, `ppm-v2/schemas/**`) changes.
  - `ISS-031` marked `resolved` in `docs/ISSUES.md` with reference to `tests/collector/test_git_containment.py`.
  - All `ISS-030` command-execution protections preserved.

---

## 2. What Changed

1. **Handle-based directory containment (`src/eap/ppm/collector/safe_open.py`)**:
   - Added `safe_resolve_dir(root, rel)` and `resolve_contained_dir(root, target)` (with Windows `ctypes` `CreateFileW` + `FILE_FLAG_OPEN_REPARSE_POINT` + `GetFinalPathNameByHandleW` + `BY_HANDLE_FILE_INFORMATION`, and Linux `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS)` / `O_NOFOLLOW` + `/proc/self/fd` fallback).
   - Rejects symlinks, junctions, reparse points, and targets escaping `root`.

2. **Git containment & version enforcement (`src/eap/ppm/collector/git.py`)**:
   - **Pre-invocation gitdir resolution (`_resolve_gitdir`)**:
     - `.git` directory -> verified via `safe_resolve_dir(root, ".git")`.
     - `.git` gitfile -> opened via `safe_open(root, ".git")` and target verified via `resolve_contained_dir(root, raw_target)`. Any escape, symlink, reparse point, or missing/malformed target returns `GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="gitdir_outside_root")` without invoking `git`.
   - **Containment metadata checks (`_check_gitdir_containment_metadata`)**:
     - `commondir` present -> `GitState(..., is_dirty=True, is_unknown=True, reason="git_commondir")` without invoking `git`.
     - `objects/info/alternates` or `objects/info/http-alternates` present -> `reason="git_alternates"` without invoking `git`.
     - `config.worktree` present or `extensions.worktreeConfig` in `<gitdir>/config` -> `reason="git_worktree_config"` without invoking `git`.
   - **Explicit containment flags & environment scrubbing**:
     - Every `git` invocation (`status`, `rev-parse`, `config --list`) passes `--git-dir=<resolved_gitdir>`, `--work-tree=<root>`, and `-c core.worktree=<root>`.
     - Child environment is built from scratch (`_build_clean_git_env`): `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_INDEX_FILE`, `GIT_NAMESPACE` are scrubbed; `GIT_CEILING_DIRECTORIES` is set to `str(root_path.parent)`.
     - `_detect_empty_tree`, `_discover_filter_and_diff_names`, and `_read_head_direct` read repository files via `safe_open` using the resolved `gitdir_rel`.
   - **Minimum Git version (`MIN_GIT_VERSION = (2, 41, 0)`)**:
     - `--attr-source=<tree>` was introduced in Git `2.41.0` (official release notes: `https://github.com/git/git/blob/master/Documentation/RelNotes/2.41.0.adoc`, accessed `2026-11-14`).
     - `get_git_version()` probes `git --version` once per process and caches the result. Below `2.41.0` (or if `git` is unavailable), `get_git_state()` logs one warning, never invokes `git status`, and returns `GitState(commit_sha=None, is_dirty=True, is_unknown=True, reason="git_version_unsupported")`.

3. **Collector reason propagation (`src/eap/ppm/collector/collector.py`)**:
   - `scan_source()` propagates `GitState.reason` (`gitdir_outside_root`, `git_commondir`, `git_alternates`, `git_worktree_config`, `git_version_unsupported`) into `collector_snapshots.partial_reasons` (falling back to `"git_state_unknown: dirty state could not be determined safely"` for generic errors).

4. **Documentation (`docs/DEV_ENVIRONMENT.md`, `docs/ISSUES.md`)**:
   - Documented `Git >= 2.41.0` in `docs/DEV_ENVIRONMENT.md`.
   - Marked `ISS-031` `resolved` in `docs/ISSUES.md` with reference to `tests/collector/test_git_containment.py`.

---

## 3. Git Version Probe Results
- **Local**: `git version 2.54.0.windows.1` -> `get_git_version()` returned `(2, 54, 0) >= (2, 41, 0)`; `test_real_git_version_probe_meets_minimum` **PASS**.
- **CI (Linux)**: *(Pending push / GitHub Actions run — not yet triggered at packet write time; will be recorded if triggered, otherwise `not_run`)*.

---

## 4. Test Matrix (`tests/collector/test_git_containment.py`)

| Test | Attack / Condition | Expected | Observed |
|---|---|---|---|
| `test_core_worktree_outside_dir_ignored_and_verified_by_spy` | `git config core.worktree <outside>` with sentinel file | `is_dirty` unaffected by outside dir mutations; spy sees `--git-dir=<root>/.git`, `--work-tree=<root>`, `-c core.worktree=<root>` | **PASS** (`state1 == state2`, `is_dirty=False`, all status calls contained explicit flags) |
| `test_alternates_returns_unknown_without_invoking_git` | `.git/objects/info/alternates` present | `is_dirty=True`, `is_unknown=True`, `reason="git_alternates"`, `git` not invoked | **PASS** (`invoked == []`, `reason == "git_alternates"`) |
| `test_http_alternates_returns_unknown_without_invoking_git` | `.git/objects/info/http-alternates` present | `is_dirty=True`, `is_unknown=True`, `reason="git_alternates"`, `git` not invoked | **PASS** (`reason == "git_alternates"`) |
| `test_gitfile_outside_root_returns_unknown_without_invoking_git` | `.git` gitfile pointing outside root | `is_dirty=True`, `is_unknown=True`, `reason="gitdir_outside_root"`, `git` not invoked | **PASS** (`reason == "gitdir_outside_root"`) |
| `test_gitfile_inside_root_valid_returns_normal_result` | `.git` gitfile pointing to `.git_internal` inside root | Normal clean/dirty detection | **PASS** (`clean_state.is_dirty=False`, `dirty_state.is_dirty=True`, `is_unknown=False`) |
| `test_linked_worktree_and_commondir_return_unknown_without_invoking_git` | `git worktree add` scanned as root; contained `.git/commondir` | Both `is_unknown=True` without invoking `git` (`gitdir_outside_root` for external gitfile, `git_commondir` for contained `commondir`) | **PASS** |
| `test_worktree_config_returns_unknown_without_invoking_git` | `.git/config.worktree` and `extensions.worktreeConfig=true` | `is_unknown=True`, `reason="git_worktree_config"`, `git` not invoked | **PASS** (`reason == "git_worktree_config"`) |
| `test_environment_injection_scrubbed_and_ceiling_set` | `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_INDEX_FILE`, `GIT_NAMESPACE` set in parent | Child env omits all 7 vars; `GIT_CEILING_DIRECTORIES == <parent_of_root>` | **PASS** |
| `test_unsupported_git_version_returns_unknown_and_caches_probe` | Monkeypatched `_probe_git_version` returning `(2, 34, 1)` | `is_unknown=True`, `reason="git_version_unsupported"`, `git status` not invoked, probe called once, 1 warning logged | **PASS** (`probe_calls == 1`, `len(warnings) == 1`) |
| `test_real_git_version_probe_meets_minimum` | Real `git --version` on host | `get_git_version() >= (2, 41, 0)` | **PASS** (`(2, 54, 0)` on Windows 11) |
| `test_collector_records_containment_reason_in_partial_reasons` | `PpmCollector.scan_source` on repo with `alternates` | `status == "partial"`, `git_dirty=True`, `"git_alternates" in partial_reasons` | **PASS** on PostgreSQL 18.6 |
| `test_iss030_regression_clean_filter_still_not_executed` | ISS-030 clean filter attack under ISS-031 hardening | Filter never executes (`marker` absent) | **PASS** |

---

## 5. Verification Suite Results (Real Observed Output)

```powershell
# 1. Package validation
uv run python tools/validate_package.py
# PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed

# 2. Negative cases
uv run python ppm-v2/tools/run_negative_cases.py
# PASS 5 templates, 20 cases

# 3. Translation check
uv run python tools/check_translation.py
# SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.

# 4. SC-06 Database Schema Lifecycle (PostgreSQL 18.6)
uv run python tools/run_sc06.py
# SC-06 Verification: ALL CYCLES PASSED (0004_snapshots head)

# 5. Lint and Typecheck
uv run ruff check src tests ppm-v2/reducer migrations
# All checks passed!
uv run mypy src tests ppm-v2/reducer migrations
# Success: no issues found in 144 source files

# 6. Web HUD Build
npm --prefix web/hud ci; npm --prefix web/hud run build
# built in 436ms (dist/assets/index-DLYonLHq.js 225.08 kB)

# 7. Full Pytest Suite (CI=true, TEST_DATABASE_URL on PostgreSQL 18.6)
uv run pytest -q
# 578 passed, 3 warnings in 172.97s (0:02:52)
```

---

## 6. Residual Risks
- **TOCTOU between handle-based gitdir check and `git` subprocess**: Because `git` is an external CLI taking path arguments (`--git-dir`, `--work-tree`), a local attacker with write access to the repository directory could swap `.git` or a subdirectory with a symlink/junction between `_resolve_gitdir()` and `subprocess.run(["git", ...])`.
  - **Mitigation**: `PpmCollector.scan_source` verifies the root directory's physical filesystem identity (`get_fs_identity` via Win32 volume serial + file index / Linux `st_dev:st_ino`, raising `RootReplacedSecurityError` on mismatch) before scanning, and `git` is run with `--attr-source=<empty_tree>`, all discovered filters/diff drivers overridden, hooks/fsmonitor disabled, and `GIT_CEILING_DIRECTORIES` set to the root's parent.
- **Symlink creation on unprivileged Windows (`ISS-026`)**: Unchanged from `P2a-WP2`.
