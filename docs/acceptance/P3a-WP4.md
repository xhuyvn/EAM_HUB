# P3a-WP4 — HUD Latency Baseline (SC-07) & Portable E2E Acceptance Packet

- **WP**: `P3a-WP4` (HUD latency baseline & portable E2E)
- **Branch**: `wp/P3a-WP4` (based on `main` `b6e5290`)
- **Status**: `ready_for_review`
- **Date**: `2026-11-16`
- **Environment**: Windows 11 (`10.0.26300`), Python `3.12.13`, PostgreSQL `18.6-trixie`, Node `v26.7.0`, Vite `6.4.5`, Vitest `5.1.0`, `@playwright/test@1.65.1`
- **Related Issues**: `ISS-043` (`resolved`), `ISS-040` (full SC-07 HUD baseline now recorded)

---

## 1. Milestone & Scope

1. **Portable Playwright E2E (`ISS-043` resolved)**:
   - Pinned `@playwright/test@1.65.1` as a `devDependency` in `web/hud/package.json` and added `"e2e": "playwright test"`.
   - Added `web/hud/playwright.config.ts` to start both the fixture API server (`tests/hud/e2e_fixture_server.py`, reading `TEST_DATABASE_URL` and `E2E_TOKEN` from environment, creating target DB if absent) and the Vite dev server (`npm run dev -- --port 5173 --strictPort`), poll `/healthz` and `/` for readiness, and tear them down cleanly.
   - Ported all 7 E2E scenarios unchanged in meaning from `web/hud/e2e/p3a_wp3_e2e.mjs` into `web/hud/e2e/hud.spec.ts` (removed the hard-coded Windows npx cache script).
   - Added `web/hud/vitest.config.ts` so `vitest run` (`npm --prefix web/hud test`) only executes unit tests under `src/` and excludes `e2e/`.
   - Added `.github/workflows/ci.yml` job `hud-e2e` (Linux `ubuntu-latest` + `postgres:18.6-trixie` service, `npx playwright install --with-deps chromium`, `npm --prefix web/hud run e2e`, and `actions/upload-artifact@v4` on failure for `web/hud/test-results/` and `web/hud/playwright-report/`).
2. **SC-07 HUD Latency Baseline (`tools/bench/hud_baseline.py` & `docs/measurements/HUD_BASELINE.md`)**:
   - Implemented `tools/bench/hud_baseline.py` to seed tasks/runs/agents/assessments and `event_log` histories (`10,000`, `100,000`, and `1,000,000` events) and measure real loopback HTTP/SSE latencies against an in-process Uvicorn/FastAPI server backed by PostgreSQL `18.6`.
   - Fixed a real cursor-encoding bug uncovered at `> 64` active runs (`src/eap/hud/snapshot.py`, `src/eap/hud/sse.py`): `_StreamCursorState.current_cursor()` now preserves `stream_offsets_omitted=True` via `encode_snapshot_cursor(..., stream_offsets_omitted=...)`, preventing mid-stream SSE polls from switching to `so_om=False` and replaying all other streams.

---

## 2. E2E Scenarios (`web/hud/e2e/hud.spec.ts`)

| # | Scenario | Result (Local) | Result (CI) |
|---|---|---|---|
| 1 | Initial snapshot, live SSE, unknown cost (`Unknown`, never `$0`), `stage_only` rendering | PASS (`3.0s`) | pending push |
| 2 | SSE reconnect with `Last-Event-ID` delivers committed event exactly once | PASS (`895ms`) | pending push |
| 3 | `resync_required` triggers fresh snapshot fetch and recovers to `live` | PASS (`60ms`) | pending push |
| 4 | Live `row.removed` drops revoked project row immediately | PASS (`126ms`) | pending push |
| 5 | Keyboard navigation (`ArrowLeft`/`ArrowRight`/`Enter`) + Run Inspector unknown outcome | PASS (`105ms`) | pending push |
| 6 | Agent Inventory renders certified capabilities and `no_data` health | PASS (`70ms`) | pending push |
| 7 | Project Assessment renders D1 diff (`Assessment #1 -> #2`), `revision_mismatch` excluded evidence, inert XSS, and zero approve buttons | PASS (`49ms`) | pending push |

---

## 3. SC-07 HUD Latency Baseline Summary (`docs/measurements/HUD_BASELINE.md`)

Executed across `10,000`, `100,000`, and `1,000,000` seeded `event_log` rows (`100` active runs, `10` agents, `10` projects / `20` assessments, `DEFAULT_SSE_POLL_INTERVAL_MS=25`, PostgreSQL `18.6-trixie`, total duration `65.989 s`).

