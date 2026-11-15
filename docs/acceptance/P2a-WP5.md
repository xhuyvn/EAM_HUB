# Milestone acceptance — P2a-WP5: Scan scheduler

## 1. Milestone and scope
Work package **P2a-WP5** implements automated, lease-fenced PPM location scanning and semantic `inputs_hash` deduplication so registered fixture sources scan automatically without overlapping scans or spurious re-assessments on unchanged data.

Implemented scope:
1. **Migration `0009_scan_scheduler`** (`migrations/versions/0009_scan_scheduler.py`, `src/eap/db/scheduler_tables.py`, `src/eap/db/__init__.py`):
   - Preserves `ppm_scan_schedules` column parity with `migrations/draft/0001_core.sql`.
   - Adds `ppm_scan_source_status` (`last_scan_started_at`, `last_successful_scan_at`, redacted `last_error`, `last_occurrence_id`, `last_trigger`, `last_task_id`, `last_run_id`, `last_snapshot_id`, `last_fencing_token`) with composite tenant-scoped FK `fk_ppm_scan_source_status_source` -> `sources(tenant_id, source_id)`.
   - Adds `ppm_scan_occurrences` (`UNIQUE (tenant_id, source_id, occurrence_id)`, trigger `CHECK IN ('startup', 'interval', 'watcher', 'manual')`, status `CHECK IN ('running', 'succeeded', 'failed')`) with composite tenant-scoped FK `fk_ppm_scan_occurrences_source` so redelivered trigger occurrences are idempotent no-ops.
2. **Scan scheduler & fencing** (`src/eap/ppm/scheduler.py`, `src/eap/ppm/collector/collector.py`):
   - `PpmScanScheduler` & `BackgroundScanScheduler` support `startup` catch-up, `interval`, debounced `watcher`, and `manual` triggers without OS cron.
   - Deterministic `occurrence_id` (`compute_occurrence_id`) deduplicates repeated deliveries of the same trigger window (`duplicate_occurrence`).
   - Scans execute as logical tasks (`task_type='ppm.inventory.scan'`) through `TaskService` and `RunService`.
   - Single active scan per source enforced via PostgreSQL row locks (`ppm_scan_schedules` -> `leases` `FOR UPDATE` ordering) and monotonically increasing `fencing_token`.
   - `PpmCollector.scan_source(..., fencing_token=...)` re-verifies the active lease and `fencing_token` under `FOR UPDATE` immediately before snapshot commit; stale tokens roll back and raise `StaleFencingTokenError`.
   - Deferred `PpmCollector` import to `TYPE_CHECKING` / lazy initialization in `PpmScanScheduler.__init__` so importing `eap.ppm.collector.collector` before `eap.api.app` has no circular dependency.
   - After a successful scan + ingest, calls `create_assessment(..., trigger="snapshot", project_snapshot_id=...)` per active non-quarantined scope (verifier is NOT invoked here).
3. **ISS-034 semantic `inputs_hash` & migration impact** (`src/eap/ppm/assessment_inputs.py`, `src/eap/ppm/assessments.py`):
   - `build_assessment_inputs` computes `inputs_hash` over semantic assessment content only: `project_id`, `scope_hash`, `assessed_revision`, `evidence_ttl_days`, `baseline` (canonical approved criteria prior to injecting runtime `previous_status`), `claims`, `evidence`, `waivers`, `dependency_refs`, and `context`.
   - Keeps `snapshot_refs`, `snapshot_seq`, `source_as_of`, `basis_seq`, `previous_assessment_id`, and snapshot-derived `now` as lineage in `inputs` outside the hash.
   - **Migration / upgrade impact**: Existing `ppm_assessments` rows are immutable and retain their pre-upgrade `inputs_hash` (no backfill or `UPDATE`). Consequently, the first scan of an already-assessed project after upgrading to semantic `inputs_hash` may create one transitional assessment row (`previous_hash != semantic_hash`); every subsequent rescan of unchanged data computes the identical semantic `inputs_hash` and deduplicates (`assessments_created=0`, `assessments_deduplicated=1`).
   - `get_assessment_detail` computes `last_successful_scan_at` as the maximum of captured `project_snapshots.created_at` and `ppm_scan_source_status.last_successful_scan_at`.
