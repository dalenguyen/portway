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


if __name__ == "__main__":
    gateway_inventory()
    same_prompt_two_voices()
