# Milestone acceptance — D1-fixture (with P2a-WP8 Scan Baseline)

## 1. Milestone and scope
This packet covers **P2a-WP8** ("Scan baseline (measurement)", SC-07) and **D1-fixture** ("sign-off scenario", steps 1–7). Acceptance of D1-fixture requires a **non-author reviewer** to answer the three D1 questions from the HUD alone.

### Part 1 — P2a-WP8 (SC-07 Scan Baseline)
- `tools/bench/scan_baseline.py`: generates fixture Git repositories at `1,000`, `10,000`, and `50,000` files, registers each source/scope once, and measures initial full scan, incremental scan (5 repeats, p50/p95), and unchanged rescan (5 repeats, p50/p95) across collector scan, ingest, assessment, DB size growth, and PostgreSQL WAL growth.
- `docs/measurements/SCAN_BASELINE.md`: records environment, PostgreSQL 18.6 settings, exact command, measured tables across 1k/10k/50k files, and methodology. No acceptance thresholds invented (`ARCH-30` / `SC-07` — thresholds belong to `DG-5`).

### Part 2 — D1-fixture (7-Step Sign-Off Scenario)
- `src/eap/ppm/d1_demo.py`, `scripts/demo_d1.sh` (LF), `scripts/demo_d1.ps1`: idempotent, resettable end-to-end runner exercising real components (`PpmScanScheduler`, `AssessmentOrchestrator`, `PpmCollector`, `ingest_snapshot`, sandboxed `run_verifier` with `PPM_VERIFIER_ENABLED=true` on fixtures, `run_analysis` with `FakeModelProvider`, `MockProvider` + `Dispatcher` + `RunService` + `OutboxRelayWorker`):
  1. Generates a temp fixture repo (`architecture.md`, `blueprint.md`, `implement_plan.md`, `.ppm/`, notes, `reports/ac003-junit.xml`, `src/`, `tests/`), 3 baseline criteria with `verification_refs` (`AC-001` weight 2, `AC-002` weight 1, `AC-003` weight 1), passing `test_ac001_export`, no tests for `AC-002`, and `AC-003` update claim + in-repo `reports/ac003-junit.xml`.
  2. Registers source + scope once, approves location + baseline; `PpmScanScheduler.startup_catch_up` produces **A1** (`AC-001` verified, `AC-002`/`AC-003` unverified) with no manual scan command.
  3. Commits `rev-r2` (breaks `test_ac001_export`, adds note `"Export done"` + update claim `AC-001` done, adds passing `test_ac002_login`); `scan_filesystem_changes` / `tick_interval` triggers scan #2 and verifier reruns mapped tests at `rev-r2` -> **A2** (`AC-001` contradicted, `AC-002` verified, `AC-003` unverified / `reported_artifact_only`).
  4. Injects **C34**: slow `rev-r1` assessment task finishes after `rev-r2` -> `project_current_assessment` pointer stays at **A2**.
  5. Re-triggers scheduler on unchanged data -> zero new assessments, unchanged domain counts. (Includes fix in `src/eap/ppm/orchestrator.py` for post-analysis semantic `inputs_hash` dedup on `trigger='snapshot'`.)
  6. HUD answers all three questions from `A1 -> A2` diff and displays `last_successful_scan_at`, with zero approve/accept buttons.
  7. Exercises **C21 / C29 / C38** with `MockProvider` (`drop_ack=True`, `lookup_supported=False`) relayed to HUD (`lost ACK -> unknown`, `cancel -> single command`, `no retry on unknown`).
- Tests: `tests/ppm/test_demo_d1_smoke.py` (end-to-end + idempotent rerun) and `web/hud/e2e/hud.spec.ts` test #8 (Playwright D1 sign-off scenario asserting all three answers + `last_successful_scan_at`).

---

## 2. Real demo output (`scripts/demo_d1.ps1`)

