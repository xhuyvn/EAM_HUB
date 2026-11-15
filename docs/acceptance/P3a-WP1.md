# P3a-WP1 — HUD Snapshot API Acceptance Packet

- **WP**: `P3a-WP1` (Snapshot API)
- **Branch**: `wp/P3a-WP1` (based on `main` `d2ac95f`; implementation commit `681199b`)
- **Status**: `ready_for_review`
- **Date**: `2026-11-15`
- **Related Issues**: `ISS-037` (P3a-WP5 agent inventory hook), `ISS-038` (`rows[].project` 1.1 contract gap)

---

## 1. Summary of Changes

- Added `hud_cursor_secret` (`EAP_HUD_CURSOR_SECRET`) and `hud_cursor_ttl_seconds` (`EAP_HUD_CURSOR_TTL_SECONDS`, default `3600`, `ge=1`) plus `get_hud_cursor_secret()` in `src/eap/config.py`.
- Implemented `src/eap/hud/snapshot.py` and re-exported public API in `src/eap/hud/__init__.py`:
  - `take_hud_snapshot(...)`: opens one PostgreSQL `REPEATABLE READ READ ONLY` transaction to read domain projection state (`tasks`, `runs`, `commands`, `usage_ledger`, `project_current_assessment`, `ppm_assessments`, `project_snapshots`, `outbox`, committed `event_log`) AND per-stream `MAX(ingest_offset)` atomically under the same MVCC snapshot, computes `projection_revision = max(1, total_offset)`, encodes an opaque cursor `snapshot_cursor`, and validates the full response against `HudProjection` (`contracts/hud-projection.schema.json` 1.1) and `check_hud_projection_rules`.
  - Distinct operator state axes: keeps `task_state`, `run_state`, `remote_outcome`, `connectivity` + `last_observed_at` + `observation_source`, `progress` (percentages/counts only when `total_units >= 1`; otherwise `kind="stage_only"`/`"none"` with `None`), `project` assessment summary, `cost` (`status="unknown"`, `amount_usd=None` when missing, never `0`), and `pending_command` separate.
  - Lost remote provider ACK (`run_state="unknown"`, `remote_outcome="unknown"`) preserves non-null `last_observed_at` and never coerces to `"failed"` or `"succeeded"`.
  - `RowKindBuilder` / `HudSnapshotBuilder.register_row_builder(...)` extensibility hook reserved for `P3a-WP5` (`ISS-037`) agent inventory rows (not implemented in WP1).
  - `replay_events_after_cursor(...)` and `apply_replayed_events_to_snapshot(...)` foundation for `P3a-WP2`, filtering strictly per stream (`ingest_offset > stream_offsets.get(stream_id, 0)`).
- Implemented `src/eap/api/hud_routes.py` (`GET /v1/hud/snapshot`, wired in `src/eap/api/app.py`):
  - Derives `tenant_id` and `viewer_scope_ref` strictly from server-side `get_principal` (`check_forbidden_headers_and_query` enforced; zero direct request `.headers` access).
  - Supports optional `?project_id=` and optional `?cursor=` verification; rejects invalid/tampered/expired/out-of-scope cursors with `resync_required` (`400`/`403`) without leaking tenant/scope/DB details.
  - Fails closed with HTTP `500` (`HUD_CONTRACT_VIOLATION`) + `ERROR` log and never emits a partial snapshot body if contract validation fails.
- Logged `ISS-038` in `docs/ISSUES.md` for the `hud-projection` 1.1 `rows[].project` schema limitation (`additionalProperties: false` with 8 keys excluding `acceptance_readiness`, `assessed_revision`, `last_successful_scan_at`).
- Added PostgreSQL 18.6 test suites: `tests/hud/test_snapshot_consistency.py` and `tests/hud/test_snapshot_api.py`.

---

## 2. Consistency Design & Real Test Output

### Two-Connection `REPEATABLE READ` Barrier + Parallel Concurrency Design (`tests/hud/test_snapshot_consistency.py`)

1. **Barrier test (`test_repeatable_read_barrier_concurrent_write_exact_once_replay`)**:
   - Seeds a task/run plus events at offsets `1..2` (`run.started`, `run.progress` 2/10) for an isolated tenant on real PG 18.6.
   - Starts Reader thread executing `take_hud_snapshot(db_engine, principal=..., on_after_rows_read=barrier)`:
     1. Opens `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY`.
     2. Reads task/run/assessment/event rows (`offsets 1..2`), then triggers `barrier`, signaling Writer and pausing *before* reading `stream_offsets`.
     3. Writer thread commits offsets `3..4` (`run.progress` 7/10, `usage.reported` `$0.42`) in separate transactions and releases `barrier`.
     4. Reader queries `MAX(ingest_offset)` inside the same `REPEATABLE READ` transaction and still sees offset `2` (proving MVCC snapshot consistency; `READ COMMITTED` would read offset `4` and drop events `3..4`).
   - Calls `replay_events_after_cursor` -> returns exact offsets `[3, 4]` (zero gaps, zero duplicates), and `apply_replayed_events_to_snapshot` produces identical `projection_revision == 4` and identical `compute_projection_hash` as a fresh final snapshot.
