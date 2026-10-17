"""
Throughput of the serving engine with 1 versus N concurrent requests (SPEC S1.12).

    uv run --extra torch python benchmarks/batching_throughput.py [--concurrency 4] [--tokens 64]

Loads the test model (`GEMSTONE_TEST_MODEL`, default SmolLM2-135M) from the local Hugging Face
cache only, runs a greedy warm-up, then measures generated tokens per second:

- `sequential`: the N prompts one after another (what concurrent callers got before batching);
- `batched`:    the N prompts at once, on N threads, served by continuous batching;
- `exclusive`:  the N prompts at once on the exclusive path (`batching=False`), for reference.

A reply may stop early at EOS, so throughput counts the tokens actually generated.
Measure alone on an idle machine (check `uptime` first): the numbers are meaningless under load.
"""
import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.tests.conftest import CHAT_TEMPLATE, TEST_MODEL  # noqa: E402

PROMPTS = [
    "The capital of France is",
    "Once upon a time, there was",
    "The best way to learn Python is",
    "My favourite fruit is",
    "The history of the Roman Empire",
    "A good breakfast includes",
    "The weather today is",
    "In computer science, a hash table",
]


def count_tokens(engine, text):
    return len(engine.tokenizer(text, add_special_tokens=False)["input_ids"])


def run(engine, prompts, tokens, concurrent):
    replies = [None] * len(prompts)

    def one(i):
        replies[i] = "".join(engine([{"role": "user", "content": prompts[i]}], temperature=0, max_new_tokens=tokens))

    start = time.perf_counter()
    if concurrent:
        threads = [threading.Thread(target=one, args=(i,)) for i in range(len(prompts))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    else:
        for i in range(len(prompts)):
            one(i)
    elapsed = time.perf_counter() - start
    generated = sum(count_tokens(engine, r) for r in replies)
    return generated, elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--tokens", type=int, default=64)
    parser.add_argument("--attn", default="sdpa_paged", choices=["sdpa_paged", "eager_paged"])
    args = parser.parse_args()

    from api.src.main.engine import Engine

    prompts = (PROMPTS * ((args.concurrency // len(PROMPTS)) + 1))[: args.concurrency]
    print(f"model={TEST_MODEL} concurrency={args.concurrency} tokens={args.tokens} attn={args.attn}")
    try:
        print(f"load average: {os.getloadavg()}")
    except OSError:
        pass

    for batching, cases in [
        (True, [("1 request", False, 1), ("sequential", False, None), ("batched", True, None)]),
        (False, [("exclusive", True, None)]),
    ]:
        engine = Engine(
            TEST_MODEL, chat_template=CHAT_TEMPLATE, local_files_only=True, batching=batching,
            attn_implementation=args.attn,
        )
        if batching and not engine.batching:
            print(f"batching unavailable: {engine.batching_unavailable}")
        run(engine, prompts[:1], 8, False)  # warm-up
        for label, concurrent, limit in cases:
            generated, elapsed = run(engine, prompts[:limit], args.tokens, concurrent)
            print(f"{label:>11}: {generated:5d} tokens in {elapsed:7.2f} s = {generated / elapsed:8.1f} tokens/s")
        engine.close()


if __name__ == "__main__":
    main()
