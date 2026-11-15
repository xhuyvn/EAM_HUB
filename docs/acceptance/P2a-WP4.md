# Milestone acceptance — P2a-WP4: Deterministic reducer (embedding PPM-M0)

## 1. Milestone and scope
Work package **P2a-WP4** embeds the pure `ppm_reducer` (`ppm-v2/reducer`) into the platform persistence and API layers without rewriting or modifying reducer rules.

Implemented scope:
1. **Migration `0006_assessments`** (`migrations/versions/0006_assessments.py`, `src/eap/db/assessment_tables.py`):
   - `ppm_baselines`: `(tenant_id, project_id, baseline_revision)` unique; `status IN ('draft', 'approved')`; `criteria` JSONB; `approved_by`, `approved_at`, `source`. Proposed `acceptance.yaml` never auto-approves.
   - `ppm_evidence`: `trust_class` (`verifier_observed|ci_attested|reported_artifact|none`), `producer`, `revision`, `test_id`, `covered_test_ids`, `outcome`, `collected_at`, `output_hash`, `artifact_path`, `simulated` boolean.
   - `ppm_waivers`: approved criterion waivers with `expires_at`.
   - `ppm_basis_seq`: monotonic per-`(tenant_id, project_id)` counter (DG-2 §4.1).
   - `ppm_assessments`: immutable assessment history (`inputs`, `inputs_hash`, `output`, `findings`, `trigger`, `analysis_status`, `previous_assessment_id`, composite tenant-scoped FKs per DG-1 Amendment A1; no UPDATE path).
2. **Input locking & determinism** (`src/eap/ppm/assessment_inputs.py`):
   - `build_assessment_inputs` locks `project_snapshots` row, reads `ppm_updates` (active + supersession chain), `ppm_baselines` (latest `approved` only; draft -> `None` per C44), `ppm_evidence`, `ppm_waivers`, and `status.yaml`/`project.yaml` from the locked `project_snapshots.claims` row (ISS-033 decision).
   - `ppm_notes` are context-only and never create claims or change criterion status.
   - Same DB state produces byte-identical `inputs` and `inputs_hash` (RFC 8785 JCS SHA-256).
3. **Assessment creation & CAS** (`src/eap/ppm/assessments.py`):
   - `create_assessment`: deduplicates unchanged `inputs_hash` against current assessment; atomically allocates `basis_seq` via `ppm_basis_seq`; invokes `ppm_reducer.assess_project`; validates output against `PpmOutput` + `check_ppm_output_rules`; inserts immutable `ppm_assessments` row; advances `project_current_assessment` via `basis_seq` compare-and-set (`WHERE project_current_assessment.basis_seq < EXCLUDED.basis_seq`); emits `ppm.assessment.created` to `outbox` in the same transaction (added to `ISS-032`).
   - Explicit `approve_baseline`, `propose_baseline`, and fixtures-only `load_fixture_evidence` / `load_fixture_waivers` (guarded by `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`).
4. **ISS-033 ingest failure handling** (`src/eap/ppm/collector/collector.py`):
   - Runs post-commit ingest in its own transaction after snapshot commit.
   - Retries once on `IntegrityError` (concurrent first-ingest `ppm_ingest_checkpoints` race -> `ingest_status='ok', already_ingested=True`).
   - On any other exception: rolls back cleanly, emits `ppm.ingest.failed` (`reason`, `project_snapshot_id`) to `outbox` in a fresh transaction, and returns `CollectorScanResult(ingest_status='failed')` without raising.
5. **D0 manual CLI** (`src/eap/ppm/cli.py`):
   - `python -m eap.ppm.cli assess --tenant <t> --project <p>`
   - `python -m eap.ppm.cli approve-baseline --tenant <t> --project <p> --fixture <path>` (fixtures-only)
   - `python -m eap.ppm.cli load-evidence --tenant <t> --project <p> --fixture <path>` (fixtures-only)
6. **Read API** (`src/eap/api/ppm_routes.py`):
   - `GET /v1/ppm/assessments/{id}`: stored output + per-criterion `explanation` with `reported` (claims with artifact path / update_id) and `verified` (`trust_class`, `producer`, `revision`, `baseline_revision`, `covered_test_ids`, `reason_code`, `simulated` flag).
   - `GET /v1/ppm/assessments/{id}/diff?against=<id>`: per-criterion status change and reasons; cross-tenant -> 404; cross-project -> 422.
   - `GET /v1/ppm/projects/{project_id}/assessments`: ordered list (`id`, `basis_seq`, `created_at`, `is_current`).

