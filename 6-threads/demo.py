"""Post 6 — Conversation state & context management.

Run:
    6-threads/start-backends.sh         # if not already running
    6-threads/start-keystore.sh start   # if not already running
    6-threads/start-gateway.sh          # if not already running
    uv run --project 6-threads python 6-threads/demo.py
"""

from __future__ import annotations

import time
import uuid

import httpx
import psycopg
from openai import OpenAI

import thread_store

GATEWAY_URL = "http://127.0.0.1:4000"
MASTER_KEY = "sk-portway-admin"
DSN = "postgresql://postgres:portway@127.0.0.1:5432/portway"

# Demonstration budget. The backends are launched with --ctx-size 131072 so we
# won't naturally overflow on a 50-turn thread of short messages — set a small
# budget here so the assembly logic visibly drops history. Same shape, smaller
# number; the lesson is "assemble respects the cap," not "this exact number."
DEMO_BUDGET_TOKENS = 2048
COMPLETION_RESERVE = 512
# A tighter cap used only for the overflow demonstrations (Blocks 3 and 4) so
# truncation visibly bites against the 50-turn thread.
OVERFLOW_BUDGET_TOKENS = 800

# Both backends launched with `--reasoning-format auto`. The models split
# reasoning tokens off the visible content; a stingy max_tokens can leave
# content empty when reasoning consumes the whole budget. Give every call
# enough room for reasoning + a short final answer, and ask gpt-oss to use
# its "low" reasoning effort. (LiteLLM's `drop_params: true` silently strips
# the parameter for qwen3.5, which doesn't accept it.)
MAX_TOKENS_CHAT = 1024
MAX_TOKENS_SUMMARY = 800
GPT_OSS_EXTRA = {"reasoning_effort": "low"}


def banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def mint_key() -> tuple[str, str]:
    """Returns (key, key_hash_for_attribution)."""
    banner("Block 0 — admin mints a threads-demo key")
    run_id = uuid.uuid4().hex[:8]
    with httpx.Client(
        base_url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        timeout=10.0,
    ) as admin:
        r = admin.post("/key/generate", json={
            "models": ["gpt-oss", "qwen3.5"],
            "rpm_limit": 120,
            "tpm_limit": 400_000,
            "duration": "1h",
            "key_alias": f"threads-demo-{run_id}",
        })
        r.raise_for_status()
        body = r.json()
        key = body["key"]
        # The metering callback stores first-16 of the hash. Mirror that for
        # thread attribution so the same value appears in both tables.
        token_id = body.get("token_id") or body.get("token") or ""
        key_hash = token_id[:16] if token_id else key[-16:]
    print(f"threads-demo key: …{key[-4:]}  (models: gpt-oss, qwen3.5)")
    return key, key_hash


# ---- Block 1 — 10-turn chat, proving the server has no session ----------

SYSTEM_PROMPT = (
    "You are a concise travel assistant. Keep every answer under 30 words. "
    "Always answer based on what the user has told you in this conversation."
)

TRIP_TURNS = [
    "I'm planning a trip to Canada in October. Just say hi.",
    "I'll have 10 days. Suggest two provinces worth visiting in autumn.",
    "Let's go with Quebec. Name two cities I shouldn't miss.",
    "I'll prioritize Montreal. What's one dish I should try there?",
    "What's the average October temperature in Montreal in Celsius?",
    "Recommend one neighborhood to stay in.",
    "Will I need French to get by there?",
    "One outdoor activity worth doing that month?",
    "Suggest one day trip out of the city.",
    "What was the original month I said I'd travel?",
]


