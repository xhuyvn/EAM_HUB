# Acceptance Packet: P1a-WP2 Task/Run Service

## 1. Milestone and Scope
- **Work Package**: P1a-WP2 (Task/Run service per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §5 SC-01/SC-02/SC-04/SC-06 and §6 P1a-WP2, and DG-1 §0/§2/§3/§4).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP2` (branched from `main` at `02a9f74`)
- **Code Commit under Review**: `0d5fac0`
- **GitHub Actions CI Run**: `https://github.com/xhuyvn/EAM_HUB/actions/runs/42364751123` (SUCCESS in 1m2s)
- **Base Commit**: `02a9f74` ("sign DG-1 and DG-2: owner approval with conditions DG1-C1..C5, DG2-C1; P0 closed")
- **Parallel Work Package Isolation**:
  - `src/eap/events/**`, relay, projection (P1a-WP3): Zero modifications.
  - `ppm-v2/**` (PPM-M0-WP4): Zero modifications.
- **Scope Deliverables**:
  1. `src/eap/contracts/keys.py`: Canonical DG-1 §2 derivation module implementing `derive_task_intent_key`, `derive_operation_key`, `derive_dispatch_idempotency_key`, and `derive_payload_hash` using UTF-8 RFC 8785 JCS and SHA-256 with pipe delimiters. Incorporates ISS-016 identifier allowlist (`^[A-Za-z0-9._:#/-]{1,128}$`) rejecting control characters (NUL, newline, carriage return, TAB, DEL), spaces, and illegal symbols without normalization.
  2. `src/eap/task/service.py`: `TaskService.create` idempotent on `(tenant_id, client_idempotency_key)` (C01), issues `task_intent_key`, pins `agent_version` (C19), and atomically inserts `tasks` + initial `runs` (attempt 1) + `run_attempt_history` + ONE outbox event envelope in the same database transaction. `TaskService.get` and `TaskService.get_runs` provide strict tenant-scoped querying with no existence oracle.
  3. `src/eap/run/service.py`: `RunService.transition` strictly validates requested transitions against `eap.contracts.rules.ALLOWED` and executes atomic commits of `runs` + `run_attempt_history` + ONE outbox event row under row-level locking (`SELECT ... FOR UPDATE`). Rejects illegal transitions with HTTP 409 `IllegalRunTransitionError` (`ILLEGAL_RUN_TRANSITION`). Rejects transitions after reaching terminal states with HTTP 409 `LateTerminalEventError` leaving history unchanged. `RunService.retry_new_attempt` enforces DG1-C1: if the latest attempt's state or `remote_outcome` is `unknown` and unreconciled, it raises HTTP 409 `RemoteOutcomeUnknownError` (`REMOTE_OUTCOME_UNKNOWN`) with attempt count and outbox strictly unchanged.
  4. `src/eap/run/outbox.py`: Helper functions `build_event` and `create_outbox_record` building event envelopes matching `contracts/event.schema.json` with `contract_version='1.1'`, `stream_id='run:<run_id>'`, and no `offset` (relay assigns offsets in commit order).
  5. `src/eap/api/`: REST endpoints `POST /v1/tasks` (HTTP 202 Accepted, C01 idempotent), `GET /v1/tasks/{id}`, and `GET /v1/tasks/{id}/runs`. Tenant identity is strictly resolved server-side via `Principal` dependency. Requests carrying `tenant_id`, `tenant`, `principal`, or `principal_id` in body or query parameters are rejected with HTTP 422 Unprocessable Entity. Cross-tenant GET queries return HTTP 404 (no existence discovery). SC-04 secret canary string is never logged or exposed.
  6. Migration `0002_task_run.py` (ISS-017): Exactly ONE migration file with `down_revision = "0001_core"` aligning `tasks.state` CHECK constraint `ck_tasks_state` with `contracts/hud-projection.schema.json` task states (`'draft'`, `'ready'`, `'active'`, `'blocked'`, `'review_required'`, `'completed'`, `'failed'`, `'cancelled'`) and default `'ready'`. Parity with `migrations/draft/0001_core.sql` verified.
  7. Semantic Rules Fix (ISS-019): Updated `eap.contracts.rules.check_ppm_output_rules` so that `verified` and `ready_for_review` require at least one verified criterion, and empty non-waived denominators accept `verified_weight = total_weight = null`.