| Endpoint / Flow | 10k p50 / p95 (ms) | 100k p50 / p95 (ms) | 1M p50 / p95 (ms) |
|---|---|---|---|
| `GET /v1/hud/snapshot` | `19.10` / `21.61` | `21.50` / `25.64` | `22.96` / `27.83` |
| SSE event delivery (commit -> client) | `46.87` / `54.91` | `47.02` / `54.96` | `46.72` / `50.81` |
| SSE reconnect replay (1,000 events) | `356.45` / `384.01` | `369.10` / `375.68` | `380.60` / `398.22` |
| Live revocation (`revoke` -> `row.removed`) | `68.08` / `83.22` | `42.84` / `82.72` | `200.44` / `252.45` |
| `GET /v1/agents` | `11.17` / `13.44` | `11.46` / `14.77` | `12.40` / `15.43` |
| `GET /v1/agents/{agent_id}` | `11.22` / `17.88` | `11.53` / `13.37` | `11.35` / `15.68` |
| `GET /v1/ppm/assessments/{id}` | `8.77` / `10.56` | `9.78` / `12.76` | `9.36` / `13.57` |
| `GET /v1/ppm/assessments/{id}/diff` | `5.25` / `6.45` | `5.88` / `8.32` | `5.55` / `7.12` |
| `GET /v1/ppm/projects/{id}/assessments` | `5.41` / `8.69` | `5.42` / `7.82` | `5.26` / `7.15` |
| `GET /v1/hud/runs/{run_id}/inspector` | `14.74` / `19.06` | `19.09` / `25.59` | `20.20` / `28.13` |

- **Assumptions**: Single tenant, `100` active runs, loopback `127.0.0.1` HTTP/SSE, `DEFAULT_SSE_POLL_INTERVAL_MS=25`, PostgreSQL `18.6` default settings (`shared_buffers=128MB`, `synchronous_commit=on`, `fsync=on`).
- **Thresholds**: No formal DG-5 thresholds exist yet (`ARCH-30`). Observed SSE delivery (`p95 <= 54.96 ms`) and revocation propagation (`p95 <= 252.45 ms`) are well below the non-binding `< 2,000 ms` spec targets in `docs/03_HUD_SPEC.md` and `docs/07_DELIVERY_AND_ACCEPTANCE.md`. Candidate DG-5 proposals are recorded in `docs/measurements/HUD_BASELINE.md` §4 as proposals only.

---

## 4. Verification Ledger (Real Command Output)

| Check | Command | Real Result |
|---|---|---|
| HUD build | `npm --prefix web/hud run build` | PASS (`built in 438ms`) |
| HUD unit tests | `npm --prefix web/hud test` | PASS (`Test Files 2 passed (2)`, `Tests 8 passed (8)`) |
| HUD portable E2E | `npm --prefix web/hud run e2e` | PASS (`7 passed (8.1s)`) |
| SC-07 HUD baseline | `uv run python tools/bench/hud_baseline.py --db-url .../eap_hud_bench --reset-db --scales 10000,100000,1000000 ...` | PASS (`65.989 s`, `docs/measurements/HUD_BASELINE.md`) |
| Lint | `uv run ruff check src tests ppm-v2/reducer migrations tools` | PASS (`All checks passed!`) |
| Typecheck | `uv run mypy src tests ppm-v2/reducer` | PASS (`Success: no issues found in 181 source files`) |
| SC-01 / Negative / Fixtures / Reducer | `validate_package.py`, `run_negative_cases.py`, `check_fixtures.py`, `run_reducer_fixtures.py` | PASS (`16 schemas, 15 JSON fixtures, 11 events, 46 negative cases`; `5 templates, 20 cases`; `23 fixtures, 3 rollup`; `23 fixtures`) |
| SC-08 (temp git clone) | `git clone --no-local . <tmp> && uv run python tools/check_translation.py` | PASS (`38 up-to-date, 0 stale, 0 missing headers`) |
| Full pytest (`CI=true`, PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="..."; uv run pytest -q` | PENDING |
| GitHub Actions CI | `push wp/P3a-WP4` | PENDING |

---

## 5. Residual Risks & Rollback

- **Local vs. CI Hardware**: SC-07 baseline numbers were measured on the owner's Windows 11 workstation (24 cores, 63.37 GB RAM, Docker PG 18.6); GitHub Actions runners have fewer cores and higher I/O variance.
- **SSE Multi-Client Fan-Out**: Baseline measured single-client SSE delivery and dual-principal revocation; multi-client (`<= 10` concurrent SSE clients per `docs/03_HUD_SPEC.md`) fan-out under sustained `200 events/s` ingest remains for `P5-WP4` / `DG-5`.
- **Rollback**: Revert `web/hud/{package.json,package-lock.json,playwright.config.ts,vitest.config.ts,e2e/}`, `.github/workflows/ci.yml` (`hud-e2e` job), `tools/bench/hud_baseline.py`, `docs/measurements/HUD_BASELINE.md`, and the 2-line `stream_offsets_omitted` fix in `src/eap/hud/{snapshot.py,sse.py}`.
