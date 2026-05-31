#!/usr/bin/env bash
# Post 4 — local Postgres container that backs LiteLLM's virtual-key store.
#
# Usage:
#   ./start-keystore.sh           # start (idempotent; reuses container if present)
#   ./start-keystore.sh stop      # stop and remove the container
#   ./start-keystore.sh status    # show running state

set -euo pipefail

CONTAINER="portway-keystore"
PORT=5432
PASSWORD=portway
DB=portway

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
      docker run -d --name "${CONTAINER}" \
        -p "${PORT}:5432" \
        -e POSTGRES_PASSWORD="${PASSWORD}" \
        -e POSTGRES_DB="${DB}" \
        postgres:16 >/dev/null
      echo "${CONTAINER} started on :${PORT}"
    fi
    # wait for readiness (pg_isready inside container)
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
    echo "${CONTAINER} stopped and removed"
    ;;
  status)
    docker ps --filter "name=${CONTAINER}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  *)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