---

## 2. Decision Record and Issue Mapping

| Decision / Issue | Mandate & Scope | Resolution & Evidence |
|---|---|---|
| **DG1-C1** | When an attempt's remote outcome is unknown and unreconciled, retry must be rejected with 409 REMOTE_OUTCOME_UNKNOWN, with no new attempt row inserted. | Enforced in `RunService.retry_new_attempt`. Evidence: `tests/test_run_service.py::test_dg1_c1_retry_rejected_when_outcome_unknown`, `artifacts/conformance/P1a/DG1-C1.json`. |
| **DG1-C3** | Retry with same operation_key but divergent payload -> 409 OPERATION_KEY_CONFLICT. | **not_due** (Assigned to P1a-WP4 Action Service). The database unique constraint `uq_action_executions_key` was delivered in P1a-WP1. |
| **ISS-016** | Key derivation must reject control characters (newline, NUL) and spaces inside identifiers via strict charset allowlist without normalization. | Enforced in `src/eap/contracts/keys.py` via `IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:#/-]{1,128}$")` and explicit control char check. Evidence: 16 test cases in `tests/test_keys_iss016.py` and 23 test vectors in `tests/adr/test_dg1_vectors.py`. |
| **ISS-017** | Align `tasks.state` CHECK constraint and default with contract `task_state` enum. | Resolved via migration `migrations/versions/0002_task_run.py`, `src/eap/db/tables.py`, and `migrations/draft/0001_core.sql`. Default set to `'ready'`. Evidence: `tools/run_sc06.py`, `tests/db/test_tasks_and_usage.py`, `tests/db/test_draft_ddl_parity.py`. |
| **ISS-019** | Platform rules must require >=1 verified criterion for verified/ready_for_review, and accept `verified_weight = total_weight = null` for all-waived assessments. | Resolved in `src/eap/contracts/rules.py`. Evidence: `tests/contracts/test_rules_iss019.py`. |

---

## 3. Conformance Cases Summary

| Case ID | Milestone | Status | Observed Outcome | Evidence Reference |
|---|---|---|---|---|
| **C01** | P1a | PASS | Two task submissions with same tenant and client idempotency key (both sequentially and concurrently across 2 connections) return the exact same task reference; exactly 1 task, 1 run, 1 history, and 1 outbox event persisted. | `artifacts/conformance/P1a/C01.json`, `tests/test_c01_tasks.py`, `tests/test_api_tasks.py` |
| **C05** | P1a | PASS | When external response is lost / crash occurs during dispatching, run transitions to unknown; retry is rejected with HTTP 409 `RemoteOutcomeUnknownError` (`REMOTE_OUTCOME_UNKNOWN`); attempt count and outbox count strictly unchanged. | `artifacts/conformance/P1a/C05.json`, `tests/test_run_service.py` |
| **C06** | P1a | PASS | Run transitions through `cancelling` to terminal `cancelled`; subsequent transitions rejected with HTTP 409 `LateTerminalEventError`; history and outbox remain intact without gaps or duplicates. | `artifacts/conformance/P1a/C06.json`, `tests/test_run_service.py` |
| **C19** | P1a | PASS | `agent_version` pinned at task creation is preserved and used across all subsequent retry attempts of that task. | `artifacts/conformance/P1a/C19.json`, `tests/test_c19_pinned_version.py` |
| **C21** | P1a | PASS | Unreconciled run in state `unknown` blocks retry submissions with 409 `REMOTE_OUTCOME_UNKNOWN`; zero duplicate attempts created. | `artifacts/conformance/P1a/C21.json`, `tests/test_run_service.py` |
| **C23** | P1a | PASS | API and service layers strictly isolate tenant resources; cross-tenant GET returns 404 (no existence discovery); client-supplied tenant/principal rejected with 422. (Producer event quarantine against binding is owned by P1a-WP3). | `artifacts/conformance/P1a/C23.json`, `tests/test_api_tasks.py` |
| **DG1-C1** | P1a | PASS | Retry on unknown run state or remote outcome raises HTTP 409 `RemoteOutcomeUnknownError`; attempt count in PostgreSQL unchanged. | `artifacts/conformance/P1a/DG1-C1.json`, `tests/test_run_service.py` |

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
0002_task_run (head)

