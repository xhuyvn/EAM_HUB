# EAM_HUB — Dev Environment

06/11/2026 · Huynn · Original: [Claude Docs](https://claude.ai/artifact/CC31A9ymgUtReLd1gPEpmK)

## Scope and principles

Platform (`src/eap`, `web/hud`) and PPM v2 (`ppm-v2/`) share a single repository `EAM_HUB`, a single Python 3.12 installation, and a single set of local services. This environment serves development and testing on the owner machine; private servers follow post-Local-pilot.

- **One repository, one Python lock file**; Node solely for `web/hud`.
- **PostgreSQL is the single source of truth** for tasks, runs, journals, assessments, and audit logs. Redis and Qdrant store derived data only, rebuildable upon loss.
- **`ppm_reducer` has no I/O, does not read `.env`, and never imports `eap`**; import direction is strictly `eap.ppm → ppm_reducer`.
- **The verifier executes untrusted tests in clean environments**, never inheriting developer `.env`.
- **The standalone PPM validator** executes with `ppm-v2/tools/requirements.txt` alone, without external services.

## Versions

Four core components are pinned per owner specification. All Docker images pin exact tags, avoiding `latest`; tags and digests are verified via `docker pull` in P0-WP1.

| Component | Version / image | Role | Required for D1 | Verification (06/11/2026) |
| --- | --- | --- | --- | --- |
| Python | 3.12 (`requires-python = ">=3.12,<3.13"`) | Platform + `ppm_reducer` + validator | Yes | Validator verified on 3.10; CI re-executes on 3.12 |
| PostgreSQL | `postgres:18.6-trixie` | Source of truth, work queue (`SKIP LOCKED`), outbox, FTS | Yes | 18.6 released 13/08/2026 ([postgresql.org](https://www.postgresql.org/)); image tag verified in P0-WP1 |
| Redis | `redis:8.10.1-trixie` | Derived cache/rate limiting (see below) | No | 8.10.1 security patch August 2026 ([release notes](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/)); image tag verified in P0-WP1 |
| Qdrant | `qdrant/qdrant:v1.20.0` | Derived vector index for retrieval | No | Python client v1.20.0 released 22/10/2026 supporting Qdrant 1.20 ([qdrant-client](https://github.com/qdrant/qdrant-client/releases/tag/v1.20.0)); server release page verification in P0-WP1 |
| Node | 26 LTS | `web/hud` (Vite + React + TS) | Yes | Node 26 Active LTS entered 28/10/2026, EOL 30/04/2029 ([DevX](https://www.devx.com/coding/nodejs-26-lts-new-features-release-date/)) |
| Ollama | local installation | Local model provider | No (fake model in D1) | Spike P0-WP6 |

PostgreSQL 18 replaces 16 (plan rev 1.5, ADR-026); P0-WP1 executes SC-06 (migration up/down/up) on 18.6. Redis 8 is licensed under AGPL-3.0/RSAL/SSPL; internal evaluation is generally uninhibited, but bank software licensing policies must be cross-checked prior to private server deployment.

## Redis and Qdrant in the architecture

Redis and Qdrant exist in the environment from the beginning but do not lie on the critical path to D1-fixture. The review selected PostgreSQL `SKIP LOCKED` for work queues and PostgreSQL FTS for search, following the rule of avoiding infrastructural additions purely for performance before empirical measurements warrant.

| Service | Used for | Prohibited for | Activation trigger |
| --- | --- | --- | --- |
| Redis | HUD projection cache, rate limiting, SSE fan-out with multi-process APIs | Run/dispatch queues, journals, leases, idempotency keys, HUD cursors (all in PostgreSQL) | Measurements from P1a-WP7 / P3a-WP4 exceed thresholds, or >1 API process deployed |
| Qdrant | Embedding vector index for note/document retrieval, rebuildable from PostgreSQL + CAS | Storing criterion state, verification decisions, single source of any record | Retrieval benchmark proves FTS insufficient (H1), embedding model authorized by egress policy |

- Both services reside under compose profile `optional`; default `docker compose up` starts PostgreSQL only.
- Code accesses Redis/Qdrant strictly through interfaces (`CacheBackend`, `VectorIndex`) with in-memory/no-op implementations for testing.
- Embeddings for Qdrant are subject to the identical egress policy as models (binding 1.1): local-only by default.
- Before either service becomes mandatory, a new ADR must be recorded in `docs/01` accompanied by empirical measurements.

## Repository and workspace

A single uv workspace with two Python packages installed editable into the same `.venv`; Node isolated within `web/hud`.

```
EAM_HUB/
  pyproject.toml            # workspace root: package eap, requires-python >=3.12,<3.13
  uv.lock                   # single lock file, committed
  src/eap/                  # platform (FastAPI, Run Service, PPM integration ...)
  ppm-v2/reducer/           # workspace member: package ppm_reducer (stdlib + jsonschema + PyYAML)
  ppm-v2/tools/             # standalone validator, independent requirements.txt
  web/hud/                  # Vite + React + TS, package-lock.json committed
  compose.yaml              # postgres (default), redis + qdrant (optional profile)
  .env.example              # committed; actual .env never committed
```

```bash
uv sync                         # creates .venv, installs eap + ppm_reducer editable
uv run pytest -q
uv run python tools/validate_package.py
uv run python ppm-v2/tools/run_negative_cases.py
npm --prefix web/hud ci && npm --prefix web/hud run build
```

Development dependencies are defined in root `[dependency-groups] dev`: `pytest`, `ruff`, `mypy`. `ppm_reducer` declares its own minimal dependencies; tests ensure it never imports `eap`, `requests`, `socket`, or `subprocess`.

## Local services (Docker Compose)

PostgreSQL executes by default; Redis and Qdrant start solely with `--profile optional`. All ports bind strictly to `127.0.0.1`, never exposing to LAN.

```yaml
services:
  postgres:
    image: postgres:18.6-trixie
    environment:
      POSTGRES_DB: eap_dev
      POSTGRES_USER: eap
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set in .env}
    ports: ["127.0.0.1:5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U eap -d eap_dev"]
      interval: 5s

  redis:
    image: redis:8.10.1-trixie
    profiles: ["optional"]
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?set in .env}", "--appendonly", "no"]
    ports: ["127.0.0.1:6379:6379"]

  qdrant:
    image: qdrant/qdrant:v1.20.0
    profiles: ["optional"]
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:?set in .env}
      QDRANT__TELEMETRY_DISABLED: "true"
    ports: ["127.0.0.1:6333:6333", "127.0.0.1:6334:6334"]
    volumes: ["qdrantdata:/qdrant/storage"]

volumes:
  pgdata:
  qdrantdata:
```

- Testing utilizes an isolated database `eap_test`, created and dropped per pytest session; tests never run against `eap_dev`.
- PostgreSQL 18 image utilizes versioned data directories under `/var/lib/postgresql`; mount paths verified in image README during P0-WP1.
- Qdrant telemetry is disabled because telemetry is governed by platform egress policy (ARCH-15).

## `.env` and secrets

Only the platform reads `.env`; the reducer, validator, and test code of verified projects never see it.

```dotenv
# .env.example — commit this file, never commit actual .env
EAP_ENV=dev
EAP_BIND=127.0.0.1:8000
DATABASE_URL=postgresql+psycopg://eap:${POSTGRES_PASSWORD}@127.0.0.1:5432/eap_dev
TEST_DATABASE_URL=postgresql+psycopg://eap:${POSTGRES_PASSWORD}@127.0.0.1:5432/eap_test
POSTGRES_PASSWORD=
REDIS_URL=            # empty = CacheBackend no-op
REDIS_PASSWORD=
QDRANT_URL=           # empty = VectorIndex disabled
QDRANT_API_KEY=
OLLAMA_BASE_URL=http://127.0.0.1:11434
EAP_EGRESS_DEFAULT=local_only
EAP_TELEMETRY=local_only
EAP_SECRET_CANARY=     # generated randomly per machine, used for SC-04
```

- New environment variables require updating `.env.example` in the identical commit; configs loaded via `pydantic-settings`, halting startup if required variables are missing.
- Tests utilize `.env.test` generated from fixtures; coding agents must never read real `.env`.
- Logs, traces, and events omit values of any `*_PASSWORD`, `*_KEY`, or `EAP_SECRET_CANARY` variable (SC-04 grep canary).
- **Verifier (P2a-WP9)** launches tests in empty environments with allowlisted variables (`PATH`, temporary `HOME`, `LANG`, `PYTHONHASHSEED`), without network, in temporary workspaces. Mandatory test: fixture test printing full environment; output must omit `EAP_SECRET_CANARY`.

## P0-WP1 Checklist and open points

Environment is considered complete when all items below have real output recorded in `VALIDATION_REPORT.md`.

- [ ] `docker compose pull` succeeds across all three tags; record image digests in `docs/DEV_ENVIRONMENT.md`
- [ ] `docker compose up -d` starts PostgreSQL only; healthcheck green; `--profile optional` starts Redis and Qdrant
- [ ] `uv sync` on clean Python 3.12; `uv.lock` committed
- [ ] `uv run python tools/validate_package.py` and `ppm-v2/tools/run_negative_cases.py` PASS on 3.12
- [ ] `pip install -r ppm-v2/tools/requirements.txt` in clean venv → standalone PPM validator runs without platform
- [ ] Test import-boundary: `ppm_reducer` does not import `eap`/network/subprocess
- [ ] SC-06 migration up/down/up on PostgreSQL 18.6
- [ ] App starts and tests pass when `REDIS_URL`, `QDRANT_URL` are empty
- [ ] SC-04: canary does not appear in logs

Open points:

- Plan rev 1.5 updated PostgreSQL 18.6 / Python 3.12 / Node 26 LTS (ADR-026).
- Qdrant v1.20.0 server release and `-trixie` tags verified directly in P0-WP1 via `docker pull`.
- Redis 8 licensing to be evaluated against bank software policies before private server deployment.
- Storage capacity: Qdrant and PostgreSQL volumes count against 100 GB shared server budget; measured in P5-WP4.

## Sources

- [PostgreSQL 18.6 release (postgresql.org)](https://www.postgresql.org/)
- [Redis Open Source 8.10 release notes](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/)
- [qdrant-client v1.20.0](https://github.com/qdrant/qdrant-client/releases/tag/v1.20.0)
- [Node.js 26 LTS (DevX)](https://www.devx.com/coding/nodejs-26-lts-new-features-release-date/)
