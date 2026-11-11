# Acceptance Packet: P2a-WP2 Collector (read-only)

## 1. Milestone and Scope
- **Work Package**: P2a-WP2 (Collector read-only per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P2a-WP2, `docs/04_PPM_INTEGRATION.md`, `docs/adr/DG-2.md` §3 and §3.2, `contracts/location-registration.schema.json`, and `evals/CONFORMANCE_CASES.md` C16, C27, C42).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P2a-WP2` (branched from `main` at `ee28a86`)
- **Base Commit**: `ee28a86`
- **Parallel Work Packages**: `P0-WP3b` (`docs/spikes/**`, `spikes/**`).
- **Safety Boundary Enforcements**:
  - Read-only: never writes into location roots; never executes code from repositories.
  - `EAP_COLLECTOR_ALLOW_REAL_ROOTS` (default `false`): when false, collector strictly refuses any root outside the OS temp directory or configured fixtures root (`RootNotPermittedSecurityError`).
  - Sources in state `pending_share` or whose share was revoked are unreadable (`PermissionError` per P2a-WP1 L3 owner decision).
- **Contract and Issue Governance**:
  - Contracts (`contracts/**`) and examples (`examples/**`) were preserved without modification.
  - Resolved `ISS-027`: `safe_open` handle-first containment on Windows ctypes and Linux openat2 / procfs fallback, single handle close safety verified.
  - Resolved `ISS-028`: Physical root filesystem identity derived from open directory handle (Windows volume serial + file index; Linux st_dev + st_ino); root replacement between scans detected and refused.
  - Logged `ISS-029` in `docs/ISSUES.md` (label `contract`): `contracts/event.schema.json` event_type enum lacks collector event type `ppm.snapshot.captured` (handled via outbox per ISS-025 pattern).
  - Resolved `ISS-030` (Critical Finding C2-1): Arbitrary command execution from untrusted git repository configurations during `git status` fully neutralized across 9 attack vectors (clean filter, process filter, .git/info/attributes, include, includeIf, submodules, fsmonitor, hooks, textconv/diff).
  - Next free issue ID is `ISS-031`.

---

## 2. Architecture and Security Implementation

### 2.1 safe_open Handle-First Containment (`src/eap/ppm/collector/safe_open.py`)
- **Windows Implementation (ctypes)**:
  - Opens handle with `CreateFileW` specifying `FILE_FLAG_OPEN_REPARSE_POINT` and `FILE_FLAG_BACKUP_SEMANTICS`.
  - Rejects reparse points (`FILE_ATTRIBUTE_REPARSE_POINT`) and directories (`FILE_ATTRIBUTE_DIRECTORY`).
  - Verifies file type is disk file (`GetFileType` == `FILE_TYPE_DISK`).
  - Queries true path via `GetFinalPathNameByHandleW(VOLUME_NAME_DOS | FILE_NAME_NORMALIZED)` and verifies case-folded containment under the canonical root.
  - Checks link count via `GetFileInformationByHandle`: if `allow_hardlinked_files=False` and `nNumberOfLinks > 1`, rejects with `HardlinkNotAllowedError`.
  - Enforces `max_file_bytes` limit against `(nFileSizeHigh << 32) | nFileSizeLow`.
  - Handle ownership transfer: uses `msvcrt.open_osfhandle(handle, os.O_RDONLY)` and wraps in Python file object; on success the C runtime owns the handle, closing it exactly once when the file object exits context. On any failure before transfer, `kernel32.CloseHandle(handle)` is called in `finally`. Verified in `tests/collector/test_safe_open.py::test_iss027_handle_closed_exactly_once`.
- **Linux Implementation (openat2 / procfs fallback)**:
  - Uses `openat2` syscall with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS` where available (kernel ≥ 5.6).
  - Robust fallback: path traversal checking with `O_NOFOLLOW` per component followed by `/proc/self/fd/{fd}` canonical path containment verification.
  - Checks `st_nlink == 1` and `st_size <= max_file_bytes`.

### 2.2 Physical Filesystem Identity (`src/eap/ppm/collector/identity.py`)
- Opens handle to canonical root directory with backup semantics.
- **Windows**: Extracts `dwVolumeSerialNumber` and 64-bit `(nFileIndexHigh << 32) | nFileIndexLow` -> `win:<vol>:<index>`.
- **Linux**: Queries `st_dev:st_ino`.
- On first scan of a source, persists physical identity in `source_physical_identities` table.
- On subsequent scans, verifies current physical identity matches recorded identity; directory swaps/moves raise `RootReplacedSecurityError` and refuse scan.

### 2.3 Safe Git Execution & Command Neutralization (`src/eap/ppm/collector/git.py`)
- **Direct safe_open HEAD Parsing**: Reads `.git/HEAD` and symbolic refs (loose or `.git/packed-refs`) directly without subprocess invocation.
- **Scrubbed Environment**:
  - `GIT_CONFIG_NOSYSTEM = "1"`, `GIT_ATTR_NOSYSTEM = "1"`
  - `GIT_CONFIG_GLOBAL = <empty_temp_file>`, `GIT_CONFIG_SYSTEM = <empty_temp_file>`
  - `HOME = <empty_temp_dir>`, `USERPROFILE = <empty_temp_dir>`
  - `GIT_TERMINAL_PROMPT = "0"`
- **Arbitrary Command Execution Neutralization (C2-1 / ISS-030)**:
  - Top-level `--attr-source=<empty_tree>` (SHA-1 `4b825dc...` or SHA-256 `6ef19b4...` based on `objectformat` detection), neutralizing worktree `.gitattributes` anywhere in the tree.
  - Automatic discovery of all declared filters (`filter.<name>.*`) and diff drivers (`diff.<name>.*`) across `.git/config`, `.git/info/attributes`, root `.gitattributes`, submodule configs in `.git/modules/**`, and `git config --list`.
  - Overrides every discovered filter: `-c filter.<name>.clean=`, `-c filter.<name>.smudge=`, `-c filter.<name>.process=`, `-c filter.<name>.required=false`.
  - Overrides every discovered diff driver: `-c diff.<driver>.textconv=`, `-c diff.<driver>.command=`.
  - Blocks submodule recursion and execution: `--ignore-submodules=all`, `-c submodule.recurse=false`, `-c status.submodulesummary=false`.
  - Blocks fsmonitor and hooks: `-c core.fsmonitor=false`, `-c core.hooksPath=<empty_temp_dir>`, `-c core.untrackedCache=false`, `-c protocol.allow=never`, `-c core.attributesFile=`.
  - Uncertainty Principle: Any failure or uncertainty in determining git status marks `is_dirty=True` and `is_unknown=True`, producing a partial snapshot with reason `git_state_unknown`. Non-git repositories fallback cleanly to file manifests via `safe_open`.

### 2.4 Safe Parsers (`src/eap/ppm/collector/parser.py`)
- Safe YAML loading via `yaml.SafeLoader` with customized constructor that raises `DuplicateKeyError` on duplicate dictionary keys.
- Safe JSON loading with `object_pairs_hook` rejecting duplicate keys.
- Depth limits (max 50) and file byte limits strictly enforced.
- Prompt injection text kept as verbatim literal string data without interpretation.

### 2.5 Scanning, Consistency, and Quarantining (`src/eap/ppm/collector/collector.py`)
- **Scan Segmentation**: Scans candidate files scoped by `scan_scope` + `metadata_root`, filtering out `deny_globs`.
- **C16 Consistency Check**: Records per-file `(mtime, size)` and git status before scanning. Re-checks all files and git status post-scan. Any discrepancy marks `mixed_snapshot = True`, `status = "partial"`, with explicit reasons.
- **C42 Repository Identity Conflict**: When `.ppm/project.yaml` claims a `project_id` already associated with another source having a different `repo_identity`:
  - Location state is updated to `suspended` (quarantined).
  - `identity_conflicts` row is inserted.
  - `ppm.identity_conflict.detected` event is written to `outbox`.
  - Project scope is excluded from snapshot results; nothing merged.
- **Persistence & Monotonic Sequences**:
  - Migration `0004_snapshots.py` adds `source_physical_identities`, `collector_snapshots`, `project_snapshot_sequences`, and `project_snapshots`.
  - Composite tenant-scoped primary/unique keys adhering to DG-1 Amendment A1.
  - Commits snapshot and atomically increments monotonic `snapshot_seq` per `(source_id, project_id)`.
  - Emits `ppm.snapshot.captured` outbox event in the same database transaction.

---

## 3. Conformance and Acceptance Matrix

| Case / Requirement | Scope & Verification | Status | Real Observed Output & Evidence |
|---|---|---|---|
| **C16: Mid-scan Race Condition** | File modified during scan via concurrent racing thread | **PASS** | `tests/collector/test_collector_conformance.py::test_c16_racing_modification_causes_mixed_snapshot`<br>Observed: `mixed_snapshot=True`, `status='partial'`, reason: `file_modified_during_scan: docs/ARCHITECTURE.md`, persisted in DB. |
| **C27: Security Findings** | Hardlink, symlink, parent traversal (`..`), ADS escape attempts in scan scope | **PASS** | `tests/collector/test_collector_conformance.py::test_c27_security_findings_produce_partial_scan`<br>Observed: Hardlink escape logged as security finding, omitted from manifest, `status='partial'`. |
| **C42: Conflicting Repo Identity** | Duplicate project_id claimed on distinct repo identity | **PASS** | `tests/collector/test_collector_conformance.py::test_c42_conflicting_repo_identity_quarantines_location`<br>Observed: Scope quarantined, location `state='suspended'`, `identity_conflicts` row recorded, `ppm.identity_conflict.detected` outbox event emitted. |
| **P0-WP3 V01–V12 Matrix** | Full path containment attack regression matrix | **PASS** | `tests/collector/test_safe_open.py` (15/15 PASS): V01 symlink, V02 junction outside, V03 junction chain, V04 hardlink, V05 NTFS ADS, V06a short name escape, V06b short name inside, V07 UNC/NT prefixes, V08 parent traversal, V09 case sensitivity, V10 TOCTOU directory swap, V11 directory as file, V12 long paths (>260 chars). |
| **ISS-027: Handle Ownership** | Handle closed exactly once upon context exit | **PASS** | `tests/collector/test_safe_open.py::test_iss027_handle_closed_exactly_once`<br>Observed: File handle closed exactly once; target file writable immediately after with no lock leaks. |
| **ISS-028: Physical Identity** | Moved or swapped root directory between scans refused | **PASS** | `tests/collector/test_collector_conformance.py::test_root_replaced_between_scans_refused`<br>Observed: `RootReplacedSecurityError: Root physical identity changed for source... Refusing scan.` |
| **Git Execution Safety & Neutralization (C2-1 / ISS-030)** | Complete 9-vector attack regression suite neutralizing arbitrary command execution | **PASS** | `tests/collector/test_git_execution.py` (12/12 PASS):<br>1. Clean filter via `.gitattributes`: PASS (0 marker)<br>2. Process filter via `.gitattributes`: PASS (0 marker)<br>3. Attributes in `.git/info/attributes`: PASS (0 marker)<br>4. Filter via `[include] path=other.cfg`: PASS (0 marker)<br>5. Filter via `[includeIf "gitdir:..."]`: PASS (0 marker)<br>6. Submodule filter in own config: PASS (0 marker)<br>7. `core.fsmonitor`: PASS (0 marker)<br>8. `core.hooksPath`: PASS (0 marker)<br>9. `textconv` / diff driver: PASS (0 marker)<br>10. Clean/dirty detection: PASS<br>11. Corrupt/uncertain config -> dirty/unknown: PASS<br>12. Non-git fallback: PASS. |
| **10,000-File Throughput** | Benchmark on 10,000 files across 100 directories | **PASS** | `tests/collector/test_collector_conformance.py::test_10000_file_throughput`<br>Observed: `Scanned 10004 files in 52.776 s (189.6 files/s)`. |
| **Safety Boundary Root Gate** | Collector refuses roots outside OS temp when `allow_real_roots=False` | **PASS** | `tests/collector/test_collector_conformance.py::test_safety_boundary_refuses_root_outside_temp`<br>Observed: `RootNotPermittedSecurityError` raised. |
| **Share Permissions Gate** | `pending_share` and revoked shares unreadable | **PASS** | `tests/collector/test_collector_conformance.py::test_pending_and_revoked_shares_are_unreadable`<br>Observed: `PermissionError` raised for pending and revoked shares. |
| **Graceful Partial Scans** | Vanishing files, oversized files, duplicate YAML keys, deny globs | **PASS** | `test_path_vanishing_mid_scan_handled_gracefully` (PASS)<br>`test_oversized_file_handled_gracefully` (PASS)<br>`test_duplicate_yaml_keys_produces_partial_scan` (PASS)<br>`test_deny_globs_excludes_files` (PASS). |
| **Monotonic Sequences** | Monotonic incrementing `snapshot_seq` per `(source_id, project_id)` | **PASS** | `test_monotonic_snapshot_seq_per_project` (PASS): sequences 1, 2, 3 verified. |

---

## 4. Verification Suite Results

```powershell
# 1. Package validation
uv run python tools/validate_package.py
# Result: PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed

# 2. Negative cases
uv run python ppm-v2/tools/run_negative_cases.py
# Result: PASS 5 templates, 20 cases

# 3. Translation check
uv run python tools/check_translation.py
# Result: 38 up-to-date, 0 stale, 0 missing headers

# 4. SC-06 Database Schema Lifecycle (Empty & Seeded Cycles on PostgreSQL 18.6)
uv run python tools/run_sc06.py
# Result:
# [Cycle 1] upgrade head -> downgrade base -> upgrade head (empty DB) PASS
# [Cycle 2] upgrade head -> seed fixture -> downgrade base -> upgrade head (post-seeded DB) PASS
# ALL CYCLES PASSED (0004_snapshots head)

# 5. Type and Style Checks
uv run ruff check src tests ppm-v2/reducer
# Result: All checks passed!
uv run mypy src tests ppm-v2/reducer
# Result: Success: no issues found in 136 source files

# 6. Web HUD Build
npm --prefix web/hud run build
# Result: built in 582ms (dist/assets/index-DLYonLHq.js 225.08 kB)

# 7. Pytest Full Suite on PostgreSQL 18.6
uv run pytest -q
# Result: 566 passed, 3 warnings in 160.83s

# 8. GitHub Actions Continuous Integration (Run 43048089136)
# URL: https://github.com/xhuyvn/EAM_HUB/actions/runs/43048089136
# Result: ALL 18 STEPS PASSED (validate-and-test in 3m4s)
```

---

## 5. Unresolved Gaps and Risks
- **Symlink creation on unprivileged Windows (`ISS-026`)**: Creation of symlinks requires Developer Mode or Administrator privileges on Windows. The test skips symlink creation if unprivileged, but containment verification is fully exercised via junctions (V02, V03) and on Linux.
- **Interim Outbox Event Type (`ISS-029`)**: `contracts/event.schema.json` lacks `ppm.snapshot.captured`. Handled via outbox table storage per ISS-025 pattern without modifying schema.

---

## 6. Recommendation
- **Verdict**: `ready_for_review`
- **Owner Action**: Review acceptance packet, merge branch `wp/P2a-WP2`, and proceed to `P2a-WP3` (Ingest `.ppm` and notes).
