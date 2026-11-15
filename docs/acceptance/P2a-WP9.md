# Milestone acceptance — P2a-WP9

## 1. Milestone and scope
Work Package **P2a-WP9** ("Verifier (sandbox, fixtures only)") replaces D0's simulated verifier loader with real `verifier_observed` execution for fixture repositories, implementing ADR-023 / `PPM-01` and conformance case **C40**.

Implemented scope:
1. **Sandboxed Verifier (`src/eap/ppm/verifier.py`)**:
   - `run_verifier(conn, tenant_id, project_id, assessed_revision, baseline_revision=None, *, requested_test_ids=None, timeout_seconds=15.0, max_output_bytes=65_536, settings=None) -> VerifierReport`.
   - Exports `assessed_revision` read-only via `export_git_archive_tar` into an isolated temp workspace outside the registered root, extracts only regular files and directories (`_extract_tar_safely`), and deletes the temp root in `finally` (`workspace_removed`).
   - Runs strictly test IDs mapped in the approved baseline using allowlisted argv (`[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--junitxml=<out_dir>/<hash>.xml", <nodeid>]`, `shell=False`, validated by `validate_nodeid` / `validate_allowed_argv`). Unmapped test IDs return `outcome="test_not_mapped"`, `executed=False`, and are never executed.
   - Scrubs parent environment variables (secrets and proxies removed; `HOME`, `USERPROFILE`, `TMPDIR`, `TEMP`, `TMP` redirected to sandbox temp directories; `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`).
   - Enforces per-test wall-clock timeout with process-tree cleanup (Linux `unshare -rn --pid --fork --kill-child --mount-proc` with fallback chain + `start_new_session=True` / `os.killpg`; Windows Win32 `ctypes` Job Object `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `TerminateJobObject`) and streams stdout/stderr through a pipe capping disk writes and hash input at `max_output_bytes` while draining excess bytes (`output_truncated`, `truncated_bytes`).
   - Requires `exit_status == 0` AND a safely parsed out-of-workspace `--junitxml` report (stdlib `xml.parsers.expat` with DTD/entity/external-ref expansion disabled) containing exactly one `<testcase>` matching the resolved nodeid with zero `<failure>`, `<error>`, or `<skipped>` children; otherwise records `outcome="inconclusive"` (`no_result_record`, `nodeid_not_executed`, `result_mismatch`).
   - Records `conftest.py` and pytest config files (`pytest.ini`, `.pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`) along the test path in `test_config_files=[{"path", "sha256"}]` for future `ISS-041` pinning.
   - Never reads repository-written JUnit XML or report files inside the workspace as results.
   - Records `network_isolation="enforced"` only when OS-level Linux `unshare` probe succeeds, otherwise records `"not_enforced"` honestly.
   - Persists one `ppm_evidence` row per mapped `test_id` (`trust_class="verifier_observed"`, `producer="eap-verifier"`, `simulated=False`) idempotently on `(tenant_id, evidence_id)`.
2. **Hardened archive export (`src/eap/ppm/collector/git.py`)**:
   - Extracted shared `_prepare_neutralized_git_exec` helper preserving `ISS-030` / `ISS-031` containment checks (`_resolve_gitdir`, `_check_gitdir_containment_metadata`, `MIN_GIT_VERSION >= 2.41.0`, scrubbed git env, `--attr-source=<empty_tree>`).
   - Added `export_git_archive_tar` and `GitArchiveContainmentError`.
3. **Database schema & Assessment integration (`src/eap/db/assessment_tables.py`, `migrations/versions/0007_verifier_evidence.py`, `src/eap/ppm/assessments.py`)**:
   - Reversible Alembic migration `0007_verifier_evidence` adding `exit_status`, `duration_ms`, `network_isolation`, `reason`, `output_truncated`, `truncated_bytes`, and `test_config_files` plus `ck_ppm_evidence_duration_nonneg`, `ck_ppm_evidence_network_isolation`, and `ck_ppm_evidence_truncated_bytes_nonneg` to `ppm_evidence` (0 new FKs; preserves 18 tenant-scoped composite FKs).
   - Updated `load_fixture_evidence` and `get_assessment_detail` to persist and expose `output_hash`, `exit_status`, `duration_ms`, `network_isolation`, `reason`, `output_truncated`, `truncated_bytes`, and `test_config_files`.
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
| Network socket attempt | Test opens TCP connection (`socket.create_connection(("127.0.0.1", port))`) to a real host listener | Fails/blocked when Linux `unshare` enforces `network_isolation="enforced"`; otherwise records honest `network_isolation="not_enforced"` without asserting outcome | PASS — Windows recorded `network_isolation="not_enforced"` (`VERIFIER_CI_NET_ISOLATION: platform=win32 mode=not_enforced outcome=passed exit_status=0`); local Linux container with userns `unshare -rn --pid --fork --kill-child --mount-proc` recorded `network_isolation="enforced"` (`mode=enforced outcome=failed exit_status=1`); deterministic socket blocker wrapper passed | `test_verifier_network_socket_isolation_or_not_enforced_flag` |
| Infinite hang / timeout | Test sleeps past `timeout_seconds` or spawns `start_new_session=True` detached grandchildren | Process tree killed on timeout; `outcome="inconclusive"`, `reason="timeout"`, `exit_status=None`; 5s delayed grandchild never writes marker | PASS — killed promptly on both Windows (Win32 Job Object) and Linux container (`unshare -rn --pid --fork --kill-child --mount-proc`); `timeout + 6s` marker absent on both platforms | `test_verifier_timeout_is_inconclusive_never_passed`, `test_verifier_timeout_kills_detached_grandchild_process` |
| Collection / syntax / missing test | Test file raises on import, has syntax error, or mapped `test_id` does not exist | All map to `outcome="inconclusive"` with `reason="collection_error"` or `"test_missing"`; never `passed` | PASS — `test_import_err` (`collection_error`), `test_syntax_err` (`collection_error`), `test_does_not_exist` (`test_missing`) | `test_verifier_collection_error_and_missing_test_inconclusive` |
| Unmapped test ID | `.ppm/updates` or `requested_test_ids` references `test_unmapped_side_effect` outside approved baseline | Never executed (canary file untouched); reported with `outcome="test_not_mapped"`, `executed=False`, 0 evidence rows | PASS — `canary_path.exists() is False`, `test_not_mapped` returned | `test_verifier_unmapped_test_id_not_executed` |
| `../../secret` traversal & dirty root | Test probes `../../secret` and attempts writes in cwd | Executed inside isolated `git archive` temp workspace copy; cannot reach registered root; repo stays clean; temp root removed in `finally` | PASS — `workspace_removed=True`, registered root `git status --porcelain == ""`, test passed without escaping | `test_verifier_workspace_isolation_blocks_parent_traversal_and_protects_root` |
| Parent secrets & proxy leakage | Parent env has `EAP_SECRET_CANARY`, `DATABASE_URL`, `OPENAI_API_KEY`, `HTTP_PROXY`, `HTTPS_PROXY` | Stripped from child environment; `HOME` points inside `eap_verifier_sandbox_` temp dir | PASS — zero leaked parent keys observed in child test; `HOME` isolated | `test_verifier_scrubbed_environment_hides_parent_secrets` |
| Output size bomb | Noisy failing test prints `"NOISE_" * 20000` with `max_output_bytes=128` vs `262144` | Streaming pipe stops disk/hash writes at `max_output_bytes`, drains excess to EOF, sets `output_truncated=true`, counts `truncated_bytes` | PASS — on-disk `.log` size verified `== 128`, `output_truncated=True`, `truncated_bytes > 100_000`; `262144` run sets `output_truncated=False`, `truncated_bytes=0` | `test_verifier_caps_large_output` |
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
| Wall-clock timeout & process-tree kill | **Enforced** | **Enforced** | Win32 `ctypes` Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `TerminateJobObject`); Linux `unshare -rn --pid --fork --kill-child --mount-proc` (with fallback probing), `PR_SET_CHILD_SUBREAPER` via `preexec_fn`, `start_new_session=True`, two-pass `/proc/<pid>/stat` descendant freeze (`SIGSTOP`) + `os.killpg` + `os.kill(..., SIGKILL)` |
| Streaming output size cap | **Enforced** | **Enforced** | `stdout=PIPE, stderr=STDOUT` drained on background thread; stops writing disk log & SHA-256 buffer at `max_output_bytes`, drains to EOF, sets `output_truncated` + `truncated_bytes` |
| JUnit XML execution attestation | **Enforced** | **Enforced** | `--junitxml=<out_dir>/<hash>.xml` outside workspace; stdlib `xml.parsers.expat` with DTD/entity expansion disabled; requires exact 1 matching `<testcase>` with no failure/error/skipped; records `test_config_files` hashes |
| Network namespace isolation | **`not_enforced`** (recorded honestly on evidence row & report) | **`enforced` when `unshare` netns probe succeeds**; else `not_enforced` | Runtime probe `_detect_network_isolation()` across flag fallback chain; never claims `enforced` unless probe exits 0 |
| Container / VM / low-privilege OS sandbox (`D-07`) | **Not implemented** (deferred to D-07) | **Not implemented** (deferred to D-07) | Guarded by `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false` + `EAP_COLLECTOR_FIXTURES_ROOT` |

## 5. Review fixes V1–V4

| Finding | Fix | Test | Observed |
|---|---|---|---|
| **V1 (High)**: Repo `conftest.py` `os._exit(0)` (or skip-all hook) returns exit status 0 and falsely marks a failing/unexecuted test `passed` | Added `--junitxml=<out_dir>/<hash>.xml` (outside workspace) to allowlisted argv; `_verify_junit_xml_result` + `_safe_parse_junit_xml` (stdlib `expat` with `StartDoctypeDeclHandler`/`EntityDeclHandler`/`UnparsedEntityDeclHandler` forbidden and `XML_PARAM_ENTITY_PARSING_NEVER`) requires `exit_status == 0`, valid XML, and exact 1 matching `<testcase>` with no `<failure>`/`<error>`/`<skipped>` (`no_result_record` / `nodeid_not_executed` / `result_mismatch`). Added `_collect_test_config_files` persisting `conftest.py`, `pytest.ini`, `.pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg` SHA-256 digests into `ppm_evidence.test_config_files` for future `ISS-041` pinning | `test_verifier_conftest_os_exit_zero_and_skip_all_cannot_pass` | PASS — `conftest.py` `os._exit(0)` -> `inconclusive` (`no_result_record`), `conftest.py` recorded in `test_config_files` & `get_assessment_detail` (`excluded_evidence`), criterion `unverified`; `conftest` skip-all -> `inconclusive` (`result_mismatch`) + `pytest.ini` recorded; clean pass -> `passed`; DTD/XXE payload -> `(False, "no_result_record")` |
| **V2 (Medium)**: Timeout only kills direct child PID, leaving `start_new_session=True` detached grandchildren running | Linux: `_detect_network_isolation()` probes `["-rn", "--pid", "--fork", "--kill-child", "--mount-proc"]` with flag-subset fallbacks; when `unshare --pid` is unavailable (e.g. Ubuntu 24.04 AppArmor `apparmor_restrict_unprivileged_userns=1` as on GitHub Actions `ubuntu-latest`), sets `PR_SET_CHILD_SUBREAPER` (`preexec_fn`), `start_new_session=True`, freezes descendants via two-pass `/proc/<pid>/stat` walk (`SIGSTOP`), and sends `SIGKILL` to all discovered PGIDs and PIDs. Windows: Win32 `ctypes` Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) attached to child and terminated via `TerminateJobObject` + `CloseHandle` | `test_verifier_timeout_kills_detached_grandchild_process` | PASS — 5s delayed `start_new_session=True` grandchild never writes marker (`marker.exists() is False` after `timeout=1.5s` + `6.0s` wait) on Windows (Win32 Job Object), local Linux userns container (`unshare -rn --pid --fork --kill-child --mount-proc`), and restricted Linux container (`mode=not_enforced` via `PR_SET_CHILD_SUBREAPER` + `/proc` tree kill) |
| **V3 (Medium)**: Network test asserted `bind(("127.0.0.1", 0))` fails, which succeeds inside Linux `unshare -rn` | Rewrote network test to start a real TCP listener on host `127.0.0.1:0` and make the fixture test execute `socket.create_connection(("127.0.0.1", port))`; asserts `outcome="failed"` when `enforced`, asserts only `network_isolation="not_enforced"` when `not_enforced`, and emits `VERIFIER_CI_NET_ISOLATION` `UserWarning` so CI logs show the actual mode | `test_verifier_network_socket_isolation_or_not_enforced_flag` | PASS — Windows: `VERIFIER_CI_NET_ISOLATION: platform=win32 mode=not_enforced outcome=passed exit_status=0`; Local Linux (`tester` userns, `enforced`): `VERIFIER_CI_NET_ISOLATION: platform=linux mode=enforced outcome=failed exit_status=1`; Local restricted Linux (`not_enforced`): `VERIFIER_CI_NET_ISOLATION: platform=linux mode=not_enforced outcome=passed exit_status=0`; GitHub Actions CI: *pending push of V2 `/proc` fix (prior run `43727741557` logged `mode=not_enforced`)* |
| **V4 (Low-Medium)**: Child stdout written directly to an unbounded log file on disk | Switched child `Popen` to `stdout=subprocess.PIPE, stderr=subprocess.STDOUT` with `_drain_pipe_with_cap` stopping disk and SHA-256 writes at `max_output_bytes`, draining remaining pipe bytes to EOF to prevent child deadlock, and persisting `output_truncated` and `truncated_bytes` in `ppm_evidence` (migration `0007`) | `test_verifier_caps_large_output` | PASS — spy confirmed on-disk `.log` stopped at `128` bytes (`observed_log_sizes == [128]`), `output_truncated=True`, `truncated_bytes > 100_000` in result & DB; `262144` cap run recorded `output_truncated=False`, `truncated_bytes=0` |

## 6. Executed checks and real output (local & CI)
All checks below executed after final code/test edits against isolated PostgreSQL `18.6-trixie` (`CI=true`, `TEST_DATABASE_URL=postgresql+psycopg://eap:***@127.0.0.1:5434/eap_test`), merged with `origin/main` (`6c8a24b`):
- `uv run python tools/run_sc06.py` -> **SC-06 Verification: ALL CYCLES PASSED** (empty up/down/up + seeded down/up, head `0007_verifier_evidence`)
- `uv run pytest -v tests/ppm/test_verifier_sandbox.py` (Windows 11, PG 18.6, post V2 `/proc` fix) -> **12 passed, 2 warnings in 21.52s** (`VERIFIER_CI_NET_ISOLATION: platform=win32 mode=not_enforced outcome=passed exit_status=0`)
- Local Linux container (`python:3.12-slim`, `util-linux 2.41.5`, unprivileged `tester` userns with `--security-opt seccomp=unconfined`, `host.docker.internal:5434/eap_test`): `pytest -v -p no:cacheprovider /repo/tests/ppm/test_verifier_sandbox.py` -> **12 passed, 2 warnings in 15.29s** (`VERIFIER_CI_NET_ISOLATION: platform=linux mode=enforced outcome=failed exit_status=1`)
- Local restricted Linux container (`python:3.12-slim`, default unprivileged container without `seccomp=unconfined`, `host.docker.internal:5434/eap_test`, post V2 `/proc` fix): `pytest -v -p no:cacheprovider /repo/tests/ppm/test_verifier_sandbox.py` -> **12 passed, 2 warnings in 14.94s** (`VERIFIER_CI_NET_ISOLATION: platform=linux mode=not_enforced outcome=passed exit_status=0`)
- `uv run pytest -q` (Windows 11, PG 18.6, full suite incl. `tests/hud/*`) -> **647 passed, 6 warnings in 233.82s (0:03:53)**
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**
- `uv run python ppm-v2/tools/check_fixtures.py` -> **PASS 23 fixtures, 3 rollup**
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> **PASS 23 fixtures**
- `uv run python tools/check_translation.py` -> **SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.**
- `uv run ruff check src tests ppm-v2/reducer migrations` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer` (win32) -> **Success: no issues found in 158 source files**
- `uv run mypy --platform linux src tests ppm-v2/reducer` -> **Success: no issues found in 158 source files**
- `npm --prefix web/hud run build` -> **built in 418ms**
- `npm --prefix web/hud test` -> **Test Files 1 passed (1) / Tests 2 passed (2)**
- `sha256sum -c SHA256SUMS.txt` -> *will be verified right after regenerating SHA256SUMS.txt*
- GitHub Actions CI (`wp/P2a-WP9`) -> *pending push*

## 7. Residual risks, blockers, and untested controls
- **D-07 / `ISS-002` / `ISS-042` still open**: Full OS container/VM/low-privilege user sandbox for arbitrary untrusted repositories is not yet selected or built. Real repositories remain blocked (`VerifierRealRootRefusedError` / `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`) until **DG-3** + **D-07**.
- **ISS-041 (test/config hash pinning) open**: `test_config_files` SHA-256 hashes are now recorded on `ppm_evidence` as requested, while baseline test/config hash comparison (`test_definition_changed`) remains intentionally deferred to `ISS-041` before Local-pilot.
- **Windows network isolation**: Local Windows runs record `network_isolation=not_enforced` because Windows lacks `unshare -n`; tests remain able to open outbound sockets locally on Windows until D-07 sandboxing.
- **Resource quotas**: Disk/CPU/memory/process-count limits (`RLIMIT_NPROC` / Job Object CPU & memory limits) belong to D-07 hardened sandbox execution.

## 8. Go/no-go decision and rollback
- **Status**: `ready_for_review` (pending owner review; branch `wp/P2a-WP9`).
- **Rollback procedure**:
  1. Set `PPM_VERIFIER_ENABLED=false` (default): `run_verifier` exits immediately (`skipped_reason="verifier_disabled"`), leaving criteria as claims with zero verifier execution.
  2. Schema rollback: `uv run alembic downgrade 0006_assessments` drops `exit_status`, `duration_ms`, `network_isolation`, `reason`, `output_truncated`, `truncated_bytes`, and `test_config_files` columns and constraints cleanly (verified in SC-06).
