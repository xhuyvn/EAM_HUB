#!/usr/bin/env bash
set -euo pipefail

# D1-fixture sign-off demo script (fixtures-only, idempotent, resettable).
# Never prints bearer tokens or token hashes.

RESET_FLAG=""
EXPORT_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --reset)
      RESET_FLAG="--reset"
      shift
      ;;
    --export-dir)
      EXPORT_ARGS=(--export-dir "$2")
      shift 2
      ;;
    *)
      echo "ERROR: Unknown argument '$1'" >&2
      exit 1
      ;;
  esac
done

TENANT_ID="${EAP_OWNER_TENANT_ID:-tenant-owner}"
PROJECT_ID="${D1_PROJECT_ID:-proj-d1-demo}"
SOURCE_ID="${D1_SOURCE_ID:-src-d1-demo}"
API_BASE_URL="${D1_API_BASE_URL:-http://127.0.0.1:8000}"
HUD_URL="${D1_HUD_URL:-http://127.0.0.1:5173/}"

# --- Prerequisite checks ---
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is required (>= 2.41.0)." >&2
  exit 1
fi

GIT_VER_RAW="$(git --version)"
GIT_VER="$(echo "$GIT_VER_RAW" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
if [ -z "$GIT_VER" ]; then
  echo "ERROR: could not parse git version from '$GIT_VER_RAW'." >&2
  exit 1
fi
GIT_MAJOR="${GIT_VER%%.*}"
GIT_REST="${GIT_VER#*.}"
GIT_MINOR="${GIT_REST%%.*}"
if [ "$GIT_MAJOR" -lt 2 ] || { [ "$GIT_MAJOR" -eq 2 ] && [ "$GIT_MINOR" -lt 41 ]; }; then
  echo "ERROR: git >= 2.41.0 required, found $GIT_VER." >&2
  exit 1
fi

if [ -z "${EAP_COLLECTOR_FIXTURES_ROOT:-}" ]; then
  echo "ERROR: EAP_COLLECTOR_FIXTURES_ROOT must be set to a fixtures directory." >&2
  exit 1
fi

if [ "${EAP_COLLECTOR_ALLOW_REAL_ROOTS:-false}" != "false" ]; then
  echo "ERROR: D1 demo requires EAP_COLLECTOR_ALLOW_REAL_ROOTS=false (fixtures-only)." >&2
  exit 1
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL must be set and point to a reachable PostgreSQL database." >&2
  exit 1
fi

if ! uv run python -c "import os, sqlalchemy as sa; e = sa.create_engine(os.environ['DATABASE_URL']); c = e.connect(); c.execute(sa.text('SELECT 1')); c.close()" >/dev/null 2>&1; then
  echo "ERROR: PostgreSQL is not reachable at DATABASE_URL." >&2
  exit 1
fi

mkdir -p "$EAP_COLLECTOR_FIXTURES_ROOT"
FIXTURES_ROOT_ABS="$(cd "$EAP_COLLECTOR_FIXTURES_ROOT" && pwd)"

ARGS=(
  --tenant "$TENANT_ID"
  --project "$PROJECT_ID"
  --source "$SOURCE_ID"
  --fixtures-root "$FIXTURES_ROOT_ABS"
  --db-url "$DATABASE_URL"
  --api-base-url "$API_BASE_URL"
  --hud-url "$HUD_URL"
)
if [ -n "$RESET_FLAG" ]; then
  ARGS+=("$RESET_FLAG")
fi
if [ "${#EXPORT_ARGS[@]}" -gt 0 ]; then
  ARGS+=("${EXPORT_ARGS[@]}")
fi

uv run python -m eap.ppm.d1_demo "${ARGS[@]}"
