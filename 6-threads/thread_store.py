"""Post 6 — thread store. Two tables, a handful of functions, no abstractions.

This module is the *application*'s memory of conversation history. The gateway
(LiteLLM) and the backends (llama-server) never see it. Each chat turn:

    msgs = thread_store.assemble(thread_id, system_prompt, strategy, budget)
    completion = openai_client.chat.completions.create(model=..., messages=msgs)
    thread_store.append_message(thread_id, "user", user_text, tokens=usage.prompt_tokens)
    thread_store.append_message(thread_id, "assistant", reply_text, tokens=usage.completion_tokens)

Schema is created idempotently on first import (same pattern as
`portway_callback.py`). The two tables live in the same `portway` Postgres DB
as `LiteLLM_*` and `portway_metering`.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Literal

import psycopg

DSN = os.environ.get(
    "PORTWAY_DSN",
    "postgresql://postgres:portway@127.0.0.1:5432/portway",
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS portway_threads (
  thread_id     TEXT PRIMARY KEY,
  api_key_hash  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  summary       TEXT
);
CREATE TABLE IF NOT EXISTS portway_messages (
  thread_id     TEXT NOT NULL REFERENCES portway_threads(thread_id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL,
  tokens        INTEGER,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (thread_id, idx)
);
"""

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(CREATE_SQL)
    _schema_ready = True


def create_thread(api_key_hash: str, thread_id: str | None = None) -> str:
    """Insert a new thread row; return its id. `thread_id` is optional so the
    caller can choose a memorable one (e.g. 'demo-block1') instead of a UUID."""
    _ensure_schema()
    tid = thread_id or f"thr_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO portway_threads (thread_id, api_key_hash) VALUES (%s, %s) "
            "ON CONFLICT (thread_id) DO NOTHING",
            (tid, api_key_hash),
        )
    return tid


def append_message(thread_id: str, role: str, content: str,
                   tokens: int | None = None) -> int:
    """Append a message; idx is auto-assigned as max(idx)+1. Returns the new idx.

    `tokens` should be the per-message token count from the backend response's
    usage when available — we use it later for budget arithmetic in assemble().
    Estimating it ourselves would defeat the point of Post 5's "trust the
    backend tokenizer" lesson.
    """
    _ensure_schema()
    with psycopg.connect(DSN, autocommit=True) as conn:
        with conn.transaction():
            row = conn.execute(
                "SELECT COALESCE(MAX(idx), -1) + 1 FROM portway_messages "
                "WHERE thread_id = %s",
                (thread_id,),
            ).fetchone()
            new_idx = row[0]
            conn.execute(
                "INSERT INTO portway_messages (thread_id, idx, role, content, tokens) "
                "VALUES (%s, %s, %s, %s, %s)",
                (thread_id, new_idx, role, content, tokens),
            )
    return new_idx


def load_messages(thread_id: str) -> list[dict[str, Any]]:
    """Return all messages for `thread_id` in idx order."""
    _ensure_schema()
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.execute(
            "SELECT idx, role, content, tokens FROM portway_messages "
            "WHERE thread_id = %s ORDER BY idx",
            (thread_id,),
        )
        return [
            {"idx": r[0], "role": r[1], "content": r[2], "tokens": r[3]}
            for r in cur.fetchall()
        ]


def get_summary(thread_id: str) -> str | None:
    _ensure_schema()
    with psycopg.connect(DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT summary FROM portway_threads WHERE thread_id = %s",
            (thread_id,),
        ).fetchone()
    return row[0] if row else None


def set_summary(thread_id: str, summary: str) -> None:
    _ensure_schema()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "UPDATE portway_threads SET summary = %s WHERE thread_id = %s",
            (summary, thread_id),
        )


def _est_tokens(text: str) -> int:
    """Rough character-based estimate for messages we have no `tokens` for —
    typically only the system prompt and the in-flight user turn before its
    response comes back. Post 5 hammered home not trusting len/4 for metering;
    here it's only used to *budget* the assembly, never to bill.
    """
    return max(1, len(text) // 4)


Strategy = Literal["truncate", "summarize"]


def assemble(
    thread_id: str,
    *,
    system_prompt: str,
    user_message: str,
    strategy: Strategy = "truncate",
    budget_tokens: int = 16384,
    completion_reserve: int = 512,
) -> list[dict[str, str]]:
    """Return the `messages[]` to send for the next turn.

    Always shaped: [system, (summary-as-system if "summarize"), ...recent
    history that fits, user_message]. The recent-history window is grown
    backwards (newest pair first) until adding another pair would exceed
    `budget_tokens - completion_reserve`.

    `budget_tokens` is the backend's `--ctx-size` (or `--max-model-len`).
    `completion_reserve` is the room left for the reply.
    """
    history = load_messages(thread_id)
    available = budget_tokens - completion_reserve - _est_tokens(system_prompt) - _est_tokens(user_message)

    framing: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if strategy == "summarize":
        s = get_summary(thread_id)
        if s:
            framing.append({"role": "system",
                            "content": f"Summary of earlier conversation:\n{s}"})
            available -= _est_tokens(s)

    # Walk history newest-first, prepending pairs while they fit.
    selected: list[dict[str, str]] = []
    running = 0
    for m in reversed(history):
        cost = m["tokens"] or _est_tokens(m["content"])
        if running + cost > available:
            break
        selected.append({"role": m["role"], "content": m["content"]})
        running += cost
    selected.reverse()

    return framing + selected + [{"role": "user", "content": user_message}]


def list_threads(api_key_hash: str | None = None) -> list[dict[str, Any]]:
    _ensure_schema()
    sql = (
        "SELECT t.thread_id, t.api_key_hash, t.created_at, "
        "       COUNT(m.idx) AS n_messages, "
        "       (t.summary IS NOT NULL) AS has_summary "
        "FROM portway_threads t LEFT JOIN portway_messages m USING (thread_id) "
    )
    params: tuple = ()
    if api_key_hash is not None:
        sql += "WHERE t.api_key_hash = %s "
        params = (api_key_hash,)
    sql += "GROUP BY t.thread_id ORDER BY t.created_at"
    with psycopg.connect(DSN, autocommit=True) as conn:
        cur = conn.execute(sql, params)
        return [
            {"thread_id": r[0], "api_key_hash": r[1], "created_at": r[2],
             "n_messages": r[3], "has_summary": r[4]}
            for r in cur.fetchall()
        ]
