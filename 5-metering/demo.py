"""Post 5 — Token tracking & metering.

Run:
    5-metering/start-backends.sh         # if not already running
    5-metering/start-keystore.sh start   # if not already running
    5-metering/start-gateway.sh          # if not already running
    uv run --project 5-metering python 5-metering/demo.py
"""

from __future__ import annotations

import time
import uuid

import httpx
import psycopg
from openai import OpenAI

GATEWAY_URL = "http://127.0.0.1:4000"
MASTER_KEY = "sk-portway-admin"
DSN = "postgresql://postgres:portway@127.0.0.1:5432/portway"


def banner(title: str) -> None:
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def mint_key() -> str:
    banner("Block 0 — admin mints a metering-demo key")
    run_id = uuid.uuid4().hex[:8]
    admin = httpx.Client(
        base_url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        timeout=10.0,
    )
    r = admin.post("/key/generate", json={
        "models": ["gpt-oss", "qwen3.5"],
        "rpm_limit": 60,
        "tpm_limit": 100_000,
        "duration": "1h",
        "key_alias": f"metering-demo-{run_id}",
    })
    r.raise_for_status()
    key = r.json()["key"]
    print(f"metering-demo key: …{key[-4:]}  (models: gpt-oss, qwen3.5)")
    return key


def metering_row(request_id: str) -> dict | None:
    """SELECT one row from portway_metering by request_id, or None."""
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.execute(
            """SELECT request_id, public_model, backend_model, prompt_tokens,
                      completion_tokens, total_tokens, computed_cost, status,
                      ttft_ms, total_latency_ms
               FROM portway_metering WHERE request_id = %s""",
            (request_id,),
        )
        row = cur.fetchone()
        cols = [d.name for d in cur.description] if cur.description else []
    if row is None:
        return None
    return dict(zip(cols, row, strict=False))


def pretty(row: dict | None) -> str:
    if row is None:
        return "  (no row)"
    keys = ["public_model", "prompt_tokens", "completion_tokens", "total_tokens",
            "computed_cost", "status", "total_latency_ms"]
    return "\n".join(f"  {k:18s} {row.get(k)}" for k in keys)


def non_streamed_metering(key: str) -> str:
    banner("Block 1 — non-streamed request → metering row")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    completion = c.chat.completions.create(
        model="gpt-oss",
        messages=[{"role": "user", "content": "Name one Canadian city."}],
        max_tokens=20,
    )
    request_id = completion.id  # chatcmpl-... — matches portway_metering.request_id
    print(f"  request_id: {request_id}")
    print(f"  usage:      {completion.usage.model_dump()}")
    # The callback writes synchronously inside the gateway request path, so
    # the row is committed before the response returns. A tiny sleep is just
    # belt-and-braces.
    time.sleep(0.3)
    row = metering_row(request_id)
    print(pretty(row))
    return request_id


def streaming_trap_bug(key: str) -> str:
    banner("Block 2 — streaming WITHOUT include_usage → the BUG")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    stream = c.chat.completions.create(
        model="gpt-oss",
        messages=[{"role": "user", "content": "Count to three."}],
        max_tokens=30,
        stream=True,
        # stream_options intentionally omitted — this is the trap
    )
    chunks_seen = 0
    content_pieces: list[str] = []
    request_id: str | None = None
    for chunk in stream:
        chunks_seen += 1
        if request_id is None and getattr(chunk, "id", None):
            request_id = chunk.id  # same chatcmpl-... id across all chunks of a stream
        if chunk.choices and chunk.choices[0].delta.content:
            content_pieces.append(chunk.choices[0].delta.content)
    print(f"  request_id:    {request_id}")
    print(f"  chunks_seen:   {chunks_seen}")
    print(f"  content:       {''.join(content_pieces)!r}")
    print(f"  usage in any chunk?  no  (we never asked for it)")
    time.sleep(0.5)
    row = metering_row(request_id) if request_id else None
    print("  metering row this produced:")
    print(pretty(row))
    print()
    print("  *** zero-billed stream — this is the trap. ***")
    print("  Fix in Block 3: stream_options={'include_usage': True}")
    return request_id or "unknown"


