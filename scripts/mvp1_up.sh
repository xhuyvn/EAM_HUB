#!/usr/bin/env bash
set -euo pipefail

# Start EAP MVP1 local deploy package (loopback only).
# Never prints tokens, passwords, or hashes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

WITH_FIXTURE="false"
NO_BUILD="false"
for arg in "$@"; do
  case "$arg" in
    --with-fixture)
      WITH_FIXTURE="true"
      ;;
    --no-build)
      NO_BUILD="true"
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--with-fixture] [--no-build]" >&2
      exit 2
      ;;
  esac
done

# 1. Preflight: Docker & Docker Compose
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required." >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not running." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin is required." >&2
  exit 1
fi

# 2. Preflight: .env present or created from .env.example with generated secrets
gen_hex() {
  local bytes="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$bytes"
  else
    python3 -c "import secrets; print(secrets.token_hex($bytes))"
  fi
}

set_env_if_empty() {
  local key="$1"
  local val="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    local current
    current="$(grep "^${key}=" "$file" | head -n1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//' | tr -d '[:space:]')"
    if [ -z "$current" ]; then
      sed -i.bak "s|^${key}=.*|${key}=${val}|" "$file"
      rm -f "${file}.bak"
    fi
  else
    printf '\n%s=%s\n' "$key" "$val" >> "$file"
  fi
}

if [ ! -f ".env" ]; then
  if [ ! -f ".env.example" ]; then
    echo "ERROR: .env.example not found." >&2
    exit 1
  fi
  cp .env.example .env
  echo "Created .env from .env.example."
fi

set_env_if_empty "POSTGRES_PASSWORD" "$(gen_hex 24)" ".env"
set_env_if_empty "EAP_SECRET_CANARY" "EAP-CANARY-$(gen_hex 16)" ".env"
set_env_if_empty "EAP_HUD_CURSOR_SECRET" "$(gen_hex 32)" ".env"

# 3. Preflight: check loopback ports 5432, 8000, 5173 if stack is not already running
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | grep -qE "[:.]${port}[[:space:]]"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN -Pn >/dev/null 2>&1
  else
    return 1
  fi
}

PG_PORT="${EAP_POSTGRES_PORT:-5432}"
API_PORT="${EAP_API_PORT:-8000}"
HUD_PORT="${EAP_HUD_PORT:-5173}"
for svc_port in "postgres:${PG_PORT}" "api:${API_PORT}" "hud:${HUD_PORT}"; do
  svc="${svc_port%%:*}"
  port="${svc_port##*:}"
  cid="$(docker compose --profile mvp1 ps -q "$svc" 2>/dev/null || true)"
  if [ -z "$cid" ] && port_in_use "$port"; then
    echo "ERROR: Port 127.0.0.1:${port} is already in use by another process." >&2
    exit 1
  fi
done

# 4. Start services
if [ "$NO_BUILD" = "true" ]; then
  docker compose --profile mvp1 up -d
else
  docker compose --profile mvp1 up -d --build
fi

# 5. Wait for health
echo "Waiting for MVP1 services to become healthy..."
deadline=$((SECONDS + 120))
while [ "$SECONDS" -lt "$deadline" ]; do
  api_ok="false"
  hud_ok="false"
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    api_ok="true"
  fi
  if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    hud_ok="true"
  fi
  worker_cid="$(docker compose --profile mvp1 ps -q worker 2>/dev/null || true)"
  worker_health=""
  if [ -n "$worker_cid" ]; then
    worker_health="$(docker inspect -f '{{.State.Health.Status}}' "$worker_cid" 2>/dev/null || true)"
  fi
  if [ "$api_ok" = "true" ] && [ "$hud_ok" = "true" ] && [ "$worker_health" = "healthy" ]; then
    break
  fi
  sleep 2
done

if ! curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  echo "ERROR: API failed to become healthy on http://127.0.0.1:8000/healthz" >&2
  docker compose --profile mvp1 logs --tail=50 api migrate >&2
  exit 1
fi
if ! curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
  echo "ERROR: HUD failed to become healthy on http://127.0.0.1:5173/" >&2
  docker compose --profile mvp1 logs --tail=50 hud >&2
  exit 1
fi
worker_cid="$(docker compose --profile mvp1 ps -q worker 2>/dev/null || true)"
worker_health=""
if [ -n "$worker_cid" ]; then
  worker_health="$(docker inspect -f '{{.State.Health.Status}}' "$worker_cid" 2>/dev/null || true)"
fi
if [ "$worker_health" != "healthy" ]; then
  echo "ERROR: Worker failed to become healthy." >&2
  docker compose --profile mvp1 logs --tail=50 worker >&2
  exit 1
fi

# 6. Register fixture mock agent + binding (idempotent P3a-WP5 CLI)
docker compose --profile mvp1 exec -T api \
  python -m eap.agents.cli register \
  --manifest src/eap/agents/fixtures/mock_agent.json \
  --binding src/eap/agents/fixtures/mock_binding.json >/dev/null

# 7. Optional D0/D1 fixture project seed
if [ "$WITH_FIXTURE" = "true" ]; then
  docker compose --profile mvp1 exec -T api python -m eap.mvp1_fixture >/dev/null
  echo "Seeded D0/D1 fixture project (proj-d0-demo) and sample tasks/runs."
fi

echo ""
echo "EAP MVP1 is running (loopback only):"
echo "  HUD URL:    http://127.0.0.1:5173"
echo "  API Health: http://127.0.0.1:8000/healthz"
echo ""
echo "Owner token setup (never stored in plaintext in .env):"
echo "  1. Generate a local token:  openssl rand -hex 32"
echo "  2. Hash it (SHA-256 hex):   printf '%s' '<token>' | sha256sum"
echo "  3. Set EAP_OWNER_TOKEN_HASH=<sha256_hex> in .env and run: docker compose --profile mvp1 up -d api"
echo "  4. Paste <token> into the HUD UI token field."
