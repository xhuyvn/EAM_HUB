# EAP MVP1 Local Deploy Runbook

MVP1 is a **local, loopback-only (`127.0.0.1`)** deployment package for evaluating the Enterprise Agent Platform (EAP) on a single workstation.

## What MVP1 Does NOT Include

- **No real agents or external adapters**: Only `MockProvider` (`agent-mock-provider`) is included.
- **No OIDC / IdP authentication**: Authentication uses local SHA-256 token hashes in `.env` (`EAP_OWNER_TOKEN_HASH`, `EAP_SECOND_TOKEN_HASH`).
- **No remote / LAN / public access**: All published ports bind strictly to `127.0.0.1`, and `src/eap/config.py` refuses non-loopback `EAP_BIND` addresses at startup.
- **No real project directories before DG-3**: `EAP_COLLECTOR_ALLOW_REAL_ROOTS` remains `false` (fixtures-only).

## Architecture & Container Images

| Service | Image / Build | User | Host Port | Purpose |
|---|---|---|---|---|
| `postgres` | `postgres:18.6-trixie` | `postgres` | `127.0.0.1:5432` | Source of truth (volume `pgdata`) |
| `migrate` | `Dockerfile` (`python:3.12.13-slim-trixie`, `uv:0.11.21`) | `10001:10001` (`eap`) | — | One-shot `alembic upgrade head` |
| `api` | `Dockerfile` (`python:3.12.13-slim-trixie`) | `10001:10001` (`eap`) | `127.0.0.1:8000` | FastAPI + Uvicorn (`--no-access-log`) |
| `worker` | `Dockerfile` (`python:3.12.13-slim-trixie`) | `10001:10001` (`eap`) | — | Outbox relay, mock dispatcher, scheduler, orchestrator |
| `hud` | `web/hud/Dockerfile` (`node:26-alpine` -> `nginxinc/nginx-unprivileged:1.27.5-alpine`) | `101:101` (`nginx`) | `127.0.0.1:5173` | Built HUD static UI + `/v1/` & `/healthz` proxy |

**HUD serving choice**: The HUD is built in a Node 26 stage and served by `nginxinc/nginx-unprivileged:1.27.5-alpine` as a dedicated `hud` container. This keeps the Python `api` image minimal without Node/static assets, runs nginx as non-root (`101:101`), and proxies `/v1/hud/events` with `proxy_buffering off` for SSE streaming on `http://127.0.0.1:5173`.

## Prerequisites

- **Docker Desktop** (Windows/macOS) or **Docker Engine + Docker Compose v2** (Linux).
- Free loopback ports: `127.0.0.1:5432`, `127.0.0.1:8000`, `127.0.0.1:5173`.
- Git `>= 2.41` is **not** required inside the containers for MVP1.

## Quick Start

### Windows (PowerShell)

```powershell
# Start stack + register mock agent & binding + seed D0/D1 fixture project:
.\scripts\mvp1_up.ps1 -WithFixture

# Check status and health:
.\scripts\mvp1_status.ps1

# Stop containers (preserves pgdata volume):
.\scripts\mvp1_down.ps1

# Reset everything including database volume (destructive; requires explicit flag):
.\scripts\mvp1_reset.ps1 -ConfirmReset
```

### Linux / macOS (Bash)

```bash
./scripts/mvp1_up.sh --with-fixture
./scripts/mvp1_status.sh
./scripts/mvp1_down.sh
./scripts/mvp1_reset.sh --confirm-reset
```

On first run, `mvp1_up` copies `.env.example` to `.env` and generates random values for `POSTGRES_PASSWORD`, `EAP_SECRET_CANARY`, and `EAP_HUD_CURSOR_SECRET`.

## Creating the Owner Token Locally

Plaintext tokens are **never** stored in `.env` or printed by scripts. Generate a token locally and store only its SHA-256 hex digest:

**Bash / macOS / WSL:**
```bash
TOKEN=$(openssl rand -hex 32)
printf '%s' "$TOKEN" | sha256sum
```

**PowerShell:**
```powershell
python -c "import secrets, hashlib; t=secrets.token_hex(32); print('SET EAP_OWNER_TOKEN_HASH=' + hashlib.sha256(t.encode()).hexdigest())"
```

Put the SHA-256 hex digest in `.env` as `EAP_OWNER_TOKEN_HASH=<hex>`, apply it with:
```bash
docker compose --profile mvp1 up -d api
```
Then open `http://127.0.0.1:5173` and paste your plaintext token into the HUD token input.

## Logs

All containers log to stdout/stderr in structured JSON (with SC-04 credential redaction and `--no-access-log` on Uvicorn so query strings are never logged):

```bash
docker compose --profile mvp1 logs -f api worker
docker compose --profile mvp1 logs --tail=100 migrate
```

## Backup and Restore (PostgreSQL Volume)

### Backup (`pg_dump`)

```bash
docker compose --profile mvp1 exec -T postgres \
  pg_dump -U eap -d eap_dev -Fc > eap_mvp1_backup.dump
```

### Restore (`pg_restore`)

Stop `api` and `worker` first so no writers hold locks, recreate the schema/database, restore, and restart:

```bash
docker compose --profile mvp1 stop api worker
docker compose --profile mvp1 exec -T postgres \
  psql -U eap -d postgres -c "DROP DATABASE IF EXISTS eap_dev;"
docker compose --profile mvp1 exec -T postgres \
  psql -U eap -d postgres -c "CREATE DATABASE eap_dev OWNER eap;"
docker compose --profile mvp1 exec -T postgres \
  pg_restore -U eap -d eap_dev --no-owner --no-acl < eap_mvp1_backup.dump
docker compose --profile mvp1 up -d api worker
```

## Upgrade

```bash
git pull
docker compose --profile mvp1 up -d --build
```
The `migrate` one-shot container runs `alembic upgrade head` before `api` and `worker` start.

## Rollback

1. Take a `pg_dump` backup before upgrading.
2. Check out or tag the previous known-good image/commit (`git checkout <prev-tag>`).
3. **Alembic downgrade limits**: `alembic downgrade <rev>` is tested in CI via `SC-06` (`tools/run_sc06.py`), but downgrading a populated database across destructive schema changes can drop new tables/columns. Prefer restoring from the pre-upgrade `pg_dump` if data loss is unacceptable.
4. Rebuild and start: `docker compose --profile mvp1 up -d --build`.

## Troubleshooting

- **Port already in use**: `mvp1_up` checks `5432`, `8000`, `5173`. Stop any local PostgreSQL, Vite, or Uvicorn process listening on those ports.
- **401 Unauthorized in HUD / API**: Verify `EAP_OWNER_TOKEN_HASH` in `.env` matches `sha256(<your-token>)` and restart `api` (`docker compose --profile mvp1 up -d api`).
- **422 / InsecureBindError**: Do not set `EAP_BIND` to `0.0.0.0` or a LAN IP; `src/eap/config.py` intentionally refuses non-loopback addresses.
- **Resetting a broken local DB**: Run `./scripts/mvp1_reset.sh --confirm-reset` (or `.\scripts\mvp1_reset.ps1 -ConfirmReset`) and then `mvp1_up`.
