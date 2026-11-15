# P3a-WP2 — SSE Replay & Permission Revocation Acceptance Packet

- **WP**: `P3a-WP2` (SSE: replay and permission revocation)
- **Milestone / Scope**: `P3a` (HUD Backend Streaming, Revocation, Cursor AEAD & Bounded Snapshots)
- **Branch**: `wp/P3a-WP2` (based on `main` `55b4434`)
- **Status**: `ready_for_review`
- **Date**: `2026-11-15`
- **Dependencies / Runtime**: Python `3.12.13`, `cryptography==50.0.2` (added via `uv lock`), PostgreSQL `18.6` (`eap_test`), Vite + React HUD (`eap-hud@0.1.0`)
- **Related Issues**: `ISS-039` (`resolved`), `ISS-040` (`resolved`; full SC-07 baseline stays in `P3a-WP4`)
- **Conformance Cases**: `C09`, `C10`, `C28`, `C30` (`artifacts/conformance/P3a/{C09,C10,C28,C30}.json`)

---

## 1. Summary of Changes

- **`ISS-039` Cursor Key Handling & AEAD Upgrade** (`pyproject.toml`, `uv.lock`, `src/eap/config.py`, `src/eap/hud/snapshot.py`):
  - Removed the constant fallback cursor secret (`OLD_INSECURE_HUD_CURSOR_SECRET`); when `EAP_HUD_CURSOR_SECRET` is unset or blank, `Settings.get_hud_cursor_secret()` generates an ephemeral per-process 32-byte key (`os.urandom(32)`) and logs a `WARNING` (existing cursors require `resync_required` across restarts). Configuring the retired static secret raises `ValueError`.
  - Added `cryptography>=43.0.0` (`50.0.2` locked) and replaced the custom HMAC-CTR XOR stream with `AESGCM` (`ehc2_` wire version `2`, 12-byte random nonce, 16-byte AEAD tag, domain-separated key derivation + `_CURSOR_AAD = b"eap-hud-cursor-v2:\x02"`). Legacy `ehc1_` and retired-key cursors fail verification with `CursorTamperedError` (`resync_required`).
- **Database Schema & Reversible Migration `0009_hud_sse_and_policy`** (`migrations/versions/0009_hud_sse_and_policy.py`, `migrations/draft/0001_core.sql`, `src/eap/db/tables.py`, `src/eap/db/policy_tables.py`, `tools/run_sc06.py`):
  - Added nullable `project_id` (`VARCHAR(64)`) columns and tenant-composite lookup indexes (`ix_tasks_tenant_project`, `ix_runs_tenant_project`) on `tasks` and `runs`, plus `ix_event_log_tenant_id_pk` on `event_log(tenant_id, id)`.
  - Added `principal_grant_epochs`, `principal_project_grants`, and `stream_retention_floors` tables.
  - Updated `TaskService.create` (`src/eap/task/service.py`) and `RunService` (`src/eap/run/service.py`) to persist `project_id` on `tasks` and `runs`.
- **Policy Grants, `grant_epoch`, and Retention Floors** (`src/eap/policy/grants.py`, `src/eap/events/relay.py`):
  - Implemented server-side per-`(tenant_id, principal_id)` `grant_epoch` and per-project grant state (`revoke_project_access`, `grant_project_access`, `get_grant_epoch`, `get_revoked_project_ids`, `is_project_authorized`, `set_stream_retention_floor`).
  - `revoke_project_access` and `grant_project_access` bump `grant_epoch`, record `granted=False`/`True`, and atomically enqueue + relay `policy.changed` (`policy:<tenant_id>`). `OutboxRelayWorker` skips `hud_runs` projection updates for `policy:*` / `policy.changed` streams.
- **SSE Endpoint & Live Revocation** (`src/eap/hud/sse.py`, `src/eap/api/hud_routes.py`, `src/eap/api/app.py`):
  - Implemented `GET /v1/hud/events` accepting `Last-Event-ID` header (highest precedence), `?cursor=`, or `?after=`.
  - Streams post-cursor events strictly after watermark offsets with zero gaps and zero duplicates, emitting a fresh `ehc2_` `id:` on every SSE frame and periodic `: heartbeat` comments.
  - Rejects expired, retention-gapped (`CursorRetentionGapError`), tampered, cross-tenant, scope-mismatched, or epoch-stale cursors with a single terminal `event: resync_required` frame and closes cleanly without leaking tenant or scope strings.
  - Bounds per-subscriber delivery buffers with `asyncio.Queue(maxsize=max_buffer_size)`; queue overflow drains stale buffered frames, emits a single terminal `resync_required` (`reason: "slow_consumer"`), and closes cleanly.
  - Detects server-side `grant_epoch` / `principal_project_grants` updates live on open SSE connections, emits `row.removed` for revoked project rows, stops emitting events for revoked projects (including defense-in-depth wire flush check), and closes stream with `resync_required` when scoped explicitly to a revoked `project_id`.
  - Added bootstrap grant mutation routes (`POST /v1/hud/grants/revoke`, `POST /v1/hud/grants/restore`, owner/admin gated) and API-only artifact/evidence routes (`GET /v1/artifacts/{artifact_id}`, `GET /v1/evidence/{evidence_id}`) enforcing per-request authorization (`signed_url=null`, `403 PERMISSION_REVOKED` after revocation).
