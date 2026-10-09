#!/usr/bin/env bash
# Start the backend (FastAPI, port 8000) and the frontend (Vite, port 5173) together.
# Stop both with Ctrl+C.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

(cd "$ROOT/backend" && uv sync --quiet && uv run uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null' EXIT

cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run dev
