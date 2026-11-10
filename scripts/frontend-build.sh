#!/usr/bin/env bash
set -e -o pipefail
cd "$(dirname "$0")/.."
if [ ! -f frontend/package-lock.json ]; then
  echo "Missing frontend/package-lock.json. Restore the committed lockfile before building." >&2
  exit 1
fi
npm ci --prefix frontend
npm run build --prefix frontend