```text
D1-fixture sign-off demo complete.
  tenant_id:               tenant-owner
  project_id:              proj-d1-demo
  source_id:               src-d1-demo
  rev-r1:                  0ea71308147aff2ef1f936f998d46bbe2b6aa990
  rev-r2:                  16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7
  A1_ID:                   asmt-d1c85ab6c0004a58
  A2_ID:                   asmt-33f57ad4432a4d86
  last_successful_scan_at: 2026-11-16T22:46:58.160045+00:00
  Step 5 counts before:    {"ppm_assessments": 3, "ppm_ingested_artifacts": 6, "ppm_notes": 2, "ppm_updates": 2, "project_snapshots": 2}
  Step 5 counts after:     {"ppm_assessments": 3, "ppm_ingested_artifacts": 6, "ppm_notes": 2, "ppm_updates": 2, "project_snapshots": 3}
  C34 out-of-order:        PASS (pointer=asmt-33f57ad4432a4d86)
  C21/C29/C38 mock:        C21=PASS C29=PASS C38=PASS
  API base URL:            http://127.0.0.1:8000
  HUD URL:                 http://127.0.0.1:5173/
```

---

## 3. Step 5 counts (unchanged data rescan)

| Table | Before Step 5 | After Step 5 | Delta |
|---|---:|---:|---:|
| `ppm_assessments` | 3 | 3 | **0** |
| `ppm_ingested_artifacts` | 6 | 6 | **0** |
| `ppm_notes` | 2 | 2 | **0** |
| `ppm_updates` | 2 | 2 | **0** |
| `project_snapshots` | 2 | 3 | +1 (raw capture recorded; `last_successful_scan_at` updated) |

---

## 4. SC-03 JSON of D1 cases

```json
{
  "tenant_id": "tenant-owner",
  "project_id": "proj-d1-demo",
  "source_id": "src-d1-demo",
  "rev_r1": "0ea71308147aff2ef1f936f998d46bbe2b6aa990",
  "rev_r2": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7",
  "A1_ID": "asmt-d1c85ab6c0004a58",
  "A2_ID": "asmt-33f57ad4432a4d86",
  "last_successful_scan_at": "2026-11-16T22:46:58.160045+00:00",
  "step5_counts_before": {
    "ppm_assessments": 3,
    "ppm_ingested_artifacts": 6,
    "ppm_notes": 2,
    "ppm_updates": 2,
    "project_snapshots": 2
  },
  "step5_counts_after": {
    "ppm_assessments": 3,
    "ppm_ingested_artifacts": 6,
    "ppm_notes": 2,
    "ppm_updates": 2,
    "project_snapshots": 3
  },
  "c34": {
    "case": "C34",
    "status": "PASS",
    "slow_r1_assessment_id": "asmt-8b26397ed1674623",
    "slow_r1_basis_seq": 3,
    "a2_id": "asmt-33f57ad4432a4d86",
    "pointer_after": "asmt-33f57ad4432a4d86"
  },
  "mock_provider": {
    "C21": {
      "task_id": "task-d1-c21-proj-d1-demo",
      "run_id": "run-d1-c21-proj-d1-demo",
      "state": "cancelled",
      "remote_outcome": "known",
      "command_count": 1,
      "status": "PASS"
    },
    "C29": {
      "task_id": "task-d1-c21-proj-d1-demo",
      "retry_blocked_on_unknown": true,
      "command_count": 1,
      "status": "PASS"
    },
    "C38": {
      "task_id": "task-d1-c38-proj-d1-demo",
      "run_id": "run-d1-c38-proj-d1-demo",
      "state": "unknown",
      "remote_outcome": "unknown",
      "retry_blocked": true,
      "status": "PASS"
    }
  }
}
```

---

## 5. Exported A1, A2, and Diff JSON excerpts

### A1 (`asmt-d1c85ab6c0004a58`, `rev-r1`)
```json
{
  "id": "asmt-d1c85ab6c0004a58",
  "project_id": "proj-d1-demo",
  "basis_seq": 2,
  "snapshot_seq": 1,
  "assessed_revision": "0ea71308147aff2ef1f936f998d46bbe2b6aa990",
  "is_current": false,
  "output": {
    "criteria": [
      {
        "criterion_id": "AC-001",
        "status": "verified",
        "reason_code": "evidence_current_pass",
        "reported_status": "claimed_done",
        "evidence_revision": "0ea71308147aff2ef1f936f998d46bbe2b6aa990",
        "supporting_evidence_refs": ["ev-verifier-0ea71308147a-d0ff902f7b88"]
      },
      {
        "criterion_id": "AC-002",
        "status": "unverified",
        "reason_code": "evidence_missing",
        "reported_status": "none"
      },
      {
        "criterion_id": "AC-003",
        "status": "unverified",
        "reason_code": "claim_only",
        "reported_status": "claimed_done"
      }
    ]
  }
}
```