2. **Parallel writers/readers stress test (`test_parallel_writers_and_snapshot_readers_exact_once_partition`)**:
   - Runs 4 stream writers (10 events each), 1 mid-flight brand-new stream writer (2 events), and 3 concurrent snapshot readers in an 8-worker `ThreadPoolExecutor` (42 total committed events across 5 streams).
   - For every snapshot captured mid-storm, verifies `(covered <= cursor) ∩ (replayed > cursor) == ∅`, `(covered ∪ replayed) == all 42 events`, and row-by-row `compute_projection_hash` equality after replay against the final snapshot.

### Real `tests/hud` Pytest Output (PG 18.6)

```text
$ $env:CI="true"; $env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run pytest tests/hud -v
tests/hud/test_snapshot_api.py::test_contract_schema_and_example_still_validate PASSED [ 10%]
tests/hud/test_snapshot_api.py::test_unknown_cost_and_missing_denominator_never_render_as_zero PASSED [ 20%]
tests/hud/test_snapshot_api.py::test_lost_provider_ack_shows_unknown_remote_outcome_with_last_observed_at PASSED [ 30%]
tests/hud/test_snapshot_api.py::test_cross_tenant_isolation_and_cursor_rejection_without_leaks PASSED [ 40%]
tests/hud/test_snapshot_api.py::test_cursor_opacity_tamper_expiry_and_grant_epoch PASSED [ 50%]
tests/hud/test_snapshot_api.py::test_expired_cursor_rejected_via_http PASSED [ 60%]
tests/hud/test_snapshot_api.py::test_contract_violation_returns_500_without_partial_body_and_logs PASSED [ 70%]
tests/hud/test_snapshot_api.py::test_project_assessment_summary_and_builder_hook PASSED [ 80%]
tests/hud/test_snapshot_consistency.py::test_repeatable_read_barrier_concurrent_write_exact_once_replay PASSED [ 90%]
tests/hud/test_snapshot_consistency.py::test_parallel_writers_and_snapshot_readers_exact_once_partition PASSED [100%]
======================= 10 passed, 2 warnings in 1.56s ========================
```

---

## 3. Example Snapshot JSON from PG Fixtures

Captured directly from `GET /v1/hud/snapshot` (`200 OK`) against PostgreSQL 18.6 fixture rows (`task-fixture-001` running with unknown cost and `task-fixture-002` with lost provider ACK, both linked to `proj-fixture`):

```json
{
  "contract_version": "1.1",
  "projection_revision": 1,
  "snapshot_cursor": "ehc1_AXrGHJdwpiRmUNDsC3AckrODJebuvjM22QHMyiBm18W6BMhgnlNLWHu9ypsJlt1yiTF8noe_z1utfCvEtgjJZoTHQ25PijhdBgrqG9Opnw6QlR7j0ZeDRbCsGFP72PtqUek3PpUr3QbK2k_7jtt-FOg4yW4QZUbY6UDnMMTVnpGJG-vD0dvFdWRQEjw99y1IO6auK_e-2Nc024cLgbZ8nJG9g5DaJoNtPiryDiSdlORfUwewPz90nZ8",
  "as_of": "2026-11-15T13:26:53.453877Z",
  "viewer_scope_ref": "principal:owner-a/tenant:tenant-a/all-projects",
  "rows": [
    {
      "task_id": "task-fixture-001",
      "task_state": "active",
      "run_id": "run-fixture-001",
      "attempt": 1,
      "run_state": "running",
      "remote_outcome": "known",
      "connectivity": "healthy",
      "last_observed_at": "2026-11-15T10:00:00Z",
      "observation_source": "local_runtime",
      "progress": {
        "kind": "none",
        "completed_units": null,
        "total_units": null,
        "unit": null,
        "stage": null
      },
      "project": {
        "project_id": "proj-fixture",
        "assessment_id": "assess-fixture-1",
        "verification": "partially_verified",
        "validity": "current",
        "acceptance": "not_requested",
        "assessment_as_of": "2026-11-15T09:45:00Z",
        "verified_weight": 3.0,
        "total_weight": 5.0
      },
      "cost": {
        "status": "unknown",
        "amount_usd": null,
        "as_of": null,
        "scope": "run"
      },
      "pending_command": null
    },
    {
      "task_id": "task-fixture-002",
      "task_state": "active",
      "run_id": "run-fixture-002",
      "attempt": 1,
      "run_state": "unknown",
      "remote_outcome": "unknown",
      "connectivity": "unknown",
      "last_observed_at": "2026-11-15T10:00:00Z",
      "observation_source": "provider_ack",
      "progress": {
        "kind": "none",
        "completed_units": null,
        "total_units": null,
        "unit": null,
        "stage": null
      },
      "project": {
        "project_id": "proj-fixture",
        "assessment_id": "assess-fixture-1",
        "verification": "partially_verified",
        "validity": "current",
        "acceptance": "not_requested",
        "assessment_as_of": "2026-11-15T09:45:00Z",
        "verified_weight": 3.0,
        "total_weight": 5.0
      },
      "cost": {
        "status": "unknown",
        "amount_usd": null,
        "as_of": null,
        "scope": "run"
      },
      "pending_command": null
    }
  ]
}
```

