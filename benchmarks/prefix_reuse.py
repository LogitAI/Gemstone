"""
Time to first token over a multi-turn chat, with prefix sharing off and on (#211).

    uv run --extra torch python benchmarks/prefix_reuse.py [--turns 10] [--tokens 48] [--system-words 300]

The trade-off and the decision this feeds are in `docs/serving/prefix-reuse.md`. The engine keeps
prefix sharing off (SPEC S1.12); this script turns it on only for its own engine, by handing the
engine a `ContinuousBatchingConfig` with `allow_block_sharing=True`.

Loads the test model (`GEMSTONE_TEST_MODEL`, default SmolLM2-135M) from the local Hugging Face cache
only. Each run starts a fresh engine and plays one scripted chat, every turn greedy:

- `off`:       sharing off (what Gemstone does);
- `on, cold`:  sharing on, the chat played for the first time;
- `on, warm`:  sharing on, the same chat played again on the same engine.

Per turn it prints the prompt length, the tokens matched from the cache (what
`PagedAttentionCache.search_prefix_match` returned) and the time to the first streamed text. At the end it compares every reply with the `off` reply.
Measure alone on an idle machine (check `uptime` first): the numbers are meaningless under load.
"""
import argparse
import contextlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.tests.conftest import CHAT_TEMPLATE, TEST_MODEL  # noqa: E402

TURNS = [
    "Hello! What can you help me with today?",
    "Tell me about the history of Rome in a few sentences.",
    "Who was the first emperor?",
    "What happened after his death?",
    "Now tell me about the Greek city states.",
    "How were Athens and Sparta different?",
    "Which one won the Peloponnesian War?",
    "Summarise everything we talked about so far.",
    "What should I read next about this period?",
    "Thank you, that is all for today.",
]

class Matches:
    """Records the tokens each prefix match shares, by wrapping the cache's lookup."""

    def __init__(self):
        self.tokens = []
        from transformers.generation.continuous_batching.cache import PagedAttentionCache

        search = PagedAttentionCache.search_prefix_match

        def recording(cache, request_id, prompt_ids):
            matched = search(cache, request_id, prompt_ids)
            self.tokens.append(matched)
            return matched

        PagedAttentionCache.search_prefix_match = recording


@contextlib.contextmanager
def prefix_sharing(on):
    """Builds the engine's batching config with `allow_block_sharing` forced to [on]."""
    import transformers

    original = transformers.ContinuousBatchingConfig

    class Config(original):
        def __init__(self, *args, **kwargs):
            kwargs["allow_block_sharing"] = on
            super().__init__(*args, **kwargs)

    transformers.ContinuousBatchingConfig = Config
    try:
        yield
    finally:
        transformers.ContinuousBatchingConfig = original


def play(engine, matches, system, tokens, turns):
    """Plays the chat; returns (prompt tokens, matched tokens, seconds to first text, reply) per turn."""
    history = [{"role": "system", "content": system}]
    rows = []
    for prompt in turns:
        history.append({"role": "user", "content": prompt})
        rendered = engine.tokenizer.apply_chat_template(
            history, chat_template=engine.chat_template, add_generation_prompt=True, tokenize=False
        )
        prompt_tokens = len(engine.tokenizer(rendered, add_special_tokens=False)["input_ids"])
        matches.tokens.clear()
        start = time.perf_counter()
        first = None
        parts = []
        for chunk in engine(history, temperature=0, max_new_tokens=tokens):
            if first is None and chunk:
                first = time.perf_counter() - start
            parts.append(chunk)
        reply = "".join(parts)
        history.append({"role": "assistant", "content": reply})
        rows.append((prompt_tokens, sum(matches.tokens), first or float("nan"), reply))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--turns", type=int, default=len(TURNS))
    parser.add_argument("--tokens", type=int, default=48)
    parser.add_argument("--system-words", type=int, default=300, help="length of the system prompt, in words")
    args = parser.parse_args()

    from api.src.main.engine import Engine

    turns = TURNS[: args.turns]
    # A long, fixed system prompt: the part every turn shares
    system = "You are a helpful history tutor. " + " ".join(f"rule{i}" for i in range(args.system_words))
    print(f"model={TEST_MODEL} turns={len(turns)} tokens={args.tokens} system_words={args.system_words}")
    try:
        print(f"load average: {os.getloadavg()}")
    except OSError:
        pass

    matches = Matches()

    results = {}
    for on in (False, True):
        with prefix_sharing(on):
            engine = Engine(TEST_MODEL, chat_template=CHAT_TEMPLATE, local_files_only=True)
        if not engine.batching:
            print(f"batching unavailable: {engine.batching_unavailable}")
            return
        list(engine([{"role": "user", "content": "Hi"}], temperature=0, max_new_tokens=4))  # warm-up
        if on:
            results["on, cold"] = play(engine, matches, system, args.tokens, turns)
            results["on, warm"] = play(engine, matches, system, args.tokens, turns)
        else:
            results["off"] = play(engine, matches, system, args.tokens, turns)
        engine.close()

    print()
    print(f"{'turn':>4} {'prompt':>7} | " + " | ".join(f"{name:>20}" for name in results))
    print(f"{'':>4} {'tokens':>7} | " + " | ".join(f"{'matched':>8} {'TTFT s':>11}" for _ in results))
    for i in range(len(turns)):
        cells = []
        for name, rows in results.items():
            _, matched, ttft, _ = rows[i]
            cells.append(f"{matched:>8} {ttft:>11.3f}")
        print(f"{i + 1:>4} {results['off'][i][0]:>7} | " + " | ".join(cells))
    for name, rows in results.items():
        print(f"total TTFT {name:>9}: {sum(r[2] for r in rows):.3f} s")

    print()
    for name in ("on, cold", "on, warm"):
        differ = [i + 1 for i in range(len(turns)) if results[name][i][3] != results["off"][i][3]]
        print(f"replies of {name!r} that differ from 'off': {differ or 'none'}")


if __name__ == "__main__":
    main()
