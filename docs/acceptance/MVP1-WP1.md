# Milestone Acceptance: MVP1-WP1 — Local Deploy Package

## 1. Milestone and Scope

- **Work package**: `MVP1-WP1` ("Local deploy package")
- **Branch**: `wp/MVP1-WP1` (based on `main` @ `69415fe`)
- **Objective**: Enable the owner to start the entire MVP1 stack on a single workstation with one command (`mvp1_up`), open the Operator HUD in a browser at `http://127.0.0.1:5173`, and inspect agents, tasks/runs, projects, and assessments — bound strictly to `127.0.0.1` with no secrets in images, logs, or the repository.
- **Included**:
  - Multi-stage Python app image (`Dockerfile`, no dev deps, non-root UID `10001`, `.dockerignore` excluding `.env`/`.git`/`node_modules`/`tests`).
  - Multi-stage HUD image (`web/hud/Dockerfile`, Node 26 build -> `nginxinc/nginx-unprivileged:1.27.5-alpine`, non-root UID `101`, `web/hud/nginx.conf` serving static `dist/` and proxying `/v1/` and `/healthz` with SSE buffering disabled).
  - `compose.yaml` profile `mvp1` (`postgres`, `migrate`, `api`, `worker`, `hud`), keeping default `postgres` and `optional` (`redis`, `qdrant`) profiles intact.
  - Unified background worker (`src/eap/worker.py`: outbox relay/projection, mock-provider dispatcher, optional scan scheduler, assessment orchestrator, fixtures-only verifier flag).
  - Idempotent D0/D1 fixture seeder (`src/eap/mvp1_fixture.py`).
  - Lifecycle scripts: `scripts/mvp1_up.{ps1,sh}`, `scripts/mvp1_down.{ps1,sh}`, `scripts/mvp1_status.{ps1,sh}`, `scripts/mvp1_reset.{ps1,sh}` (explicit confirmation flag required to drop volumes).
  - Operations runbook (`docs/MVP1_RUNBOOK.md`) and CI job (`mvp1-deploy-smoke` in `.github/workflows/ci.yml`).
