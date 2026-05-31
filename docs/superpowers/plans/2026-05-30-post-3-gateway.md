# Post 3 — The gateway: route by model name — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a LiteLLM gateway in front of Post 2's two `llama-server` backends so clients hit a single OpenAI-compatible endpoint and pick the model via the `model` field, with reasoning output segregated into `reasoning_content`. Land it as a runnable `3-gateway/` artifact plus a `docs/3 - ...md` walkthrough.

**Architecture:** A third local process (`litellm` proxy on port 4000) sits in front of the two llama-server processes from Post 2 (8010, 8011). LiteLLM's config-file routing maps the public alias (`gpt-oss`, `qwen3.5`) to the right backend; LiteLLM's default normalization places each model's reasoning tokens in `choices[].message.reasoning_content`.

**Tech Stack:** LiteLLM proxy, `uv` for Python dep management, llama.cpp `llama-server` (from Post 2), `openai` Python SDK for the client side, bash for the start/stop wrapper.

**Reference spec:** `docs/superpowers/specs/2026-05-30-post-3-gateway-design.md`

**Prerequisite for any task that hits the network:** Post 2's `2-two-models/start-backends.sh` is running and both backends respond on `:8010` and `:8011`. First-run GGUF downloads can take 5–15 minutes — Tasks 4–8 assume those caches already exist on the dev machine.

---

### Task 1: Back-edit `start-backends.sh` to populate `reasoning_content` for Qwen3.5

**Files:**
- Modify: `2-two-models/start-backends.sh:33-40` (gpt-oss invocation) and `2-two-models/start-backends.sh:46-52` (qwen3.5 invocation)

