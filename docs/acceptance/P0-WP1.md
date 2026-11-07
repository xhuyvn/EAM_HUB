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
  - Enforce domain purity for `ppm_reducer` (zero imports of `eap`, `requests`, `socket`, `subprocess`, `urllib`, `dotenv`, `importlib`, `os`, `pathlib`, `http`, `io`, or calls to `open`/`__import__`).
  - Implement SC-04 secret canary redaction checks using `SecretStr` across all sensitive settings.
  - Author CI automation script `scripts/ci.ps1` and GitHub Actions workflow `.github/workflows/ci.yml`.
  - Address all Owner Review feedback items (R1–R9).

## 2. Source Commit and Traceability
- **Base Commit**: `3cce1cb6b9de754be1c14407e75b3e4ba4c84400` (on `main`)
- **Branch**: `wp/P0-WP1`
- **Code Commit under Review**: `91f9294` (and successor fix commit on branch `wp/P0-WP1`)
  *(Note: The acceptance packet commit on `main` is recorded by the owner upon merge)*
- **Environment**:
  - Python: 3.12.13 (via `uv 0.11.21`)
  - Node.js: 26.7.0 LTS / npm 11.18.0
  - Docker: 29.9.2
- **Image Tags & RepoDigests**:
  - `postgres:18.6-trixie`: `sha256:bd5dfa494a5a259704365813dbfa5fa6b3e69be7348d43b547f540c730478583`
  - `redis:8.10.1-trixie`: `sha256:8a1efc5f479551822b47424ccae982026b633f28818eab0387348120a61e10e2`
  - `qdrant/qdrant:v1.20.0`: `sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2`

## 3. Owner Review Feedback Matrix (R1–R9)

| Review Item | Description | Resolution / Status |
|---|---|---|
| **R1 (SC-04 SecretStr)** | In `src/eap/config.py` make sensitive fields `SecretStr`; update callers to `.get_secret_value()`; add DSN password and caplog tests | **FIXED / PASS**: `database_url`, `test_database_url`, `postgres_password`, `redis_password`, `qdrant_api_key`, `eap_secret_canary` typed as `SecretStr \| None`; empty coerced to `None`; callers updated; tests pass. |
| **R2 (.env.example & DB config)** | Move `POSTGRES_PASSWORD` above interpolated lines; remove hardcoded `sqlalchemy.url` from `alembic.ini`; `migrations/env.py` raises error if unset; test interpolation | **FIXED / PASS**: `.env.example` reordered; `alembic.ini` URL removed; `migrations/env.py` raises `RuntimeError`; `test_env_example_interpolation` passes. |
| **R3 (Traceability)** | Cite code commit under review; state that packet commit is recorded by owner at merge | **FIXED / PASS**: Section 2 updated per specification. |
| **R4 (Raw Evidence)** | Append raw command outputs to `VALIDATION_REPORT.md` P0-WP1 section for docker inspect, compose ps, alembic cycle, clean venv, and GitHub Actions run | **FIXED / PASS**: Raw command outputs recorded verbatim in `VALIDATION_REPORT.md`. |
| **R5 (Import Boundary)** | Resolve path from `__file__`; forbid `importlib`, `os`, `pathlib`, `http`, `io`, `open`/`__import__`; pass from any cwd | **FIXED / PASS**: `tests/test_import_boundary.py` resolves from `__file__`, checks AST for all forbidden modules and builtin calls; passes from any working directory. |
| **R6 (Settings in tests)** | Use `_env_file=None` in tests; test `create_app()` serves `/healthz` 200 with empty optional services | **FIXED / PASS**: `tests/test_config.py` and `tests/test_secret_canary.py` use `_env_file=None`; `test_create_app_empty_optional_services_serves_healthz` passes. |
| **R7 (CI pinning)** | `uv sync --locked`; pin setup-uv version; add `"engines": {"node": ">=26"}` to `web/hud/package.json` | **FIXED / PASS**: `setup-uv@v5.1.0` pinned to `0.11.21`; `uv sync --locked` used; engines field added to `web/hud/package.json`. |
| **R9 (HUD error handling)** | `web/hud/src/App.tsx`: on fetch failure show "Backend unreachable" + last-checked timestamp | **FIXED / PASS**: `web/hud/src/App.tsx` updated with state and timestamp display; built and verified. |

## 4. Acceptance Criteria and Observed Results

