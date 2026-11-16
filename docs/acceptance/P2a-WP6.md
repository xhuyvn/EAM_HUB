# Milestone acceptance — P2a-WP6: Assessment Orchestrator

## 1. Milestone and scope
Work package **P2a-WP6** ("Assessment orchestrator") establishes `src/eap/ppm/orchestrator.py` (`AssessmentOrchestrator`) as the single component owning the `snapshot -> assessment` transition and all independent reassessment triggers (`DG-2`, `ADR-021 rev. 2`).

Implemented scope:
1. **Database schema & migration `0011_assessment_orchestrator`** (`src/eap/db/orchestrator_tables.py`, `src/eap/db/__init__.py`, `migrations/versions/0011_assessment_orchestrator.py`):
   - `ppm_assessment_tasks`: deduplicates admitted tasks by `UNIQUE (tenant_id, project_id, trigger, trigger_ref)` and `UNIQUE (tenant_id, project_id, basis_seq)`, locks inputs at creation time (`project_snapshot_id`, `snapshot_seq`, `assessed_revision`, `baseline_revision`, `evidence_ttl_days`, `locked_now`, `previous_assessment_id`, `dependency_refs`, `context`, `fixture_id`), and links to `tasks`, `project_snapshots`, and `ppm_assessments` via 3 composite tenant-scoped FKs (24/24 FKs verified in `tests/db/test_tenant_isolation_fks.py`).
   - `ppm_orchestrator_cursors`: durable outbox polling cursor (`consumer_name`, `last_outbox_id`).
2. **Orchestrator (`src/eap/ppm/orchestrator.py`)**:
   - Consumes `ppm.snapshot.captured` (outbox), `ppm.baseline.approved` (outbox, emitted by `approve_baseline` in `src/eap/ppm/assessments.py`), evidence TTL timer (`check_evidence_ttl_timers`), waiver expiry timer (`check_waiver_expiry_timers`), model health (`notify_model_recovered`), and manual command (`trigger_manual`).
   - Locks `basis_seq` and all inputs at task creation time (`admit_trigger`).
   - "Bypass unchanged scope" (dedup on `inputs_hash`) applies strictly to trigger `snapshot`; `baseline_changed`, `evidence_ttl_expired`, `model_recovered`, and `manual` always evaluate with the locked wall-clock `now` (closes the remaining part of `ISS-034`).
   - Pipeline per task (`execute_task`): optional verifier (`run_verifier` when `PPM_VERIFIER_ENABLED=true`) -> optional analysis (`run_analysis` when `PPM_ANALYSIS_ENABLED=true`; model failure continues with `analysis_status="model_failed"`) -> reducer + store + `basis_seq` CAS (`create_assessment` inside savepoint; reducer error fails task with redacted `error_reason` and leaves `project_current_assessment` pointer unchanged).
   - Crash recovery (`recover_pending_tasks`): resumes `pending`/`running` tasks without duplicate assessments or duplicate pointer moves.
3. **Scheduler decoupling (`src/eap/ppm/scheduler.py`)**:
   - Removed direct `create_assessment` import/call from `PpmScanScheduler`; post-scan assessment requests now route through `AssessmentOrchestrator.consume_outbox_events(..., event_types=("ppm.snapshot.captured",))` when `PPM_ORCHESTRATOR_ENABLED=true`.
4. **Input locking support (`src/eap/ppm/assessment_inputs.py`, `src/eap/ppm/analysis.py`, `src/eap/ppm/assessments.py`)**:
   - Added `baseline_revision: int | None = None` to `build_assessment_inputs`, `load_locked_analysis_inputs`, and `create_assessment` so in-flight assessments evaluate strictly their locked baseline revision even if a newer baseline is approved concurrently (`C39`).
5. **Rollback flag (`src/eap/config.py`, `.env.example`)**:
   - `PPM_ORCHESTRATOR_ENABLED` (default `true` for fixtures). When `false`, scheduler stops requesting assessments while manual `python -m eap.ppm.cli assess` continues to work.

## 2. Trigger -> dedup key table

| Trigger | Source | `trigger_ref` | Dedup key | Bypasses unchanged scope? |
|---|---|---|---|---|
| `snapshot` | `ppm.snapshot.captured` (outbox) | `project_snapshot_id` (fallback `snapshot_id`) | `(tenant_id, project_id, 'snapshot', project_snapshot_id)` | **Yes** (semantic `inputs_hash` match) |
| `baseline_changed` | `ppm.baseline.approved` (outbox) | `str(baseline_revision)` | `(tenant_id, project_id, 'baseline_changed', str(baseline_revision))` | **No** |
| `evidence_ttl_expired` (evidence) | Evidence TTL timer (`check_evidence_ttl_timers`) | `f"{evidence_id}:{expiry_iso}"` | `(tenant_id, project_id, 'evidence_ttl_expired', f"{evidence_id}:{expiry_iso}")` | **No** (evaluates with `locked_now`) |
| `evidence_ttl_expired` (waiver) | Waiver expiry timer (`check_waiver_expiry_timers`) | `f"waiver:{waiver_id}:{expires_at_iso}"` | `(tenant_id, project_id, 'evidence_ttl_expired', f"waiver:{waiver_id}:{expires_at_iso}")` | **No** (evaluates with `locked_now`) |
| `model_recovered` | Model health (`notify_model_recovered`) | `failed_assessment_id` | `(tenant_id, project_id, 'model_recovered', failed_assessment_id)` | **No** |
| `manual` | Manual command (`trigger_manual`) | `request_id` | `(tenant_id, project_id, 'manual', request_id)` | **No** |

