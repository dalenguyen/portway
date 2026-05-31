#!/usr/bin/env bash
# Post 4 — launch the LiteLLM proxy with virtual-key support.
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

# Refuse to start without the keystore — LiteLLM's schema migration on first boot
# produces a confusing error otherwise.
if ! docker exec portway-keystore pg_isready -U postgres -d portway >/dev/null 2>&1; then
  echo "ERROR: portway-keystore not ready. Run ./start-keystore.sh first." >&2
  exit 1
fi

# LiteLLM's DB layer uses Prisma, but `litellm[proxy]` does not pull it. We declared
# `prisma` in pyproject.toml; this block generates its Python client once against
# LiteLLM's bundled schema. Idempotent — re-run is a no-op once `prisma.client` exists.
if ! uv run --project . python -c "import prisma.client" >/dev/null 2>&1; then
  echo "generating prisma client (one-time)..."
  SCHEMA=$(uv run --project . python -c "import os, litellm_proxy_extras as m; print(os.path.join(os.path.dirname(m.__file__), 'schema.prisma'))")
  uv run --project . prisma generate --schema="$SCHEMA" >/dev/null 2>&1
fi

uv run --project . litellm \
  --config config.yaml \
  --port 4000 \
  --host 127.0.0.1 \
  >logs/gateway.log 2>&1 &
echo $! >"$PID_FILE"
echo "gateway   pid=$(cat $PID_FILE) port=4000 log=logs/gateway.log"
echo
echo "Tail with: tail -f 4-auth/logs/gateway.log"
echo "Stop with: ./start-gateway.sh stop"
