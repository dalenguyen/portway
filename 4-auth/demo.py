"""Post 4 — Auth, API keys, and per-key model scoping.

Run:
    2-two-models/start-backends.sh        # in another shell
    4-auth/start-keystore.sh start
    4-auth/start-gateway.sh
    uv run --project 4-auth python 4-auth/demo.py
"""

import uuid

import httpx
import openai
from openai import OpenAI

GATEWAY_URL = "http://127.0.0.1:4000"
MASTER_KEY = "sk-portway-admin"


def mint_keys() -> tuple[str, str]:
    print("=" * 60)
    print("Block 0 — admin mints two virtual keys")
    print("=" * 60)
    run_id = uuid.uuid4().hex[:8]
    admin = httpx.Client(
        base_url=GATEWAY_URL,
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        timeout=10.0,
    )

    full = admin.post(
        "/key/generate",
        json={
            "models": ["gpt-oss", "qwen3.5"],
            "rpm_limit": 60,
            "tpm_limit": 100_000,
            "duration": "1h",
            "key_alias": f"full-access-demo-{run_id}",
        },
    )
    full.raise_for_status()
    full_key = full.json()["key"]

    scoped = admin.post(
        "/key/generate",
        json={
            "models": ["gpt-oss"],
            "rpm_limit": 3,
            "tpm_limit": 200,
            "duration": "1h",
            "key_alias": f"gpt-oss-only-demo-{run_id}",
        },
    )
    scoped.raise_for_status()
    scoped_key = scoped.json()["key"]

    print(f"full-access key:  …{full_key[-4:]}  (models: gpt-oss, qwen3.5)")
    print(f"scoped key:       …{scoped_key[-4:]}  (models: gpt-oss; rpm=3, tpm=200)")
    return full_key, scoped_key


def models_per_key(full_key: str, scoped_key: str) -> None:
    print()
    print("=" * 60)
    print("Block 1 — /v1/models reflects per-key scope")
    print("=" * 60)
    for label, key in [("full-access", full_key), ("scoped", scoped_key)]:
        c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=key)
        ids = sorted(m.id for m in c.models.list().data)
        print(f"{label:13s} -> {ids}")


def scope_violation(scoped_key: str) -> None:
    print()
    print("=" * 60)
    print("Block 2 — scoped key blocked on out-of-scope model")
    print("=" * 60)
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=scoped_key)
    try:
        c.chat.completions.create(
            model="qwen3.5",
            messages=[{"role": "user", "content": "hi"}],
        )
    except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
        print(f"status:  {e.status_code}")
        print(f"body:    {e.body}")
        return
    raise SystemExit("Block 2 FAILED: scoped key was allowed to call qwen3.5")


def rpm_trip(scoped_key: str) -> None:
    print()
    print("=" * 60)
    print("Block 3 — RPM limit trips 429")
    print("=" * 60)
    c = OpenAI(base_url=f"{GATEWAY_URL}/v1", api_key=scoped_key, max_retries=0)
    # scoped key has rpm_limit=3. Fire 4 quick requests; expect 4th to 429.
    for i in range(1, 5):
        try:
            c.chat.completions.create(
                model="gpt-oss",
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=4,
            )
            print(f"  request {i}: 200")
        except openai.RateLimitError as e:
            print(f"  request {i}: {e.status_code} (RateLimitError) — RPM tripped")
            return
    raise SystemExit("Block 3 FAILED: RPM limit never tripped over 4 requests")


if __name__ == "__main__":
    full_key, scoped_key = mint_keys()
    models_per_key(full_key, scoped_key)
    scope_violation(scoped_key)
    rpm_trip(scoped_key)
