# P3a-WP3 — HUD UI Acceptance Packet

- **WP**: `P3a-WP3` (Operator HUD UI)
- **Branch**: `wp/P3a-WP3` (based on `main` `f980b94`)
- **Status**: `ready_for_review`
- **Date**: `2026-11-16`
- **Environment**: Windows 11, Python `3.12.13`, PostgreSQL `18.6-trixie`, Node `v26.7.0`, Vite `6.4.5`, Vitest `5.1.0`, Playwright `1.65.1` (cached Chromium 1243)
- **Related Issues**: `ISS-038` (HUD reads `acceptance_readiness`, `assessed_revision`, `analysis_status`, `last_successful_scan_at` from PPM API rather than extending `hud-projection` v1.1), `ISS-022` (pending cancel on unknown run surfaced in Attention Queue)

---

## 1. Milestone & Scope

Implemented the read-mostly Operator HUD for MVP1-local in `web/hud/src/` (superseding the disposable D0 static page `D0App.tsx` as the primary `index.html` application) plus one minimal read-only backend endpoint (`GET /v1/hud/runs/{run_id}/inspector` in `src/eap/api/hud_routes.py`, zero migrations, zero contract changes):

1. **Views (`web/hud/src/App.tsx`, `web/hud/src/components/`)**:
   - **Attention Queue (`AttentionQueueView.tsx`)**: surfaces runs with `remote_outcome="unknown"` / `run_state="unknown"` (including pending cancel commands per `ISS-022`), contradicted required criteria, source scan errors, and stale evidence/connectivity.
   - **Tasks & Runs (`TasksRunsView.tsx`)**: independent columns for Task status, Run & Attempt status, Remote Outcome, Connectivity + `last_observed_at` (displayed in local timezone), Progress (percent rendered ONLY when `kind === 'units'` and `total_units >= 1`; `stage_only` renders stage without `%`), Cost (`"Unknown"` when unknown, never `"$0"`; `"≥ $X.XX (some calls unknown)"` for `partial`), Pending Command, and Inspect/Cancel/Retry actions (Retry disabled on unknown remote outcome per DG1-C1).
   - **Run Inspector (`RunInspectorView.tsx`)**: displays attempts, commands, redacted event timeline, API-only artifacts (`/v1/artifacts/{id}`, `signed_url=null`), usage/cost, and explicit `"Remote outcome unknown — no retry until reconciled"` banner (never coerced to success/failure).
   - **Agent Inventory (`AgentInventoryView.tsx`)**: consumes `GET /v1/agents`, displaying agent metadata, version, bindings, `execution_boundary`, `certified_capabilities` with API-only `capability_evidence_ref` inspection, derived health (`no_data` on 0 runs, `unknown` on lost ACK/missing usage), and `unattributed_runs_count`.
   - **Project Assessment (`ProjectAssessmentView.tsx`)**: replaces D0 page; displays Reported vs Verified columns per criterion, evidence with `trust_class`, `producer`, `simulated` badge, and API-only `/v1/evidence/{id}` inspection, `excluded_evidence[]` with reasons, `A_n` vs `A_{n-1}` diff, `last_successful_scan_at`, `analysis_status`, inert-text notes, and zero approve/accept buttons (deferred to P2b/P3b).
2. **Live Data (`web/hud/src/sseClient.ts`, `web/hud/src/state.ts`)**:
   - Fetches `GET /v1/hud/snapshot`, then streams `GET /v1/hud/events` via `fetch()` + `ReadableStream` with `Authorization: Bearer <token>` and `Last-Event-ID` on reconnect (exponential backoff + jitter).
   - Handles `resync_required` by refetching `/v1/hud/snapshot` and reopening SSE; handles `row.removed` by dropping the row immediately and clearing Run Inspector if open on that row.
   - Displays connection state (`live`, `reconnecting`, `resyncing`, `stale since <time>`, `disconnected`).
3. **Security & Accessibility**:
   - Bearer token held in React state memory only (`type="password"`, never written to `localStorage`, `sessionStorage`, cookies, or URLs).
   - All repository/agent text rendered via React JSX text nodes (no `innerHTML` / `dangerouslySetInnerHTML`).
   - All evidence/artifact links route strictly through `/v1/evidence/{id}` and `/v1/artifacts/{id}`.
   - Keyboard navigation across tabs (`ArrowLeft`/`ArrowRight`) and Tasks & Runs rows (`ArrowUp`/`ArrowDown` + `Enter`), visible `:focus-visible` outlines, and status badges combining text + symbol + color.

---

## 2. State-Mapping Table

| Axis | Source Field(s) | UI Display Rule | Never Confused With |
|---|---|---|---|
| **Task** | `row.task_state` | Symbol + text badge (`active`, `review_required`, `completed`, `failed`, `cancelled`, `blocked`) | `run_state` |
| **Run** | `row.run_state`, `row.attempt` | `"Attempt N · <run_state>"` (`running`, `unknown`, `succeeded`, `failed`, etc.) | `task_state` or `remote_outcome` |
| **Remote Outcome** | `row.remote_outcome` | `unknown` -> `"Remote outcome unknown — no retry until reconciled"` (Retry disabled) | `succeeded` / `failed` |
| **Connectivity** | `row.connectivity`, `row.last_observed_at`, `row.observation_source` | `"Stale · last observed <local_time> via <source>"` | `run_state` or HUD SSE state |
| **HUD Connection** | SSE client state | `live` / `reconnecting` / `resyncing` / `stale since <time>` / `disconnected` | Worker `connectivity` |
| **Progress** | `row.progress` | `%` ONLY when `kind === 'units'` & `total_units >= 1`; `stage_only` -> `"Stage: <stage>"` (no `%`); `none` -> `"No progress"` | Fabricated `0%` |
| **Cost** | `row.cost` | `unknown` -> `"Unknown"` (never `"$0"`); `partial` -> `"≥ $X.XX (some calls unknown)"`; `actual`/`estimated` -> `"$X.XX (<status>)"` | `$0.00` |
| **PPM Verification / Readiness** | `assessment.output.verification`, `acceptance_readiness`, `analysis_status` | Separate badges + Reported vs Verified columns; no approve button | `run_state` or agent `"done"` claim |