## 3. Conformance evidence (SC-03 JSON)

### C32 (`artifacts/conformance/P2a/C32.json`)
```json
{
  "case": "C32",
  "status": "PASS",
  "tenant_id": "tenant-c32-507c30e4",
  "pointer_before": {
    "proj-app-a": "asmt-731881a5a9e54a18",
    "proj-app-b": "asmt-81f4472e6bcb46da",
    "proj-shared": "asmt-1513ca823d674428"
  },
  "pointer_after": {
    "proj-app-a": "asmt-39de1bdfc46a4a76",
    "proj-app-b": "asmt-81f4472e6bcb46da",
    "proj-shared": "asmt-1513ca823d674428"
  },
  "rollup_total_weight": 4.0
}
```

### C34 (`artifacts/conformance/P2a/C34.json`)
```json
{
  "case": "C34",
  "status": "PASS",
  "tenant_id": "tenant-c34-c822691f",
  "project_id": "proj-c34",
  "scan_a": {
    "assessment_id": "asmt-35d8400af782467f",
    "basis_seq": 2,
    "snapshot_seq": 1,
    "is_current": false
  },
  "scan_b": {
    "assessment_id": "asmt-529866aced924c27",
    "basis_seq": 3,
    "snapshot_seq": 2,
    "is_current": true
  },
  "pointer_before": null,
  "pointer_after": {
    "assessment_id": "asmt-529866aced924c27",
    "basis_seq": 3,
    "snapshot_seq": 2
  }
}
```

### C36 (`artifacts/conformance/P2a/C36.json`)
```json
{
  "case": "C36",
  "status": "PASS",
  "tenant_id": "tenant-c36-14a9d404",
  "project_id": "proj-c36",
  "failed_assessment": {
    "assessment_id": "asmt-c83fd3761fc6445a",
    "basis_seq": 2,
    "analysis_status": "model_failed"
  },
  "recovered_assessment": {
    "assessment_id": "asmt-10727f9138634469",
    "basis_seq": 3,
    "analysis_status": "complete"
  },
  "pointer_before": {
    "assessment_id": "asmt-c83fd3761fc6445a",
    "basis_seq": 2
  },
  "pointer_after": {
    "assessment_id": "asmt-10727f9138634469",
    "basis_seq": 3
  }
}
```

### C39 (`artifacts/conformance/P2a/C39.json`)
```json
{
  "case": "C39",
  "status": "PASS",
  "tenant_id": "tenant-c39-0fbdf910",
  "project_id": "proj-c39",
  "old_baseline_assessment": {
    "assessment_id": "asmt-a5e962dc222449d9",
    "basis_seq": 2,
    "baseline_revision": 1,
    "is_current": false
  },
  "new_baseline_assessment": {
    "assessment_id": "asmt-a30bdfcdc5f248c5",
    "basis_seq": 3,
    "baseline_revision": 2,
    "is_current": true
  },
  "pointer_after": {
    "assessment_id": "asmt-a30bdfcdc5f248c5",
    "basis_seq": 3,
    "baseline_revision": 2
  }
}
```

## 4. Executed checks and real output
Executed on Windows (`PostgreSQL 18.6`, `CI=true`, `TEST_DATABASE_URL=postgresql+psycopg://eap:***@127.0.0.1:5432/eap_test`):
- `uv run pytest tests/ppm/test_assessment_orchestrator.py -v` -> **7 passed, 2 warnings in 5.33s**
- `uv run pytest -q` -> **706 passed, 7 warnings in 253.24s (0:04:13)**
- `uv run python tools/run_sc06.py` -> **SC-06 Verification: ALL CYCLES PASSED** (Cycle 1 empty up/down/up + Cycle 2 seeded down/up through `0011_assessment_orchestrator (head)`)
- `uv run ruff check src tests ppm-v2/reducer migrations` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer` -> **Success: no issues found in 184 source files**
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**
- `uv run python ppm-v2/tools/check_fixtures.py` -> **PASS 23 fixtures, 3 rollup**
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> **PASS 23 fixtures**
- `uv run python tools/check_translation.py` (clean git clone) -> verified post-commit (see `STATUS.md`)
- `sha256sum -c SHA256SUMS.txt` -> verified against regenerated LF checksum manifest
- GitHub Actions CI -> `not_run` before push (branch `wp/P2a-WP6` pushed at end of session)

## 5. Residual risks and rollback
- **Residual risks**:
  - Real Ollama (`DG-3`) and `ISS-041` test-definition hash pinning remain open for Local-pilot (`PPM_VERIFIER_ENABLED` and `PPM_ANALYSIS_ENABLED` remain `false` by default).
  - Timer checks (`check_evidence_ttl_timers`, `check_waiver_expiry_timers`) and `notify_model_recovered` are invoked by the orchestrator loop/API rather than an external OS cron; if the background worker is stopped, timers wait until the next worker tick.
- **Rollback**:
  1. Set `PPM_ORCHESTRATOR_ENABLED=false` -> scheduler stops requesting assessments while manual `cli assess` continues to work.
  2. Schema rollback: `uv run alembic downgrade 0010_hud_sse_and_policy`.
