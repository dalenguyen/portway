"""Post 5 — custom LiteLLM callback writing one row per request to portway_metering.

Mirrors what LiteLLM's built-in LiteLLM_SpendLogs records, but with the explicit
schema from series.md so the walkthrough can show 'here is the row that
represents your bill.' Block 4 of demo.py proves the two tables agree.

LiteLLM 1.86's proxy runs on its async path (`acompletion`), so the modern
callback interface is a CustomLogger subclass with `async_log_success_event` /
`async_log_failure_event`. Registered in config.yaml:

    litellm_settings:
      callbacks: ["portway_callback.portway_meter"]
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg
from litellm.integrations.custom_logger import CustomLogger

DSN = os.environ.get(
    "PORTWAY_DSN",
    "postgresql://postgres:portway@127.0.0.1:5432/portway",
)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS portway_metering (
  request_id        TEXT PRIMARY KEY,
  ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
  api_key_hash      TEXT NOT NULL,
  public_model      TEXT NOT NULL,
  backend_model     TEXT,
  prompt_tokens     INTEGER NOT NULL,
  completion_tokens INTEGER NOT NULL,
  total_tokens      INTEGER NOT NULL,
  computed_cost     NUMERIC(12,8) NOT NULL,
  status            TEXT NOT NULL,
  ttft_ms           INTEGER,
  total_latency_ms  INTEGER NOT NULL
)
"""

INSERT_SQL = """
INSERT INTO portway_metering
  (request_id, api_key_hash, public_model, backend_model,
   prompt_tokens, completion_tokens, total_tokens,
   computed_cost, status, ttft_ms, total_latency_ms)
VALUES
  (%(request_id)s, %(api_key_hash)s, %(public_model)s, %(backend_model)s,
   %(prompt_tokens)s, %(completion_tokens)s, %(total_tokens)s,
   %(computed_cost)s, %(status)s, %(ttft_ms)s, %(total_latency_ms)s)
ON CONFLICT (request_id) DO NOTHING
"""

_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(CREATE_SQL)
    _schema_ready = True


def _api_key_hash(kwargs: dict[str, Any]) -> str:
    slo = kwargs.get("standard_logging_object") or {}
    meta = slo.get("metadata") or {}
    h = (
        meta.get("user_api_key_hash")
        or slo.get("metadata", {}).get("user_api_key")
        or kwargs.get("user_api_key")
        or "unknown"
    )
    return h[:16] if isinstance(h, str) else "unknown"


def _usage(response_obj: Any) -> dict[str, int]:
    if response_obj is None:
        return {}
    u = getattr(response_obj, "usage", None)
    if u is None and isinstance(response_obj, dict):
        u = response_obj.get("usage")
    if u is None:
        return {}
    if hasattr(u, "model_dump"):
        return u.model_dump()
    return dict(u)


def _backend_model(response_obj: Any) -> str | None:
    if response_obj is None:
        return None
    m = getattr(response_obj, "model", None)
    if m is None and isinstance(response_obj, dict):
        m = response_obj.get("model")
    return m


def _latency_ms(start_time: Any, end_time: Any) -> int:
    if start_time is None or end_time is None:
        return 0
    if isinstance(start_time, datetime) and isinstance(end_time, datetime):
        return int((end_time - start_time).total_seconds() * 1000)
    return 0


def _response_id(response_obj: Any) -> str | None:
    """Prefer the OpenAI response id (chatcmpl-...). LiteLLM_SpendLogs keys on
    this for successful requests; matching it makes Block 4's agreement check
    a clean join."""
    if response_obj is None:
        return None
    rid = getattr(response_obj, "id", None)
    if rid is None and isinstance(response_obj, dict):
        rid = response_obj.get("id")
    return rid


def _row(kwargs: dict[str, Any], response_obj: Any,
         start_time: Any, end_time: Any, status: str) -> dict[str, Any]:
    usage = _usage(response_obj)
    return {
        "request_id":        _response_id(response_obj) or kwargs.get("litellm_call_id") or "unknown",
        "api_key_hash":      _api_key_hash(kwargs),
        "public_model":      kwargs.get("model") or "unknown",
        "backend_model":     _backend_model(response_obj),
        "prompt_tokens":     int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens":      int(usage.get("total_tokens") or 0),
        "computed_cost":     float(kwargs.get("response_cost") or 0),
        "status":            status,
        "ttft_ms":           None,  # CustomLogger doesn't surface TTFT in 1.86 for non-streamed; populated by streaming path if available
        "total_latency_ms":  _latency_ms(start_time, end_time),
    }


def _insert(row: dict[str, Any]) -> None:
    _ensure_schema()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(INSERT_SQL, row)


class PortwayMeter(CustomLogger):
    """LiteLLM CustomLogger that writes one portway_metering row per request.

    Implements the async hooks the proxy actually calls (it runs on acompletion).
    Sync variants are provided too for non-proxy SDK use, but the proxy never
    invokes them.
    """

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _insert(_row(kwargs, response_obj, start_time, end_time, "success"))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _insert(_row(kwargs, response_obj, start_time, end_time, "failure"))

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _insert(_row(kwargs, response_obj, start_time, end_time, "success"))

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _insert(_row(kwargs, response_obj, start_time, end_time, "failure"))


# Module-level instance referenced from config.yaml as "portway_callback.portway_meter".
portway_meter = PortwayMeter()
