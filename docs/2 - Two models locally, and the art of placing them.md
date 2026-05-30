# Post 2 — Two models locally, and the art of placing them

> Goal: run two models simultaneously on one machine, talk to each via the OpenAI SDK on its own port, and survive a handful of concurrent requests without OOM. Internalize the placement vocabulary — co-located vs. one-device-each vs. base+LoRA — because everything in Posts 3+ is "the same pattern, multiplied."

This walkthrough is the concrete, runnable counterpart to Post 2 in [`series.md`](./series.md). Everything here runs locally for $0.

← Previous: [Post 1 — Local-first: a model on your own machine, zero cloud](./1%20-%20Local-first:%20a%20model%20on%20your%20own%20machine,%20zero%20cloud.md)

## What's in this post

- `2-two-models/start-backends.sh` — launches two `llama-server` processes (one per model) on two ports.
- `2-two-models/demo.py` — three blocks:
  1. **Inventory:** GET `/v1/models` on both ports, print served names.
  2. **Same prompt, two voices:** send identical `messages` to each backend, compare `content` + `usage`.
  3. **Concurrent placement test:** six parallel chat completions split 3+3 across the backends, prove the machine doesn't OOM.

## Engine choice on this machine

Post 1 used **Ollama** for a single-model round-trip because that was the friction-free path. Post 2 switches to **llama.cpp** for two reasons:

1. **Placement vocabulary.** Ollama's mental model is one server, many models swapped in and out (controlled by `OLLAMA_MAX_LOADED_MODELS`). That's a perfectly fine product, but it obscures the decision the series is teaching: *where does each model live, and what does it cost?* `llama-server` is the canonical one-process-per-model shape — same shape vLLM uses — so the lesson transfers cleanly to Post 3's router and beyond.
2. **Series prescription.** `series.md` explicitly says "Stay on vLLM/llama.cpp, not Ollama, for Qwen3.5." Post 2 introduces Qwen3.5, so we adopt the prescribed tool here.

The OpenAI contract is unchanged: clients still hit `/v1/chat/completions` with a `model` field; only the engine behind the port differs.

## Placement strategies (verbatim from `series.md`)

| Strategy                                            | When                                       | Trade                                                                       |
| --------------------------------------------------- | ------------------------------------------ | --------------------------------------------------------------------------- |
| **Co-located** (two processes, one device)          | Both fit in VRAM/RAM                       | Cheapest; processes share the memory budget — size carefully                |
| **One device each**                                 | Need isolation / independent scaling       | Cleaner; needs a second device                                              |
| **Base + LoRA adapters**                            | Your "two models" share a base             | Near-free second model (not our case — different bases)                     |