---

## 4. Cursor Format, Security Properties, and `resync_required` Semantics (P3a-WP2)

- **Format**: `"ehc1_" + base64url_nopad(version(1B=0x01) || nonce(16B os.urandom) || ciphertext || tag(32B))`.
- **Plaintext JSON (encrypted before encoding)**: `{"v": 1, "tid": tenant_id, "scp": viewer_scope_ref, "off": total_offset, "so": {stream_id: max_ingest_offset, ...}, "ge": grant_epoch, "iat": unix_ts, "exp": unix_ts + ttl}`.
- **Cryptographic design (Encrypt-then-MAC)**:
  - Keys derived via HMAC-SHA256 domain separation (`eap-hud-cursor-enc-v1`, `eap-hud-cursor-mac-v1`) from `Settings.get_hud_cursor_secret()`.
  - Stream cipher XORs plaintext with `HMAC-SHA256(enc_key, nonce || counter_be32)` blocks; a fresh 128-bit random nonce ensures identical snapshot states yield unlinkable opaque cursors and hides raw DB IDs, stream IDs, tenants, and offsets from both the token string and base64-decoded bytes.
  - Full 256-bit HMAC-SHA256 tag over `version || nonce || ciphertext` is verified with `hmac.compare_digest` **before** decryption.
- **`resync_required` protocol for P3a-WP2**:
  - `CursorTamperedError` / `CursorExpiredError` -> HTTP `400` (`{"detail": "...", "code": "resync_required", "resync_required": true}`).
  - `CursorScopeMismatchError` (cross-tenant cursor, viewer scope mismatch, or `grant_epoch` change) -> HTTP `403` (`{"detail": "...", "code": "resync_required", "resync_required": true}`) with generic error text that omits expected/actual tenant or scope identity.
  - Client/SSE handler on `resync_required == true` discards buffered stream state and re-fetches `GET /v1/hud/snapshot`.

---

## 5. Verification Ledger

| Check | Command | Real Result |
|---|---|---|
| HUD suites (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="..."; uv run pytest tests/hud -v` | PASS (`10 passed, 2 warnings in 1.56s`) |
| Full pytest suite (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="..."; uv run pytest -q` | PASS (`633 passed, 5 warnings in 181.39s`) |
| Lint | `uv run ruff check src tests ppm-v2/reducer migrations` | PASS (`All checks passed!`) |
| Type check | `uv run mypy src tests ppm-v2/reducer` | PASS (`Success: no issues found in 156 source files`) |
| SC-01 Package contracts & fixtures | `uv run python tools/validate_package.py` | PASS (`16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed`) |
| PPM negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | PASS (`5 templates, 20 cases`) |
| PPM fixtures | `uv run python ppm-v2/tools/check_fixtures.py` | PASS (`23 fixtures, 3 rollup`) |
| PPM reducer fixtures | `uv run python ppm-v2/tools/run_reducer_fixtures.py` | PASS (`23 fixtures`) |
| SC-08 Translation staleness | `uv run python tools/check_translation.py` | PASS (`38 up-to-date, 0 stale, 0 missing headers`) |
| SC-06 Migrations round-trip | `$env:TEST_DATABASE_URL="..."; uv run python tools/run_sc06.py` | PASS (`ALL CYCLES PASSED`) |
| Web HUD unit tests | `npm --prefix web/hud test` | PASS (`1 passed (1)`, `2 passed (2)`) |
| Web HUD build | `npm --prefix web/hud run build` | PASS (`built in 412ms`) |
| Integrity checksums | `sha256sum -c SHA256SUMS.txt` | Re-verified after commit/doc updates (see `STATUS.md`) |
| GitHub Actions CI | `wp/P3a-WP1` push workflow | `not_run` locally at packet creation (queued on `git push -u origin wp/P3a-WP1`) |

---

## 6. Residual Risks

1. **Contract 1.1 `rows[].project` omission (`ISS-038`)**: `acceptance_readiness`, `assessed_revision`, and `last_successful_scan_at` are populated in `ProjectAssessmentSummary` but omitted from JSON output until `contracts/hud-projection.schema.json` 1.2 permits them.
2. **Symmetric local cursor secret default**: local/test configurations fall back to a static HMAC key when `EAP_HUD_CURSOR_SECRET` is unset; deployed environments must configure a dedicated secret (`EAP_HUD_CURSOR_SECRET`) and rotate it via cursor expiry / `resync_required`.
3. **Unlinked multi-project task Heuristic**: when a tenant has `>1` project in `project_current_assessment` and a task/run has no explicit `project_id` in its event/outbox/command payloads, `rows[].project` remains `null` rather than guessing; `tasks` table itself does not carry a `project_id` FK in schema v0006.
4. **`grant_epoch` placeholder**: cursors encode and enforce `grant_epoch=0` until `P1b-WP3` / grants store lands dynamic principal epochs.
