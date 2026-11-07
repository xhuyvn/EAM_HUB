# Acceptance Packet: P0-WP7 Decision Record DG-1/DG-2

## 1. Milestone and Scope
- **Work Package**: P0-WP7 (Decision record DG-1/DG-2 per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §3, §5, §6 P0-WP7).
- **Target State**: `P0 ready_for_owner_review` (gates owner signature of DG-1 and DG-2 before P1a-WP1).
- **Branch**: `wp/P0-WP7`
- **Code Commit**: `db61cf7`
- **Scope**:
  - Deliver `docs/adr/DG-1.md` covering key derivations, canonical byte encoding, test vectors, `action_slot` lifecycle & persistence, conflict handling, ADR-020 single-relay commit-order offsets, ADR-022 source/scope decoupling, and conformance case enforcement mapping table.
  - Deliver `docs/adr/DG-2.md` covering scan scheduler policies (interval, debounce, catch-up, concurrency lease), the five independent reassessment triggers, input locking at creation time, ADR-021 rev. 2 `basis_seq` Compare-and-Set (CAS) current-assessment pointer rule, and D1-fixture demonstration scenario.
  - Deliver `docs/adr/sequences.md` containing normative Mermaid sequence diagrams for Conformance Cases C29, C29b, C33, C34, and C39, explicitly rendering caller, authorization decision, source of truth, persisted state, failure point, and HUD display.
  - Deliver `migrations/draft/0001_core.sql` (draft, unexecuted) with all 16 core platform tables carrying `tenant_id`, all required PK, UNIQUE, and FK constraints, and comments linking each constraint to the conformance case it enforces.
  - Reconcile `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` ADR-021 to rev. 2 (`basis_seq` CAS) with a revision note, confirming agreement across `contracts/ppm-output.schema.json`, `examples/ppm-output.json`, and `src/eap/contracts/rules.py` (`apply_assessment`).
  - Implement automated test suites:
    - `tools/compute_dg1_vectors.py` and `tests/adr/test_dg1_vectors.py`: recomputes every key and payload hash test vector against the canonical byte encoding.
    - `tests/adr/test_draft_ddl.py`: parses `migrations/draft/0001_core.sql` using pinned PostgreSQL parser `pglast==8.7` to assert table presence (16/16), `tenant_id` on every table, PKs, FKs, and named UNIQUE constraints.

---

## 2. Decisions List

### DG-1 Decisions
1. **Key Derivation and Byte Encoding**:
   - `task_intent_key = sha256(tenant_id + "|" + task_id)`
   - `operation_key = sha256(tenant_id + "|" + task_id + "|" + action_slot)`
   - `dispatch_idempotency_key = sha256(binding_id + "|" + run_id + "|" + operation)` (default operation `"submit"`)
   - `payload_hash = sha256(tool_id + "|" + arguments_sha256 + "|" + resource_ref)`
   - Standardized canonical UTF-8 encoding with ASCII pipe delimiter (`|`, 0x7C), NFC normalization, whitespace trimming, lowercase hexadecimal strings, and empty string representation for null/omitted fields. Verified with 9 canonical test vectors in `DG-1.md`.
2. **`action_slot` Authority and Lifecycle (C29b)**:
   - Authority belongs to the platform (Action Service / Workflow Orchestrator), not autonomous agents.
   - Slot excludes dynamic targets/arguments; persists in `task_intents` prior to initial tool call in the same transaction as execution intent.
   - Upon restart/retry, Action Service recovers persisted slot. Agent proposing a new slot name (e.g. `create_pr#2` after restart) is mapped back to the recovered slot (`create_pr#1`) or rejected with `422/409 ACTION_SLOT_UNDECLARED`, guaranteeing exactly 1 PR created.
3. **Conflict and Unknown Run Handling (C29, C38)**:
   - Matching `operation_key` + identical `payload_hash` -> return journaled result (idempotent replay).
   - Matching `operation_key` + differing `payload_hash` (e.g. altered target repository) -> `409 OPERATION_KEY_CONFLICT`.
   - Run in `unknown` state -> `retry_new_attempt` rejected with `409 REMOTE_OUTCOME_UNKNOWN`. Automated retry blocked pending lookup/reconciliation.