**Apple Silicon footnote.** On a unified-memory Mac you co-locate by default — there is exactly one memory pool, shared between CPU and GPU. "One device each" needs a second physical machine, not a second card. The trade-off the series teaches still applies; the lever is `--ctx-size` (KV cache footprint), not `--gpu-memory-utilization` (a vLLM/CUDA concept that doesn't exist in llama.cpp).

## Hardware budget on this machine

- **Box:** Apple Silicon, 48 GB unified memory.
- **gpt-oss-20b** (MXFP4 native): ~13 GB on disk, ~14 GB working.
- **Qwen3.5-9B Q4_K_M:** ~5.5 GB on disk, ~6.5 GB working.
- **Two KV caches @ ctx-size 8192:** ~1–2 GB combined.
- **Headroom for OS, browser, editor:** ~25 GB.

If you're on a smaller Mac, drop both quants: `Qwen3.5-4B Q4_K_M` (~2.5 GB) plus a smaller gpt-oss variant or skip gpt-oss for this post and pair two Qwen sizes. The placement *pattern* doesn't change — only the numbers.

## Prerequisites

- [llama.cpp](https://github.com/ggml-org/llama.cpp) installed and on PATH (`llama-server --version` works).
  ```bash
  brew install llama.cpp     # macOS; ships with Metal acceleration
  ```
- [uv](https://docs.astral.sh/uv/) installed (`uv --version`).
- ~20 GB of free disk for the first-run GGUF downloads (cached under `~/Library/Caches/llama.cpp/` afterward).
- Network access for the first run; subsequent runs are offline.

## Run it

From the repo root:

```bash
# 1. Launch both backends (first run downloads ~19 GB; expect 5–15 min).
2-two-models/start-backends.sh
# Tail with:  tail -f 2-two-models/logs/gpt-oss.log 2-two-models/logs/qwen3.5.log
# Stop with:  2-two-models/start-backends.sh stop

# 2. Once both logs say "server is listening", run the demo:
uv sync
uv run --project 2-two-models python 2-two-models/demo.py
```

## Sample output

_(Captured on this machine — M4 Pro Mac, 48 GB, llama.cpp build 9430 / Metal.)_

```
============================================================
Block 1 — /v1/models on both ports
============================================================
http://localhost:8010/v1/models -> ['gpt-oss']
http://localhost:8011/v1/models -> ['qwen3.5']

============================================================
Block 2 — same prompt, two voices
============================================================
--- gpt-oss ---
content: Ottawa was selected as Canada's capital because its central location
between Ontario and Quebec, its established role as a political hub, and its
symbolic neutrality made it the ideal seat of the federal government.
usage:   CompletionUsage(completion_tokens=312, prompt_tokens=77, total_tokens=389, ...)
--- qwen3.5 ---
content: Ottawa was selected as Canada's capital in 1857 by Queen Victoria as a
political compromise between the English-speaking region of Ontario and the
French-speaking region of Quebec.
usage:   CompletionUsage(completion_tokens=3813, prompt_tokens=21, total_tokens=3834, ...)

============================================================
Block 3 — 6 concurrent calls, 3 per backend
============================================================
backend     latency_s   total_tokens
gpt-oss          5.76            145
qwen3.5         31.44            378
gpt-oss          4.39            121
qwen3.5         24.58            241
gpt-oss         23.44            692
qwen3.5         41.03            702

wall time for all 6 concurrent calls: 41.03s
No OOM = the two co-located processes share unified memory cleanly.
```

**Two things worth staring at in Block 2:**

- **`prompt_tokens` differ by 3.7× for the exact same input string** (gpt-oss: 77, qwen3.5: 21). Different tokenizers, different vocab sizes (`n_vocab` is 201088 for gpt-oss vs 248320 for Qwen3.5), different `prompt_tokens`. There is no global "tokens" unit you can bill on without naming the model. Post 5 will make this explicit; Post 2 just makes it visible.
- **`completion_tokens` is much higher than the visible answer.** gpt-oss returned a ~60-token sentence but the response is billed at 312 tokens — the rest is the **reasoning channel** (Harmony format). Qwen3.5 has its own thinking mode (`thinking = 1` shows up in the server log) and silently spent 3813 tokens to produce a similarly short answer. The engine hides the thinking from `content` but the meter sees every token. Post 3 will segregate the reasoning channel at the gateway.

**Block 3 latency note.** The wildly different per-request latencies aren't a Qwen-vs-gpt-oss capability story — they're explained by the thinking-token issue above. Without a `max_tokens` cap, a "thinking" model can spend thousands of tokens before emitting visible content. Post 7 owns proper benchmarking with constrained budgets; Block 3's bar is the one the DoD asks for: nothing OOM'd.

## Definition of Done

- [x] Both `:8010/v1/models` and `:8011/v1/models` report their served names (`gpt-oss` and `qwen3.5`) — Block 1.
- [x] Both backends answer chat completions — Block 2.
- [x] The machine doesn't OOM under six concurrent requests split across the two backends — Block 3.

## Things that bit, worth noting now

- **`-hf repo:Q4_K_M` is a quant tag, not a filename.** llama.cpp's `-hf` flag takes `<user>/<model>[:quant]` — the colon part picks among preset quantizations. For files that don't map to a standard quant (e.g. `gpt-oss-20b-mxfp4.gguf`), use `-hff <filename>` alongside `-hf <repo>`. The error message when you get this wrong is misleading: it says "no GGUF files found" and then lists the files. Read carefully.
- **`--gpu-memory-utilization` doesn't exist in llama.cpp.** That's a vLLM/CUDA flag. On Apple Silicon, macOS pages memory between processes and the system on demand; over-commit produces swap (slowness), not OOM kills. The relevant lever for co-location is **`--ctx-size`**, which caps the per-process KV cache.
- **`--alias` ≠ filename ≠ HF repo — three layers of model identity.** The alias is what clients send in the `model` field; the GGUF filename is the artifact; the HF repo is where it came from. Post 3's router will key off aliases only.
- **Tokenizers disagree by a lot.** Block 2 sends an identical prompt string to both backends; `prompt_tokens` differs by ~3.7× (77 vs 21) because gpt-oss and Qwen3.5 use different tokenizers and different vocab sizes. This is why Post 5's metering must be per-model — cost-per-token is meaningless without naming the tokenizer.
- **Thinking tokens cost real money.** Both gpt-oss (Harmony reasoning channel) and Qwen3.5 (thinking mode, surfaced as `thinking = 1` in the llama-server log) generate large amounts of internal-reasoning output that's stripped from `content` but counted in `completion_tokens`. A "two-sentence answer" can bill at 3000+ tokens. Post 3 will segregate the reasoning channel at the gateway; Post 5 will decide whether to bill on visible content, raw completion, or split rates.
- **Pick any two free ports.** This post uses 8010/8011 — placement isn't about specific numbers, and the conventional 8000/8001 collide with whatever else you might already have running locally (MCP servers, dev proxies, the last container you forgot to stop). `lsof -i :PORT` before you commit to a port pair.
- **First run is slow.** llama.cpp pulls each GGUF on first use. Tail the logs to watch progress; cached after.
- **Concurrent ≠ parallel on one Metal device.** Six concurrent requests share the GPU; per-request latency rises vs. single-request. Expected. Post 7 owns proper throughput measurement.
- **No streaming yet.** Same as Post 1 — Post 5 owns the `stream_options.include_usage` trap.
