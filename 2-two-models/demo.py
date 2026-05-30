"""Post 2 — Two models locally: prove the placement pattern.

Run:
    ./start-backends.sh                        # in another shell, wait for "server is listening"
    uv run --project 2-two-models python 2-two-models/demo.py
"""

import concurrent.futures
import time

from openai import OpenAI

BACKENDS = [
    ("gpt-oss", "http://localhost:8010/v1"),
    ("qwen3.5", "http://localhost:8011/v1"),
]
API_KEY = "llama"  # any non-empty string; llama-server ignores it locally
clients = {name: OpenAI(base_url=url, api_key=API_KEY) for name, url in BACKENDS}


def inventory() -> None:
    print("=" * 60)
    print("Block 1 — /v1/models on both ports")
    print("=" * 60)
    for name, url in BACKENDS:
        ids = [m.id for m in clients[name].models.list().data]
        print(f"{url}/models -> {ids}")


def same_prompt_two_voices() -> None:
    print()
    print("=" * 60)
    print("Block 2 — same prompt, two voices")
    print("=" * 60)
    prompt = "In one sentence, what makes Ottawa Canada's capital?"
    for name, _ in BACKENDS:
        r = clients[name].chat.completions.create(
            model=name, messages=[{"role": "user", "content": prompt}]
        )
        print(f"--- {name} ---")
        print("content:", r.choices[0].message.content.strip())
        print("usage:  ", r.usage)


def concurrent_placement() -> None:
    print()
    print("=" * 60)
    print("Block 3 — 6 concurrent calls, 3 per backend")
    print("=" * 60)
    prompts = [
        ("gpt-oss", "Name three Canadian cities."),
        ("qwen3.5", "Name three Canadian cities."),
        ("gpt-oss", "What is the capital of Ontario?"),
        ("qwen3.5", "What is the capital of Quebec?"),
        ("gpt-oss", "What is the largest lake in Canada?"),
        ("qwen3.5", "What is the tallest mountain in Canada?"),
    ]

    def call(item: tuple[str, str]) -> tuple[str, float, int]:
        name, q = item
        t0 = time.perf_counter()
        r = clients[name].chat.completions.create(
            model=name, messages=[{"role": "user", "content": q}]
        )
        return name, time.perf_counter() - t0, r.usage.total_tokens

    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(call, prompts))
    t_total = time.perf_counter() - t_start

    print(f"{'backend':<10} {'latency_s':>10} {'total_tokens':>14}")
    for name, latency, tokens in results:
        print(f"{name:<10} {latency:>10.2f} {tokens:>14}")
    print(f"\nwall time for all 6 concurrent calls: {t_total:.2f}s")
    print("No OOM = the two co-located processes share unified memory cleanly.")


if __name__ == "__main__":
    inventory()
    same_prompt_two_voices()
    concurrent_placement()