- **`ISS-040` Bounded Snapshot Reads** (`src/eap/hud/snapshot.py`):
  - Eliminated `outbox` and full `event_log` scans from `take_hud_snapshot` using indexed `tasks.project_id` / `runs.project_id`, `projection_checkpoints` (`hud_runs`), `stream_heads`, and bounded post-checkpoint tail queries (`event_log.id > checkpoint`), while retaining exact MVCC watermark parity under `REPEATABLE READ READ ONLY`.

---

## 2. Cursor Format & Key Handling (`ISS-039`)

- **Token Wire Format**: `"ehc2_" + base64url_nopad(version(1B=0x02) || nonce(12B os.urandom) || ciphertext_with_16B_tag)`
- **Authenticated Associated Data (AAD)**: `b"eap-hud-cursor-v2:\x02"`
- **Encrypted JSON Plaintext**:
  `{"exp": unix_ts, "ge": grant_epoch, "iat": unix_ts, "lid": last_event_id, "off": total_offset, "scp": viewer_scope_ref, "so": {stream_id: ingest_offset, ...}, "so_om": bool, "tid": tenant_id, "v": 2}`
- **Key Management (`src/eap/config.py`)**:
  - When `EAP_HUD_CURSOR_SECRET` is configured, its UTF-8 secret is domain-separated via `HMAC-SHA256(secret, b"eap-hud-cursor-aead-aes256gcm-v2")` into a 256-bit `AESGCM` key.
  - Setting `EAP_HUD_CURSOR_SECRET` to the retired constant key raises `ValueError("EAP_HUD_CURSOR_SECRET must not be set to the retired static key (ISS-039)")`, and verification fails with `CursorTamperedError` (`resync_required`).
  - When `EAP_HUD_CURSOR_SECRET` is unset, the process uses a one-time module-level CSPRNG key `os.urandom(32)` and logs a `WARNING` once (`EAP_HUD_CURSOR_SECRET is unset; using a random per-process 32-byte ephemeral cursor key (existing cursors will require resync_required across restarts)`).
- **Verified in**: `tests/hud/test_sse_replay_and_revocation.py::test_iss039_cursor_secret_warning_retired_key_and_aead` (both `ehc1_` constant-key tokens and `ehc2_` tokens forged with the retired constant key rejected with `CursorTamperedError` / SSE `resync_required`).

---

## 3. Event-Sequence & Failure Test Evidence (`C09`, `C10`, `C28`, `C30`)

### 3.1 Reconnect Exact-Once Replay (`C09`)
Verified against PostgreSQL 18.6 in `tests/hud/test_sse_replay_and_revocation.py::test_c09_sse_disconnect_and_reconnect_exact_once_no_gap_no_dup`:
1. Task created (`TaskService.create`, `project_id="proj-c09"`) and initial `run.started` relayed (`ingest_offset=1`); caller fetches `GET /v1/hud/snapshot` (`snapshot_cursor` with `ehc2_` prefix, capturing offset `1`).
2. Events `stage-1`, `stage-2`, `stage-3` (`run.progress`, `ingest_offset` `2, 3, 4`) are committed to `event_log`.
3. Client connects to `GET /v1/hud/events?cursor=<initial_cursor>&max_events=2`, receives `N=2` frames (`ingest_offset` `[2, 3]`, each carrying a rotating `ehc2_` `id:`), and disconnects; records `last_event_id = part1_frames[-1]["id"]`.
4. While disconnected, events `stage-4`, `stage-5` (`run.progress`, `ingest_offset` `5, 6`) and completion `run.state_changed` (`running -> succeeded`, `ingest_offset` `7`) are committed.
5. Client reconnects to `GET /v1/hud/events?max_events=4` with header `Last-Event-ID: <last_event_id>`:
   - Receives `4` frames (`ingest_offset` `[4, 5, 6, 7]`) matching `emitted_ids[2:]` (`stage-3`, `stage-4`, `stage-5`, `run.state_changed`).
   - Combined sequence across disconnect/reconnect is strictly `[2, 3, 4, 5, 6, 7]` (`0` gaps, `0` duplicates).
   - `apply_replayed_events_to_snapshot(initial_snapshot, all_replayed)` matches a fresh `GET /v1/hud/snapshot` (`run_state == "succeeded"`, `progress.completed_units == 5`).

