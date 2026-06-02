#!/usr/bin/env bash
# Post 6 — launch the LiteLLM proxy. Same shape as Post 5: the metering callback
# from Post 5 stays wired up, so summarization calls in demo.py's Block 4 still
# produce metering rows. Threads are an app-side store that the gateway is
# blissfully unaware of.
#
# Usage:
#   ./start-gateway.sh           # start in background, log to ./logs/gateway.log
#   ./start-gateway.sh stop      # kill by saved PID

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

PID_FILE="logs/gateway.pid"

stop() {
  if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" && echo "stopped $(cat "$PID_FILE")"
  fi
  rm -f "$PID_FILE"
}

if [[ "${1:-}" == "stop" ]]; then
  stop
  exit 0
fi

# Refuse to start when the saved gateway PID is still live — port 4000 is
# already taken and a second launch would overwrite the pid file, leaving
# `stop` unable to target the original process.
if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "gateway already running (pid=$(cat "$PID_FILE"))"
  exit 0
fi
rm -f "$PID_FILE"

# Refuse to start without the keystore — LiteLLM's schema migration on first boot
# produces a confusing error otherwise. Same guard as Posts 4 and 5.
if ! docker exec portway-keystore pg_isready -U postgres -d portway >/dev/null 2>&1; then
  echo "ERROR: portway-keystore not ready. Run ./start-keystore.sh first." >&2
  exit 1
fi

# Generate the Prisma client on first boot (carried over from Post 4 — see that
# walkthrough's "things that bit" #9 for the why).
if ! uv run --project . python -c "import prisma.client" >/dev/null 2>&1; then
  echo "generating prisma client (one-time)..."
  SCHEMA=$(uv run --project . python -c "import os, litellm_proxy_extras as m; print(os.path.join(os.path.dirname(m.__file__), 'schema.prisma'))")
  uv run --project . prisma generate --schema="$SCHEMA" >/dev/null 2>&1
fi

# Same as Post 5: put this directory on PYTHONPATH so LiteLLM can import
# `portway_callback` (a module-level file, not a package).
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

uv run --project . litellm \
  --config config.yaml \
  --port 4000 \
  --host 0.0.0.0 \
  >logs/gateway.log 2>&1 &
echo $! >"$PID_FILE"
echo "gateway   pid=$(cat $PID_FILE) port=4000 log=logs/gateway.log"
echo
echo "Tail with: tail -f 6-threads/logs/gateway.log"
echo "Stop with: ./start-gateway.sh stop"
