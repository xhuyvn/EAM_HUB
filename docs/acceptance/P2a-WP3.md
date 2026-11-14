# Milestone Acceptance — P2a-WP3: Ingest `.ppm` and notes

## 1. Milestone and Scope
- **Work Package:** `P2a-WP3` — Ingest `.ppm` and notes
- **Branch:** `wp/P2a-WP3` (based on `main` `db54218`)
- **Objective:** Turn committed `project_snapshots` rows into durable, deduplicated domain records (`ppm_ingested_artifacts`, `ppm_updates`, `ppm_notes`, `ppm_ingest_checkpoints`) and emit `ppm.ingest.completed` to `outbox` in the same transaction. Ingest never decides status or acceptance; claims and notes remain untrusted data.

## 2. Environment and Dependencies
- **Python:** 3.12.13 (`uv` workspace; `ppm_reducer` + `eap`)
- **PostgreSQL:** `postgres:18.6-trixie` (`eap_test` via Docker Compose)
- **Contract validation reuse:** Extracted pure semantic checks (`uses_21_fields`, `parse_ts`, `semantic_doc_errors`, `semantic_set_errors`) into `ppm-v2/reducer/ppm_reducer/contract_validation.py` (stdlib `datetime` + `typing` only; `tests/test_import_boundary.py` 8/8 PASS). `ppm-v2/tools/validate_contract.py` imports from it with `sys.path` fallback for standalone CLI mode.

## 3. Rule → Test → Observed Table

| Rule / Requirement | Test (`tests/ppm/...`, `tests/db/...`) | Observed Result |
|---|---|---|
| C17 supersession chain visible; old row retained as `superseded` | `tests/ppm/test_ingest_updates.py::test_c17_supersession_chain_visible` | PASS: `get_supersession_chain` returns `["upd-001", "upd-002"]` with states `superseded`, `active` |
| Same `update_id` + same bytes -> duplicate no-op | `tests/ppm/test_ingest_updates.py::test_duplicate_same_bytes_and_conflict_different_bytes` | PASS: `counts["duplicate"] == 1`, `counts["conflict"] == 0` |
| Same `update_id` + different bytes -> conflict; first payload preserved | `tests/ppm/test_ingest_updates.py::test_duplicate_same_bytes_and_conflict_different_bytes` | PASS: `counts["conflict"] == 1`, `ppm_updates.payload["summary"] == "Original payload"`, `ppm_ingested_artifacts.reason_code == "update_id_conflict"` |
| Orphan supersedes resolved on later target arrival; cycle -> `conflict` | `tests/ppm/test_ingest_updates.py::test_orphan_supersedes_resolved_and_cycle_conflict` | PASS: `upd-002` starts `orphan_supersedes`, transitions to `active` (`upd-001` -> `superseded`) on arrival; `upd-a <-> upd-b` both marked `conflict` |
| Late-arriving updates (older `recorded_at`) ingested | `tests/ppm/test_ingest_updates.py::test_late_arriving_update_ingested` | PASS: `upd-old` (`2026-10-01`) ingested after `upd-new` (`2026-11-10`) |
| Shared component (`s08` pattern) stored once under owning project | `tests/ppm/test_ingest_updates.py::test_shared_component_s08_no_duplication` | PASS: 1 `ppm_updates` row under `proj-shared`, 0 duplicated into `proj-cons-a`/`proj-cons-b` |
| `2.0` using `2.1` fields rejected; `2.2`, `3.0`, `schema_version: 1` quarantined | `tests/ppm/test_ingest_validation_and_safety.py::test_version_matrix_20_with_21_field_22_30_and_v1` | PASS: `2.0`+2.1 rejected (`v2_1_field_in_v2_0`); `2.2`, `3.0`, `v1` quarantined (`unsupported_version`) with 3 read-only gaps |
| Re-ingest idempotent; crash-before-commit rerun produces 1 set of rows + 1 checkpoint advance | `tests/ppm/test_ingest_validation_and_safety.py::test_idempotent_reingest_and_crash_before_commit` | PASS: simulated crash rolled back 0 rows; rerun ingested 1 row, checkpoint `1`, 1 outbox event; second ingest `already_ingested=True` |
| Prompt-injection note stored verbatim; no status/claim change; `max_file_bytes` enforced | `tests/ppm/test_ingest_validation_and_safety.py::test_prompt_injection_note_stored_verbatim_and_size_cap` | PASS: injection text stored verbatim in `ppm_notes`, 0 `ppm_updates` rows; oversized note rejected (`note_exceeds_max_file_bytes`) |
| Collector-rejected duplicate YAML keys / oversized files recorded as `rejected` without crash | `tests/ppm/test_ingest_validation_and_safety.py::test_collector_rejected_duplicate_yaml_and_oversized_file` | PASS: `scan.status == "partial"`, `upd-ok` `ingested`, `upd-dupkey` `rejected` (`duplicate_yaml_key`), `upd-huge` `rejected` (`file_oversized`) |
| Cross-tenant FK isolation (A1) & `ppm.ingest.enabled=false` rollback | `tests/ppm/test_ingest_validation_and_safety.py::test_cross_tenant_fk_isolation_and_rollback_setting`, `tests/db/test_tenant_isolation_fks.py` | PASS: `IntegrityError` on cross-tenant insert; 16/16 FKs include `tenant_id`; `ingest_enabled=False` captures snapshot with 0 ingest rows |
| Autogenerate diff empty | `tests/db/test_autogenerate_diff.py::test_autogenerate_diff_is_empty` | PASS: `diff == []` |