def streaming_trap_fix(key: str) -> str:
    banner("Block 3 — streaming WITH include_usage → the FIX")
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key, max_retries=0)
    stream = c.chat.completions.create(
        model="gpt-oss",
        messages=[{"role": "user", "content": "Count to three."}],
        max_tokens=30,
        stream=True,
        stream_options={"include_usage": True},          # THE FIX — one line
    )
    chunks_seen = 0
    content_pieces: list[str] = []
    final_usage = None
    request_id: str | None = None
    for chunk in stream:
        chunks_seen += 1
        if request_id is None and getattr(chunk, "id", None):
            request_id = chunk.id
        # The final chunk has empty `choices` and only carries `usage`.
        # Guard the choices access; read usage separately.
        if chunk.choices and chunk.choices[0].delta.content:
            content_pieces.append(chunk.choices[0].delta.content)
        if getattr(chunk, "usage", None):
            final_usage = chunk.usage
    print(f"  request_id:    {request_id}")
    print(f"  chunks_seen:   {chunks_seen}")
    print(f"  content:       {''.join(content_pieces)!r}")
    print(f"  final usage:   {final_usage.model_dump() if final_usage else None}")
    time.sleep(0.5)
    row = metering_row(request_id) if request_id else None
    print("  metering row this produced:")
    print(pretty(row))
    return request_id or "unknown"


def _wait_for_spendlogs(request_id: str, timeout_s: float = 30.0) -> tuple | None:
    """Poll LiteLLM_SpendLogs until the row for `request_id` shows up, or timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with psycopg.connect(DSN, autocommit=True) as conn:
            row = conn.execute(
                'SELECT request_id, model, spend, total_tokens, prompt_tokens, completion_tokens '
                'FROM "LiteLLM_SpendLogs" WHERE request_id = %s',
                (request_id,),
            ).fetchone()
        if row is not None:
            return row
        time.sleep(0.5)
    return None


def spendlogs_vs_portway(request_id: str) -> None:
    banner("Block 4 — LiteLLM_SpendLogs vs portway_metering agree")
    print(f"  comparing request_id: {request_id}")
    print(f"  waiting for SpendLogs batch flush ...")
    sl = _wait_for_spendlogs(request_id, timeout_s=30.0)
    with psycopg.connect(DSN, autocommit=True) as conn:
        pm = conn.execute(
            "SELECT request_id, public_model, computed_cost, total_tokens, "
            "prompt_tokens, completion_tokens "
            "FROM portway_metering WHERE request_id = %s",
            (request_id,),
        ).fetchone()
    print()
    print("  LiteLLM_SpendLogs:")
    if sl is None:
        print("    (no row after 30s — batch flush slow or write disabled?)")
    else:
        print(f"    model={sl[1]}  spend={float(sl[2]):.8f}  tokens={sl[3]} (in={sl[4]}, out={sl[5]})")
    print("  portway_metering:")
    if pm is None:
        print("    (no row)")
    else:
        print(f"    model={pm[1]}  cost ={float(pm[2]):.8f}  tokens={pm[3]} (in={pm[4]}, out={pm[5]})")
    if sl is not None and pm is not None:
        agree = (sl[3] == pm[3] and abs(float(sl[2]) - float(pm[2])) < 1e-10)
        print()
        print(f"  agreement: {'matched (tokens + cost)' if agree else 'DIVERGENT — investigate'}")


def spend_by_key_model() -> None:
    banner("Block 5 — spend grouped by key + model")
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            """SELECT api_key_hash, public_model,
                      COUNT(*)         AS requests,
                      SUM(total_tokens) AS tokens,
                      SUM(computed_cost) AS spend
               FROM portway_metering
               WHERE status = 'success'
               GROUP BY 1, 2
               ORDER BY 3 DESC"""
        ).fetchall()
    if not rows:
        print("  (no rows yet)")
        return
    print(f"  {'api_key_hash':18s} {'model':10s} {'reqs':>5s} {'tokens':>8s} {'spend':>12s}")
    for h, m, reqs, tokens, spend in rows:
        print(f"  {h:18s} {m:10s} {reqs:>5d} {tokens:>8d} ${float(spend):>11.8f}")


if __name__ == "__main__":
    key = mint_key()
    nonstream_id = non_streamed_metering(key)
    bug_id = streaming_trap_bug(key)
    fix_id = streaming_trap_fix(key)
    spendlogs_vs_portway(fix_id)
    spend_by_key_model()
