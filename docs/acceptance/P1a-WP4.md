# Acceptance Packet: P1a-WP4 Dispatcher, Mock Provider, Recovery, Cancel

## 1. Milestone and Scope
- **Work Package**: P1a-WP4 Dispatcher, mock provider, recovery, cancel (plan rev 1.5 §6 P1a-WP4; DG-1 §0 DG1-C1..C3, §3 action slots/C29b, §4 conflicts and unknown outcome, §9 sequences; `docs/adr/sequences.md`; `docs/01`; `docs/02_RUNTIME_AND_SECURITY.md`; D-01 option b).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP4` (branched from `main` at `8151e94`)
- **Code Commit under Review**: (this commit)
- **Base Commit**: `8151e94` ("accept P1a-WP2 and PPM-M0-WP4; owner fixture amendment ISS-021 (s08, s10 authorization lists)")
- **Parallel Work Packages Respected**:
  - P1a-WP5 paths untouched: `src/eap/api/dependencies.py`, `src/eap/config.py`, app startup (`src/eap/api/app.py`).
  - PPM-M0-WP5 paths untouched: `ppm-v2/**`.
  - Published contracts and examples untouched: `contracts/**`, `examples/**`.
  - Migration: No schema changes required; `0001_core` and `0002_task_run` provide all required tables (`leases`, `dispatch_intents`, `task_intents`, `action_executions`, `commands`, `runs`, `run_attempt_history`, `outbox`, `event_log`). Alembic autogenerate diff remains empty.
- **Scope Delivered**:
  1. `src/eap/adapters/mock_provider.py`: Deterministic in-process provider with switches `drop_ack`, `crash_after_send`, `slow_cancel`, `lookup_supported`, and `create_pr` accepting a client token (`task_intent_key`). Observable counters `submissions[idempotency_key]` and `prs_created`.
  2. `src/eap/run/dispatcher.py`:
     - Leases with monotonically increasing fencing tokens per run (`leases` table).
     - Stale fencing token writes rejected with `StaleFencingTokenError` (C15).
     - Dispatch journal in `dispatch_intents` table: `prepared` -> `sent` -> `acknowledged` | `ambiguous`, each step committed BEFORE the next external call.
     - On ambiguous / dropped ACK, run transitions to `unknown` (`remote_outcome=unknown`) and is reconciled via provider lookup when supported.
     - Rollback feature flag `dispatch.enabled`.
  3. `src/eap/action/executor.py`: Action Service with platform slot authority:
     - `action_slot` issued/validated from declared step list and persisted in `task_intents` BEFORE the first tool call.
     - `operation_key` derived via `eap.contracts.keys.derive_operation_key`.
     - `payload_hash` derived via `eap.contracts.keys.derive_payload_hash`.
     - Idempotent deduplication against `action_executions`:
       - Same key + same payload_hash -> returns journaled result without invoking tool.
       - Same key + divergent payload_hash -> HTTP 409 `OPERATION_KEY_CONFLICT`, 0 tool calls (DG1-C3, C29).
     - Agent-proposed undeclared slot after restart mapped back to recovered persisted slot or rejected with `ACTION_SLOT_UNDECLARED` (C29b).
  4. Cancel and Retry API Routes:
     - `POST /v1/runs/{run_id}/cancel`: Cancel while unknown -> command recorded in `commands` table with status `accepted`, run remains `unknown` until reconciled (C21, C06); late result after cancel is rejected with `LateTerminalEventError` (409) and logged.
     - `POST /v1/runs/{run_id}/retry`: Retry while unknown -> HTTP 409 `REMOTE_OUTCOME_UNKNOWN` (DG1-C1).
     - Both endpoints strictly scoped to tenant via server-side principal from `get_principal`.
  5. State changes & HUD projection:
     - All run state changes commit through `RunService` + atomic `outbox` event in one transaction.
     - `src/eap/events/projection.py` extended with `_sync_pending_command` and `get_attention_items`: HUD row asserts `remote_outcome=unknown` and `pending_command.kind=cancel` during the unknown phase (C21).

---

## 2. Mapping of Conformance Cases and Conditions to Evidence

| Case / Condition | Scenario / Requirement | Test Name | SC-03 Artifact | Observed Outcome |
|---|---|---|---|---|
| **C21** | Provider accepts, ACK dropped, worker restarts, user cancels while unknown | `test_c21_lost_ack_reconciliation_and_cancel` | `artifacts/conformance/P1a/C21.json` | `mock.submissions[key] == 1`; state sequence `dispatching` -> `unknown` -> `running` -> `cancelling` -> `cancelled`; cancel command `accepted` while unknown; stale worker fenced; HUD projection row asserts `remote_outcome=unknown` and `pending_command.kind=cancel` during unknown phase, cleared upon cancel completion. |
| **C29** | Provider creates PR, response lost, worker restart, retry, conflict detection | `test_c29_pr_created_lost_ack_retry_and_conflict` | `artifacts/conformance/P1a/C29.json` | Retry while unknown rejected with 409 `REMOTE_OUTCOME_UNKNOWN`; post-reconcile retry attempt reuses `operation_key` and returns journaled PR result (`invoked_tool=False`, `mock.prs_created=1`); retry with differing `resource_ref` yields 409 `OPERATION_KEY_CONFLICT` with 0 tool calls (DG1-C3). |
| **C29b** | Agent proposes `create_pr#2` after restart | `test_c29b_agent_proposes_new_slot_after_restart` | `artifacts/conformance/P1a/C29b.json` | Slot `create_pr#1` persisted in `task_intents` prior to tool call; proposal `create_pr#2` mapped to recovered slot `create_pr#1` (or rejected with `ACTION_SLOT_UNDECLARED` under strict policy); `mock.prs_created == 1`. |
| **C38** | `lookup_supported=False` -> run stays unknown | `test_c38_lookup_unsupported_rejection` | `artifacts/conformance/P1a/C38.json` | Run remains strictly in `unknown` state; retry rejected with 409 `REMOTE_OUTCOME_UNKNOWN`; `mock.submissions[key] == 1` (0 fresh submissions); attention item present in projection queries (DG1-C2). |
| **C15** | Worker with stale fencing token writes | `test_c15_lease_fencing_stale_worker` | `artifacts/conformance/P1a/C15.json` | Worker with stale fencing token (1 < 2) is rejected with `StaleFencingTokenError`; run state remains `queued`, history untouched. |
| **C05** | Worker crash before response | `test_c05_dispatch_journal_recovery` | `artifacts/conformance/P1a/C05.json` | Journal advances to `sent` then `ambiguous`; run enters `unknown`; re-dispatch reconciles without issuing blind resubmissions (`mock.submissions == 1`). |
| **C06** | Late completion after cancellation | `test_c06_late_result_after_cancel_logged_not_applied` | `artifacts/conformance/P1a/C06.json` | Run in `cancelled` state rejects late completion with `LateTerminalEventError` (409); terminal state preserved. |
| **DG1-C1** | No retry while outcome is unknown and unreconciled | `test_c21_lost_ack_reconciliation_and_cancel`, `test_c29_pr_created_lost_ack_retry_and_conflict`, `test_c38_lookup_unsupported_rejection`, `test_api_cancel_and_retry_routes` | `artifacts/conformance/P1a/DG1-C1.json` | Retry via `RunService.retry_new_attempt` or `POST /v1/runs/{run_id}/retry` returns 409 `REMOTE_OUTCOME_UNKNOWN`; mock receives 0 submissions. |
| **DG1-C2** | Adapter without lookup keeps run unknown | `test_c38_lookup_unsupported_rejection` | `artifacts/conformance/P1a/C38.json` | `lookup_supported=False` keeps run in `unknown`; attention item surfaced for operator. |
| **DG1-C3** | Same operation_key with divergent target/arguments rejected | `test_c29_pr_created_lost_ack_retry_and_conflict` | `artifacts/conformance/P1a/DG1-C3.json` | Different `resource_ref` with same key raises `OperationKeyConflictError` (409); mock 0 tool calls. |
| **Crash Matrix** | 3 journal steps x 2 drop_ack modes | `test_crash_matrix` (6 parameterizations) | `artifacts/conformance/P1a/crash_matrix.json` | For all 6 crash points, total external effects at provider never exceed 1; run is never silently re-dispatched while unknown. |

---

## 3. Rollback Procedure
- **Feature Flag**: `dispatch.enabled = false` (or environment variable `DISPATCH_ENABLED=false`).
- **Rollback Behavior**: When disabled, `dispatcher.dispatch` immediately raises `DispatchDisabledError`, claiming no leases, writing no journal records, and contacting no external providers. Verified in `test_dispatch_disabled_feature_flag`.
- **Database Rollback**: No migration was added in this WP (migration head remains `0002_task_run`); database schema is backwards-compatible.

---

## 4. Checks and Verification Results

Executed locally against real PostgreSQL 18.6 (`postgres:18.6-trixie`):
```text
uv run python tools/validate_package.py -> PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed; 5 templates, 20 cases
uv run python ppm-v2/tools/run_negative_cases.py -> PASS 20 negative cases
uv run python tools/check_translation.py -> PASS (37 stale headers detected from prior WPs; no broken links)
uv run pytest -q -> 341 passed in 45.68s (0 skipped, 0 failed against PostgreSQL 18.6)
uv run ruff check src tests ppm-v2/reducer migrations -> All checks passed!
uv run mypy src tests ppm-v2/reducer -> Success: no issues found in 108 source files
npm --prefix web/hud run build -> vite built in 403ms, 0 errors
```

## 5. Next Issues Recorded
- `ISS-022`: Indefinitely accepted cancel commands on runs with unlookupable adapters (C38); operator alert recommended in P3a-WP3 HUD attention queue.
