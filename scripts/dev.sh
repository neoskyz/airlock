#!/usr/bin/env bash
set -e -o pipefail
cd "$(dirname "$0")/.."
bash scripts/frontend-build.sh
uv sync
if [ -f .env ]; then
  exec uv run --no-sync uvicorn airlock.app:app --host 127.0.0.1 --port "${PORT:-8000}" --env-file .env
fi
exec uv run --no-sync uvicorn airlock.app:app --host 127.0.0.1 --port "${PORT:-8000}"