## 2. Source commit and environment pins
- Base commit: `e9c6fde` (`main`)
- Branch: `wp/P2a-WP4`
- Python: 3.12.13 (`uv` workspace with `ppm-reducer` editable member)
- Database: PostgreSQL 18.6 (`postgres:18.6-trixie` via Docker Compose, `eap_test` database)
- Fixtures-only mode: `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`, zero model/provider calls, zero network egress.

## 3. Rule -> test -> observed table

| Rule / Requirement | Test(s) | Observed Result |
|---|---|---|
| DB parity across all 23 PPM-M0 fixtures | `tests/ppm/test_assessment_parity.py::test_db_parity_matches_direct_reducer_and_expected` (23 parametrized) | **PASS** (20/20 assessments + findings match `ppm_reducer.assess_project` and `expected.json`; 3/3 expected error fixtures `s29`, `s31`, `s32` raise `InputRejected` with exact reason and persist 0 rows) |
| Golden `examples/ppm-output.json` (AC-001/002/003) & Read API | `tests/ppm/test_assessment_golden_and_api.py::test_golden_scenario_and_read_api_endpoints` | **PASS** (AC-001 `contradicted` with `upd-0002`, PASS rev-r1 excluded, FAIL rev-r2 verifier on `test_ac001_export`; AC-002 `verified` at rev-r2; AC-003 `reported_artifact_only` for `reports/ac003-junit.xml`; diff A1->A2; cross-tenant 404; cross-project diff 422) |
| Property: `reported_artifact` never verifies & fixtures-only guard | `tests/ppm/test_assessment_parity.py::test_reported_artifact_never_verifies_and_fixtures_guard` | **PASS** (`s21` never `verified`; `EAP_COLLECTOR_ALLOW_REAL_ROOTS=true` raises `FixtureLoaderDisabledError`) |
| Conformance C08, C31 | `test_db_parity_matches_direct_reducer_and_expected[s01-claim-done-test-failed]`, `[s02a-...]`, `[s02b-...]` | **PASS** |
| Conformance C24 (shared component counted once) | `tests/ppm/test_assessment_parity.py::test_c24_shared_component_counted_once_in_rollup` | **PASS** (`total_weight` excludes consumer dependency; `rollup_portfolio` matches `expected_rollup.json`) |
| Conformance C44 (draft `acceptance.yaml`, no approved baseline) | `tests/ppm/test_assessment_parity.py::test_c44_draft_acceptance_yaml_never_auto_approved` | **PASS** (`baseline_revision=None`, `progress.basis="none"`, `acceptance_readiness="not_ready"`) |
| Determinism, unchanged-inputs dedup, injection note inertness | `tests/ppm/test_assessment_parity.py::test_determinism_dedup_and_note_injection_changes_nothing` | **PASS** (identical `inputs_hash`, `deduplicated=True` on re-run, malicious note changes nothing) |
| DG2-C1 CAS with real parallel DB connections | `tests/ppm/test_assessment_cas_and_iss033.py::test_cas_parallel_db_connections_reverse_completion_never_regresses` | **PASS** (`n+1` commits first, `n` commits second -> pointer remains at `n+1`, `n` retained in history) |
| ISS-033 injected ingest failure + concurrent first ingest race | `tests/ppm/test_assessment_cas_and_iss033.py::test_iss033_*` | **PASS** (snapshot committed, `ingest_status='failed'`, `ppm.ingest.failed` in `outbox`, session usable; concurrent race -> `[('ok', False), ('ok', True)]`) |
| CLI manual triggers (`approve-baseline`, `load-evidence`, `assess`) | `tests/ppm/test_assessment_golden_and_api.py::test_cli_approve_baseline_load_evidence_and_assess` | **PASS** (exit 0, `verification: verified`, `acceptance_readiness: ready_for_review`) |
| DG-1 Amendment A1 FK tenant isolation & autogenerate parity | `tests/db/test_tenant_isolation_fks.py`, `tests/db/test_autogenerate_diff.py` | **PASS** (18/18 FKs include `tenant_id`; autogenerate diff empty) |

## 4. Golden stored assessment JSON and diff JSON