def ten_turn_stateless_chat(key: str, key_hash: str) -> str:
    banner("Block 1 — 10-turn chat; prompt_tokens grow because we resend history")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    tid = thread_store.create_thread(key_hash, thread_id=f"thr-trip-{uuid.uuid4().hex[:8]}")
    print(f"  thread_id: {tid}")
    print()
    print(f"  {'turn':>4s}  {'prompt_tok':>10s}  {'compl_tok':>10s}  {'reply (first 60 chars)':<60s}")
    for i, user_msg in enumerate(TRIP_TURNS, start=1):
        msgs = thread_store.assemble(
            tid,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_msg,
            strategy="truncate",
            budget_tokens=DEMO_BUDGET_TOKENS * 8,   # plenty of headroom for 10 short turns
            completion_reserve=COMPLETION_RESERVE,
        )
        completion = c.chat.completions.create(
            model="gpt-oss", messages=msgs, max_tokens=MAX_TOKENS_CHAT,
            extra_body=GPT_OSS_EXTRA,
        )
        reply = (completion.choices[0].message.content or "").strip()
        thread_store.append_message(tid, "user", user_msg,
                                    tokens=None)  # per-message prompt tokens not directly given
        thread_store.append_message(tid, "assistant", reply,
                                    tokens=completion.usage.completion_tokens)
        print(f"  {i:>4d}  {completion.usage.prompt_tokens:>10d}  "
              f"{completion.usage.completion_tokens:>10d}  {reply[:60]:<60s}")
    print()
    print("  *** prompt_tokens grows each turn because we resend the whole history.")
    print("      If the server held a session, prompt_tokens would stay roughly flat.")
    print("      Block 2's cold thread is the negative control: same question with no")
    print("      history sent shows the model can't recover the earlier context.")
    return tid


# ---- Block 2 — prefix-cache benefit: warm thread vs cold thread ----------

CACHE_PROBE = "Recap our plan in one sentence."


def _stream_with_ttft(client: OpenAI, model: str, messages: list[dict]) -> dict:
    """Stream a request; return {ttft_ms, total_ms, prompt_tokens, completion_tokens,
    cached_tokens, content, request_id}.

    TTFT = time from request send to the first chunk that carries ANY delta
    (content OR reasoning_content). Measuring on reasoning is honest: it's the
    point at which the backend stopped prefilling and started decoding, which
    is exactly what TTFT means. `cached_tokens` comes from the final usage
    chunk's `prompt_tokens_details.cached_tokens` (Post 5 already wired the
    `include_usage` flag, so the row exists)."""
    t0 = time.perf_counter()
    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=MAX_TOKENS_CHAT,
        stream=True,
        stream_options={"include_usage": True},
        extra_body=GPT_OSS_EXTRA if model == "gpt-oss" else None,
    )
    ttft_ms: float | None = None
    content_pieces: list[str] = []
    final_usage = None
    request_id = None
    for chunk in stream:
        if request_id is None and getattr(chunk, "id", None):
            request_id = chunk.id
        if chunk.choices:
            delta = chunk.choices[0].delta
            has_any = bool(getattr(delta, "content", None)) or \
                      bool(getattr(delta, "reasoning_content", None))
            if has_any and ttft_ms is None:
                ttft_ms = (time.perf_counter() - t0) * 1000
            if delta.content:
                content_pieces.append(delta.content)
        if getattr(chunk, "usage", None):
            final_usage = chunk.usage
    total_ms = (time.perf_counter() - t0) * 1000
    usage = final_usage.model_dump() if final_usage else {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return {
        "ttft_ms": ttft_ms,
        "total_ms": total_ms,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cached_tokens": cached,
        "content": "".join(content_pieces),
        "request_id": request_id,
    }


def cold_vs_warm_prefix_cache(key: str, key_hash: str, warm_thread_id: str) -> None:
    banner("Block 2 — prefix cache + stateless control: warm vs cold same probe")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)

    # Run warm FIRST. The 10-turn prefix from Block 1's last call is still in
    # the llama.cpp KV slot; running cold first would evict it.
    warm_msgs = thread_store.assemble(
        warm_thread_id,
        system_prompt=SYSTEM_PROMPT,
        user_message=CACHE_PROBE,
        strategy="truncate",
        budget_tokens=DEMO_BUDGET_TOKENS * 8,
        completion_reserve=COMPLETION_RESERVE,
    )
    warm = _stream_with_ttft(c, "gpt-oss", warm_msgs)
    thread_store.append_message(warm_thread_id, "user", CACHE_PROBE, tokens=None)
    thread_store.append_message(warm_thread_id, "assistant", warm["content"],
                                tokens=warm["completion_tokens"])

    # Cold: fresh thread, no history. Doubles as the stateless control —
    # the model can't recap a plan it was never told about.
    cold_tid = thread_store.create_thread(key_hash,
                                          thread_id=f"thr-cold-{uuid.uuid4().hex[:8]}")
    cold_msgs = thread_store.assemble(
        cold_tid,
        system_prompt=SYSTEM_PROMPT,
        user_message=CACHE_PROBE,
        strategy="truncate",
        budget_tokens=DEMO_BUDGET_TOKENS * 8,
        completion_reserve=COMPLETION_RESERVE,
    )
    cold = _stream_with_ttft(c, "gpt-oss", cold_msgs)
    thread_store.append_message(cold_tid, "user", CACHE_PROBE, tokens=None)
    thread_store.append_message(cold_tid, "assistant", cold["content"],
                                tokens=cold["completion_tokens"])

    print(f"  probe: {CACHE_PROBE!r}")
    print()
    print(f"  {'':<22s} {'cold thread':>14s} {'warm thread':>14s}")
    print(f"  {'prompt_tokens':<22s} {cold['prompt_tokens']:>14d} {warm['prompt_tokens']:>14d}")
    print(f"  {'cached_tokens':<22s} {cold['cached_tokens']:>14d} {warm['cached_tokens']:>14d}")
    ttft_cold = f"{cold['ttft_ms']:.0f}ms" if cold["ttft_ms"] is not None else "n/a"
    ttft_warm = f"{warm['ttft_ms']:.0f}ms" if warm["ttft_ms"] is not None else "n/a"
    print(f"  {'ttft':<22s} {ttft_cold:>14s} {ttft_warm:>14s}")
    print(f"  {'total_latency':<22s} {cold['total_ms']:>13.0f}ms {warm['total_ms']:>13.0f}ms")
    print()
    print(f"  cold reply: {cold['content'][:120]!r}")
    print(f"  warm reply: {warm['content'][:120]!r}")
    print()
    print("  *** Two readings in one block:")
    print("      • Cache: warm cached_tokens >> cold cached_tokens — the 10-turn")
    print("        prefix is reused; the cold thread only shares the system bytes.")
    print("      • Stateless: cold can't recap a plan it was never told. The server")
    print("        kept nothing between threads.")