4. **ADR-020 Single-Relay Offsets in Commit Order (C33)**:
   - Business transactions insert events into `outbox` table with **no offset column**.
   - Single relay per tenant stream holds an advisory lock (`pg_advisory_xact_lock(tenant_stream_hash)`).
   - Relay assigns contiguous, strictly monotonic `ingest_offset` in commit order post-commit in dedicated transactions. Eliminates gap races during SSE client replay.
5. **ADR-022 Physical Source vs Project Scope Decoupling (C32)**:
   - One physical directory/repo = one `sources` record (`source_id`).
   - Project scope mapped via `project_scopes` with constraint `UNIQUE (tenant_id, source_id, metadata_root)`.
   - Supports monorepos with multiple projects; shared components referenced via `dependency_refs` and counted once in portfolio aggregations without double-counting. Scope hash comparisons allow unchanged projects to bypass reassessment.

### DG-2 Decisions
1. **Scheduler Policy (C35)**:
   - Module runs on Task/Run Service (avoids OS cron). Configured with `interval_seconds` (default 300s) and `debounce_seconds` (default 5s).
   - Mutual exclusion: at most 1 active scan per physical source (`source_id`), enforced via lease fencing on `ppm_scan_schedules`.
   - Catch-up: startup or wakeup after sleep collapses all overdue intervals into exactly ONE catch-up scan.
2. **Five Independent Reassessment Triggers**:
   - `snapshot`: physical file changes (bypasses unchanged scope).
   - `baseline_changed`: owner approves new baseline revision (evaluates new criteria/test mappings).
   - `evidence_ttl_expired`: evidence exceeds retention TTL (demotes criterion to stale).
   - `model_recovered`: Model Gateway restores health of a failed model profile (re-runs analysis step).
   - `manual`: operator forces evaluation.
   - Deduplicated by `UNIQUE (project_id, trigger, trigger_ref)`. Inputs locked at task creation time.
3. **Current-Assessment Rule (ADR-021 rev. 2, C34, C39)**:
   - Monotonic `basis_seq` issued at assessment-task creation time orders assessment evaluations.
   - Pointer in `project_current_assessment` advances strictly via Compare-and-Set: `basis_seq < :incoming_basis_seq`.
   - Out-of-order scan completion (C34): late older scan finishes with lower `basis_seq`; CAS rejects update; older result stored in history; current pointer remains on newer assessment.
   - Baseline modification on identical code (C39): assessment on new baseline is issued higher `basis_seq`; in-flight older baseline assessment cannot overwrite pointer even if it finishes last.

---

## 3. Reconciliation Verification
- `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` Round 3 table updated for ADR-021 to revision 2 (`basis_seq` CAS) with explicit revision note.
- Verified agreement across:
  - `contracts/ppm-output.schema.json` lines 348-355 (`basis_seq` required, monotonic integer per project ordering assessment inputs).
  - `examples/ppm-output.json` line 117 (`basis_seq: 3`).
  - `src/eap/contracts/rules.py` line 433 (`apply_assessment(current, incoming)` implements CAS comparison: `if inc["basis_seq"] > curr["basis_seq"]`).

---

## 4. Open Questions for Owner Before Signing DG-1/DG-2
1. **External Trusted Adapters without Client Tokens (ISS-014)**: For third-party CLI or webhook adapters lacking client request token support (no idempotency key support), should the platform mandate host-level execution lease locks (preventing concurrent invocations), or treat any unknown outcome as permanently requiring audited human manual retry?
2. **Scheduler Baseline Interval**: Confirm whether the default 300-second (5 minute) interval and 5-second debounce window meet expected interactive pilot responsiveness, or if an aggressive 60-second interval is preferred for small test repositories.
3. **Evidence TTL Granularity**: Default evidence TTL is set to 7 days (D-03). Does the owner require a granular policy allowing specific critical evidence categories (e.g. security vulnerability scans) to enforce shorter TTLs (e.g. 24 hours)?

---

## 5. Issues Opened
- **ISS-013**: Align `action-proposal.schema.json` description from `intent_key` to `action_slot` in contract 1.2 to match DG-1 standard.
- **ISS-014**: Policy for external_trusted adapters lacking client token support: whether to mandate host-level execution lease locks or require audited manual retry.