- **Excluded (by owner decision 2026-11-15)**: Real agents/adapters, OIDC/RBAC (`P1b-WP1`/`P1b-WP2`), remote/LAN access, real project directories before DG-3 (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`).

## 2. Source Commits, Environment, and Dependency Pins

- **Python**: `python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36`
- **uv**: `ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946` (`uv sync --locked --no-dev`)
- **Node**: `node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5`
- **HUD Static Server**: `nginxinc/nginx-unprivileged:1.27.5-alpine@sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0`
- **PostgreSQL**: `postgres:18.6-trixie`
- **Built image sizes** (`docker images`):
  - `local_deploy_package_mvp1-api`: `494MB`
  - `local_deploy_package_mvp1-migrate`: `494MB`
  - `local_deploy_package_mvp1-worker`: `494MB`
  - `local_deploy_package_mvp1-hud`: `74.1MB`
  - `postgres:18.6-trixie`: `650MB`

## 3. Acceptance Criteria and Observed Results

| Check | Command / Procedure | Result | Evidence / Notes |
|---|---|---|---|
| One-command startup (`mvp1_up`) | `$env:EAP_POSTGRES_PORT = "5433"; .\scripts\mvp1_up.ps1 -WithFixture` | **PASS** | Created `.env` from `.env.example`, generated `POSTGRES_PASSWORD`/`EAP_SECRET_CANARY`/`EAP_HUD_CURSOR_SECRET`, migrated DB, started `api`/`worker`/`hud`, registered `agent-mock-provider` + `binding-mock-provider-local`, seeded `proj-d0-demo` (note: port `5433` used locally because `eam_d1_pg` from parallel `wp/D1` occupied `5432`) |
| Status & health (`mvp1_status`) | `.\scripts\mvp1_status.ps1` | **PASS** | `api`, `hud`, `postgres`, `worker` all `(healthy)`; `API /healthz: OK (200)`, `HUD /: OK (200)` |
| Authenticated API via HUD proxy | `GET http://127.0.0.1:5173/v1/{agents,hud/snapshot,ppm/projects/proj-d0-demo/assessments}` | **PASS** | `Agents count: 1 (agent-mock-provider)`, `Snap rows: 3`, `Asmts count: 3` |
| HUD visual verification | Playwright Chromium against `http://127.0.0.1:5173/` | **PASS** | Live SSE (`• live`), Tasks & Runs, Agent Inventory, and Project Assessment captured in `docs/acceptance/assets/mvp1-hud-{tasks,agents,assessments}.png` |
| Stop & Reset guard | `.\scripts\mvp1_down.ps1`; `.\scripts\mvp1_reset.ps1` (no flag); `.\scripts\mvp1_reset.ps1 -ConfirmReset` | **PASS** | `mvp1_reset` without flag exited non-zero (`REFUSED`); `-ConfirmReset` removed `local_deploy_package_mvp1_pgdata` |
| Python test suite (`CI=true`, PG 18.6) | `uv run pytest -q` (against `postgres:18.6-trixie`) | **PASS** | `710 passed` in `255.69s` |
| Lint & Typecheck | `uv run ruff check src tests ppm-v2/reducer migrations`; `uv run mypy src tests ppm-v2/reducer` | **PASS** | `All checks passed!`; `Success: no issues found in 187 source files` |
| Package / Negative / Reducer / SC-08 | `validate_package.py`, `run_negative_cases.py`, `check_fixtures.py`, `run_reducer_fixtures.py`, `check_translation.py` (in fresh `git clone`) | **PASS** | 16 schemas / 15 fixtures / 46 negatives PASS; 20 negative cases PASS; 23/23 reducer fixtures PASS; SC-08 `38 up-to-date, 0 stale` |
| HUD Build & Unit Tests | `npm --prefix web/hud run build && npm --prefix web/hud test` | **PASS** | Vite build `443ms`; Vitest `8 passed (8)` |
| CI Linux `mvp1-deploy-smoke` | `.github/workflows/ci.yml` (`mvp1-deploy-smoke`) | `pending_push` | Job added to CI; run ID will be recorded after pushing `wp/MVP1-WP1` |

## 4. Security & Recovery Checks Executed

1. **Loopback-only port bindings** (`docker compose --profile mvp1 config` parsed with PyYAML):
   - `OK api: 127.0.0.1:8000`
   - `OK hud: 127.0.0.1:5173`
   - `OK postgres: 127.0.0.1:5433` (default `5432`)
   - Zero `0.0.0.0` bindings.
2. **Non-root container execution**:
   - `docker compose --profile mvp1 exec -T api id -u` -> `10001`
   - `docker compose --profile mvp1 exec -T worker id -u` -> `10001`
   - `docker compose --profile mvp1 exec -T hud id -u` -> `101`
3. **Image layer secret scan** (`docker history --no-trunc` on `api` and `hud` images):
   - `0` matches for `EAP_OWNER_TOKEN_HASH`, `POSTGRES_PASSWORD`, `EAP-CANARY`, `EAP_HUD_CURSOR_SECRET`.
4. **API refuses non-loopback bind** (inside `api` container):
   - `create_app(Settings(eap_bind='0.0.0.0:8000'))` -> `REFUSED: EAP_BIND host '0.0.0.0' is not a loopback address.`
5. **Backup / Restore Drill (`pg_dump` -> wipe -> `pg_restore`)**:
   - Before dump: `1|1|3|3|3|asmt-d6f034d956c44715` (`150,866` bytes dump)
   - Dropped and recreated `eap_dev`, restored via `pg_restore -U eap -d eap_dev --no-owner --no-acl`
   - After restore: `1|1|3|3|3|asmt-d6f034d956c44715` (identical row counts and latest assessment ID).

## 5. Screenshots

- [mvp1-hud-tasks.png](file:///C:/Users/xhuy/.gemini/antigravity/worktrees/EAM_HUB/local_deploy_package_mvp1/docs/acceptance/assets/mvp1-hud-tasks.png)
- [mvp1-hud-agents.png](file:///C:/Users/xhuy/.gemini/antigravity/worktrees/EAM_HUB/local_deploy_package_mvp1/docs/acceptance/assets/mvp1-hud-agents.png)
- [mvp1-hud-assessments.png](file:///C:/Users/xhuy/.gemini/antigravity/worktrees/EAM_HUB/local_deploy_package_mvp1/docs/acceptance/assets/mvp1-hud-assessments.png)

## 6. Residual Risks

- `ISS-045`: `scripts/mvp1_up.sh` (Bash) was not executed natively on Linux during this Windows Docker Desktop session; its Compose stack and security checks are covered by the Linux `mvp1-deploy-smoke` CI job.

## 7. Go/No-Go Decision

- **Status**: `ready_for_review` (coding agent implementation complete; awaiting owner review and CI run verification).
- **Rollback**: Stop stack with `scripts/mvp1_down.ps1` (or `.sh`); revert to prior commit/image tag and restore pre-upgrade `pg_dump` if schema changed.