### Stored assessment `output` (`asmt-example-002`)
```json
{
  "as_of": "2026-11-05T09:10:00Z",
  "trigger": "snapshot",
  "criteria": [
    {
      "status": "contradicted",
      "weight": 2.0,
      "required": true,
      "waiver_ref": null,
      "explanation": "Active refutation: verifier/test failure observed at revision rev-r2.",
      "reason_code": "evidence_refuted",
      "criterion_id": "AC-001",
      "deliverable_id": "DEL-001",
      "evidence_trust": "verifier_observed",
      "previous_status": "verified",
      "reported_status": "claimed_done",
      "covered_test_ids": ["test_ac001_export"],
      "missing_evidence": [],
      "owner_project_id": "proj-example",
      "evidence_revision": "rev-r2",
      "reported_claim_refs": ["upd-0002"],
      "refuting_evidence_refs": ["ev-r2-verifier-fail"],
      "supporting_evidence_refs": []
    },
    {
      "status": "verified",
      "weight": 1.0,
      "required": true,
      "waiver_ref": null,
      "explanation": "Verified by verifier_observed evidence at revision rev-r2.",
      "reason_code": "evidence_current_pass",
      "criterion_id": "AC-002",
      "deliverable_id": "DEL-001",
      "evidence_trust": "verifier_observed",
      "previous_status": "unverified",
      "reported_status": "none",
      "covered_test_ids": ["test_ac002_login"],
      "missing_evidence": [],
      "owner_project_id": "proj-example",
      "evidence_revision": "rev-r2",
      "reported_claim_refs": [],
      "refuting_evidence_refs": [],
      "supporting_evidence_refs": ["ev-r2-verifier-pass"]
    },
    {
      "status": "unverified",
      "weight": 1.0,
      "required": true,
      "waiver_ref": null,
      "explanation": "In-repo test report authored by producer cannot verify deliverable criterion.",
      "reason_code": "reported_artifact_only",
      "criterion_id": "AC-003",
      "deliverable_id": "DEL-002",
      "evidence_trust": "reported_artifact",
      "previous_status": "unverified",
      "reported_status": "claimed_done",
      "covered_test_ids": [],
      "missing_evidence": [
        "Independent verifier execution or CI attestation of test_ac003_audit at rev-r2"
      ],
      "owner_project_id": "proj-example",
      "evidence_revision": null,
      "reported_claim_refs": [
        "upd-0002-ac003",
        "artifact:repo/reports/ac003-junit.xml"
      ],
      "refuting_evidence_refs": [],
      "supporting_evidence_refs": []
    }
  ],
  "progress": {
    "basis": "baseline_weighted",
    "total_weight": 4.0,
    "criteria_total": 3,
    "verified_weight": 1.0,
    "criteria_with_current_evidence": 2
  },
  "validity": "current",
  "basis_seq": 2,
  "project_id": "proj-example",
  "snapshot_seq": 2,
  "source_as_of": "2026-11-05T09:00:00Z",
  "verification": "contradicted",
  "assessment_id": "asmt-example-002",
  "evidence_refs": [
    "ev-r2-ac003-reported",
    "ev-r2-verifier-pass",
    "ev-r2-verifier-fail",
    "ev-r1-verifier-run"
  ],
  "snapshot_refs": ["snap-example-r2"],
  "analysis_status": "complete",
  "dependency_refs": [
    {
      "deliverable_id": "DEL-GW-001",
      "observed_status": "verified",
      "owner_project_id": "proj-shared-gateway"
    }
  ],
  "contract_version": "1.1",
  "missing_evidence": [
    "Independent verifier execution or CI attestation of test_ac003_audit at rev-r2"
  ],
  "assessed_revision": "rev-r2",
  "baseline_revision": 1,
  "acceptance_readiness": "not_ready",
  "previous_assessment_id": "asmt-example-001",
  "reported_progress_percent": 100.0
}
```

### Diff JSON (`asmt-example-002` against `asmt-example-001`)
```json
{
  "project_id": "proj-example",
  "from_assessment_id": "asmt-example-001",
  "to_assessment_id": "asmt-example-002",
  "from_basis_seq": 1,
  "to_basis_seq": 2,
  "from_assessed_revision": "rev-r1",
  "to_assessed_revision": "rev-r2",
  "changes": [
    {
      "criterion_id": "AC-001",
      "from_status": "verified",
      "to_status": "contradicted",
      "changed": true,
      "from_reason_code": "evidence_current_pass",
      "to_reason_code": "evidence_refuted",
      "from_evidence_revision": "rev-r1",
      "to_evidence_revision": "rev-r2",
      "reasons": [
        "status: verified -> contradicted",
        "reason_code: evidence_refuted",
        "Active refutation: verifier/test failure observed at revision rev-r2."
      ]
    },
    {
      "criterion_id": "AC-002",
      "from_status": "unverified",
      "to_status": "verified",
      "changed": true,
      "from_reason_code": "evidence_missing",
      "to_reason_code": "evidence_current_pass",
      "from_evidence_revision": null,
      "to_evidence_revision": "rev-r2",
      "reasons": [
        "status: unverified -> verified",
        "reason_code: evidence_current_pass",
        "Verified by verifier_observed evidence at revision rev-r2."
      ]
    },
    {
      "criterion_id": "AC-003",
      "from_status": "unverified",
      "to_status": "unverified",
      "changed": false,
      "from_reason_code": "evidence_missing",
      "to_reason_code": "reported_artifact_only",
      "from_evidence_revision": null,
      "to_evidence_revision": null,
      "reasons": [
        "reason_code: reported_artifact_only",
        "In-repo test report authored by producer cannot verify deliverable criterion."
      ]
    }
  ]
}
```

