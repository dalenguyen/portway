"""Post 1 — Local-first: prove the OpenAI-compatible contract against a local model.

Run:
    uv run --project 1-local-first python 1-local-first/demo.py
"""

import os

from openai import OpenAI

BASE_URL = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
API_KEY = "ollama"  # any non-empty string; Ollama ignores it locally
# Default targets a 48 GB box. On ~9 GB machines, override with a smaller model:
#   MODEL=llama3.2:3b uv run --project 1-local-first python 1-local-first/demo.py
#   MODEL=qwen2.5:3b  uv run --project 1-local-first python 1-local-first/demo.py
MODEL = os.environ.get("MODEL", "gpt-oss:20b")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


def round_trip() -> None:
    print("=" * 60)
    print(f"Block 1 — round-trip via OpenAI SDK against localhost ({MODEL})")
    print("=" * 60)
    r = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Name three Canadian cities."}],
    )
    print("content:", r.choices[0].message.content.strip())
    print("usage:  ", r.usage)


def stateless_proof() -> None:
    print()
    print("=" * 60)
    print("Block 2 — same final question, 1-turn vs 5-turn history")
    print("=" * 60)

    final_q = "Driving. What's the capital of Canada?"

    one_turn = [{"role": "user", "content": final_q}]

    five_turn = [
        {"role": "user", "content": "Hi, I'm planning a trip across Canada."},
        {"role": "assistant", "content": "Nice! Which provinces are you thinking of visiting?"},
        {"role": "user", "content": "Mainly Quebec and Ontario, with a stop in the Maritimes."},
        {"role": "assistant", "content": "Great picks. Are you flying in or driving?"},
        {"role": "user", "content": final_q},
    ]

    r1 = client.chat.completions.create(model=MODEL, messages=one_turn)
    r5 = client.chat.completions.create(model=MODEL, messages=five_turn)

    print(f"1-turn response: {r1.choices[0].message.content.strip()}")
    print(f"5-turn response: {r5.choices[0].message.content.strip()}")
    print()
    print(f"1-turn prompt_tokens: {r1.usage.prompt_tokens}")
    print(f"5-turn prompt_tokens: {r5.usage.prompt_tokens}")
    print(f"delta:                {r5.usage.prompt_tokens - r1.usage.prompt_tokens}")
    print()
    print("Why the delta exists: the server holds NO conversation state between")
    print("requests. The 5-turn call's prompt_tokens is higher only because the")
    print("client re-sent the full history in the request body. Each call is")
    print("evaluated from scratch — history is the client's responsibility.")


if __name__ == "__main__":
    round_trip()
    stateless_proof()
