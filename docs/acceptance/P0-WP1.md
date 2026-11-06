# Acceptance Packet: P0-WP1 Skeleton, CI, and Environment

## 1. Milestone and Scope
- **Work Package**: P0-WP1 (Skeleton, CI, and environment per `STATUS.md`, `docs/DEV_ENVIRONMENT.md`, and `docs/IMPLEMENTATION_PLAN.md` §6).
- **Scope**:
  - Establish Python 3.12 uv workspace root `pyproject.toml` with members `.` (`eap`) and `ppm-v2/reducer` (`ppm-reducer`), generating `uv.lock`.
  - Create modular monolith package skeleton in `src/eap/{api,run,task,events,policy,action,evidence,hud,model_gateway,adapters,ppm,db}/` with zero business logic.
  - Implement FastAPI `/healthz` endpoint and Pydantic configuration (`pydantic-settings`) supporting empty optional services (`REDIS_URL`, `QDRANT_URL`).
  - Author Node 26 LTS Vite + React + TypeScript HUD web shell in `web/hud/` with clean production build (`package-lock.json` committed).
  - Configure `compose.yaml` with PostgreSQL 18.6 default profile (healthy on 127.0.0.1:5432) and Redis 8.10.1 / Qdrant 1.20.0 under `optional` profile; provide `.env.example`.
  - Validate image tags and digests via `docker pull`; record in `docs/DEV_ENVIRONMENT.md` and `VALIDATION_REPORT.md`.
  - Implement Alembic migration scaffolding and execute SC-06 (migration up/down/up) against live PostgreSQL 18.6.
  - Verify standalone PPM validator runs in isolated virtual environment using `ppm-v2/tools/requirements.txt` alone.
  - Enforce domain purity for `ppm_reducer` (zero imports of `eap`, `requests`, `socket`, `subprocess`, `urllib`, `dotenv`).
  - Implement SC-04 secret canary redaction checks.
  - Author CI automation script `scripts/ci.ps1` and GitHub Actions workflow `.github/workflows/ci.yml`.
  - Update `AGENTS.md`, `GEMINI.md`, `STATUS.md`, `VALIDATION_REPORT.md`, and regenerate `SHA256SUMS.txt`.

## 2. Source Commit and Environment
- **Base Commit**: `3cce1cb6b9de754be1c14407e75b3e4ba4c84400` (on `main`)
- **Branch**: `wp/P0-WP1`
- **Commit**: `f1a9557`
- **Environment**:
  - Python: 3.12.13 (via `uv 0.11.21`)
  - Node.js: 26.7.0 LTS / npm 11.18.0
  - Docker: 29.9.2
- **Image Tags & Digests**:
  - `postgres:18.6-trixie`: `sha256:bd5dfa494a5a259704365813dbfa5fa6b3e69be7348d43b547f540c730478583`
  - `redis:8.10.1-trixie`: `sha256:8a1efc5f479551822b47424ccae982026b633f28818eab0387348120a61e10e2`
  - `qdrant/qdrant:v1.20.0`: `sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2`

## 3. Acceptance Criteria and Observed Results

| Acceptance Criterion | Expected | Observed Result | Evidence |
|---|---|---|---|
| AC-P0-WP1-1: uv workspace and lockfile | `eap` and `ppm-reducer` synchronized; `uv.lock` generated | Both packages installed editable in `.venv`; `uv.lock` committed | `uv sync` |
| AC-P0-WP1-2: Modular monolith packages | Packages in `src/eap/` with zero business logic | 11 packages + `db` created with clean docstrings | Directory inspection |
| AC-P0-WP1-3: FastAPI `/healthz` | GET `/healthz` returns 200 with status & env | Returns `{"status":"ok","env":"dev",...}` | `uv run pytest -q tests/test_healthz.py` |
| AC-P0-WP1-4: App starts with empty optional URLs | App runs when `REDIS_URL` and `QDRANT_URL` empty | Settings convert empty string to `None`; app functions cleanly | `uv run pytest -q tests/test_config.py` |
| AC-P0-WP1-5: Web HUD build on Node 26 | Vite + React + TS shell builds without error | Production bundle emitted to `web/hud/dist` in 402ms | `npm --prefix web/hud run build` |
| AC-P0-WP1-6: Docker Compose service isolation | Postgres starts by default; Redis/Qdrant optional | Postgres starts healthy; `--profile optional` starts Redis and Qdrant; all bind 127.0.0.1 | `docker compose up -d postgres`, `docker compose --profile optional up -d` |
| AC-P0-WP1-7: SC-06 Alembic database migrations | Up/down/up cycle on PostgreSQL 18.6 | `alembic upgrade head` -> `downgrade base` -> `upgrade head` succeeds | `uv run alembic upgrade head/downgrade base` |
| AC-P0-WP1-8: Standalone PPM validator | Runs in clean venv with requirements.txt alone | Clean venv runs `run_negative_cases.py` (PASS 5 templates, 20 cases) | `pip install -r ppm-v2/tools/requirements.txt` |
| AC-P0-WP1-9: Domain purity import boundary | `ppm_reducer` has zero forbidden imports | 0 forbidden imports (`eap`, `requests`, `socket`, `subprocess`, `urllib`, `dotenv`) | `uv run pytest -q tests/test_import_boundary.py` |
| AC-P0-WP1-10: SC-04 Secret canary redaction | Canary string never exposed in config `repr` | `repr(settings)` excludes canary and password values | `uv run pytest -q tests/test_secret_canary.py` |
| AC-P0-WP1-11: CI automation scripts | `scripts/ci.ps1` and GitHub Actions workflow | Both created and validated | `scripts/ci.ps1`, `.github/workflows/ci.yml` |
| AC-P0-WP1-12: Full shared check suite | All minimum checks pass | SC-01 PASS, SC-08 PASS, negative cases PASS, SHA256 PASS, pytest PASS, HUD build PASS | `scripts/ci.ps1` |

## 4. Security, Quality, and Operational Verification
- **Egress & Secrets**: Zero cloud egress, zero billable provider calls, zero credentials logged. Canary redaction verified in `tests/test_secret_canary.py`.
- **Network Isolation**: All compose port mappings bind exclusively to `127.0.0.1` (`127.0.0.1:5432`, `127.0.0.1:6379`, `127.0.0.1:6333-6334`).
- **Domain Boundary**: `ppm_reducer` strictly isolated with no I/O, no network, and no imports of `eap`.
- **Code Quality**: `uv run ruff check src tests ppm-v2/reducer migrations` passes (0 errors); `uv run mypy src tests ppm-v2/reducer` passes (0 errors across 21 source files).
- **Checksum Verification**: `sha256sum -c SHA256SUMS.txt` verified on all tracked repository files.

## 5. Unresolved Gaps
- None. All P0-WP1 checklist items in `docs/DEV_ENVIRONMENT.md` are satisfied.
- Successor work packages:
  - **P0-WP2 Contract models** (platform track)
  - **PPM-M0-WP1 Fixture corpus** (PPM domain track, unblocked in parallel)

## 6. Recommendation
- **Status**: `ready_for_review` (set in `STATUS.md`).
- **Owner Action**: Review branch `wp/P0-WP1` and merge into `main` if accepted.
- **Rollback Procedure**: Discard branch `wp/P0-WP1` via `git checkout main && git branch -D wp/P0-WP1`.
