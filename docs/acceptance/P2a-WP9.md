# Milestone acceptance — P2a-WP9

## 1. Milestone and scope
Work Package **P2a-WP9** ("Verifier (sandbox, fixtures only)") replaces D0's simulated verifier loader with real `verifier_observed` execution for fixture repositories, implementing ADR-023 / `PPM-01` and conformance case **C40**.

Implemented scope:
1. **Sandboxed Verifier (`src/eap/ppm/verifier.py`)**:
   - `run_verifier(conn, tenant_id, project_id, assessed_revision, baseline_revision=None, *, requested_test_ids=None, timeout_seconds=15.0, max_output_bytes=65_536, settings=None) -> VerifierReport`.
   - Exports `assessed_revision` read-only via `export_git_archive_tar` into an isolated temp workspace outside the registered root, extracts only regular files and directories (`_extract_tar_safely`), and deletes the temp root in `finally` (`workspace_removed`).
   - Runs strictly test IDs mapped in the approved baseline using allowlisted argv (`[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", <nodeid>]`, `shell=False`, validated by `validate_nodeid` / `validate_allowed_argv`). Unmapped test IDs return `outcome="test_not_mapped"`, `executed=False`, and are never executed.
   - Scrubs parent environment variables (secrets and proxies removed; `HOME`, `USERPROFILE`, `TMPDIR`, `TEMP`, `TMP` redirected to sandbox temp directories; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`).
   - Enforces per-test wall-clock timeout and read cap (`max_output_bytes`). Maps timeout, syntax/import collection failures, and missing tests to `outcome="inconclusive"` (`reason="timeout" | "collection_error" | "test_missing"`), never `passed`.
   - Never reads repository-written JUnit XML or report files as results.
   - Records `network_isolation="enforced"` only when OS-level Linux `unshare -n` / `-rn` probe succeeds, otherwise records `"not_enforced"` honestly.
   - Persists one `ppm_evidence` row per mapped `test_id` (`trust_class="verifier_observed"`, `producer="eap-verifier"`, `simulated=False`) idempotently on `(tenant_id, evidence_id)`.
2. **Hardened archive export (`src/eap/ppm/collector/git.py`)**:
   - Extracted shared `_prepare_neutralized_git_exec` helper preserving `ISS-030` / `ISS-031` containment checks (`_resolve_gitdir`, `_check_gitdir_containment_metadata`, `MIN_GIT_VERSION >= 2.41.0`, scrubbed git env, `--attr-source=<empty_tree>`).
   - Added `export_git_archive_tar` and `GitArchiveContainmentError`.
3. **Database schema & Assessment integration (`src/eap/db/assessment_tables.py`, `migrations/versions/0007_verifier_evidence.py`, `src/eap/ppm/assessments.py`)**:
   - Reversible Alembic migration `0007_verifier_evidence` adding nullable columns `exit_status`, `duration_ms`, `network_isolation`, and `reason` plus `ck_ppm_evidence_duration_nonneg` and `ck_ppm_evidence_network_isolation` to `ppm_evidence` (0 new FKs; preserves 18 tenant-scoped composite FKs).
   - Updated `load_fixture_evidence` and `get_assessment_detail` to persist and expose `output_hash`, `exit_status`, `duration_ms`, `network_isolation`, and `reason`.
4. **Feature flag & fixtures-only boundary (`src/eap/config.py`, `.env.example`)**:
   - Added `PPM_VERIFIER_ENABLED` (default `False` / `false`). When off, no workspace checkout or execution occurs and criteria stay claims (rollback path).
   - Refuses `EAP_COLLECTOR_ALLOW_REAL_ROOTS=true` (`VerifierRealRootRefusedError`) and roots outside `EAP_COLLECTOR_FIXTURES_ROOT` (`RootNotPermittedSecurityError`).
5. **CLI and D0 integration (`src/eap/ppm/cli.py`, `scripts/demo_d0.ps1`, `scripts/demo_d0.sh`)**:
   - Added `python -m eap.ppm.cli verify --tenant --project --revision [--baseline-revision] [--timeout]`.
   - Added `--real-verifier` (`demo_d0.sh`, LF verified) / `-RealVerifier` (`demo_d0.ps1`) option while retaining the default simulated D0 path.
6. **Tests & Conformance (`tests/ppm/test_verifier_sandbox.py`, `tests/ppm/test_demo_d0_smoke.py`, `tests/test_config.py`, `artifacts/conformance/P2a/C40.json`)**.

## 2. Source commit and environment pins
- Base commit: `d2ac95f` (`main`)
- Branch: `wp/P2a-WP9`
- Python: `3.12.13` (`uv` workspace, `pytest 9.2.0`)
- Database: PostgreSQL `18.6-trixie` (isolated container on `127.0.0.1:5434/eap_test` during verification)
- Git: `2.54.0.windows.1` (local)
- Node/UI: Vite `6.4.5`, Vitest `5.1.0`
- Mode: fixtures only (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`), zero model calls, zero external network access.

