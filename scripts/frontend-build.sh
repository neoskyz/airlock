#!/usr/bin/env bash
set -e -o pipefail
cd "$(dirname "$0")/.."
if [ ! -f frontend/package-lock.json ]; then
  npm install --package-lock-only --ignore-scripts --prefix frontend
fi
npm ci --prefix frontend
npm run build --prefix frontend