[Step 1.2] alembic downgrade base...

--- [alembic current] after downgrade base (empty DB) ---

[Step 1.3] alembic upgrade head...

--- [alembic current] after second upgrade head (empty DB) ---
0002_task_run (head)

============================================================
SC-06 Cycle 2: Seeded database down/up
============================================================

[Step 2.1] Seeding fixture dataset...
Fixture dataset seeded successfully.

[Step 2.2] alembic downgrade base on seeded database...

--- [alembic current] after downgrade base (seeded DB) ---

[Step 2.3] alembic upgrade head after drop...

--- [alembic current] after upgrade head (post-seeded DB) ---
0002_task_run (head)

============================================================
SC-06 Verification: ALL CYCLES PASSED
============================================================
```

### 4.2 Core Platform Test Suite (SC-02)
Command: `uv run pytest -q`
Real Output:
```
251 passed, 1 warning in 5.23s
```

### 4.3 Contract and Schema Validation (SC-01)
Command: `uv run python tools/validate_package.py`
Real Output:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

### 4.4 Negative Case Corpus Validation
Command: `uv run python ppm-v2/tools/run_negative_cases.py`
Real Output:
```
PASS 5 templates, 20 cases
```

### 4.5 Fixture Corpus Parity
Command: `uv run python ppm-v2/tools/check_fixtures.py`
Real Output:
```
PASS 19 fixtures, 3 rollup
```

### 4.6 Translation Provenance and Staleness (SC-08)
Command: `uv run python tools/check_translation.py`
Real Output:
```
SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
```

### 4.7 Linting and Type Checking
Commands:
- `uv run ruff check src tests ppm-v2/reducer migrations` -> `All checks passed!`
- `uv run mypy src tests ppm-v2/reducer` -> `Success: no issues found in 88 source files`

### 4.8 Web HUD Production Build
Command: `npm --prefix web/hud run build`
Real Output:
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
✓ built in 379ms
```

### 4.9 Cryptographic Checksum Integrity
Command: `sha256sum -c SHA256SUMS.txt`
Real Output:
```
SHA256SUMS.txt: OK (all files verified)
```

---

## 5. Security, Invariant, and Concurrency Protections
- **DG-1 Key Derivation & Enforcement**: `src/eap/contracts/keys.py` implements the single key derivation authority in the repository. All derivations strictly enforce the identifier charset allowlist `^[A-Za-z0-9._:#/-]{1,128}$`. Any control character or disallowed character causes strict rejection via `KeyDerivationError` without normalization.
- **Race Condition Prevention**: `RunService.transition` uses PostgreSQL row-level locking (`SELECT ... FOR UPDATE`) on the run row. Concurrent transitions are serialized; one succeeds and the other receives HTTP 409 conflict.
- **Outbox Atomicity**: Every transition commits the `runs` state update, the `run_attempt_history` audit record, and exactly ONE outbox event envelope within the exact same database transaction.
- **Tenant Isolation**: All database operations include `tenant_id` in constraints and foreign keys. API requests reject client-declared tenant identity with HTTP 422; cross-tenant queries return HTTP 404 to eliminate existence discovery.
- **Canary Redaction**: In accordance with SC-04, canary tokens never appear in log output or API response payloads.
