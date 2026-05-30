# Post 3 — The gateway: route by model name (design spec)

> Status: approved 2026-05-30. Implementation plan to follow.

## 1. Goal & scope

Stand up a single local endpoint that fronts the two Post-2 backends. Clients pick the model via the standard OpenAI `model` field; the gateway looks up the route and forwards. This is the "be OpenRouter" core — pure software, identical local or cloud.

In scope for Post 3:
- A LiteLLM proxy in front of the two `llama-server` processes from Post 2.
- A public `/v1/models` listing both routes.
- Clean OpenAI-shaped 404 on an unknown `model`.
- Segregation of the model's reasoning channel into `choices[].message.reasoning_content` (instead of inlining it in `content`).
- `reasoning_effort` passthrough for gpt-oss.

Out of scope (owned by later posts):
- Customer API keys, per-key model scoping, rate limits — Post 4.
- Streaming, `stream_options.include_usage`, metering DB — Post 5.
- Conversation state / thread store — Post 6.
- Load characterization, TTFT, throughput — Post 7.

## 2. Artifact layout

Follows the established `<n>-<name>/` pattern from Posts 1–2.

```
3-gateway/
  config.yaml          # LiteLLM model_list + master_key + drop_params
  start-gateway.sh     # start | stop | logs wrapper around `litellm --config ...`
  demo.py              # three blocks (see §4)
  pyproject.toml       # openai, litellm[proxy], httpx
  logs/                # gitignored; gateway stdout/stderr
docs/
  3 - The gateway: route by model name.md   # walkthrough mirroring posts 1–2
```

Prereq for the post: Post 2's `2-two-models/start-backends.sh` is already running on ports 8010 and 8011. Post 3 stands a third process up on port 4000 in front.

## 3. Routing config & reasoning channel

### 3.1 `config.yaml`

```yaml
model_list:
  - model_name: gpt-oss
    litellm_params:
      model: openai/gpt-oss
      api_base: http://127.0.0.1:8010/v1
      api_key: dummy
  - model_name: qwen3.5
    litellm_params:
      model: openai/qwen3.5
      api_base: http://127.0.0.1:8011/v1
      api_key: dummy

general_settings:
  master_key: sk-portway-local

litellm_settings:
  drop_params: true
```

Notes:
- `model_name` is the public alias clients send. `litellm_params.model` uses the `openai/` provider prefix because both backends speak the OpenAI wire format (`llama-server` does).
- `api_key: dummy` because Post 2's `llama-server` doesn't check it. Post 4 will tighten this when virtual keys land.
- `master_key` is the single client-side credential for Post 3. Post 4 replaces it with per-customer virtual keys backed by Postgres.
- `drop_params: true` keeps unknown params from erroring — useful for forward compatibility with newer SDK fields, with the trade-off noted in §5.

### 3.2 Reasoning channel

Both demo models emit a reasoning channel. We segregate it (the option chosen in brainstorming) rather than strip or inline it:

- LiteLLM's default normalization places reasoning in `choices[].message.reasoning_content` and the visible answer in `choices[].message.content`. No extra setting needed — specifically, we do **not** set `merge_reasoning_content_in_choices`.
- Pass `reasoning_effort` through unchanged so clients can dial gpt-oss between `low | medium | high`. LiteLLM forwards it; no gateway code required.
- For Qwen3.5 to populate `reasoning_content`, the `llama-server` process needs `--jinja --reasoning-format auto` (or `deepseek`). This is a small back-edit to `2-two-models/start-backends.sh`. Post 2's `demo.py` doesn't read `reasoning_content`, so its captured sample output and DoD are unaffected — the field simply becomes available for Post 3's demo to consume.

## 4. Demo blocks (`3-gateway/demo.py`)

Three blocks, mirroring Post 2's rhythm. All non-streaming.

### Block 1 — `/v1/models` on the gateway

```
GET http://localhost:4000/v1/models
Authorization: Bearer sk-portway-local
→ ["gpt-oss", "qwen3.5"]
```

Print the IDs to prove the public catalog is correct. Visual contrast against Post 2, where the same check required hitting two different ports.

### Block 2 — Same prompt, two voices, one base URL

```python
client = OpenAI(base_url="http://localhost:4000/v1", api_key="sk-portway-local")
for model in ["gpt-oss", "qwen3.5"]:
    r = client.chat.completions.create(model=model, messages=PROMPT)
    print(model, r.choices[0].message.content)
    print(model, "reasoning:", (r.choices[0].message.reasoning_content or "")[:200])
    print(model, "usage:", r.usage)
```

The money block: one client, one base URL, one call shape. Flipping `model` hits a different backend. `reasoning_content` is visibly broken out from `content`, making the channel that Post 2 surfaced concretely addressable.

### Block 3 — Bad model name returns a clean 404

```python
try:
    client.chat.completions.create(model="gpt-99", messages=PROMPT)
except openai.NotFoundError as e:
    print(e.status_code, e.body)
```

Demonstrates the OpenAI-shaped error contract: `{"error": {"message": ..., "type": ..., "code": ...}}`, status 404 — not a bare 500. Directly addresses the third DoD bullet.

Total demo: ~50 LOC. No streaming, no concurrency.

## 5. "Things that bit" — coverage plan for the walkthrough

The walkthrough's closing section will cover these, in this order:

1. **Gateway port collision.** Port `4000` is LiteLLM's convention but commonly in use locally (other proxies, dashboards). Same `lsof -i :PORT` discipline as Post 2.
2. **`openai/` prefix is the *provider*, not your alias.** `model_name` is the public alias clients send; `litellm_params.model` is how LiteLLM talks to the backend. Easy to wire backwards because both fields hold a string that looks like a model name.
3. **Two layers of auth.** `master_key` is what clients send to the gateway; `api_key` per route is what the gateway sends to the backend. Conflating them is a common first-day bug. Post 4 makes the customer side concrete with virtual keys.
4. **Reasoning lives in `reasoning_content`, not `content`.** A client that logs only `content` will wonder where 3000 tokens went (same observation as Post 2 Block 2, now with a place to look).
5. **`drop_params: true` silently swallows unknown params.** Useful default for forward-compatibility with newer SDK fields, but means typos in request bodies don't error. Worth flagging.
6. **`/v1/models` matters for frameworks.** LangChain, Continue.dev, Cursor, and similar clients call it on startup; a wrong or empty list produces confusing downstream errors with no obvious cause.

## 6. Definition of Done (Cost: $0)

Verbatim from `series.md` Post 3:
- [ ] One base URL; flipping `model` between the two names hits different local backends (Block 2).
- [ ] `/v1/models` lists both model names (Block 1).
- [ ] A bad model name returns a clean 404 (Block 3).

Implicit (from §3.2):
- [ ] `reasoning_content` is populated for both models in Block 2 output.

## 7. Open back-edits to earlier posts

One small change to a Post-2 artifact:
- `2-two-models/start-backends.sh` gains `--jinja --reasoning-format auto` on both `llama-server` invocations so Qwen3.5's thinking output lands in `reasoning_content`. Post 2's `demo.py` and captured sample output are untouched; the field is simply available now for Post 3's demo to read. The Post-2 DoD is unaffected.

No other earlier-post edits planned.
