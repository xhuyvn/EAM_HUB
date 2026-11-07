# Acceptance Packet: P1a-WP3 Outbox Relay, Ingestion, and Projection

## 1. Milestone and Scope
- **Work Package**: P1a-WP3 Outbox relay, ingestion, projection (per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P1a-WP3, DG-1 §5 ADR-020, DG-2 §0 DG2-C1, `docs/03_HUD_SPEC.md`).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP3` (branched from `main` at `02a9f74`)
- **Code Commit under Review**: `af1739c`
- **Base Commit**: `02a9f74` ("accept P1a-WP1 and PPM-M0-WP3; fix mypy in reducer tests; register ISS-019")
- **Parallel Work Packages Respected**:
  - P1a-WP2 paths untouched (`src/eap/task/**`, `src/eap/run/**`, `src/eap/api/**`, `src/eap/contracts/keys.py`, `rules.py`).
  - PPM-M0-WP4 paths untouched (`ppm-v2/**`).
  - Published contracts and examples untouched (`contracts/**`, `examples/**`).
  - No changes to `docs/ISSUES.md` (P1a-WP2 owns numbering; new issues listed below as `ISS-NEW-n`).
- **Scope Delivered**:
  - `migrations/versions/0002_events.py`: Exactly one Alembic migration with `down_revision = "0001_core"`. Creates tenant-scoped tables:
    - `outbox_relay_marks`: Tracks outbox rows relayed (`outcome IN ('ingested', 'duplicate', 'quarantined')`, `ingest_offset`, `relayed_at`); outbox itself remains append-only and offset-free per ADR-020.
    - `stream_heads`: Tracks per-(tenant, stream) monotonic offset head and per-producer sequence numbers for gap detection.
    - `event_quarantine`: Quarantined events from ingestion failures (C02 content conflict, C23 tenant mismatch, producer authorization failures).
    - `security_events`: Security findings raised during ingestion with tenant isolation.
    - `projection_checkpoints`: Versioned HUD projection checkpoints per `(tenant_id, stream_id, projection_name)` with canonical RFC 8785 state hash.
  - `src/eap/db/event_tables.py` & `src/eap/db/__init__.py`: SQLAlchemy Core table metadata definitions registered with core metadata; catalog FK invariant (all 7 FKs tenant-scoped, no cross-table leaks) preserved.
  - `src/eap/events/binding.py`: Authoritative producer binding context (`ProducerBinding`, `StaticBindingProvider`). Enforces that tenant identity and capabilities are bound from governance, never inferred or trusted from the event payload.
  - `src/eap/events/quarantine.py`: Quarantine and security event persistence helpers.
  - `src/eap/events/projection.py`: Deterministic, versioned projection reducer folding events into HUD run view (`contracts/hud-projection.schema.json`). Replay invariant satisfied: replay from ANY cursor `k` matches full rebuild and fresh snapshot state hash. Unknown remote outcome and unknown cost stay explicitly unknown.
  - `src/eap/events/ingest.py`: Ingestion pipeline enforcing:
    1. Producer authorization & tenant check from binding (C23).
    2. Same `event_id` different content -> quarantine with `event_content_conflict`, no overwrite (C02).
    3. Same `event_id` same content -> idempotent duplicate no-op.
    4. Gap detection per producer within stream.
    5. Append to `event_log` with strictly increasing `ingest_offset`.
  - `src/eap/events/relay.py`: Transactional single relay worker holding PostgreSQL `pg_advisory_xact_lock` per `(tenant_id, stream_id)`. Serializes concurrent workers, assigns offsets post-commit strictly in commit order (C33), marks outbox relayed, updates projection checkpoint, all within one transaction. Crash-safe without duplicates or gaps.
  - `src/eap/config.py`: Added feature flag `relay_enabled: bool = True` (`RELAY_ENABLED`). Disabling the relay halts all outbox processing while keeping un-relayed outbox rows completely intact in PostgreSQL.

---

## 2. Conformance Cases Evidence (C02, C23, C33)

### 2.1 C33: Outbox Relay Offset Assignment in Commit Order
- **Requirement**: Two concurrent transactions obtain outbox IDs 10 and 11, force 11 to COMMIT before 10 (two connections + barrier); relay assigns offsets in commit order; replay from every cursor matches fresh snapshot; 0 lost, 0 duplicated events.
- **Evidence**: `tests/events/test_c33_relay_commit_order.py::test_c33_relay_commit_order_offsets`
  - Conn 1 opened, began Tx 10 (`evt-tx-10`, `run.started`), obtained outbox ID $N$.
  - Conn 2 opened, began Tx 11 (`evt-tx-11`, `run.progress`), obtained outbox ID $N+1$.
  - Barrier: Tx 11 committed first. Tx 10 held uncommitted.
  - Relay polled: processed Tx 11 (the only visible committed row), assigned `ingest_offset = 1`.
  - Tx 10 committed second.
  - Relay polled: processed Tx 10, assigned `ingest_offset = 2`.
  - Verified `event_log`: offset 1 = `evt-tx-11`, offset 2 = `evt-tx-10` (strict commit order).
  - Replay verified: `replay_stream(cursor=0)` == `replay_stream(cursor=1)` == `replay_stream(cursor=2)` == `build_fresh_snapshot()` (hash matches across all cursors, 0 lost, 0 duplicated).
- **SC-03 Artifact**: `artifacts/conformance/P1a/C33.json` (`status: "passed"`).

### 2.2 C02: Same Event ID Different Content Conflict Quarantine
- **Requirement**: Same `event_id` with different content -> quarantine, no overwrite; same `event_id` with same content -> idempotent no-op.
- **Evidence**: `tests/events/test_c02_c23_ingestion_security.py`:
  - `test_c02_event_content_conflict_quarantine`: Initial event `evt-c02-target` ingested (offset 1). Conflicting event with same `event_id` and divergent payload submitted. Relay flagged conflict, inserted row in `event_quarantine` with `reason="event_content_conflict"`, inserted security event in `security_events`, left `event_log` un-overwritten, and baseline projection hash remained identical.
  - `test_c02_idempotent_duplicate_no_op`: Duplicate event with identical payload submitted. Relay flagged as duplicate, marked `outbox_relay_marks.outcome = 'duplicate'`, 0 quarantine rows, 0 duplicate appends in `event_log`.
- **SC-03 Artifact**: `artifacts/conformance/P1a/C02.json` (`status: "passed"`).

### 2.3 C23: Producer Event with Tenant ID Differing from Binding
- **Requirement**: Producer authorization and tenant taken from binding, never from event body. Event `tenant_id != binding tenant` -> quarantine + security event, projection unchanged.
- **Evidence**: `tests/events/test_c02_c23_ingestion_security.py::test_c23_tenant_mismatch_quarantine_and_security_event`:
  - Producer authorized for `tenant-corp-c23`.
  - Injected forged event envelope with `tenant_id = "tenant-attacker-c23"`.
  - Relay quarantined event with `reason="tenant_mismatch"` under `tenant-corp-c23`.
  - Recorded security event in `security_events` table (`kind="tenant_mismatch"`).
  - Asserted `event_log` has 0 rows appended for forged event.
  - Verified projection state hash was unchanged.
- **SC-03 Artifact**: `artifacts/conformance/P1a/C23.json` (`status: "passed"`).

---

## 3. Concurrency, Crash Safety, and Property Verification

### 3.1 Concurrent Relays Serialized by Advisory Lock
- **Evidence**: `tests/events/test_concurrent_relays_lock.py::test_concurrent_relays_serialized_by_advisory_lock`
- 40 events seeded in outbox.
- Two independent `OutboxRelayWorker` instances executed concurrently in parallel threads against real PostgreSQL 18.6 with separate connections.
- Transaction-level advisory lock (`pg_advisory_xact_lock(stream_advisory_lock_id(tenant, stream))`) serialized execution.
- Result: 40/40 events ingested, offsets strictly contiguous (1..40), 0 concurrency exceptions, 0 duplicate marks.

### 3.2 Relay Crash and Transaction Atomicity Recovery
- **Evidence**: `tests/events/test_relay_crash_recovery.py::test_relay_crash_abort_recovery`
- 3 outbox events seeded. Worker simulated crash / rollback mid-relay before transaction commit.
- Verified 0 rows persisted in `event_log` or `outbox_relay_marks`.
- Rerun relay: all 3 events ingested cleanly with contiguous offsets (1, 2, 3), stream head updated to 3, projection checkpoint saved.
- Subsequent run on empty stream returned idle with 0 duplicates.

### 3.3 Property-Based Testing (Hypothesis)
- **Evidence**: `tests/events/test_property_commit_order_projection.py`
  - `test_property_replay_from_any_cursor_matches_full_rebuild`: Tested random sequences of events; proved that replaying suffix from ANY cursor $k \in [0..N]$ on top of prefix checkpoint matches full rebuild state hash 100%.
  - `test_property_unknown_cost_and_remote_outcome_stay_explicit`: Proved initial HUD state and unknown events preserve explicit `unknown` status and `amount_usd = None` (never converted to 0.0 or known).

### 3.4 Gap Detection per Stream
- **Evidence**: `tests/events/test_c02_c23_ingestion_security.py::test_gap_detection_recorded_in_stream_heads`
- Producer jumped from `producer_seq = 1` to `producer_seq = 4`.
- Gap was detected and recorded in `stream_heads.producer_state["gaps"]` with `expected_seq = 2`, `received_seq = 4`.

---

## 4. Unthresholded Throughput Smoke Benchmark (DG1-C4 Preparation)

- **Test**: `tests/events/test_throughput_smoke.py::test_outbox_relay_10k_throughput_smoke`
- **Environment**: Local PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2 on x86_64), containerized, batch size = 1000.
- **Measured Metrics**:
  - Outbox seeded with 10,000 events: **0.33s**
  - Relay execution for 10,000 events across 10 transaction batches: **35.70s**
  - Unthresholded Throughput: **280.1 events/s**
  - Total events ingested: **10,000 / 10,000**
  - Lost events: **0**
  - Duplicated events: **0**
- *Note*: This smoke test establishes local baseline performance and indicates readiness for the formal 200 events/s gate in P1a-WP7.

---

## 5. Verification Commands and Real Output

### 5.1 Shared Checks Suite
```bash
# 1. Package validation (SC-01)
uv run python tools/validate_package.py
# Output: PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed

# 2. PPM v2 negative cases
uv run python ppm-v2/tools/run_negative_cases.py
# Output: PASS 5 templates, 20 cases

# 3. PPM v2 fixture corpus check
uv run python ppm-v2/tools/check_fixtures.py
# Output: PASS 19 fixtures, 3 rollup

# 4. Translation staleness check (SC-08)
uv run python tools/check_translation.py
# Output: SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.

# 5. Alembic migration cycle (SC-06)
uv run python tools/run_sc06.py
# Output:
# SC-06 Cycle 1: Empty database up/down/up -> PASSED (0002_events head)
# SC-06 Cycle 2: Seeded database down/up -> PASSED (0002_events head)
# ALL CYCLES PASSED

# 6. Python test suite (SC-02)
uv run pytest -q
# Output: 222 passed, 1 warning in 42.06s

# 7. Lint and typecheck
uv run ruff check src tests ppm-v2/reducer migrations
# Output: All checks passed!
uv run mypy src tests ppm-v2/reducer
# Output: Success: no issues found in 88 source files

# 8. Web HUD build
npm --prefix web/hud run build
# Output: built in 404ms (dist/index.html, dist/assets)
```

---

## 6. Rollback Procedure
- The relay worker checks `relay_enabled` from platform settings (`RELAY_ENABLED` env var).
- If `relay.enabled = false`:
  - `OutboxRelayWorker.relay_stream_batch` immediately returns `status="disabled"`, polling 0 rows and performing 0 database mutations.
  - Un-relayed rows remain durably stored in the `outbox` table.
  - Re-enabling the flag (`relay.enabled = true`) causes the relay to resume processing the un-relayed outbox backlog in commit order with zero data loss.
- Documented and verified in `tests/events/test_rollback_feature_flag.py`.

---

## 7. New Issues Logged
- **ISS-NEW-1**: Migration revision chain: `migrations/versions/0002_events.py` has `down_revision = "0001_core"`. When merging parallel branch `wp/P1a-WP2`, the owner will re-chain the migration down_revision to point to P1a-WP2's migration without introducing merge conflicts, per instructions.

---

## 8. Recommendation and Readiness
All acceptance criteria for work package P1a-WP3 are met with empirical evidence against real PostgreSQL 18.6. P1a-WP3 is recommended for owner review and set to `ready_for_review`.