## 3. Attack / Failure Matrix (Expected -> Observed)

| Case / Vector | Attack or failure scenario | Expected | Observed | Evidence test |
|---|---|---|---|---|
| **C40** forged JUnit report | Agent writes `reports/ac001-junit.xml` & `reports/ac003-junit.xml` claiming PASS while `src/export.py` fails `test_ac001_export` | JUnit XML stays `reported_artifact`; real verifier executes `test_ac001_export` -> `failed`; `AC-001` `contradicted`; `AC-003` (XML only) stays `unverified` (`reported_artifact_only`) | PASS — `AC-001` `contradicted`, `AC-003` `unverified` (`reported_artifact_only`), XML rows remain `reported_artifact`, verifier rows carry `eap-verifier` & `simulated=False` | `test_C40_in_repo_junit_pass_cannot_override_failed_real_verifier_execution`, `test_demo_d0_script_real_verifier_mode` |
| Revision regression (`r1` PASS -> `r2` FAIL) | Code passes `test_ac001_export` at `rev-r1` then breaks at `rev-r2` | Two distinct `verifier_observed` `ppm_evidence` rows; `rev-r2` assessment `contradicted` (`rev-r1` PASS excluded as `revision_mismatch`) | PASS — 2 rows created; `AC-001` flips `verified` -> `contradicted`, `rev-r1` PASS excluded (`revision_mismatch`) | `test_verifier_pass_at_r1_fail_at_r2_creates_two_rows_and_contradicts_r2` |
| Network socket attempt | Test opens AF_INET socket (`s.bind(("127.0.0.1", 0))`) | Fails/blocked when Linux `unshare` enforces `network_isolation="enforced"`; otherwise records honest `network_isolation="not_enforced"` | PASS — Windows recorded `network_isolation="not_enforced"` honestly in report & `ppm_evidence`; simulated `enforced` wrapper failed test with `network_isolation="enforced"` (Linux `unshare` exercised in CI) | `test_verifier_network_socket_isolation_or_not_enforced_flag` |
| Infinite hang / timeout | Test sleeps past `timeout_seconds` | Process killed on timeout; `outcome="inconclusive"`, `reason="timeout"`, `exit_status=None`; never `passed`; criterion remains `unverified` | PASS — killed promptly, `outcome="inconclusive"`, `reason="timeout"`, `exit_status=None`, criterion `unverified` | `test_verifier_timeout_is_inconclusive_never_passed` |
| Collection / syntax / missing test | Test file raises on import, has syntax error, or mapped `test_id` does not exist | All map to `outcome="inconclusive"` with `reason="collection_error"` or `"test_missing"`; never `passed` | PASS — `test_import_err` (`collection_error`), `test_syntax_err` (`collection_error`), `test_does_not_exist` (`test_missing`) | `test_verifier_collection_error_and_missing_test_inconclusive` |
| Unmapped test ID | `.ppm/updates` or `requested_test_ids` references `test_unmapped_side_effect` outside approved baseline | Never executed (canary file untouched); reported with `outcome="test_not_mapped"`, `executed=False`, 0 evidence rows | PASS — `canary_path.exists() is False`, `test_not_mapped` returned | `test_verifier_unmapped_test_id_not_executed` |
| `../../secret` traversal & dirty root | Test probes `../../secret` and attempts writes in cwd | Executed inside isolated `git archive` temp workspace copy; cannot reach registered root; repo stays clean; temp root removed in `finally` | PASS — `workspace_removed=True`, registered root `git status --porcelain == ""`, test passed without escaping | `test_verifier_workspace_isolation_blocks_parent_traversal_and_protects_root` |
| Parent secrets & proxy leakage | Parent env has `EAP_SECRET_CANARY`, `DATABASE_URL`, `OPENAI_API_KEY`, `HTTP_PROXY`, `HTTPS_PROXY` | Stripped from child environment; `HOME` points inside `eap_verifier_sandbox_` temp dir | PASS — zero leaked parent keys observed in child test; `HOME` isolated | `test_verifier_scrubbed_environment_hides_parent_secrets` |
| Output size bomb | Noisy failing test prints `"NOISE_" * 20000` with `max_output_bytes=128` vs `262144` | Read buffer bounded by `max_output_bytes`, deterministic 64-hex SHA-256 `output_hash` stored, no hang/OOM | PASS — bounded hash differs cleanly from 256 KiB run, `exit_status=1` stored | `test_verifier_caps_large_output` |
| Rollback flag (`PPM_VERIFIER_ENABLED=false`) | Default disabled configuration with existing claims | Verifier skips execution (`skipped_reason="verifier_disabled"`), 0 evidence rows written, criteria remain `unverified` claims | PASS — canary untouched, 0 rows, criterion stays `reported_status="claimed_done"` & `status="unverified"` | `test_verifier_flag_off_real_roots_guard_allowlist_and_cli` |
| Real-root guard (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=true` or outside root) | Invocation against real repo or outside `EAP_COLLECTOR_FIXTURES_ROOT` | Refused before any git export or test spawn | PASS — `VerifierRealRootRefusedError` / `RootNotPermittedSecurityError` raised | `test_verifier_flag_off_real_roots_guard_allowlist_and_cli` |
| Allowlist injection | `-k test_x`, `--rootdir=/tmp`, `../escape.py`, `a;rm -rf /`, `""`, or `bash -c` argv | Rejected by `build_allowed_verifier_argv` / `validate_allowed_argv` | PASS — `VerifierAllowlistError` raised on all bad nodeids and non-allowlisted argv | `test_verifier_flag_off_real_roots_guard_allowlist_and_cli` |

## 4. Isolation guarantees: Windows vs Linux

| Isolation guarantee | Windows (local host) | Linux (CI / target runtime) | Enforcement mechanism |
|---|---|---|---|
| Separate read-only revision workspace (`git archive`) | **Enforced** | **Enforced** | `export_git_archive_tar` + `_extract_tar_safely` (regular files/dirs only; skips symlinks/device nodes/traversal); `finally` cleanup (`shutil.rmtree`) |
| Registered-root & git containment (`ISS-030` / `ISS-031`) | **Enforced** | **Enforced** | Handle/containment checks before `git`, `GIT_CEILING_DIRECTORIES`, `--attr-source=<empty_tree>`, neutralized filters/hooks/fsmonitor |
| Allowlisted non-shell execution (`shell=False`) | **Enforced** | **Enforced** | `python -m pytest -q -p no:cacheprovider <nodeid>`, `validate_nodeid` + `validate_allowed_argv`, mapped baseline IDs only |
| Environment & secret scrubbing | **Enforced** | **Enforced** | Fresh minimal env dict (`HOME`, `USERPROFILE`, `TMPDIR`/`TEMP`/`TMP` in sandbox temp); zero parent secrets or proxy vars |
| Wall-clock timeout & captured output cap | **Enforced** | **Enforced** | `proc.wait(timeout=timeout_seconds)` -> `proc.kill()`; `read(max_output_bytes)` before SHA-256 |
| Network namespace isolation | **`not_enforced`** (recorded honestly on evidence row & report) | **`enforced` when `unshare -n` or `-rn` permitted**; else `not_enforced` | Runtime probe `_detect_network_isolation()`; never claims `enforced` unless probe exits 0 |
| Container / VM / low-privilege OS sandbox (`D-07`) | **Not implemented** (deferred to D-07) | **Not implemented** (deferred to D-07) | Guarded by `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false` + `EAP_COLLECTOR_FIXTURES_ROOT` |

## 5. Executed checks and real output (local)
All checks below executed after final code/test edits against isolated PostgreSQL `18.6-trixie` (`CI=true`, `TEST_DATABASE_URL=postgresql+psycopg://eap:***@127.0.0.1:5434/eap_test`):
- `uv run pytest -v tests/ppm/test_verifier_sandbox.py tests/ppm/test_demo_d0_smoke.py` -> **12 passed, 2 warnings in 55.02s**
- `uv run python tools/run_sc06.py` -> **SC-06 Verification: ALL CYCLES PASSED** (empty up/down/up + seeded down/up, head `0007_verifier_evidence`)
- `uv run pytest -q` -> **635 passed, 5 warnings in 218.44s (0:03:38)**
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**
- `uv run python ppm-v2/tools/check_fixtures.py` -> **PASS 23 fixtures, 3 rollup**
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> **PASS 23 fixtures**
- `uv run python tools/check_translation.py` (working copy + temporary `git clone`) -> **SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.**
- `uv run ruff check src tests ppm-v2/reducer migrations tools` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer migrations tools` -> **Success: no issues found in 170 source files**
- `npm --prefix web/hud test` -> **Test Files 1 passed (1) / Tests 2 passed (2)**
- `npm --prefix web/hud run build` -> **built in 407ms**
- `scripts/demo_d0.sh` line ending check -> **LF (13953 bytes, zero CRLF)**
- `sha256sum -c SHA256SUMS.txt` -> **PASS** (527 tracked files verified, LF line endings)
- GitHub Actions CI (`wp/P2a-WP9`) -> pending push

## 6. Residual risks, blockers, and untested controls
- **D-07 / `ISS-002` still open**: Full OS container/VM/low-privilege user sandbox for arbitrary untrusted repositories is not yet selected or built. Real repositories remain blocked (`VerifierRealRootRefusedError` / `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`) until **DG-3** + **D-07**.
- **Windows network isolation**: Local Windows runs record `network_isolation=not_enforced` because Windows lacks `unshare -n`; tests remain able to bind/open sockets locally on Windows unless blocked by D-07 sandbox containerization.
- **Process/disk-write/CPU quota limits**: Windows/Linux per-test disk quota and subprocess count (`RLIMIT_NPROC` / Job Object CPU/memory caps) belong to D-07 hardened sandbox execution; currently wall-clock timeout kills the spawned direct child process (`proc.kill()`), not OS process trees if a test intentionally forks detached descendants on Windows.

## 7. Go/no-go decision and rollback
- **Status**: `ready_for_review` (pending owner review; branch `wp/P2a-WP9`).
- **Rollback procedure**:
  1. Set `PPM_VERIFIER_ENABLED=false` (default): `run_verifier` exits immediately (`skipped_reason="verifier_disabled"`), leaving criteria as claims with zero verifier execution.
  2. Schema rollback: `uv run alembic downgrade 0006_assessments` drops `exit_status`, `duration_ms`, `network_isolation`, and `reason` columns and constraints cleanly (verified in SC-06).
