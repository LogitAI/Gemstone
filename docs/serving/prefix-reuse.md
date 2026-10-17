# Reusing the prefix KV across turns

Design note for #211, which replaces #37. Status: `planned`. Nothing here is built; this note
states the question, what the engine already offers, the trade-off, the measurements, and the
options.

## The problem

Every chat turn sends the whole history (SPEC S1.4). The engine tokenizes it with the chat template
and prefills all of it, so the cost of a turn grows with the length of the conversation. Ollama
keeps the KV of the previous turn and prefills only the new part. A long chat on Gemstone therefore
waits longer for its first token than the same chat on Ollama.

#37 proposed per-session caches for the GGUF and BIN backends. Those backends were removed in #84
(SPEC S1.8), so its plan does not apply. The goal does.

## What the engine already offers

The engine runs on transformers' continuous batching with a paged KV cache (SPEC S1.12, S1.13).
transformers 5.20.1 (the version in `uv.lock`) already implements prefix sharing:

- Each fully written cache block gets a content hash, chained from its parent block
  (`compute_block_hash` in `generation/continuous_batching/cache_allocators/cache_allocator.py`).
- A new request looks up the longest run of matching blocks and shares them instead of prefilling
  them again (`PagedAttentionCache.search_prefix_match`, called by the scheduler for a pending
  request). At least one token is always prefilled.
- A block that no request references any more stays cached while it is complete (has a hash), until
  the pool needs it (`CacheAllocator.release`). So the blocks of a finished turn can serve the next
  turn, as long as they have not been reclaimed.
- The switch is `ContinuousBatchingConfig.allow_block_sharing` (default `True`).

Gemstone turns it off: `allow_block_sharing=False` in `api/src/main/engine.py`, and SPEC S1.12 says
why: "so that a reply does not depend on earlier requests. Turning it on is a later, measured
decision." This note prepares that decision.

## The trade-off