# ---- Block 3 — overflow via truncation ----------------------------------

def synthesize_50_turn_thread(key_hash: str, thread_id: str) -> None:
    """Pre-populate a thread with 50 alternating user/assistant messages. The
    content is canned — we don't want to spend 50 LLM calls just to fill a
    thread for the assembly demo. Token counts are estimated; that's fine
    because assemble() falls back to len/4 when `tokens` is NULL.

    Assistant replies are deliberately verbose so the thread's total token
    count comfortably exceeds OVERFLOW_BUDGET_TOKENS and truncation visibly
    bites.
    """
    thread_store.create_thread(key_hash, thread_id=thread_id)
    topics = [
        "lakes", "mountains", "cuisine", "wildlife", "hockey",
        "winter sports", "national parks", "Indigenous history",
        "the Northern Lights", "Maritimes seafood",
    ]
    for i in range(1, 26):  # 25 user/assistant pairs = 50 messages
        topic = topics[(i - 1) % len(topics)]
        thread_store.append_message(
            thread_id, "user",
            f"Tell me one interesting fact about Canadian {topic} (item #{i}). "
            f"Give a concrete example I can imagine.",
            tokens=None,
        )
        thread_store.append_message(
            thread_id, "assistant",
            f"Here is a concrete fact about Canadian {topic}: it's woven through "
            f"daily life in ways visitors often underestimate, and it varies "
            f"dramatically between regions, seasons, and cultural communities. "
            f"A traveller in October would notice it especially around small towns "
            f"and rural roads, where the landscape and the local routines reinforce "
            f"each other. (Synthesized turn {i} of 50 — placeholder so the truncate "
            f"and summarize blocks have a realistic thread length to work against.)",
            tokens=None,
        )


