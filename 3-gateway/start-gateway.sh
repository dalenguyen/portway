#!/usr/bin/env bash
# Post 3 — launch the LiteLLM proxy in front of Post 2's two backends.
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

# LiteLLM proxy on :4000. `uv run --project .` pins the env (script is in 3-gateway/).
uv run --project . litellm \
  --config config.yaml \
  --port 4000 \
  --host 127.0.0.1 \
  >logs/gateway.log 2>&1 &
echo $! >"$PID_FILE"
echo "gateway   pid=$(cat $PID_FILE) port=4000 log=logs/gateway.log"
echo
echo "Tail with: tail -f 3-gateway/logs/gateway.log"
echo "Stop with: ./start-gateway.sh stop"