---

## 6. Execution Evidence

### Dependency Pinning
- Pinned dependency added to `pyproject.toml` dev group: `pglast>=8.7` (resolved `pglast==8.7` in `uv.lock`).
- Parser Primary Documentation: https://pglast.readthedocs.io/ (accessed 2026-11-07).
- Verified via `uv sync --locked`.

### Executed Checks and Real Output

#### 1. Package Contracts Validation (SC-01)
```bash
uv run python tools/validate_package.py
```
**Output**:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

#### 2. PPM v2 Negative Cases
```bash
uv run python ppm-v2/tools/run_negative_cases.py
```
**Output**:
```
template templates/project/.ppm/project.yaml: PASS
template templates/project/.ppm/status.yaml: PASS
template templates/project/.ppm/updates/upd-example-001.yaml: PASS
template templates/project/.ppm/acceptance.yaml: PASS
template templates/registry.example.yaml: PASS
case absolute artifact path: correctly rejected
case parent traversal: correctly rejected
case unknown contract: correctly rejected
case repo grants cloud: correctly rejected
case invalid progress: correctly rejected
case timezone absent: correctly rejected
case recorded_at not a date: correctly rejected
case status updated_at without timezone: correctly rejected
case duplicate YAML keys: correctly rejected
case acceptance file claims approved state: correctly rejected
case registry enables command execution: correctly rejected
case 2.0 document using 2.1 field: correctly rejected
case recorded_at far in the future: correctly rejected
case verification ref with unknown kind: correctly rejected
case duplicate criterion_id: correctly rejected
case supersession cycle: correctly rejected
case supersedes unknown update: correctly rejected
case update references unknown criterion: correctly rejected
case two project_ids in one metadata root: correctly rejected
case valid correction chain accepted: correctly accepted
PASS 5 templates, 20 cases
```

#### 3. PPM v2 Fixture Corpus
```bash
uv run python ppm-v2/tools/check_fixtures.py
```
**Output**:
```
PASS 19 fixtures, 3 rollup
```

#### 4. Translation Staleness and Fixtures (SC-08)
```bash
uv run python tools/check_translation.py --fixture
uv run python tools/check_translation.py
```
**Output**:
```
PASS: Fixture detection verified:
  - Detected removed identifier: 'ARCH-01'
  - Detected changed number: '18.6' -> '18.7'
SC-08 Staleness Report: checking 38 translations against git history...
SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
```

#### 5. Python Test Suite (SC-02)
```bash
uv run pytest -q
```
**Output**:
```
.................................................................        [100%]
65 passed in 1.25s
```
*(Includes 12 tests in `tests/adr/test_dg1_vectors.py` and 7 tests in `tests/adr/test_draft_ddl.py`)*

#### 6. Linting and Typechecking
```bash
uv run ruff check src tests ppm-v2/reducer migrations
uv run mypy src tests ppm-v2/reducer
```
**Output**:
```
All checks passed!
Success: no issues found in 51 source files
```

#### 7. Web HUD Build
```bash
npm --prefix web/hud run build
```
**Output**:
```
> eap-hud@0.1.0 build
> tsc -b && vite build

vite v6.4.5 building for production...
transforming...
✓ 29 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.39 kB │ gzip:  0.27 kB
dist/assets/index-a8RAso0v.css    0.71 kB │ gzip:  0.39 kB
dist/assets/index-DLYonLHq.js   225.08 kB │ gzip: 70.02 kB
✓ built in 551ms
```

#### 8. SHA256 File Checksums
```bash
& "C:\Program Files\Git\usr\bin\sha256sum.exe" -c SHA256SUMS.txt
```
**Output**:
*(All files OK; SHA256SUMS.txt regenerated and verified across the tree)*

---

## 7. Rollback Procedure
This work package produces documentation and draft DDL only. Zero migrations have been executed against any database.
Rollback consists entirely of reverting commits on branch `wp/P0-WP7`:
```bash
git revert --no-edit HEAD
```
or discarding the branch prior to owner acceptance.
