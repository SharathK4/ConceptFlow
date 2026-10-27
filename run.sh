#!/usr/bin/env bash
# ConceptFlow AI - launcher for macOS / Linux.
#
#   ./run.sh          build the page if needed, then serve everything on http://127.0.0.1:8010
#   ./run.sh dev      API on :8010 plus the Vite dev server on :5179 (hot reload)
#   ./run.sh setup    create .venv, install Python and npm dependencies, create .env
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python

setup() {
  python3 -m venv .venv
  $PY -m pip install --upgrade pip
  $PY -m pip install -r requirements.txt
  (cd frontend && npm install)
  [ -f .env ] || { cp .env.example .env; echo "Created .env - add your API keys there."; }
}

case "${1:-}" in
  setup) setup; exit 0 ;;
esac
[ -x "$PY" ] || setup

if [ "${1:-}" = "dev" ]; then
  $PY -m uvicorn conceptflow.server:app --app-dir backend --host 127.0.0.1 --port 8010 --reload --reload-dir backend &
  API=$!
  trap 'kill $API 2>/dev/null' EXIT
  (cd frontend && npm run dev -- --host 127.0.0.1 --port 5179 --strictPort)
  exit 0
fi

[ -f frontend/dist/index.html ] || (cd frontend && npm run build)
echo "ConceptFlow -> http://127.0.0.1:8010"
exec $PY -m uvicorn conceptflow.server:app --app-dir backend --host 127.0.0.1 --port 8010