| Acceptance Criterion | Expected | Observed Result | Evidence |
|---|---|---|---|
| AC-P0-WP1-1: uv workspace and lockfile | `eap` and `ppm-reducer` synchronized; `uv.lock` generated | Both packages installed editable in `.venv`; `uv.lock` committed | `uv sync --locked` |
| AC-P0-WP1-2: Modular monolith packages | Packages in `src/eap/` with zero business logic | 11 packages + `db` created with clean docstrings | Directory inspection |
| AC-P0-WP1-3: FastAPI `/healthz` | GET `/healthz` returns 200 with status & env | Returns `{"status":"ok","env":"test",...}` | `uv run pytest -q tests/test_healthz.py` |
| AC-P0-WP1-4: App starts with empty optional URLs | App runs when `REDIS_URL` and `QDRANT_URL` empty | Settings convert empty string to `None`; app functions cleanly | `uv run pytest -q tests/test_config.py` |
| AC-P0-WP1-5: Web HUD build on Node 26 | Vite + React + TS shell builds without error | Production bundle emitted to `web/hud/dist` in 591ms | `npm --prefix web/hud run build` |
| AC-P0-WP1-6: Docker Compose service isolation | Postgres starts by default; Redis/Qdrant optional | Postgres starts healthy; `--profile optional` starts Redis and Qdrant; all bind 127.0.0.1 | `docker compose up -d postgres`, `docker compose --profile optional up -d` |
| AC-P0-WP1-7: SC-06 Alembic database migrations | Up/down/up cycle on PostgreSQL 18.6 | `alembic upgrade head` -> `downgrade base` -> `upgrade head` succeeds | `VALIDATION_REPORT.md` Section 3 |
| AC-P0-WP1-8: Standalone PPM validator | Runs in clean venv with requirements.txt alone | Clean venv runs `run_negative_cases.py` (PASS 5 templates, 20 cases) | `VALIDATION_REPORT.md` Section 4 |
| AC-P0-WP1-9: Domain purity import boundary | `ppm_reducer` has zero forbidden imports/calls | 0 forbidden imports/calls (`eap`, `requests`, `socket`, `subprocess`, `urllib`, `dotenv`, `importlib`, `os`, `pathlib`, `http`, `io`, `open`, `__import__`) | `uv run pytest -q tests/test_import_boundary.py` |
| AC-P0-WP1-10: SC-04 Secret canary redaction | Canary string and passwords never exposed | `repr(settings)`, `str(settings)`, and `caplog` exclude secrets | `uv run pytest -q tests/test_secret_canary.py` |
| AC-P0-WP1-11: CI automation scripts | `scripts/ci.ps1` and GitHub Actions workflow | Both created, pinned, and validated | `scripts/ci.ps1`, `.github/workflows/ci.yml` |
| AC-P0-WP1-12: Full shared check suite | All minimum checks pass | SC-01 PASS, SC-08 PASS, negative cases PASS, SHA256 PASS, pytest PASS (9 passed), HUD build PASS | `scripts/ci.ps1` |

## 5. Security, Quality, and Operational Verification
- **Egress & Secrets**: Zero cloud egress, zero billable provider calls, zero credentials logged. Canary and password redaction verified via `SecretStr` in `tests/test_secret_canary.py`.
- **Network Isolation**: All compose port mappings bind exclusively to `127.0.0.1` (`127.0.0.1:5432`, `127.0.0.1:6379`, `127.0.0.1:6333-6334`).
- **Domain Boundary**: `ppm_reducer` strictly isolated with no I/O, no network, and no imports of `eap` or filesystem modules.
- **Code Quality**: `uv run ruff check src tests ppm-v2/reducer migrations` passes (0 errors); `uv run mypy src tests ppm-v2/reducer` passes (0 errors across 21 source files).
- **Checksum Verification**: `sha256sum -c SHA256SUMS.txt` verified on all 197 tracked repository files.

## 6. Unresolved Gaps
- **Redis 8 Licensing Open Point**:
  - *Description*: Redis 8 dual license (RSALv2 / SSPLv1) requires evaluation against enterprise/bank software licensing policies before deployment to private server infrastructure.
  - *Owner*: Architecture Lead / Owner
  - *Deadline*: Prior to P5-WP2 (server deployment).
  - *Impact*: In local MVP (P0–D1), Redis is purely optional and not on the critical path; all queues and state reside in PostgreSQL 18.6.

## 7. Recommendation
- **Status**: `ready_for_review` (set in `STATUS.md`).
- **Owner Action**: Review branch `wp/P0-WP1` and merge into `main` if accepted.
- **Rollback Procedure**: Discard branch `wp/P0-WP1` via `git checkout main && git branch -D wp/P0-WP1`.