**Why:** llama.cpp only fills `choices[].message.reasoning_content` when the server is launched with `--jinja --reasoning-format auto`. Without this, Post 3's Block 2 will read `None` for the reasoning side. The change is invisible to Post 2's existing `demo.py` (which doesn't read `reasoning_content`).

- [ ] **Step 1: Read the file**

Run: open `2-two-models/start-backends.sh` and locate both `llama-server` invocations.

- [ ] **Step 2: Add the flags to the gpt-oss invocation**

Change the block at lines 33–40 from:

```bash
llama-server \
  -hf ggml-org/gpt-oss-20b-GGUF \
  -hff gpt-oss-20b-mxfp4.gguf \
  --alias gpt-oss \
  --port 8010 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  >logs/gpt-oss.log 2>&1 &
```

to:

```bash
llama-server \
  -hf ggml-org/gpt-oss-20b-GGUF \
  -hff gpt-oss-20b-mxfp4.gguf \
  --alias gpt-oss \
  --port 8010 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  --jinja \
  --reasoning-format auto \
  >logs/gpt-oss.log 2>&1 &
```

- [ ] **Step 3: Add the flags to the qwen3.5 invocation**

Change the block at lines 46–52 from:

```bash
llama-server \
  -hf unsloth/Qwen3.5-9B-GGUF:Q4_K_M \
  --alias qwen3.5 \
  --port 8011 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  >logs/qwen3.5.log 2>&1 &
```

to:

```bash
llama-server \
  -hf unsloth/Qwen3.5-9B-GGUF:Q4_K_M \
  --alias qwen3.5 \
  --port 8011 \
  --host 127.0.0.1 \
  --ctx-size 8192 \
  --jinja \
  --reasoning-format auto \
  >logs/qwen3.5.log 2>&1 &
```

- [ ] **Step 4: Re-run shellcheck-style sanity**

Run: `bash -n 2-two-models/start-backends.sh`
Expected: no output (syntax OK).

- [ ] **Step 5: Restart backends so the flags take effect**

Run:
```bash
2-two-models/start-backends.sh stop
2-two-models/start-backends.sh
```
Expected: two PID lines printed, no errors. Tail `2-two-models/logs/gpt-oss.log` and `2-two-models/logs/qwen3.5.log` until both report `server is listening`.

- [ ] **Step 6: Verify reasoning_content is populated via a direct backend call**

Run:
```bash
curl -s http://localhost:8011/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer llama' \
  -d '{"model":"qwen3.5","messages":[{"role":"user","content":"What is 2+2? Answer in one word."}]}' \
  | python -c 'import json,sys; r=json.load(sys.stdin); m=r["choices"][0]["message"]; print("content=", repr(m.get("content"))); print("reasoning_content=", repr(m.get("reasoning_content"))[:200])'
```
Expected: `reasoning_content=` prints a non-empty string (Qwen3.5's thinking trace). `content=` prints the visible answer.

- [ ] **Step 7: Commit**

```bash
git add 2-two-models/start-backends.sh
git commit -m "Post 2: enable --jinja --reasoning-format on both backends

So Post 3's gateway demo can read choices[].message.reasoning_content
from both gpt-oss and Qwen3.5. Post 2's demo.py and captured sample
output are unaffected — the field is simply available now."
```

---

### Task 2: Scaffold the `3-gateway/` uv project

**Files:**
- Create: `3-gateway/pyproject.toml`
- Modify: `pyproject.toml` (root workspace) — add `3-gateway` to `members`
- Modify: `.gitignore` — add `3-gateway/logs/`
- Create: `3-gateway/logs/.gitkeep` (so the directory exists in git without checking in real logs)

- [ ] **Step 1: Create `3-gateway/pyproject.toml`**

Write:

```toml
[project]
name = "3-gateway"
version = "0.1.0"
description = "Post 3 — The gateway: route by model name."
requires-python = ">=3.11"
dependencies = [
  "openai>=1.0",
  "litellm[proxy]>=1.50",
]
```

- [ ] **Step 2: Add `3-gateway` to the root workspace**

Modify root `pyproject.toml`. Change:

```toml
[tool.uv.workspace]
members = ["1-local-first", "2-two-models"]
```

to:

```toml
[tool.uv.workspace]
members = ["1-local-first", "2-two-models", "3-gateway"]
```

- [ ] **Step 3: Add the logs directory to `.gitignore`**

Modify `.gitignore`. Change:

```
.venv/
__pycache__/
*.pyc
.env
2-two-models/logs/
```

to:

```
.venv/
__pycache__/
*.pyc
.env
2-two-models/logs/
3-gateway/logs/
```

- [ ] **Step 4: Create the `logs/` directory with a `.gitkeep`**

Run: `mkdir -p 3-gateway/logs && touch 3-gateway/logs/.gitkeep`

- [ ] **Step 5: Resolve dependencies**

Run: `uv sync`
Expected: lockfile updates, no errors. `litellm` and its proxy extras land in `.venv`.

- [ ] **Step 6: Verify litellm is callable**

Run: `uv run --project 3-gateway litellm --version`
Expected: prints a version number (e.g. `1.5x.x`), exit 0.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore 3-gateway/pyproject.toml 3-gateway/logs/.gitkeep uv.lock
git commit -m "Post 3: scaffold 3-gateway uv project with litellm[proxy]"
```

---

### Task 3: Write the LiteLLM config

**Files:**
- Create: `3-gateway/config.yaml`

- [ ] **Step 1: Write the config**

Create `3-gateway/config.yaml` with:

```yaml
# Post 3 — LiteLLM proxy routing two local llama-server backends.
# Clients hit http://localhost:4000/v1 with model="gpt-oss" or "qwen3.5".

model_list:
  - model_name: gpt-oss
    litellm_params:
      model: openai/gpt-oss
      api_base: http://127.0.0.1:8010/v1
      api_key: dummy            # llama-server doesn't check; placeholder for the SDK
  - model_name: qwen3.5
    litellm_params:
      model: openai/qwen3.5
      api_base: http://127.0.0.1:8011/v1
      api_key: dummy

general_settings:
  master_key: sk-portway-local  # single client-facing key for Post 3; Post 4 replaces this

litellm_settings:
  drop_params: true             # silently drop unsupported params instead of erroring
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c 'import yaml; yaml.safe_load(open("3-gateway/config.yaml"))'`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add 3-gateway/config.yaml
git commit -m "Post 3: LiteLLM config routing gpt-oss and qwen3.5"
```

---

### Task 4: Write `start-gateway.sh` and verify the proxy boots

**Files:**
- Create: `3-gateway/start-gateway.sh`

**Why this pattern:** mirrors `2-two-models/start-backends.sh` so the series feels uniform — `start | stop | logs` UX, PID file, background process.

- [ ] **Step 1: Write the script**

Create `3-gateway/start-gateway.sh` with:

```bash
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

# LiteLLM proxy on :4000. `uv run --project 3-gateway` pins the env.
uv run --project 3-gateway litellm \
  --config 3-gateway/config.yaml \
  --port 4000 \
  --host 127.0.0.1 \
  >logs/gateway.log 2>&1 &
echo $! >"$PID_FILE"
echo "gateway   pid=$(cat $PID_FILE) port=4000 log=logs/gateway.log"
echo
echo "Tail with: tail -f 3-gateway/logs/gateway.log"
echo "Stop with: ./start-gateway.sh stop"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x 3-gateway/start-gateway.sh`

- [ ] **Step 3: Lint the script**

Run: `bash -n 3-gateway/start-gateway.sh`
Expected: no output (syntax OK).

- [ ] **Step 4: Confirm Post 2 backends are running**

Run:
```bash
curl -s http://localhost:8010/v1/models -H 'Authorization: Bearer llama' | python -m json.tool
curl -s http://localhost:8011/v1/models -H 'Authorization: Bearer llama' | python -m json.tool
```
Expected: each returns a JSON object with `data: [{ id: "gpt-oss", ... }]` and `data: [{ id: "qwen3.5", ... }]` respectively. If either fails, run `2-two-models/start-backends.sh` and wait for `server is listening` in the logs.

- [ ] **Step 5: Start the gateway**

Run: `3-gateway/start-gateway.sh`
Expected: a `gateway pid=NNN port=4000 ...` line, exit 0.

- [ ] **Step 6: Wait for the proxy to bind, then smoke-test `/v1/models`**

Run:
```bash
until curl -sf http://localhost:4000/v1/models -H 'Authorization: Bearer sk-portway-local' >/dev/null; do sleep 1; done
curl -s http://localhost:4000/v1/models -H 'Authorization: Bearer sk-portway-local' | python -m json.tool
```
Expected: a JSON object whose `data` array contains entries with `id` values `gpt-oss` and `qwen3.5`. If the `until` loop hangs more than 30s, inspect `3-gateway/logs/gateway.log` — typical first-run failures are port collision on `:4000` or a missing litellm dep.

- [ ] **Step 7: Smoke-test routing with one chat completion**

Run:
```bash
curl -s http://localhost:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer sk-portway-local' \
  -d '{"model":"qwen3.5","messages":[{"role":"user","content":"Reply with the single word: ok"}]}' \
  | python -c 'import json,sys; r=json.load(sys.stdin); print(r["choices"][0]["message"])'
```
Expected: a dict with `content` (visible answer) and `reasoning_content` (Qwen's thinking trace, non-empty if Task 1 is wired up correctly).

- [ ] **Step 8: Commit**

```bash
git add 3-gateway/start-gateway.sh
git commit -m "Post 3: start-gateway.sh wrapper for the LiteLLM proxy"
```

Leave the gateway running — the next tasks talk to it. (Stop with `3-gateway/start-gateway.sh stop` between sessions.)

---

### Task 5: Implement `demo.py` Block 1 — `/v1/models` on the gateway

**Files:**
- Create: `3-gateway/demo.py`

- [ ] **Step 1: Write the initial scaffold with Block 1**

Create `3-gateway/demo.py` with:

```python
"""Post 3 — The gateway: route by model name.

Run:
    2-two-models/start-backends.sh                # in another shell, wait for "server is listening"
    3-gateway/start-gateway.sh                    # in another shell, wait for /v1/models to respond
    uv run --project 3-gateway python 3-gateway/demo.py
"""

from openai import OpenAI

GATEWAY_URL = "http://localhost:4000/v1"
MASTER_KEY = "sk-portway-local"

client = OpenAI(base_url=GATEWAY_URL, api_key=MASTER_KEY)


def gateway_inventory() -> None:
    print("=" * 60)
    print("Block 1 — /v1/models on the gateway")
    print("=" * 60)
    ids = [m.id for m in client.models.list().data]
    print(f"{GATEWAY_URL}/models -> {sorted(ids)}")


if __name__ == "__main__":
    gateway_inventory()
```

- [ ] **Step 2: Run it**

Run: `uv run --project 3-gateway python 3-gateway/demo.py`
Expected output (exact):

```
============================================================
Block 1 — /v1/models on the gateway
============================================================
http://localhost:4000/v1/models -> ['gpt-oss', 'qwen3.5']
```

If the list is empty or `[]`, the gateway didn't pick up `config.yaml` — restart it (`3-gateway/start-gateway.sh stop && 3-gateway/start-gateway.sh`) and check `3-gateway/logs/gateway.log` for load errors.

- [ ] **Step 3: Commit**

```bash
git add 3-gateway/demo.py
git commit -m "Post 3: demo.py Block 1 — /v1/models lists both routes"
```

---

### Task 6: Implement `demo.py` Block 2 — same prompt, two voices, one base URL

**Files:**
- Modify: `3-gateway/demo.py` (append `same_prompt_two_voices` function, call from `__main__`)

- [ ] **Step 1: Add the Block 2 function**

Append to `3-gateway/demo.py` (above the `if __name__` block):

```python
def same_prompt_two_voices() -> None:
    print()
    print("=" * 60)
    print("Block 2 — same prompt, two voices, one base URL")
    print("=" * 60)
    prompt = "In one sentence, what makes Ottawa Canada's capital?"
    for model in ["gpt-oss", "qwen3.5"]:
        r = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}]
        )
        msg = r.choices[0].message
        reasoning = (msg.reasoning_content or "").strip()
        print(f"--- {model} ---")
        print("content:          ", (msg.content or "").strip())
        print("reasoning (≤200): ", reasoning[:200] + ("…" if len(reasoning) > 200 else ""))
        print("usage:            ", r.usage)
```

- [ ] **Step 2: Wire it into `__main__`**

Replace the existing `if __name__` block with:

```python
if __name__ == "__main__":
    gateway_inventory()
    same_prompt_two_voices()
```

- [ ] **Step 3: Run it**

Run: `uv run --project 3-gateway python 3-gateway/demo.py`
Expected pattern (exact strings will vary):

```
============================================================
Block 1 — /v1/models on the gateway
============================================================
http://localhost:4000/v1/models -> ['gpt-oss', 'qwen3.5']

============================================================
Block 2 — same prompt, two voices, one base URL
============================================================
--- gpt-oss ---
content:           Ottawa was chosen ...
reasoning (≤200):  We need to ...
usage:             CompletionUsage(completion_tokens=..., prompt_tokens=..., total_tokens=..., ...)
--- qwen3.5 ---
content:           Ottawa was selected ...
reasoning (≤200):  The user is asking ...
usage:             CompletionUsage(completion_tokens=..., prompt_tokens=..., total_tokens=..., ...)
```

Acceptance: both `content:` and `reasoning (≤200):` are non-empty for both models. If `reasoning (≤200):` is empty for a model, that backend is not running with `--jinja --reasoning-format auto` — go back to Task 1 and restart backends.

- [ ] **Step 4: Commit**

```bash
git add 3-gateway/demo.py
git commit -m "Post 3: demo.py Block 2 — same prompt, two voices, reasoning_content broken out"
```

---

### Task 7: Implement `demo.py` Block 3 — bad model name returns a clean OpenAI-shaped error

**Files:**
- Modify: `3-gateway/demo.py` (append `bad_model_name`, call from `__main__`, add `openai` import for the exception class)

- [ ] **Step 1: Update the imports at the top**

Replace:

```python
from openai import OpenAI
```

with:

```python
import openai
from openai import OpenAI
```

- [ ] **Step 2: Add the Block 3 function**

Append to `3-gateway/demo.py` (above the `if __name__` block):

```python
def bad_model_name() -> None:
    print()
    print("=" * 60)
    print("Block 3 — unknown model name returns a clean OpenAI-shaped error")
    print("=" * 60)
    try:
        client.chat.completions.create(
            model="gpt-99", messages=[{"role": "user", "content": "hi"}]
        )
    except (openai.BadRequestError, openai.NotFoundError) as e:
        print(f"status:  {e.status_code}")
        print(f"body:    {e.body}")
        return
    raise SystemExit("Block 3 FAILED: gateway accepted a bogus model name")
```

- [ ] **Step 3: Wire it into `__main__`**

Replace the existing `if __name__` block with:

```python
if __name__ == "__main__":
    gateway_inventory()
    same_prompt_two_voices()
    bad_model_name()
```

- [ ] **Step 4: Run it**

Run: `uv run --project 3-gateway python 3-gateway/demo.py`
Expected ending block:

```text
============================================================
Block 3 — unknown model name returns a clean OpenAI-shaped error
============================================================
status:  400
body:    {'message': '/chat/completions: Invalid model name passed in model=gpt-99...', 'type': 'None', 'param': 'None', 'code': '400', ...}
```

Acceptance: `status` is `400` (or `404` on future LiteLLM versions — the catch tuple covers both), `body` is a dict in the OpenAI error shape. The OpenAI SDK's `.body` exposes the inner error dict (envelope stripped); the wire payload carries the `{error: {...}}` envelope. Exact `type`/`message` strings can vary by LiteLLM version.

- [ ] **Step 5: Commit**

```bash
git add 3-gateway/demo.py
git commit -m "Post 3: demo.py Block 3 — unknown model returns OpenAI-shaped error"
```

---

### Task 8: Capture full demo output for the walkthrough

**Files:**
- Create: `3-gateway/logs/demo-sample-output.txt` (gitignored — local capture only, used as raw material for the doc)

- [ ] **Step 1: Run the full demo and capture stdout**

Run:
```bash
uv run --project 3-gateway python 3-gateway/demo.py | tee 3-gateway/logs/demo-sample-output.txt
```
Expected: all three blocks print in sequence, exit 0.

- [ ] **Step 2: Sanity-check the capture**

Run: `grep -c '^=====' 3-gateway/logs/demo-sample-output.txt`
Expected: `9` (three header lines per block × three blocks).

- [ ] **Step 3: No commit**

The capture file is gitignored (`3-gateway/logs/` is excluded). It exists only to seed the next task's "Sample output" section.

---

### Task 9: Write the walkthrough `docs/3 - ...md`

**Files:**
- Create: `docs/3 - The gateway: route by model name.md`

**Style guide:** mirror the structure of `docs/2 - Two models locally, and the art of placing them.md`. Sections in order: lead blockquote with goal, "What's in this post", "How this differs from Post 2" (why LiteLLM, why a gateway now), "Prerequisites", "Run it", "Sample output" (paste from Task 8 capture, trimmed where reasoning traces are huge), "Definition of Done", "Things that bit, worth noting now".

- [ ] **Step 1: Draft the walkthrough**

Create `docs/3 - The gateway: route by model name.md` with the following structure. Where the template says `<paste …>`, paste the matching block from `3-gateway/logs/demo-sample-output.txt`, trimming any reasoning trace longer than ~200 chars with an ellipsis to keep the doc readable.

```markdown
# Post 3 — The gateway: route by model name

> Goal: one local endpoint; clients pick the model via the OpenAI `model` field; the gateway routes to the right Post-2 backend. Plus: surface each model's reasoning channel as its own first-class field (`reasoning_content`) instead of inlining it in `content`.

This walkthrough is the concrete, runnable counterpart to Post 3 in [`series.md`](./series.md). Everything here runs locally for $0.

← Previous: [Post 2 — Two models locally, and the art of placing them](./2%20-%20Two%20models%20locally,%20and%20the%20art%20of%20placing%20them.md)

## What's in this post

- `3-gateway/config.yaml` — LiteLLM proxy config: two routes (`gpt-oss`, `qwen3.5`), one master key, `drop_params` for forward-compatibility.
- `3-gateway/start-gateway.sh` — start/stop wrapper for the proxy on `:4000`.
- `3-gateway/demo.py` — three blocks:
  1. **Gateway inventory:** GET `/v1/models` on the gateway, see both routes.
  2. **Same prompt, two voices, one base URL:** flip `model` between `gpt-oss` and `qwen3.5` against the same client; reasoning lives in its own field.
  3. **Bad model name:** request `gpt-99`, get an OpenAI-shaped error instead of a bare 500.

## How this differs from Post 2

[Post 2](./2%20-%20Two%20models%20locally,%20and%20the%20art%20of%20placing%20them.md) gave every model its own port and made the client pick the URL. That works until the client list grows past two — every consumer suddenly has to know your backend topology. The gateway flips that: one base URL, the standard OpenAI `model` field is the routing key, and adding a third backend is a config edit instead of a client-side change.

**Why LiteLLM (instead of ~40 lines of FastAPI).** The series prescribes LiteLLM as the recommended option, and the reason it pays off here is what comes next: Post 4 will graft per-customer virtual keys and per-key model scoping onto this same proxy, Post 5 will wire metering callbacks into it. Building the DIY version now means rewriting it twice. LiteLLM also handles OpenAI's error-shape contract and reasoning-content normalization out of the box — both of which Post 3's DoD calls for.

## Prerequisites

- Post 2 is working: `2-two-models/start-backends.sh` runs cleanly and both `:8010/v1/models` and `:8011/v1/models` respond.
- `2-two-models/start-backends.sh` has been updated to pass `--jinja --reasoning-format auto` to both `llama-server` invocations (see "Things that bit"). Restart Post 2's backends after that change.
- [uv](https://docs.astral.sh/uv/) installed (`uv --version`).

## Run it

From the repo root:

```bash
# 1. Backends from Post 2 (if not already running).
2-two-models/start-backends.sh
# Wait for "server is listening" in both 2-two-models/logs/*.log.

# 2. Sync dependencies (first time only).
uv sync

# 3. Launch the gateway.
3-gateway/start-gateway.sh
# Tail with:  tail -f 3-gateway/logs/gateway.log
# Stop with:  3-gateway/start-gateway.sh stop

# 4. Once /v1/models on :4000 responds, run the demo.
uv run --project 3-gateway python 3-gateway/demo.py
```

## Sample output

_(Captured on this machine — M4 Pro Mac, 48 GB, llama.cpp build 9430 / Metal, LiteLLM 1.5x.)_

```
<paste Block 1 from 3-gateway/logs/demo-sample-output.txt>

<paste Block 2 from 3-gateway/logs/demo-sample-output.txt — trim each reasoning trace to ~200 chars with an ellipsis>

<paste Block 3 from 3-gateway/logs/demo-sample-output.txt>
```

**Worth staring at in Block 2:**

- **`content` is the same shape Post 2 produced**, but now arrives via one base URL. Adding a third route is a config edit on the gateway, not a client change.
- **`reasoning_content` is its own field.** gpt-oss's Harmony trace and Qwen3.5's `<think>` block both land in the same slot — Post 5's metering will need to count both separately.
- **`reasoning_effort` passes through unchanged.** Add `extra_body={"reasoning_effort": "low"}` to a gpt-oss call and the gateway forwards it — useful when you want the visible answer without the long reasoning trace. LiteLLM does this for free; no gateway code involved.

**Worth staring at in Block 3:** the wire body is the OpenAI shape (`{"error": {"message": ..., "type": ...}}`) and the status is 400 (LiteLLM 1.86.x's choice — newer versions may return 404). The demo catches both `BadRequestError` and `NotFoundError`; either way, stock OpenAI SDKs raise the matching exception automatically.

## Definition of Done

- [x] `/v1/models` on `:4000` lists both `gpt-oss` and `qwen3.5` — Block 1.
- [x] Flipping `model` between the two names on the same base URL hits different local backends — Block 2.
- [x] An unknown model name returns a clean OpenAI-shaped error — Block 3.
- [x] `reasoning_content` is populated for both models in Block 2.

## Things that bit, worth noting now

- **Port `4000` is convention, not law.** LiteLLM defaults to it but anything you've touched recently is a hazard (other proxies, dashboards, the last container you forgot to stop). `lsof -i :4000` before you commit to it, same discipline as Post 2's `:8010/:8011`.
- **`openai/` in `litellm_params.model` is the *provider prefix*, not your alias.** `model_name` is the public alias clients send; `litellm_params.model` tells LiteLLM how to talk to the backend. Both fields hold strings that look like model names — easy to wire backwards. The error when you do is confusing ("model not found" with the right name visible in logs).
- **Two layers of auth, two different keys.** `master_key` (in `general_settings`) is what clients send to the gateway. The per-route `api_key` (in `litellm_params`) is what the gateway sends to the backend. Conflating them is the most common first-day bug. Post 4 turns the client-side key into per-customer virtual keys.
- **Reasoning lives in `reasoning_content`, not `content`.** A client that logs only `content` will wonder where 3000 tokens went — same observation as Post 2, now with a place to look. Different llama-server flags control this: without `--jinja --reasoning-format auto`, the field stays `None`. That's why Post 3 includes a one-line back-edit to `2-two-models/start-backends.sh`.
- **`drop_params: true` silently swallows unknown params.** Useful default — it means newer OpenAI SDK fields don't error the proxy out — but it also means typos in request bodies vanish without a peep. Worth knowing now so a bug hunt in Post 5 doesn't take an hour.
- **`/v1/models` matters for framework clients.** LangChain, Continue.dev, Cursor, and similar tools call it on startup; a wrong or empty list produces confusing downstream errors with no obvious cause. Always verify the proxy's `/v1/models` returns what you expect *before* pointing real tooling at it.
```

- [ ] **Step 2: Spot-check the rendered doc**

Run: `wc -l "docs/3 - The gateway: route by model name.md"`
Expected: ~80–120 lines (similar to Post 2's ~135).

Run: `grep -c '<paste' "docs/3 - The gateway: route by model name.md"`
Expected: `0` (every `<paste …>` placeholder has been replaced with real captured output).

- [ ] **Step 3: Commit**

```bash
git add "docs/3 - The gateway: route by model name.md"
git commit -m "Post 3: walkthrough — LiteLLM gateway, two routes, one base URL"
```

---

### Task 10: Update README and final smoke

**Files:**
- Modify: `README.md:56` — flip the Post 3 checkbox and turn the title into a link.

- [ ] **Step 1: Update the series progress list**

In `README.md`, change line 56 from:

```
- [ ] **Post 3** — The gateway: route by model name *(no cloud · $0)*
```

to:

```
- [x] [**Post 3** — The gateway: route by model name](./docs/3%20-%20The%20gateway:%20route%20by%20model%20name.md) *(no cloud · $0)*
```

- [ ] **Step 2: Full end-to-end sanity (proxy + backends + demo)**

Run (from a fresh shell to flush any cached state):
```bash
2-two-models/start-backends.sh stop || true
3-gateway/start-gateway.sh stop || true
2-two-models/start-backends.sh
until curl -sf http://localhost:8010/v1/models -H 'Authorization: Bearer llama' >/dev/null \
   && curl -sf http://localhost:8011/v1/models -H 'Authorization: Bearer llama' >/dev/null; do sleep 2; done
3-gateway/start-gateway.sh
until curl -sf http://localhost:4000/v1/models -H 'Authorization: Bearer sk-portway-local' >/dev/null; do sleep 1; done
uv run --project 3-gateway python 3-gateway/demo.py
```
Expected: all three blocks print, demo exits 0, no tracebacks.

- [ ] **Step 3: Stop the local processes**

Run:
```bash
3-gateway/start-gateway.sh stop
2-two-models/start-backends.sh stop
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Post 3: mark complete in README progress checklist"
```

- [ ] **Step 5: Confirm the branch is clean**

Run: `git status`
Expected: `nothing to commit, working tree clean`.