### 3.2 Expiry, Retention Gap, Heartbeat, Cross-Tenant & Slow Consumer (`C10`, `ISS-039`)
- **Expired & Retention Gap + Recovery (`test_c10_expired_cursor_retention_gap_heartbeat_and_cross_tenant`)**:
  - Expired `ehc2_` cursor -> single terminal SSE frame `event: resync_required` (`reason="cursor_expired"`) and closed stream.
  - Retention floor advanced via `set_stream_retention_floor` (`min_retained_offset=3` above cursor offset `1`) -> single terminal `event: resync_required` (`reason="retention_gap"`) and closed stream; client recovers cleanly via fresh `GET /v1/hud/snapshot` + `GET /v1/hud/events` (`run.progress` `completed_units=2` delivered).
  - Idle SSE connection emits periodic `: heartbeat` comment frames (`type="comment"`).
  - Cross-tenant cursor (`tenant-a` token sent by `tenant-b` caller) -> single terminal `event: resync_required` (`reason="cursor_scope_mismatch"`) with `tenant-a` / `owner-a` never present in the response body.
- **Slow Consumer Bounded Buffer (`test_slow_client_bounded_buffer_emits_resync_required`)**:
  - With `max_buffer_size=3` and `8` backlog events pending after the cursor, `iter_hud_sse_frames` overflows its bounded queue before unbounded allocation and emits a single terminal `event: resync_required` (`reason="slow_consumer"`) then closes.

### 3.3 Dual-Principal Live Revocation Timeline & Latencies (`C28`, `C30`)
Verified in `tests/hud/test_sse_replay_and_revocation.py::test_c28_c30_dual_principal_live_revocation_evidence_403_and_latency` on PostgreSQL 18.6 (`127.0.0.1`, local bootstrap principals `principal-a` (`owner, admin`) and `principal-b` (`admin`) in the same tenant):
1. **Baseline**: `principal-b` snapshots both `proj-alpha` and `proj-beta` (`GET /v1/hud/snapshot` -> `200 OK`), and reads `GET /v1/evidence/{ev_alpha}` (`200 OK`, `signed_url=null`) and `GET /v1/artifacts/{art_alpha}` (`200 OK`, `signed_url=null`).
2. **Pre-revocation live event**: `principal-b` holds open `GET /v1/hud/events?cursor=<pre_revocation_cursor>`; receives pre-revocation `proj-alpha` `run.progress` (`stage="pre-revocation"`).
3. **Revocation**: `principal-a` calls `POST /v1/hud/grants/revoke` (`principal_id="principal-b"`, `project_id="proj-alpha"` -> `200 OK`, `grant_epoch=1`, emits `policy.changed`).
4. **Live SSE enforcement**: `principal-b`'s active SSE stream emits `event: row.removed` (`task_id=task_alpha`, `project_id="proj-alpha"`, `grant_epoch=1`), delivers `0` post-revocation `proj-alpha` events (`post-revocation-secret` committed after revoke never appears in any frame), and continues delivering `proj-beta` events (`beta-still-visible`).
5. **Artifact/Evidence & Reconnect enforcement**:
   - `GET /v1/evidence/{ev_alpha}`, `GET /v1/artifacts/{art_alpha}`, `GET /v1/artifacts/{ev_alpha}` -> `403 Forbidden` for `principal-b`; `200 OK` for `principal-a`.
   - `GET /v1/hud/events?cursor=<pre_revocation_cursor>` -> single terminal `resync_required` (`reason="grant_epoch_changed"`).
   - Fresh `GET /v1/hud/snapshot` for `principal-b` omits `task_alpha` (only `{task_beta}` visible); `GET /v1/hud/snapshot?project_id=proj-alpha` -> `403 Forbidden`.
6. **Measured Live Propagation Latency (`n=20` live revocations via `iter_hud_sse_frames`, `poll_interval_ms=5`)**:
   - **p50**: `16.00 ms`
   - **p95**: `31.05 ms` (threshold `< 500.0 ms`)

---

## 4. Snapshot Latency Before / After `ISS-040` (100,000 Seeded Events)

Measured on PostgreSQL 18.6 in `tests/hud/test_sse_replay_and_revocation.py::test_iss040_snapshot_bounded_reads_at_100k_events_benchmark` with `100,000` seeded `event_log` rows for an isolated tenant (`20` active tasks/runs with `project_id` populated and matching `hud_runs` projection checkpoints + `stream_heads` watermarks):