4. **Configuration & policy guards** (`src/eap/config.py`, `.env.example`):
   - `PPM_SCHEDULER_ENABLED=false` (default disabled; setting `false` disables background/automatic triggers immediately while manual scans remain functional).
   - `PPM_SCAN_INTERVAL_SECONDS=300` (minimum override `>= 60` s enforced by `Settings`, `validate_schedule_config`, and `PUT /v1/ppm/sources/{source_id}/schedule`).
   - `PPM_SCAN_DEBOUNCE_SECONDS=5` (`>= 1`), `PPM_SCAN_LEASE_TTL_SECONDS=60` (`>= 5`).
   - Fixtures-only collector guards (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`, `EAP_COLLECTOR_FIXTURES_ROOT`) remain unchanged and enforced.
5. **Observability & HUD-readable API** (`src/eap/ppm/service.py`, `src/eap/api/ppm_routes.py`):
   - Persists per-source `last_scan_started_at`, `last_successful_scan_at`, redacted `last_error` (`redact_text`), `next_due_at`, `interval_seconds`, `debounce_seconds`, `fencing_token`, `lease_holder`, `lease_expires_at`, and last trigger/task/run/snapshot IDs.
   - Exposed on `LocationService.list_sources` / `get_source` (`GET /v1/ppm/sources`, `GET /v1/ppm/sources/{source_id}`), `GET /v1/ppm/sources/{source_id}/schedule`, `PUT /v1/ppm/sources/{source_id}/schedule` (returns `422` for `< 60` s), and `POST /v1/ppm/sources/{source_id}/scan`.

## 2. Source commit and environment pins
- Base commit on `main`: `10d3c54` (immediately atop `55b4434`)
- Branch: `wp/P2a-WP5`
- Python: `3.12.13` (`uv` workspace)
- Database: PostgreSQL `18.6` (`eap_test` via local PG instance)
- Fixtures-only mode: `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`, zero model/provider calls, zero cloud egress.

## 3. Rule -> test -> observed table

| Rule / Requirement | Test(s) | Observed Result |
|---|---|---|
| Config defaults (`disabled`, `300s`, `5s`) & production `>= 60s` enforcement | `tests/ppm/test_scan_scheduler_c35.py::test_c35_config_validation_enforces_60s_minimum_in_production` | **PASS** (default `false`/`300`/`5`; `Settings.model_validate` rejects `59s` interval & `0s` debounce; `validate_schedule_config(59, 5)` raises `ValueError`; explicit `_allow_sub_minimum_interval_for_testing=True` allowed for tests only) |
| Auto scan on modified note + update within debounce + interval (no manual scan); HUD schedule endpoints (`GET`/`PUT`) | `tests/ppm/test_scan_scheduler_c35.py::test_c35_auto_scan_on_fixture_modification_within_debounce_and_interval` | **PASS** (`BackgroundScanScheduler` triggers auto scan after `.ppm/notes/kickoff.md` & `upd-002.yaml` edits; `ppm_assessments=2`; `ppm.inventory.scan` tasks created; `GET /v1/ppm/sources/{id}/schedule` 200 with observability fields; `PUT` 30s -> HTTP 422) |
| Deterministic `occurrence_id` redelivery is idempotent no-op | `tests/ppm/test_scan_scheduler_c35.py::test_c35_same_occurrence_delivered_twice_runs_one_scan` | **PASS** (first call `executed=True`, `status='succeeded'`; second call `executed=False`, `status='duplicate_occurrence'`; `collector_snapshots=1`, `tasks=1`, `ppm_scan_occurrences=1`) |
| **ISS-034** unchanged-data rescan creates 0 new assessments and 0 domain ingest rows, updates `last_successful_scan_at` | `tests/ppm/test_scan_scheduler_c35.py::test_c35_rescan_unchanged_data_zero_new_assessments_and_domain_rows` | **PASS** (two subsequent unchanged rescans produce `assessments_created=0, assessments_deduplicated=1`; `ppm_assessments=1`, `ppm_notes=1`, `ppm_updates=1`, `ppm_ingested_artifacts=2` unchanged; `obs.last_successful_scan_at` & `get_assessment_detail().last_successful_scan_at` advance) |
| Concurrent schedulers race & stale fencing token cannot commit (real PG connections) | `tests/ppm/test_scan_scheduler_c35.py::test_c35_two_schedulers_race_and_stale_fencing_token_cannot_commit` | **PASS** (Part A: second racer returns `status='lease_busy'`, `executed=False`; Part B: expired worker resumed before commit receives `status='stale_fencing_token'`, `executed=False`, rolls back without adding a snapshot while winner commits with higher `fencing_token`) |
| Host sleep / clock jump across 12 intervals -> single catch-up scan | `tests/ppm/test_scan_scheduler_c35.py::test_c35_host_sleep_clock_jump_produces_single_catchup_scan` | **PASS** (jump +3600s runs 1 scan with `status='succeeded'`, schedules `next_due_at=wake_at+300s`; immediate follow-up `tick_interval` at `+1s` returns `[]`) |
| Rollback flag `PPM_SCHEDULER_ENABLED=false` blocks auto triggers while manual works | `tests/ppm/test_scan_scheduler_c35.py::test_c35_scheduler_disabled_blocks_auto_triggers_but_manual_works` | **PASS** (`startup_catch_up`, `tick_interval`, `scan_filesystem_changes` return `[]`; `run_occurrence` returns `scheduler_disabled`; `trigger_manual` returns `succeeded`, `assessments_created=1`) |
| Scan failure (`ISS-028` replaced root + secret redaction) | `tests/ppm/test_scan_scheduler_c35.py::test_c35_scan_failure_iss028_records_redacted_error_schedules_next_attempt` | **PASS** (`RootReplacedSecurityError` caught, `last_error` persisted, `ppm_assessments` stays `1`, `next_due_at=t_fail+300s`, `+1s` tick does not retry; injected `Bearer` + `EAP-CANARY-...` redacted to `***`) |
| Circular-import regression check (`collector` <-> `scheduler`) | `uv run python -c "import eap.ppm.collector.collector; import eap.ppm.scheduler; ..."` and reverse order | **PASS** (`IMPORT_ORDER_1_OK`, `IMPORT_ORDER_2_OK`) |
| Reducer parity, golden, CAS, location, and DG-1 Amendment A1 FK isolation | `tests/ppm/test_assessment_parity.py`, `test_assessment_golden_and_api.py`, `test_assessment_cas_and_iss033.py`, `tests/test_ppm_locations.py`, `tests/db/` | **PASS** (all included in `666 passed` full pytest run; 21/21 tenant-scoped FKs verified incl. `fk_ppm_scan_source_status_source` and `fk_ppm_scan_occurrences_source`; autogenerate diff empty) |

## 4. DB counts before and after unchanged-data rescan
Observed directly against PostgreSQL 18.6 (`eap_test`, schema at `0009_scan_scheduler`) across an initial manual scan (`r1` at `2026-11-15T10:00:00Z`, `inputs_hash=7dba28f07ea71e2299325ba59fab784c797b5e8fd947411fec266746eea868a7`) followed by two unchanged rescans (`r2` at `10:05:00Z`, `r3` at `10:10:00Z`):

| Table / Field | After Scan 1 (`r1`) | After 2 Unchanged Rescans (`r2`, `r3`) | Delta |
|---|---|---|---|
| `collector_snapshots` | `1` | `3` | `+2` (raw collector captures) |
| `project_snapshots` | `1` | `3` | `+2` (project snapshots captured) |
| `ppm_ingested_artifacts` | `2` | `2` | `0` |
| `ppm_notes` | `1` | `1` | `0` |
| `ppm_updates` | `1` | `1` | `0` |
| `ppm_assessments` | `1` | `1` | `0` (`r2`/`r3`: `assessments_created=0`, `assessments_deduplicated=1`) |
| `ppm_scan_occurrences` | `1` | `3` | `+2` (one per distinct manual trigger) |
| `tasks` | `1` | `3` | `+2` (logical `TaskService` scan tasks) |
| `ppm_scan_source_status.last_successful_scan_at` | `2026-11-15T10:00:00+00:00` | `2026-11-15T10:10:00+00:00` | Updated to latest scan timestamp |
| `get_assessment_detail().last_successful_scan_at` | `2026-11-15T16:35:55.911061+00:00` | `2026-11-15T16:35:56.177948+00:00` | Updated from latest captured snapshot |

## 5. Scheduler structured log excerpts
Captured from `eap.observability.logging.configure_logging` (`JsonFormatter` + `RedactionFilter`) during real PostgreSQL execution on `eap_test`:

```json
{"timestamp": "2026-11-15T16:35:55.846917+00:00", "level": "INFO", "logger": "eap.ppm.scheduler", "message": "scheduler_trigger_ignored_disabled source_id=src-3c3a9d85b8 trigger=interval occurrence_id=occ-disabled-evidence", "tenant_id": null, "principal_id": null, "task_id": null, "run_id": null, "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:55.867227+00:00", "level": "INFO", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_started source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-588737b82e3cea2ed73d8a2b594aeafb fencing_token=***", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": null, "run_id": null, "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.022670+00:00", "level": "INFO", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_succeeded source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-588737b82e3cea2ed73d8a2b594aeafb snapshot_id=snap-95355ef8ecef435b assessments_created=1 assessments_deduplicated=0", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-81850c1a26a4", "run_id": "run-707372adfaa7", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.134123+00:00", "level": "INFO", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_succeeded source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-8af9d5992db1cda122e97a8c82ae6119 snapshot_id=snap-6ba12be5f4404841 assessments_created=0 assessments_deduplicated=1", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-553cc705bd35", "run_id": "run-3042024dddcd", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.320373+00:00", "level": "INFO", "logger": "eap.ppm.scheduler", "message": "scheduler_duplicate_occurrence_noop source_id=src-3c3a9d85b8 trigger=interval occurrence_id=occ-d6388e7362c9faed0bba585296e42085", "tenant_id": null, "principal_id": null, "task_id": "task-90448e5e1471", "run_id": "run-953427519e07", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.501640+00:00", "level": "WARNING", "logger": "eap.ppm.collector", "message": "collector_stale_fencing_token_rejected source_id=src-3c3a9d85b8 fencing_token=*** current_fencing_token=***", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-d7088212a026", "run_id": "run-b952b2307d0c", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.513961+00:00", "level": "WARNING", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_stale_fencing_token source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-stale-ev fencing_token=***", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-d7088212a026", "run_id": "run-b952b2307d0c", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.580804+00:00", "level": "WARNING", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_failed source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-1b5535fb41b7cd0e5101f970c99f6188 error=RootReplacedSecurityError: Root physical identity changed from 'win:18f6742b:001500000011bfe9' to 'win:18f6742b:001600000011bfe9'", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-bcc48779c346", "run_id": "run-8b18f0eb11ed", "request_id": null, "taskName": null}
{"timestamp": "2026-11-15T16:35:56.637267+00:00", "level": "WARNING", "logger": "eap.ppm.scheduler", "message": "scheduler_scan_failed source_id=src-3c3a9d85b8 trigger=manual occurrence_id=occ-91e33f1d3027f3623b932cf3864e8404 error=RuntimeError: Sync failed with Bearer *** and ***", "tenant_id": "tenant-evidence-9869f7c2", "principal_id": null, "task_id": "task-c92ec90ca904", "run_id": "run-855cfbb48714", "request_id": null, "taskName": null}
```

## 6. Executed checks and real output
All commands below were executed locally on Windows (`PostgreSQL 18.6`, `CI=true`):
- `$env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; $env:CI="true"; uv run pytest -q` -> **666 passed, 7 warnings in 239.77s (0:03:59)** (0 failed, 0 skipped; run after final code edit to `src/eap/ppm/scheduler.py`)
- `uv run python tools/run_sc06.py` -> **SC-06 Verification: ALL CYCLES PASSED** (Cycle 1 empty DB & Cycle 2 seeded DB through `0009_scan_scheduler (head)`)
- `uv run ruff check src tests ppm-v2/reducer migrations` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer` -> **Success: no issues found in 168 source files**
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**
- `uv run python ppm-v2/tools/check_fixtures.py` -> **PASS 23 fixtures, 3 rollup**
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> **PASS 23 fixtures**
- `uv run python tools/check_translation.py` -> **SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.** (also verified in a clean git clone after commit)
- `npm --prefix web/hud test -- --run` -> **Test Files 1 passed (1), Tests 2 passed (2)**
- `npm --prefix web/hud run build` -> **tsc -b && vite build -> ✓ 33 modules transformed, built in 416ms**
- `C:\Program Files\Git\usr\bin\sha256sum.exe -c SHA256SUMS.txt` -> verified against regenerated LF checksum manifest
- GitHub Actions CI -> **not_run** before final local commit (branch `wp/P2a-WP5` pushed at end of session; see `STATUS.md` if owner records remote run ID)

## 7. Residual risks and follow-ups
- **ISS-034 wall-clock triggers**: Snapshot rescan deduplication via semantic `inputs_hash` is resolved in P2a-WP5; time-based re-evaluation (`evidence_ttl_expired`, waiver expiry with explicit wall-clock `now`) remains open for **P2a-WP6** (assessment orchestrator), where P2a-WP6 will also replace direct `create_assessment` invocation after scan+ingest.
- **Polling watcher fallback vs OS native FS events**: `scan_filesystem_changes` implements deterministic recursive mtime/size fingerprinting with configurable debounce (`5s` default) rather than requiring OS-specific inotify/ReadDirectoryChangesW threads.
- **Migration renumbering check at merge**: Parallel branch **P3a-WP2** may also add migration `0009_*`; if both branches add `0009_*`, the owner relinks `down_revision` at merge per `STATUS.md`.
- **Verifier**: Intentionally not invoked by the scheduler in P2a-WP5; wired by P2a-WP6.

## 8. Go/no-go and rollback
- Status: **ready_for_review** (branch `wp/P2a-WP5`).
- Rollback procedure:
  1. Runtime rollback: leave `PPM_SCHEDULER_ENABLED=false` (disables all automatic startup/interval/watcher triggers; manual scans remain operational and unaffected).
  2. Schema rollback: `uv run alembic downgrade 0008_agents`.