## 5. ISS-033 decision and evidence
- **Ingest failure handling**: `PpmCollector.scan_source` runs post-commit `ingest_snapshot` in a separate transaction after `db.commit()` of `collector_snapshots` and `project_snapshots`. On `IntegrityError` (concurrent first-ingest race on `ppm_ingest_checkpoints`), it rolls back and retries once, returning `ingest_status="ok", already_ingested=True`. On any other exception, it rolls back, records `ppm.ingest.failed` (`reason`, `project_snapshot_id`) to `outbox` in a fresh transaction, and returns `CollectorScanResult(ingest_status="failed")` without raising, leaving the session clean and usable.
- **Status/acceptance/project payloads**: `build_assessment_inputs` reads `status.yaml` and `project.yaml` directly from the locked `project_snapshots.claims` JSONB row (referenced by `project_snapshot_id`) rather than copying them into new ingest tables. `acceptance.yaml` in `project_snapshots.claims` never auto-approves a baseline.
- **Evidence**: `tests/ppm/test_assessment_cas_and_iss033.py` (both tests PASS on PG 18.6).

## 6. Executed checks and real output
- `CI=true TEST_DATABASE_URL=... uv run pytest -q` -> **620 passed** in 162.83s (PG 18.6)
- `uv run alembic upgrade head / downgrade base / upgrade head / downgrade 0005_ingest / upgrade head` (empty + seeded) -> **PASS** (`0006_assessments (head)`)
- `uv run ruff check src tests ppm-v2/reducer migrations` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer migrations` -> **Success: no issues found in 159 source files**
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**
- `uv run python ppm-v2/tools/check_fixtures.py` -> **PASS 23 fixtures, 3 rollup**
- `uv run python ppm-v2/tools/run_reducer_fixtures.py` -> **PASS 23 fixtures**
- `uv run python tools/check_translation.py` -> **SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.**
- `npm --prefix web/hud run build` -> **PASS** (`vite v6.4.5`, built in 417ms)
- GitHub Actions CI -> **not_run** (branch not pushed to remote in this local session)

## 7. Residual risks
- `contracts/event.schema.json` `event_type` enum does not yet list `ppm.ingest.completed`, `ppm.ingest.failed`, or `ppm.assessment.created` (tracked in `ISS-032`).
- Real verifier sandbox execution arrives in P2a-WP9; D0 uses `simulated=True` evidence rows loaded via `load-evidence --fixture`.

## 8. What D0 needs
- **Script steps** (`scripts/demo_d0.ps1` / `.sh`):
  1. Generate `rev-r1` fixture repo, register source + scope, approve, run collector (`scan_source`).
  2. Run `python -m eap.ppm.cli approve-baseline --tenant <t> --project <p> --fixture <fixture_r1.json>`.
  3. Run `python -m eap.ppm.cli load-evidence --tenant <t> --project <p> --fixture <fixture_r1.json>`.
  4. Run `python -m eap.ppm.cli assess --tenant <t> --project <p> --assessed-revision rev-r1` -> Assessment `A1`.
  5. Commit `rev-r2` changes (note/update/failing & passing simulated verifier evidence), re-run collector + `load-evidence` + `assess` -> Assessment `A2`.
- **Read API endpoints**:
  - `GET /v1/ppm/projects/{project_id}/assessments` (pick `A1` / `A2`)
  - `GET /v1/ppm/assessments/{id}` (render `output` + `explanation.<cid>.reported` / `explanation.<cid>.verified` columns)
  - `GET /v1/ppm/assessments/{A2}/diff?against={A1}` (render per-criterion status transitions and reasons)
- **Fixture**: Any JSON/YAML fixture with `baseline` (`baseline_revision`, `criteria`), `evidence` (`trust_class: verifier_observed`, `simulated: true`), and `waivers` (can reuse `examples/ppm-output.json` scenario).

## 9. Go/no-go
- Status: **ready_for_review** (pending owner review and merge).
- Rollback: downgrade migration `0006_assessments` -> `0005_ingest` (`alembic downgrade 0005_ingest`).