### A2 (`asmt-33f57ad4432a4d86`, `rev-r2`)
```json
{
  "id": "asmt-33f57ad4432a4d86",
  "project_id": "proj-d1-demo",
  "basis_seq": 4,
  "snapshot_seq": 2,
  "assessed_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7",
  "last_successful_scan_at": "2026-11-16T22:46:58.160045+00:00",
  "is_current": true,
  "output": {
    "criteria": [
      {
        "criterion_id": "AC-001",
        "status": "contradicted",
        "reason_code": "evidence_refuted",
        "previous_status": "verified",
        "reported_status": "claimed_done",
        "evidence_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7",
        "refuting_evidence_refs": ["ev-verifier-16d462326bb6-2e4819b92deb"]
      },
      {
        "criterion_id": "AC-002",
        "status": "verified",
        "reason_code": "evidence_current_pass",
        "previous_status": "unverified",
        "reported_status": "none",
        "evidence_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7",
        "supporting_evidence_refs": ["ev-verifier-16d462326bb6-d2c7dfd2a841"]
      },
      {
        "criterion_id": "AC-003",
        "status": "unverified",
        "reason_code": "reported_artifact_only",
        "reported_status": "claimed_done",
        "evidence_trust": "reported_artifact"
      }
    ]
  }
}
```

### Diff (`A1 -> A2`)
```json
{
  "project_id": "proj-d1-demo",
  "from_assessment_id": "asmt-d1c85ab6c0004a58",
  "to_assessment_id": "asmt-33f57ad4432a4d86",
  "from_basis_seq": 2,
  "to_basis_seq": 4,
  "from_assessed_revision": "0ea71308147aff2ef1f936f998d46bbe2b6aa990",
  "to_assessed_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7",
  "changes": [
    {
      "criterion_id": "AC-001",
      "from_status": "verified",
      "to_status": "contradicted",
      "changed": true,
      "from_reason_code": "evidence_current_pass",
      "to_reason_code": "evidence_refuted",
      "from_evidence_revision": "0ea71308147aff2ef1f936f998d46bbe2b6aa990",
      "to_evidence_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7"
    },
    {
      "criterion_id": "AC-002",
      "from_status": "unverified",
      "to_status": "verified",
      "changed": true,
      "from_reason_code": "evidence_missing",
      "to_reason_code": "evidence_current_pass",
      "from_evidence_revision": null,
      "to_evidence_revision": "16d462326bb6f1bf85e184ba14f4d6ecfcd1c2c7"
    },
    {
      "criterion_id": "AC-003",
      "from_status": "unverified",
      "to_status": "unverified",
      "changed": false,
      "from_reason_code": "claim_only",
      "to_reason_code": "reported_artifact_only",
      "from_evidence_revision": null,
      "to_evidence_revision": null
    }
  ]
}
```

---

## 6. How a reviewer answers the three questions

1. **Which criteria advanced?**
   Open the **Project Assessment** tab for `proj-d1-demo` and inspect **Diff: Assessment #2 → #4**. `AC-002` shows `unverified → verified` (`evidence_current_pass`) with `verifier_observed` evidence at `rev-r2` (`16d4623`).
2. **Which evidence belongs to the current revision?**
   In **Criteria: Reported vs Verified** (and the **Excluded Evidence** column), `AC-001`'s `rev-r1` (`0ea7130`) passing evidence is listed under **Excluded Evidence** with badge `revision_mismatch`, while the `rev-r2` (`16d4623`) `eap-verifier` failure (`outcome: failed`) is active. **Last Successful Scan** displays `last_successful_scan_at` (`2026-11-16T22:46:58+00:00` / local time).
3. **Why is an item reported "done" by an agent not accepted?**
   - **Acceptance Readiness** shows `not_ready` and there is no approve/accept button in the HUD.
   - `AC-001` shows `Reported: claimed_done` (`.ppm/updates/upd-0002.yaml`, `"Export done"`) alongside `Verified: contradicted` (`evidence_refuted` at `rev-r2`).
   - `AC-003` shows `Reported: claimed_done` alongside `Verified: unverified` (`reported_artifact_only`) because `reports/ac003-junit.xml` is an in-repo agent-written report, not `verifier_observed` evidence.

