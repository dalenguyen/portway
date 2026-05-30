#!/usr/bin/env bash
# Post 2 — launch two llama-server processes, one per model, on two ports.
#
# Usage:
#   ./start-backends.sh           # start both, background, log to ./logs/
#   ./start-backends.sh stop      # kill both by saved PIDs
#
# First run pulls ~19 GB of GGUFs from Hugging Face (cached after).

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs

PID_FILE_GPT="logs/gpt-oss.pid"
PID_FILE_QWEN="logs/qwen3.5.pid"

stop() {
  for f in "$PID_FILE_GPT" "$PID_FILE_QWEN"; do
    if [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null; then
      kill "$(cat "$f")" && echo "stopped $(cat "$f")"
    fi
    rm -f "$f"
  done
}

if [[ "${1:-}" == "stop" ]]; then
  stop
  exit 0
fi

# Backend 1 — gpt-oss-20b (MXFP4 native) on :8010
# -hf gives the repo; -hff names the specific GGUF (MXFP4 isn't a standard quant tag).
llama-server \
  -hf ggml-org/gpt-oss-20b-GGUF \
  -hff gpt-oss-20b-mxfp4.gguf \
  --alias gpt-oss \
  --port 8010 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  >logs/gpt-oss.log 2>&1 &
echo $! >"$PID_FILE_GPT"
echo "gpt-oss   pid=$(cat $PID_FILE_GPT) port=8010 log=logs/gpt-oss.log"

# Backend 2 — Qwen3.5-9B Q4_K_M on :8011
# Q4_K_M is the default quant for -hf, so the suffix is optional here; keeping it explicit for the post.
llama-server \
  -hf unsloth/Qwen3.5-9B-GGUF:Q4_K_M \
  --alias qwen3.5 \
  --port 8011 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  >logs/qwen3.5.log 2>&1 &
echo $! >"$PID_FILE_QWEN"
echo "qwen3.5   pid=$(cat $PID_FILE_QWEN) port=8011 log=logs/qwen3.5.log"

echo
echo "Tail with: tail -f logs/gpt-oss.log logs/qwen3.5.log"
echo "Stop with: ./start-backends.sh stop"
