# Post 1 — Local-first: a model on your own machine, zero cloud

> Goal: stand up a single model behind an OpenAI-compatible endpoint **on hardware you already own**, call it from the official OpenAI SDK, and internalize the stateless contract.

This walkthrough is the concrete, runnable counterpart to Post 1 in [`series.md`](./series.md). Everything here runs locally for $0.

## What's in this post

- `1-local-first/demo.py` — two blocks:
  1. **Round-trip:** one chat call via the OpenAI SDK, prints the content and the `usage` object.
  2. **Stateless proof:** sends the same final question as a 1-turn message and as the last turn of a 5-turn fabricated history; prints both `prompt_tokens` values and explains the delta.

## Engine choice on this machine

Apple Silicon Mac, 48 GB unified memory, **Ollama** already installed. We use Ollama's OpenAI-compatible endpoint at `http://localhost:11434/v1` and the `gpt-oss:20b` model (~14 GB).

> The wider series uses `llama.cpp` on Mac (Ollama is called out as problematic for Qwen3.5 in Post 2). For Post 1 — one model, prove the contract — Ollama is fine and already on the box.

## Prerequisites

- [Ollama](https://ollama.com) running locally (`curl -s http://localhost:11434/api/tags` should return JSON)
- [uv](https://docs.astral.sh/uv/) installed (`uv --version`)
- The `gpt-oss:20b` model pulled:
  ```bash
  ollama pull gpt-oss:20b
  ```

## Run it

From the repo root:

```bash
uv sync                                  # creates .venv at root, installs deps
uv run --project 1-local-first python 1-local-first/demo.py
```

## Sample output

A real run on this machine (M4-class Mac, 48 GB, `gpt-oss:20b` via Ollama):

```
============================================================
Block 1 — round-trip via OpenAI SDK against localhost
============================================================
content: Toronto, Vancouver, Montreal.
usage:   CompletionUsage(completion_tokens=43, prompt_tokens=72, total_tokens=115, completion_tokens_details=None, prompt_tokens_details=None)

============================================================
Block 2 — same final question, 1-turn vs 5-turn history
============================================================
1-turn response: The capital of Canada is **Ottawa**.

> **Safety note**: If you're driving, keep your focus on the road. Take breaks when needed and stay hydrated. Safe travels!
5-turn response: The capital of Canada is **Ottawa**, located in the province of Ontario.

1-turn prompt_tokens: 75
5-turn prompt_tokens: 139
delta:                64

Why the delta exists: the server holds NO conversation state between
requests. The 5-turn call's prompt_tokens is higher only because the
client re-sent the full history in the request body. Each call is
evaluated from scratch — history is the client's responsibility.
```

`completion_tokens` and the response text will vary run-to-run (sampling is non-deterministic at default temperature). `prompt_tokens` for the same input is deterministic — 75 and 139 should reproduce. Notice how the 5-turn response picks up the road-trip context ("located in the province of Ontario") while the 1-turn answer riffs on the bare "Driving." in its prompt — same model, different framing in the client-supplied messages.

## Definition of Done

- [x] OpenAI SDK round-trips against `localhost` — Block 1 prints a real `content` and a `usage` object.
- [x] Can explain why 5 turns vs 1 turn changes `prompt_tokens` while the server remembers nothing — Block 2 prints both numbers and the one-paragraph explanation. The server's only "memory" between requests is the **prefix cache** (a compute optimization), never conversation state.

## Things that bit, worth noting now

- **Context size eats RAM/VRAM.** Ollama's default context for `gpt-oss:20b` is conservative; raising it (`/set parameter num_ctx 32768`) costs unified memory. We didn't change it for Post 1.
- **gpt-oss emits a reasoning channel** (Harmony format). The engine applies the template; you still get a normal `message.content`. We'll segregate the reasoning channel at the gateway in Post 3.
- **No streaming yet.** Post 5 covers the streaming `usage` trap (you must opt in via `stream_options.include_usage`).
