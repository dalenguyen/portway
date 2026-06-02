#!/usr/bin/env bash
# Post 6 — local Postgres container backing LiteLLM virtual keys, portway_metering,
# AND the new portway_threads / portway_messages tables added in this post.
#
# Same shape as Post 5 (the `-v $(pwd)/pgdata` mount makes rows survive `docker rm`
# + restart). Run `rm -rf pgdata/` to start clean.
#
# Usage:
#   ./start-keystore.sh           # start (idempotent; reuses container if present)
#   ./start-keystore.sh stop      # stop and remove the container (data persists in ./pgdata)
#   ./start-keystore.sh status    # show running state

set -euo pipefail
cd "$(dirname "$0")"

CONTAINER="portway-keystore"
PORT=5432
PASSWORD=portway
DB=portway
DATA_DIR="$(pwd)/pgdata"

cmd="${1:-start}"

case "$cmd" in
  start)
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}\$"; then
      echo "${CONTAINER} already running on :${PORT}"
      exit 0
    fi
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}\$"; then
      docker start "${CONTAINER}" >/dev/null
      echo "${CONTAINER} restarted on :${PORT}"
    else
      mkdir -p "${DATA_DIR}"
      docker run -d --name "${CONTAINER}" \
        -p "${PORT}:5432" \
        -e POSTGRES_PASSWORD="${PASSWORD}" \
        -e POSTGRES_DB="${DB}" \
        -v "${DATA_DIR}:/var/lib/postgresql/data" \
        postgres:16 >/dev/null
      echo "${CONTAINER} started on :${PORT} (data: ${DATA_DIR})"
    fi
    for i in {1..30}; do
      if docker exec "${CONTAINER}" pg_isready -U postgres -d "${DB}" >/dev/null 2>&1; then
        echo "ready"
        exit 0
      fi
      sleep 1
    done
    echo "ERROR: postgres did not become ready in 30s" >&2
    exit 1
    ;;
  stop)
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
    echo "${CONTAINER} stopped and removed (data preserved in ${DATA_DIR})"
    ;;
  status)
    docker ps --filter "name=${CONTAINER}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