def truncate_overflow(key: str, key_hash: str) -> str:
    banner(f"Block 3 — 50-turn thread, truncate to fit budget={OVERFLOW_BUDGET_TOKENS}")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    tid = f"thr-50turn-{uuid.uuid4().hex[:8]}"
    synthesize_50_turn_thread(key_hash, tid)

    user_msg = "Given everything we discussed, recommend one province for a first-time visitor."
    all_msgs = thread_store.load_messages(tid)
    truncated = thread_store.assemble(
        tid,
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        strategy="truncate",
        budget_tokens=OVERFLOW_BUDGET_TOKENS,
        completion_reserve=COMPLETION_RESERVE,
    )

    naive_tokens = sum((m["tokens"] or len(m["content"]) // 4) for m in all_msgs)
    truncated_history_count = len(truncated) - 2  # minus system + final user
    print(f"  full thread in DB:           {len(all_msgs)} messages, ~{naive_tokens} tokens")
    print(f"  budget (OVERFLOW_BUDGET):    {OVERFLOW_BUDGET_TOKENS} tokens "
          f"(minus {COMPLETION_RESERVE} reserve)")
    print(f"  assembled (truncate):        {len(truncated)} messages "
          f"(system + {truncated_history_count} recent + new user)")

    completion = c.chat.completions.create(model="gpt-oss", messages=truncated,
                                           max_tokens=MAX_TOKENS_CHAT,
                                           extra_body=GPT_OSS_EXTRA)
    reply = (completion.choices[0].message.content or "").strip()
    thread_store.append_message(tid, "user", user_msg, tokens=None)
    thread_store.append_message(tid, "assistant", reply,
                                tokens=completion.usage.completion_tokens)
    print(f"  call prompt_tokens:          {completion.usage.prompt_tokens}")
    print(f"  reply:                       {reply[:160]}")
    print()
    print("  *** Naive 'send everything' would have shipped the full thread.")
    print("      assemble(truncate) kept only the most-recent pairs that fit.")
    return tid


# ---- Block 4 — overflow via summarization (gateway call to qwen3.5) ------

def summarize_overflow(key: str, key_hash: str) -> str:
    banner(f"Block 4 — same 50-turn shape, but summarize older turns via qwen3.5")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    tid = f"thr-50sum-{uuid.uuid4().hex[:8]}"
    synthesize_50_turn_thread(key_hash, tid)

    # Compact turns 1..40 into a summary. Pull them from the DB and ask qwen3.5
    # via the gateway to compress them.
    history = thread_store.load_messages(tid)
    to_summarize = history[:40]
    summary_input = "\n".join(f"{m['role']}: {m['content']}" for m in to_summarize)
    summary_resp = c.chat.completions.create(
        model="qwen3.5",
        messages=[
            # `/no_think` is Qwen3's chat-template directive to skip its
            # thinking pass. Without it, --reasoning-format auto can route the
            # entire token budget into reasoning_content and leave .content empty.
            {"role": "system",
             "content": "/no_think Compress the following conversation into "
                        "4 short bullet points preserving the user's preferences "
                        "and any decisions made. Output only the bullets."},
            {"role": "user", "content": summary_input},
        ],
        max_tokens=MAX_TOKENS_SUMMARY,
    )
    msg = summary_resp.choices[0].message
    summary = (msg.content or "").strip()
    if not summary:
        # llama.cpp with --reasoning-format auto exposes the model's thinking
        # under message.reasoning_content. If .content is empty but reasoning
        # is populated, the model did its work — we just have to pull from
        # the other field. Cap the fallback at 400 chars: a summary that's
        # the same size as the history it replaces defeats the point.
        rc = (getattr(msg, "reasoning_content", None) or "").strip()
        if rc:
            summary = (rc[:400] + "…") if len(rc) > 400 else rc
        else:
            summary = ("(no summary content emitted; older turns covered "
                       "varied Canadian regional topics.)")
    thread_store.set_summary(tid, summary)
    print(f"  summary call request_id:  {summary_resp.id}")
    print(f"  summary tokens:           in={summary_resp.usage.prompt_tokens} "
          f"out={summary_resp.usage.completion_tokens}")
    print(f"  summary (stored):         {summary[:200]}{'…' if len(summary) > 200 else ''}")

    user_msg = "Given everything we discussed, recommend one province for a first-time visitor."
    assembled = thread_store.assemble(
        tid,
        system_prompt=SYSTEM_PROMPT,
        user_message=user_msg,
        strategy="summarize",
        budget_tokens=OVERFLOW_BUDGET_TOKENS,
        completion_reserve=COMPLETION_RESERVE,
    )
    # Count message types in assembled for the readout.
    n_system = sum(1 for m in assembled if m["role"] == "system")
    n_user = sum(1 for m in assembled if m["role"] == "user")
    n_assistant = sum(1 for m in assembled if m["role"] == "assistant")
    print(f"  assembled (summarize):    {len(assembled)} messages "
          f"(system×{n_system} + user×{n_user} + assistant×{n_assistant})")

    completion = c.chat.completions.create(model="gpt-oss", messages=assembled,
                                           max_tokens=MAX_TOKENS_CHAT,
                                           extra_body=GPT_OSS_EXTRA)
    reply = (completion.choices[0].message.content or "").strip()
    thread_store.append_message(tid, "user", user_msg, tokens=None)
    thread_store.append_message(tid, "assistant", reply,
                                tokens=completion.usage.completion_tokens)
    print(f"  call prompt_tokens:       {completion.usage.prompt_tokens}")
    print(f"  reply:                    {reply[:160]}")
    print()
    print("  *** The summary lives in portway_threads.summary; assemble() injects")
    print("      it as a second system message. The summarization call itself")
    print("      produced a portway_metering row — see Block 5.")
    return tid


# ---- Block 5 — persistence / cross-table check --------------------------

def persistence_and_listing(summarize_thread_id: str, key_hash: str) -> None:
    banner("Block 5 — thread persistence + metering tie-in")

    threads = thread_store.list_threads(api_key_hash=key_hash)
    print(f"  threads owned by this run's key hash ({key_hash}):")
    print(f"  {'thread_id':<24s} {'msgs':>5s} {'summary?':>8s} {'created_at':>26s}")
    for t in threads:
        ts = t["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {t['thread_id']:<24s} {t['n_messages']:>5d} "
              f"{('yes' if t['has_summary'] else 'no'):>8s} {ts:>26s}")

    # The summarization call in Block 4 went through the gateway, so it
    # produced a portway_metering row — show the join.
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT request_id, public_model, prompt_tokens, completion_tokens, "
            "       computed_cost "
            "FROM portway_metering "
            "WHERE public_model = 'qwen3.5' AND status = 'success' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    if row is None:
        print("\n  (no qwen3.5 metering row yet — callback may still be flushing)")
        return
    print()
    print("  most recent qwen3.5 metering row (from Block 4's summarize call):")
    print(f"    request_id:        {row[0]}")
    print(f"    public_model:      {row[1]}")
    print(f"    prompt_tokens:     {row[2]}")
    print(f"    completion_tokens: {row[3]}")
    print(f"    computed_cost:     ${float(row[4]):.8f}")
    print()
    print(f"  threads persist across `docker restart portway-keystore` because")
    print(f"  pgdata is volume-mounted (carried over from Post 5).")


if __name__ == "__main__":
    key, key_hash = mint_key()
    trip_tid = ten_turn_stateless_chat(key, key_hash)
    cold_vs_warm_prefix_cache(key, key_hash, trip_tid)
    truncate_overflow(key, key_hash)
    summarize_tid = summarize_overflow(key, key_hash)
    # Tiny pause so the metering callback's commit lands before we SELECT.
    time.sleep(1.0)
    persistence_and_listing(summarize_tid, key_hash)
