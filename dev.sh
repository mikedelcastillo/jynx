#!/usr/bin/env bash
#
# dev.sh — run Jynx in dev mode.
#
# Runs both services in the current terminal with color-labeled, interleaved
# logs:
#   • [backend ] FastAPI backend  (http://localhost:8000, auto-reload)
#   • [frontend] Next.js frontend (http://localhost:4000)
#
# Ctrl-C stops both. On first run it also bootstraps the backend virtualenv,
# the frontend node_modules, and the root .env.
#
# Override ports with FRONTEND_PORT / BACKEND_PORT.
#
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"

FRONTEND_PORT="${FRONTEND_PORT:-4000}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

log() { printf '\033[1;36m[dev]\033[0m %s\n' "$*"; }

# prefix LABEL COLOR — tag each line of stdin with a colored [LABEL].
# Child ANSI colors still render: we only prepend at the start of each line.
prefix() {
  local label="$1" color="$2" line
  while IFS= read -r line; do
    printf '\033[%sm[%s]\033[0m %s\n' "$color" "$label" "$line"
  done
}

# ── First-run setup ────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  log "no .env found — copying .env.example -> .env (set OPENAI_* for your LLM)"
  cp .env.example .env
fi

if [ ! -x backend/.venv/bin/python ]; then
  log "setting up backend virtualenv + dependencies (first run)..."
  if command -v uv >/dev/null 2>&1; then
    ( cd backend && uv venv .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt )
  else
    ( cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt )
  fi
fi

if [ ! -d frontend/node_modules ]; then
  log "installing frontend dependencies (first run)..."
  ( cd frontend && npm install )
fi

# ── Service commands ───────────────────────────────────────────────────────
# `exec` so the server replaces its `bash -c` wrapper (no extra shell lingers).
# PYTHONUNBUFFERED=1 makes Python flush line-by-line through the pipe — without
# a tty it would block-buffer stdout and the live log would stall.
# The frontend already defaults to 4000 (package.json); only pass -p to override.
BACKEND_CMD="cd '$ROOT/backend' && HOST=0.0.0.0 PORT=$BACKEND_PORT RELOAD=true PYTHONUNBUFFERED=1 exec .venv/bin/python -m app"
FRONTEND_ARGS=""
[ "$FRONTEND_PORT" != "4000" ] && FRONTEND_ARGS=" -- -p $FRONTEND_PORT"
FRONTEND_CMD="cd '$ROOT/frontend' && exec npm run dev$FRONTEND_ARGS"

log "backend  -> http://localhost:$BACKEND_PORT"
log "frontend -> http://localhost:$FRONTEND_PORT"
log "Ctrl-C stops both."

# ── Launch ─────────────────────────────────────────────────────────────────
# Both run as background pipelines in this terminal; `kill 0` on Ctrl-C signals
# the whole process group, reaping the children that uvicorn --reload / next dev
# spawn (a single PID wouldn't — `$!` points at the `prefix` stage, not the server).
trap 'kill 0' INT TERM
bash -c "$BACKEND_CMD"  2>&1 | prefix 'backend ' 36 &
bash -c "$FRONTEND_CMD" 2>&1 | prefix 'frontend' 35 &
wait
