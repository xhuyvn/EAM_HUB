#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

CONFIRMED="false"
for arg in "$@"; do
  case "$arg" in
    --confirm-reset|--yes)
      CONFIRMED="true"
      ;;
  esac
done

if [ "$CONFIRMED" != "true" ]; then
  echo "REFUSED: mvp1_reset drops all Docker volumes (database data will be lost)." >&2
  echo "Pass --confirm-reset (or --yes) to proceed." >&2
  exit 1
fi

docker compose --profile mvp1 down -v --remove-orphans
echo "MVP1 containers and volumes removed."
