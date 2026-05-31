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


if __name__ == "__main__":
    full_key, scoped_key = mint_keys()