**Reproducibility.** With sharing, a turn reuses KV computed by an earlier forward pass, in a
different batch, possibly with a different prefill chunking. Floating-point results can differ in
the last bits from a fresh prefill. Greedy decoding can then pick a different token where two
logits are nearly tied, and the same seed can give a different sample. Today SPEC S1.11 and S1.12
promise that a greedy reply equals transformers' `generate`, and that the same seed gives the same
tokens batched or alone (#182). Sharing would make those hold only up to these differences, or
only when the cache is cold.

**How much is shared.** The next turn's prompt is the history rendered again by the chat
template. The match covers only the leading tokens that are identical:

- The previous prompt part (system prompt, earlier turns) usually re-renders identically.
- The previous reply often does not. Qwen3's template drops earlier turns' `<think>` blocks, so the
  reply re-renders without the reasoning that was generated, and the match ends where the reply
  begins. Tool records (S1.6) are rendered from the history the client sends back, which may also
  differ from the generated text.
- Only whole pages are shared (`page_size`, 64 tokens by default in `engine.py`), so a prefix
  shorter than a page shares nothing.

So the gain is the system prompt plus earlier turns, not the whole history. For a chat of a few
short turns it may be nothing.

**Memory.** Cached blocks occupy the pool until reclaimed. They are reclaimed before new requests
are refused, so this costs no capacity, but the residency accounting (S1.14) counts the cache as
a whole either way.

## Options

1. Keep sharing off (today). Exact reproducibility; every turn prefills the whole history.
2. Turn it on everywhere. Faster long chats; the reproducibility promises of S1.11 and S1.12 become
   "up to floating-point differences when the cache is warm", which the SPEC and the tests must say.
3. Turn it on per request: off for a request with a seed or temperature 0 (which asks for
   reproducibility), on otherwise. This needs a per-request switch, which transformers does not
   offer today (the setting is per manager), so it means two managers or an upstream change.
4. Make the template keep the reply's tokens (render earlier turns with their reasoning). That
   raises the match length but changes what the model sees, so it changes replies. Not proposed.

## Measurement plan

On upstream PyTorch, SmolLM2-135M and Qwen3-0.6B, CPU, run alone after checking `uptime`, the
TypedPython MEASURING flag and the machine lock (or in CI):

- Time to first token per turn for a scripted 10-turn chat (short and long turns), with sharing
  off and on. The script lives in `benchmarks/`.
- The number of matched tokens per turn (`search_prefix_match` logs it at debug level).
- Greedy equality: the same 10-turn chat with sharing on, cold and warm, compared token by token
  with sharing off. Record how often and where the replies differ.

The decision (option 1, 2 or 3) goes to the maintainer with these numbers, and then into SPEC
S1.12.

## Results (2026-10-17)

`benchmarks/prefix_reuse.py`, CPU (Mac mini), upstream PyTorch, transformers 5.20.1, a 300-word system
prompt, 10 turns of 48 greedy tokens, sharing off, on (cold: the chat played once) and on (warm: the
same chat played again). Each model loads as the engine loads it, with no dtype given (both
checkpoints declare bfloat16). The run started at 22:43 with a 5-minute load average of 2.36, under
the MEASURING flag and the machine lock; the load during the run is mostly the run itself. Single
outliers (SmolLM2 "off" turn 10: 27 s against 9.7 s for turn 9) look like interference, so the
medians below leave turn 1 out (no turn before it to share) and are the figures to compare.

**Time to first token** (seconds; median of turns 2 to 10; total of all 10 turns):

| Model | Prompt tokens | Off | On, cold | On, warm |
|---|---|---|---|---|
| SmolLM2-135M (tests' template) | 1116 to 1689 | 6.83 (total 86.0) | 0.83 (total 24.3) | 0.26 (total 3.1) |
| Qwen3-0.6B (its own template) | 1120 to 1722 | 38.64 (total 385.9) | 1.75 (total 43.6) | 1.76 (total 17.8) |

With sharing off, the time grows with the chat (Qwen3: 32 s at turn 1, 49 s at turn 10). With it on,
from turn 2 each turn prefills only what is new: about 8 times faster on SmolLM2 and 22 times on
Qwen3. Turn 1 is the same in every mode (nothing to share; SmolLM2 "on, cold" turn 1 took 15 s, an
outlier).

**Matched tokens.** SmolLM2: turn *n* reuses the previous prompt in whole 64-token pages (1088 of
1116, then 1152 of 1184), never the previous reply. Qwen3: turn 2 reused 1152 tokens, more than the
whole previous prompt (1120), so part of the previous turn's rendered reply matched as well. Warm,
both reuse each prompt but its last partial page.

**Greedy equality.** SmolLM2: all 10 replies identical in the three modes. Qwen3: turn 1 identical,
**turn 2 different** with sharing on (cold and warm alike). From turn 3 on the histories differ, so
the later differences follow from turn 2 and are not independent. This is the reproducibility cost
above, observed: on a bfloat16 model the reused KV changes a greedy reply.

## Options, with these numbers

1. **Off (today).** Replies reproduce exactly (S1.11, S1.12, #182). Every turn prefills the whole
   history: on Qwen3-0.6B on this CPU, 32 s to the first token at turn 1, 49 s at turn 10.
2. **On.** About 1.8 s to the first token from turn 2 on Qwen3-0.6B (22 times faster). A reply can
   differ from the same request on a cold engine (observed at turn 2 on Qwen3); SPEC S1.11 and S1.12
   and the greedy and seed tests must say "on a cold cache" or be rewritten. The real-model tests
   would need sharing off, or a fresh engine per comparison.
3. **Per request.** Off for a request that asks for reproducibility (a `seed`, or temperature 0),
   on otherwise. Keeps the promises where they are asked for and gives the speed to ordinary chats.
   transformers sets sharing per manager, not per request, so this needs either two managers (two
   KV caches: double the cache memory, which the residency budget S1.14 counts) or an upstream
   change to `ContinuousBatchingManager`.

Recommendation for the decision: option 3 if the reproducibility promises stay, as an upstream
change rather than two caches; otherwise option 2 with the SPEC and tests restated. Option 1 keeps
every turn's prefill proportional to the whole history (measured above). The choice belongs to the
maintainer.