---

## 3. Answering the Three D1 Questions from the HUD

Verified against live PostgreSQL 18.6 fixture data (`tests/hud/e2e_fixture_server.py`, `A1` at `rev-r1`, `A2` at `rev-r2`):

1. **Which criteria advanced?**
   - In **Project Assessment**, the **Diff (`Assessment #1 → #2`)** table shows `AC-002`: `unverified → verified` (`changed`), `reason_code: evidence_missing → evidence_current_pass`, `evidence_revision: — → rev-r2`.
2. **Which evidence belongs to the current revision?**
   - In **Project Assessment**, `AC-001` **Verified** column shows `ev-r2-fail` (`verifier_observed`, producer `simulated-verifier`, outcome `failed`, `rev-r2`), while **Excluded Evidence** explicitly lists `ev-r1-pass` (`rev rev-r1`) with badge `revision_mismatch`.
3. **Why does an item reported "done" by an agent remain unaccepted?**
   - `AC-001` shows `Reported: claimed_done (.ppm/updates/upd-0002.yaml — "Export done")` alongside `Verified: ✗ contradicted (evidence_refuted)` at `rev-r2` (and surfaces in the **Attention Queue** as a critical contradicted required criterion).
   - `AC-003` shows `Reported: claimed_done (.ppm/updates/upd-0003.yaml — "Self-written junit only")` alongside `Verified: ○ unverified (evidence_missing)` with zero `verifier_observed` evidence.
   - `acceptance_readiness` is `not_ready`, `last_successful_scan_at` is displayed in local time, and no approve/accept button exists.

---

## 4. Fixture Screenshots (`docs/acceptance/assets/`)

- **Tasks & Runs**: `docs/acceptance/assets/p3a-wp3-tasks-runs.png`
- **Run Inspector**: `docs/acceptance/assets/p3a-wp3-run-inspector.png`
- **Attention Queue**: `docs/acceptance/assets/p3a-wp3-attention-queue.png`
- **Agent Inventory**: `docs/acceptance/assets/p3a-wp3-agent-inventory.png`
- **Project Assessment**: `docs/acceptance/assets/p3a-wp3-project-assessment.png`

---

## 5. Verification Ledger (Real Command Output)

| Check | Command | Real Result |
|---|---|---|
| Web HUD unit tests | `npm --prefix web/hud test` | PASS (`Test Files 2 passed (2)`, `Tests 8 passed (8)` — `D0App.test.tsx` 2 + `App.test.tsx` 6) |
| Web HUD build | `npm --prefix web/hud run build` | PASS (`tsc -b && vite build`, `built in 434ms`) |
| Playwright E2E (live FastAPI + PG 18.6 + Vite) | `node web/hud/e2e/p3a_wp3_e2e.mjs` | PASS (7/7 scenarios: initial snapshot + unknown cost + stage_only, SSE reconnect with `Last-Event-ID` exact-once delivery, `resync_required` snapshot refetch, live `row.removed` revocation, keyboard navigation + Run Inspector unknown outcome, Agent Inventory `no_data`, Project Assessment D1 diff/excluded/inert XSS) |
| Run Inspector API test (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="..."; uv run pytest tests/hud/test_run_inspector_api.py -q` | PASS (`1 passed`) |
| Full pytest suite (PG 18.6) | `$env:CI="true"; $env:TEST_DATABASE_URL="..."; uv run pytest -q` | PASS (`673 passed, 7 warnings in 238.12s`) |
| Lint | `uv run ruff check src tests ppm-v2/reducer migrations tools` | PASS (`All checks passed!`) |
| Typecheck | `uv run mypy src tests ppm-v2/reducer` | PASS (`Success: no issues found in 174 source files`) |
| SC-01 Package validation | `uv run python tools/validate_package.py` | PASS (`16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed`) |
| PPM negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | PASS (`5 templates, 20 cases`) |
| SC-08 Translation check | `uv run python tools/check_translation.py` | PASS (`38 up-to-date, 0 stale, 0 missing headers`) |
| GitHub Actions CI | — | `not_run` (local branch before push) |

---

## 6. Residual Risks & Go/No-Go

- **Playwright browser binary**: E2E ran against locally cached Chromium (`ms-playwright/chromium_headless_shell-1243`) via `findCachedChromium()` because `npm install` of a newer Playwright version was skipped (`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`).
- **D1 auto-scan orchestrator**: `P2a-WP6` is not yet merged on this branch; Project Assessment was verified against seeded `A1`/`A2` assessments and `create_assessment` in `e2e_fixture_server.py`.
- **`ISS-038` & `ISS-022`**: HUD reads PPM readiness/revision/scan timestamps from `/v1/ppm/...` without modifying `contracts/hud-projection.schema.json`; pending cancel on unknown runs remains surfaced in Attention Queue until manual reconciliation or P2b/P3b.
- **Rollback**: Revert `src/eap/api/hud_routes.py` (`get_run_inspector`) and `web/hud/src/` (zero DB migrations, zero contract changes).