---

## 7. Screenshots

- **Step 2 (A1 at `rev-r1`)**: [d1-assessment-a1.png](file:///docs/acceptance/assets/d1-assessment-a1.png)
- **Steps 3–6 (A2 at `rev-r2`, `A1 → A2` diff, excluded evidence, `last_successful_scan_at`, inert XSS note)**: [d1-assessment-a2-diff.png](file:///docs/acceptance/assets/d1-assessment-a2-diff.png)
- **Step 7 (C21/C29/C38 MockProvider runs in Tasks & Runs)**: [d1-tasks-runs-mock.png](file:///docs/acceptance/assets/d1-tasks-runs-mock.png)
- **Step 7 (C38 unknown outcome in Attention Queue)**: [d1-attention-queue.png](file:///docs/acceptance/assets/d1-attention-queue.png)

---

## 8. Exclusions from D1 sign-off (`docs/IMPLEMENTATION_PLAN.md`)
- Real Windows directories (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false` throughout)
- Real Ollama (uses `FakeModelProvider`)
- OIDC (`P1b-WP1`) and multi-user RBAC/ABAC (`P1b-WP2`)
- Real Action Service tools
- Cloud egress (`egress:local-only`)
- Restore drills
- Real adapters (uses `MockProvider`)
- Multi-tenant cross-tenant tests
- Server load testing

---

## 9. Residual items (not in D1 scope)
- **ISS-041**: Test-definition pinning at baseline approval (`test_config_files` hashes recorded, enforcement before Local-pilot).
- **ISS-042** (verifier): Windows / restricted-Linux OS network/PID/quota sandbox (`D-07` before real repositories).
- **ISS-042** (model gateway): Deriving `endpoint_class` from resolved endpoint host instead of self-declared profile field (before real Ollama / `P0-WP6`).
- **ISS-044**: `AssessmentOrchestrator._resolve_binding` fallback to built-in default binding when no active binding is registered (before real provider / Local-pilot).
- **DG-3**: `P0-WP3` (accepted) + `P0-WP6` (Ollama throughput, `not_started`) + `D-06` (egress local-only).

---

## 10. Executed checks and real output (local)
- `uv run python tools/bench/scan_baseline.py --db-url <redacted> --reset-db --scales 1000,10000,50000 --repeats 5 --md-output docs/measurements/SCAN_BASELINE.md` -> **PASS** (`953.819 s` on PG 18.6; results in `docs/measurements/SCAN_BASELINE.md`).
- `$env:CI="true"; uv run pytest -q` (PG 18.6) -> **707 passed, 7 warnings in 261.53s (0:04:21)** (includes `tests/ppm/test_demo_d1_smoke.py`).
- `npm --prefix web/hud run e2e` -> **8 passed (10.5s)** (Playwright Chromium against live FastAPI + PG 18.6 + Vite, incl. test #8 D1 sign-off).
- `npm --prefix web/hud test -- --run` -> **Test Files 2 passed (2), Tests 8 passed (8)**.
- `npm --prefix web/hud run build` -> **tsc -b && vite build -> ✓ 41 modules transformed, built in 432ms**.
- `uv run ruff check src tests ppm-v2/reducer migrations tools/bench/scan_baseline.py` -> **All checks passed!**
- `uv run mypy src tests ppm-v2/reducer tools/bench/scan_baseline.py` -> **Success: no issues found in 187 source files**.
- `uv run python tools/run_sc06.py` -> **SC-06 Verification: ALL CYCLES PASSED** (Cycle 1 empty & Cycle 2 seeded through `0011_assessment_orchestrator`).
- `uv run python tools/validate_package.py` -> **PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed**.
- `uv run python ppm-v2/tools/run_negative_cases.py` -> **PASS 5 templates, 20 cases**.
- `uv run python tools/check_translation.py` -> **SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.** (verified locally and in clean `-c core.autocrlf=false` git clone).
- **GitHub Actions CI** (`wp/D1` code commit `e24b2b5`, run `43975235944`): **PASS** (`validate-and-test` job `133677416174` in `3m50s` + `hud-e2e` job `133677416565` in `1m35s`).