## 4. Example `IngestReport` (from fixture run)
```json
{
  "project_snapshot_id": "psnap-8a4c1d2e3f0912ab",
  "snapshot_id": "snap-4f9e2b1c8d7a6503",
  "source_id": "src-c17-01",
  "project_id": "proj-alpha",
  "snapshot_seq": 2,
  "already_ingested": false,
  "counts": {
    "ingested": 1,
    "duplicate": 1,
    "conflict": 0,
    "quarantined": 0,
    "rejected": 0
  },
  "gaps": [],
  "artifacts": [
    {"path": ".ppm/updates/upd-001.yaml", "kind": "update", "outcome": "duplicate", "reason_code": "duplicate_update_id"},
    {"path": ".ppm/updates/upd-002.yaml", "kind": "update", "outcome": "ingested", "reason_code": null}
  ],
  "outbox_event_id": "evt-9b7c6d5e4f3a2109"
}
```

## 5. Input Contract for P2a-WP4 (Reducer)
P2a-WP4 reads durable, tenant-scoped domain rows from PostgreSQL:
- **`ppm_updates`**: `(tenant_id, project_id, update_id)` unique; `content_hash`, `contract_version` (`'2.0'|'2.1'`), `supersedes_update_id` (nullable), `supersession_state` (`'active'|'superseded'|'orphan_supersedes'|'conflict'`), `payload` (JSONB validated against `contract.schema.json`), `source_id`, `source_snapshot_id`, `artifact_path`, `ingested_artifact_id`, `recorded_at`, `ingested_at`.
- **`ppm_notes`**: `(tenant_id, project_id, source_id, artifact_path, content_hash)` unique; `kind` (`'idea'|'blocker'|'question'|'observation'|'unknown'`), `body` (verbatim text), `byte_length`, `first_seen_snapshot_id`, `ingested_at`.
- **`ppm_ingested_artifacts`**: audit/outcome log per `(tenant_id, project_id, source_id, artifact_path, content_hash)` with `kind` (`'update'|'status'|'project'|'acceptance'|'decision'|'note'`), `outcome` (`'ingested'|'duplicate'|'conflict'|'quarantined'|'rejected'`), `reason_code`, `detail` (JSONB).
- **`ppm_ingest_checkpoints`**: `(tenant_id, source_id, project_id)` -> `last_ingested_snapshot_seq`, `last_ingested_snapshot_id`, `updated_at`.
- **Helper**: `eap.ppm.ingest.get_supersession_chain(conn, tenant_id, project_id, update_id)` returns ordered `[oldest, ..., newest]` rows.

## 6. Checks Executed
- `uv run python tools/validate_package.py` -> `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed`
- `uv run python ppm-v2/tools/run_negative_cases.py` -> `PASS 5 templates, 20 cases`
- `uv run python ppm-v2/tools/check_fixtures.py` -> `PASS 23 fixtures, 3 rollup`
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> `PASS 23 fixtures`
- `uv run python tools/check_translation.py` -> `SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.`
- `uv run python tools/run_sc06.py` -> `SC-06 Verification: ALL CYCLES PASSED` (empty & seeded cycles through `0005_ingest`)
- `uv run ruff check src tests ppm-v2/reducer migrations` -> `All checks passed!`
- `uv run mypy src tests ppm-v2/reducer` -> `Success: no issues found in 142 source files`
- `uv run pytest -q` (with `CI=true`, `TEST_DATABASE_URL=.../eap_test` on PG 18.6) -> `576 passed, 3 warnings in 165.58s (0:02:45)`
- `npm --prefix web/hud run build` -> `✓ 29 modules transformed. ... ✓ built in 421ms`
- GitHub Actions CI run id -> `not_run` (pre-push; will run on `wp/P2a-WP3` push)

## 7. Residual Risks and Rollback
- **Residual (`ISS-032`):** `contracts/event.schema.json` `event_type` enum does not yet include `ppm.ingest.completed` (same pattern as `ISS-025` / `ISS-029`). Recorded directly in `outbox` without modifying `contracts/`.
- **Rollback:** Set `PPM_INGEST_ENABLED=false` (`Settings.ppm_ingest_enabled = False`). `PpmCollector` continues capturing `collector_snapshots` and `project_snapshots` and skips `ingest_snapshot`. Migration `0005_ingest` is cleanly reversible via `alembic downgrade 0004_snapshots`.
