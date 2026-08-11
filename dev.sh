#!/usr/bin/env bash
# Start the whole stack for local development.
#
#   ./dev.sh          run API + web
#   ./dev.sh --seed   wipe the database and reload demo data first
#
# API  -> http://127.0.0.1:8000  (docs at /docs)
# Web  -> http://localhost:5173
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null; then
  echo "uv is not installed: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

echo "==> Installing backend dependencies"
(cd server && uv sync --quiet)

echo "==> Applying migrations"
(cd server && uv run alembic upgrade head >/dev/null)

if [[ "${1:-}" == "--seed" ]]; then
  echo "==> Seeding demo tournament"
  (cd server && rm -f kitchen_pass.db && uv run alembic upgrade head >/dev/null \
     && uv run python scripts/seed.py)
fi

if [[ ! -d web/node_modules ]]; then
  echo "==> Installing frontend dependencies"
  (cd web && npm install)
fi

cleanup() { kill 0 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> Starting API on :8000"
(cd server && KP_DEBUG=true uv run uvicorn app.main:app --port 8000 --reload) &

echo "==> Starting web on :5173"
(cd web && npm run dev) &

echo
echo "  Web:  http://localhost:5173"
echo "  API:  http://127.0.0.1:8000/docs"
echo "  Demo login: organizer@kitchenpass.dev / seed-password-123"
echo
wait