| Mode | Query Profile | p50 Latency | p95 Latency |
|---|---|---|---|
| **Before (`_load_committed_event_rows` full tenant scan)** | Full `event_log` scan across 100k rows | `699.29 ms` | `717.62 ms` |
| **After (`take_hud_snapshot` bounded checkpoint + heads)** | `0` `outbox` scans; `event_log` bounded strictly to `ORDER BY id DESC LIMIT 1` / post-checkpoint tail | `15.62 ms` | `18.84 ms` |

> Full end-to-end HUD capacity baseline (`SC-07`) remains scheduled under `P3a-WP4`.

---

## 5. Verification Ledger

| Check | Command | Real Result |
|---|---|---|
| P3a-WP2 + HUD suites (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run pytest tests/hud -s -v` | PASS (`16 passed, 2 warnings in 10.84s`; C30 `p50=16.00ms p95=31.05ms`; ISS-040 before `p50=699.29ms p95=717.62ms`, after `p50=15.62ms p95=18.84ms`) |
| Full pytest suite (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run pytest -q` | PASS (`662 passed, 6 warnings in 248.00s (0:04:08)`) |
| Lint | `uv run ruff check src tests ppm-v2/reducer migrations tools` | PASS (`All checks passed!`) |
| Typecheck | `uv run mypy src tests ppm-v2/reducer` | PASS (`Success: no issues found in 169 source files`) |
| SC-01 Package validation | `uv run python tools/validate_package.py` | PASS (`16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed`) |
| PPM negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | PASS (`5 templates, 20 cases`) |
| PPM fixture corpus | `uv run python ppm-v2/tools/check_fixtures.py` | PASS (`23 fixtures, 3 rollup`) |
| PPM reducer corpus | `uv run python ppm-v2/tools/run_reducer_fixtures.py` | PASS (`23 fixtures`) |
| SC-08 Translation check (workspace) | `uv run python tools/check_translation.py` | PASS (`38 up-to-date, 0 stale, 0 missing headers`) |
| SC-08 Translation check (temp git clone) | `git clone --no-local . <tmp>; <venv>/python tools/check_translation.py` | PASS (`38 up-to-date, 0 stale, 0 missing headers`) |
| SC-06 Migrations up/down/up (PG 18.6) | `$env:TEST_DATABASE_URL="..."; uv run python tools/run_sc06.py` | PASS (`ALL CYCLES PASSED`, head `0009_hud_sse_and_policy`) |
| Web HUD build | `npm --prefix web/hud ci && npm --prefix web/hud run build` | PASS (`built in 409ms`) |
| Web HUD unit tests | `npm --prefix web/hud test` | PASS (`Test Files 1 passed (1)`, `Tests 2 passed (2)`) |
| Integrity checksums | `sha256sum -c SHA256SUMS.txt` | Re-generated with LF endings and verified post-doc edits before commit (see `STATUS.md`) |
| GitHub Actions CI | Workflow `.github/workflows/ci.yml` on `wp/P3a-WP2` | `not_run` at packet creation (queued on `git push -u origin wp/P3a-WP2`) |

---

## 6. Residual Risks & Notes

1. **Migration numbering on parallel branches**: Migration `0009_hud_sse_and_policy` revises `0008_agents`. If `P2a-WP5` (running in parallel) also introduces `0009_*`, the owner relinks `down_revision` at merge per `STATUS.md` and `AGENTS.md`.
2. **Ephemeral cursor secret restart invalidation**: When `EAP_HUD_CURSOR_SECRET` is unset, `get_hud_cursor_secret()` generates a random per-process key (`WARNING` logged), so server restarts invalidate previously issued `ehc2_` cursors with `resync_required`. Production/persistent local setups should set `EAP_HUD_CURSOR_SECRET`.
3. **DB-polling SSE cadence**: Live `/v1/hud/events` delivery and revocation detection poll at `poll_interval_ms` (default `250ms`, minimum `5ms`), trading higher sub-second PostgreSQL `SELECT` polling against single-node simplicity (`pg_notify` optimization can be layered cleanly later if needed).
4. **Legacy unlinked rows & unmapped `project_id`**: Pre-`0009` `tasks`/`runs` rows with `project_id IS NULL` fall back to explicit `commands`/`ppm_evidence` lookup or single-project tenant attribution; multi-project tenants should set `project_id` on new tasks/runs (`TaskService.create`).
5. **Local bootstrap auth only**: Enforcement uses local bootstrap principals bound to `127.0.0.1` (`P1a-WP5`); multi-node / OIDC token revocation (`C30` with IdP) remains in `P1b-WP1`/`P3b-WP2`, and `SC-07` full HUD load/latency baseline remains in `P3a-WP4`.
6. **Rollback**: Set `EAP_HUD_CURSOR_SECRET` / revert routes or run `alembic downgrade 0008_agents` cleanly (verified reversible in `SC-06`).
