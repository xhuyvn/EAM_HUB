#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

docker compose --profile mvp1 ps
echo ""
if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
  echo "API /healthz: OK (http://127.0.0.1:8000/healthz)"
else
  echo "API /healthz: UNREACHABLE"
fi
if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
  echo "HUD /:        OK (http://127.0.0.1:5173/)"
else
  echo "HUD /:        UNREACHABLE"
fi
