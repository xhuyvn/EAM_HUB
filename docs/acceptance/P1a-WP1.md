# Acceptance Packet: P1a-WP1 Schema and Migrations

## 1. Milestone and Scope
- **Work Package**: P1a-WP1 (Schema and migrations per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §5 SC-02/SC-04/SC-06 and §6 P1a-WP1, and `docs/DEV_ENVIRONMENT.md`).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP1` (branched from `main` at `fab6cd3`)
- **Code Commit under Review**: `a5a3c4b`
- **Base Commit**: `fab6cd3` ("sign DG-1 and DG-2: owner approval with conditions DG1-C1..C5, DG2-C1; P0 closed")
- **Scope**:
  - `migrations/versions/0001_core.py` (down_revision `0000_init`): Implements all 16 core platform tables and constraints from `migrations/draft/0001_core.sql` exactly as signed by the owner in DG-1 and DG-2 with Amendment A1.
  - Added PostgreSQL CHECK constraints for enumerated state and status columns using canonical values from `contracts/*.schema.json` (`tasks.state`, `runs.state`, `runs.remote_outcome`, `run_attempt_history.from_state`, `run_attempt_history.to_state`, `action_executions.state`, `task_intents.status`, `dispatch_intents.state`, `commands.status`, `project_scopes.status`).
  - Downgrade cleanly drops all 16 created tables in strict reverse-dependency order.
  - `src/eap/db/tables.py` and `src/eap/db/__init__.py`: SQLAlchemy Core table metadata matching the migration as the single source of truth for platform table metadata.
  - Test DB fixture (`tests/conftest.py`): Session-scoped fixture creates and drops `eap_test` from `TEST_DATABASE_URL`; tests never touch `eap_dev`. If `TEST_DATABASE_URL` is unset, DB tests skip with the explicit reason `"TEST_DATABASE_URL is not set; skipping database tests"`. In CI environments (`CI=true`), missing `TEST_DATABASE_URL` fails collection immediately with exit code 1 (P1-2).
  - Database constraint test suite (`tests/db/`): 38 automated tests asserting exact constraint names in `IntegrityError` covering DG1-C3 / C29, C02, C32 / W7-3, tasks keys, usage ledger, outbox offset absence, leases fencing token NOT NULL, non-nullable `tenant_id` across all 16 tables, catalog FK inspection, and cross-tenant composite FK rejection (P1-1).
  - Alembic autogenerate diff test (`tests/db/test_autogenerate_diff.py`): Proves zero schema drift (`compare_metadata == []`).
  - Draft DDL parity test (`tests/db/test_draft_ddl_parity.py`): Parses `migrations/draft/0001_core.sql` with `pglast` to verify 100% table, column, PK, FK, and unique constraint parity with metadata.
  - SC-06 runner (`tools/run_sc06.py`): Full up/down/up cycle on empty database and seeded fixture database with `alembic current` verification after each step.
  - CI automation: Added `postgres:18.6-trixie` service and environment variables to `.github/workflows/ci.yml` and `scripts/ci.ps1`. DB tests cannot skip in CI; secrets supplied via `env` only (SC-04).

---

## 2. Review Items Evidence (P1-1 Tenant Isolation & P1-2 CI Enforcement)

### 2.1 P1-1: Tenant Isolation at the Database Level (Composite Foreign Keys)
Review finding: Parent tables referenced by simple `id` allowed cross-tenant references (e.g., tenant-B referencing tenant-A's task or source).
Amendment A1 (owner-approved 2026-11-07):
- **Parents**: Added `CONSTRAINT uq_tasks_tenant_id UNIQUE (tenant_id, id)` to `tasks` and `CONSTRAINT uq_sources_tenant_source_id UNIQUE (tenant_id, source_id)` to `sources`.
- **Children**: Replaced every foreign key with a composite foreign key referencing `(tenant_id, <pk_col>)`:
  1. `runs`: `CONSTRAINT fk_runs_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT`
  2. `run_attempt_history`: `CONSTRAINT fk_run_attempt_history_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT`
  3. `task_intents`: `CONSTRAINT fk_task_intents_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT`
  4. `commands`: `CONSTRAINT fk_commands_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT`
  5. `artifacts`: `CONSTRAINT fk_artifacts_tenant_task FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id) ON DELETE RESTRICT`
  6. `project_scopes`: `CONSTRAINT fk_project_scopes_tenant_source FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, source_id) ON DELETE RESTRICT`
  7. `ppm_scan_schedules`: `CONSTRAINT fk_ppm_scan_schedules_tenant_source FOREIGN KEY (tenant_id, source_id) REFERENCES sources (tenant_id, source_id) ON DELETE RESTRICT`

Observable evidence in `tests/db/test_tenant_isolation_fks.py`:
- `test_catalog_every_fk_includes_tenant_id`: Catalog introspection proves all 7 foreign keys in metadata and PostgreSQL catalog enforce `tenant_id` in both constrained and referred columns.
- `test_cross_tenant_fk_runs_rejected`: Tenant-B inserting run against Tenant-A's task raises `IntegrityError` with `fk_runs_tenant_task`; same tenant succeeds.
- `test_cross_tenant_fk_run_attempt_history_rejected`: Tenant-B history against Tenant-A task raises `fk_run_attempt_history_tenant_task`; same tenant succeeds.
- `test_cross_tenant_fk_task_intents_rejected`: Tenant-B intent against Tenant-A task raises `fk_task_intents_tenant_task`; same tenant succeeds.
- `test_cross_tenant_fk_commands_rejected`: Tenant-B command against Tenant-A task raises `fk_commands_tenant_task`; same tenant succeeds.
- `test_cross_tenant_fk_artifacts_rejected`: Tenant-B artifact against Tenant-A task raises `fk_artifacts_tenant_task`; same tenant succeeds.
- `test_cross_tenant_fk_project_scopes_rejected`: Tenant-B scope against Tenant-A source raises `fk_project_scopes_tenant_source`; same tenant succeeds.
- `test_cross_tenant_fk_ppm_scan_schedules_rejected`: Tenant-B schedule against Tenant-A source raises `fk_ppm_scan_schedules_tenant_source`; same tenant succeeds.

### 2.2 P1-2: CI Enforcement (No DB Test Skipping)
In `tests/conftest.py`:
- `pytest_collection_modifyitems`: Hook checks if `os.environ.get("CI")` is set. If `TEST_DATABASE_URL` is missing, pytest immediately halts collection with exit code 1 and error:
  `TEST_DATABASE_URL is not set in CI environment; database tests must not skip in CI`.
- `test_db_url` fixture likewise raises `pytest.fail` if called in CI with missing database URL.

### 2.3 DG1-C3 Evidence (Action Key Conflict & Tenant Isolation)
Condition DG1-C3 requires: *"Retry with the same operation_key but a different target or arguments is rejected. Enforced by P1a-WP1 (constraint), P1a-WP4 (C29). Same key + different payload_hash -> 409 OPERATION_KEY_CONFLICT."*

In P1a-WP1, the database layer enforces uniqueness on `(tenant_id, operation_key)`:
- Constraint definition on `action_executions`:
  ```sql
  CONSTRAINT uq_action_executions_key UNIQUE (tenant_id, operation_key)
  ```
- Observable evidence from `tests/db/test_action_executions.py::test_dg1_c3_duplicate_operation_key_rejected`:
  Inserting a duplicate `operation_key` with divergent `payload_hash` in the same tenant fails with `IntegrityError` naming `uq_action_executions_key`.
- Cross-tenant isolation verified by `test_dg1_c3_same_operation_key_different_tenant_allowed`:
  The identical `operation_key` inserted under `tenant-alpha` and `tenant-beta` succeeds with 2 distinct execution records.

---

## 3. Conformance Cases Summary

| Case ID | Milestone | Status | Observed Outcome | Evidence Reference |
|---|---|---|---|---|
| **C02** | P1a | PASS | `event_log` rejects duplicate `event_id` within tenant via `uq_event_log_event_id`; rejects duplicate `(stream_id, ingest_offset)` via `uq_event_log_stream_offset`; allows cross-tenant sharing | `artifacts/conformance/P1a/C02.json`, `tests/db/test_event_log.py` |
| **C15** | P1a | PASS | `leases` table has `fencing_token` column (NOT NULL); mutual exclusion enforced via `uq_leases_resource` | `artifacts/conformance/P1a/C15.json`, `tests/db/test_leases.py` |
| **C29** | P1a | PASS | Two `action_executions` with same `(tenant_id, operation_key)` rejected by PostgreSQL with `uq_action_executions_key`; different tenant permitted; state checked via `ck_action_executions_state` | `artifacts/conformance/P1a/C29.json`, `tests/db/test_action_executions.py` |
| **C32 / W7-3** | P1a | PASS | `project_scopes` enforces `UNIQUE(tenant_id, source_id, metadata_root)` (`uq_project_scopes_source_metadata`); allows multiple scopes per physical source; allows multiple scopes per project (W7-3); duplicate (source, metadata_root) rejected | `artifacts/conformance/P1a/C32.json`, `tests/db/test_project_scopes.py` |
| **C33** | P1a | PASS | `outbox` has no offset column (ADR-020 enforced in PostgreSQL schema and SQLAlchemy metadata); `event_log` enforces strictly contiguous stream offset per tenant | `artifacts/conformance/P1a/C33.json`, `tests/db/test_tasks_and_usage.py` |
| **DG1-C3** | P1a | PASS | Duplicate `operation_key` rejected by DB unique constraint `uq_action_executions_key`; cross-tenant isolation preserved | `artifacts/conformance/P1a/DG1-C3.json`, `tests/db/test_action_executions.py` |

---

## 4. Execution Evidence and Verification Results

### 4.1 SC-06 Migration Up/Down/Up on Empty and Seeded Database
Command: `uv run python tools/run_sc06.py`
Target Database: `postgresql+psycopg://eap:***@127.0.0.1:5432/eap_test` (PostgreSQL 18.6)
Real Output:
```
============================================================
SC-06 Cycle 1: Empty database up/down/up
============================================================

[Step 1.1] alembic upgrade head on empty database...

--- [alembic current] after upgrade head (empty DB) ---
0001_core (head)

[Step 1.2] alembic downgrade base...

--- [alembic current] after downgrade base (empty DB) ---

[Step 1.3] alembic upgrade head...

--- [alembic current] after second upgrade head (empty DB) ---
0001_core (head)

============================================================
SC-06 Cycle 2: Seeded database down/up
============================================================

[Step 2.1] Seeding fixture dataset...
Fixture dataset seeded successfully.

[Step 2.2] alembic downgrade base on seeded database...

--- [alembic current] after downgrade base (seeded DB) ---

[Step 2.3] alembic upgrade head after drop...

--- [alembic current] after upgrade head (post-seeded DB) ---
0001_core (head)

============================================================
SC-06 Verification: ALL CYCLES PASSED
============================================================
```

### 4.2 Autogenerate Diff and Draft DDL Parity
- Autogenerate Diff (`tests/db/test_autogenerate_diff.py`):
  ```python
  mc = MigrationContext.configure(db_conn, opts={"compare_type": True, "compare_server_default": True})
  diff = compare_metadata(mc, metadata)
  assert diff == []  # PASSED: 0 diffs
  ```
- Draft vs Migration Parity (`tests/db/test_draft_ddl_parity.py`):
  - 16/16 tables match exactly.
  - Column sets across all 16 tables match draft DDL.
  - Named unique constraints match draft DDL.
  - Foreign key relations match draft DDL.
  - `tenant_id NOT NULL` verified across all tables.

### 4.3 Full Minimum Shared Checks Suite
1. **Package Validation (SC-01)**:
   ```bash
   uv run python tools/validate_package.py
   # PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
   ```
2. **PPM v2 Negative Cases**:
   ```bash
   uv run python ppm-v2/tools/run_negative_cases.py
   # PASS 5 templates, 20 cases
   ```
3. **PPM v2 Fixture Corpus**:
   ```bash
   uv run python ppm-v2/tools/check_fixtures.py
   # PASS 19 fixtures, 3 rollup
   ```
4. **Translation Provenance & Staleness (SC-08)**:
   ```bash
   uv run python tools/check_translation.py
   # SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
   ```
5. **Python Test Suite (SC-02)**:
   ```bash
   uv run pytest -q
   # 160 passed, 1 warning in 3.04s
   ```
6. **Code Formatting and Linting**:
   ```bash
   uv run ruff check src tests ppm-v2/reducer migrations
   # All checks passed!
   ```
7. **Static Type Checking**:
   ```bash
   uv run mypy src tests ppm-v2/reducer
   # Success: no issues found in 69 source files
   ```
8. **Web HUD Build**:
   ```bash
   npm --prefix web/hud run build
   # built in 561ms
   ```
9. **File Checksums**:
   ```bash
   sha256sum -c SHA256SUMS.txt
   # All files OK
   ```
10. **GitHub Actions CI Automation**:
    - Workflow: `validate-and-test` with `postgres:18.6-trixie`
    - Run URLs: [https://github.com/xhuyvn/EAM_HUB/actions/runs/42353502611](https://github.com/xhuyvn/EAM_HUB/actions/runs/42353502611) (latest, commit `a5a3c4b`), [https://github.com/xhuyvn/EAM_HUB/actions/runs/42350572193](https://github.com/xhuyvn/EAM_HUB/actions/runs/42350572193) (commit `10674ec`), and [https://github.com/xhuyvn/EAM_HUB/actions/runs/42350393234](https://github.com/xhuyvn/EAM_HUB/actions/runs/42350393234) (commit `6cfe452`)
    - Status: `completed success` (all 13 steps passed in 55s)

---

## 5. Security and Operational Verification
- **PostgreSQL 18.6 Health**: Tested locally against Docker container `postgres:18.6-trixie` on port 5432 and configured as a health-checked service in `.github/workflows/ci.yml`.
- **Canary Redaction & Credentials (SC-04)**: Zero passwords or secrets logged in CI or test outputs. Settings use `SecretStr` exclusively.
- **Tenant Isolation**: Introspection test proves all 16 core platform tables carry `tenant_id NOT NULL`.
- **ADR-020 Invariant**: `outbox` table introspection confirms absence of `offset`, `ingest_offset`, or `stream_offset` columns.

---

## 6. Issues Status
- **ISS-001**: Marked `resolved` (Owner signed DG-1 and DG-2 on 2026-11-07; P0 closed).
- **ISS-017**: Opened (Align `tasks.state` CHECK constraint and draft DDL default `'queued'` with `contracts/hud-projection.schema.json` task_state enum in contract 1.2 / P1a-WP2 lifecycle).

---

## 7. Recommendation and Rollback
- **Status**: `ready_for_review` (set in `STATUS.md`).
- **Owner Action**: Review branch `wp/P1a-WP1` and merge into `main` after review. Never merged by agent.
- **Rollback Procedure**:
  - Against database: `alembic downgrade 0000_init` (or `alembic downgrade base` on empty DB).
  - Against git: `git checkout main && git branch -D wp/P1a-WP1`.
