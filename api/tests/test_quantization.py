"""
q8_0 and q4_k weights on torchnative (SPEC S1.11, S1.16): the engine loads the model through torchnative's
`TorchnativeConfig`, streams, and its output does not depend on how requests are scheduled; q4_k stays
within the stated quality bound of the float model.
Not collected on upstream PyTorch (`torchnative_only`); skipped on a torchnative build without
q8_0 (`needs_quantization`), or, for the concurrent test, without batching (`needs_batching`).
"""
import os

import pytest

from api.tests.conftest import ALLOW_DOWNLOAD, CHAT_TEMPLATE, TEST_DEVICE, TEST_MODEL
from api.tests.test_batching import PROMPTS, generate, run_concurrently

pytestmark = [pytest.mark.real_model, pytest.mark.needs_quantization, pytest.mark.torchnative_only]


@pytest.fixture(scope="module")
def q8_engine():
    import torch
    from api.src.main.engine import Engine

    engine = Engine(
        TEST_MODEL,
        dtype=torch.float32,
        device=TEST_DEVICE,
        quantization="q8_0",
        chat_template=CHAT_TEMPLATE,
        local_files_only=not ALLOW_DOWNLOAD,
    )
    yield engine
    engine.close()


def test_q8_0_engine_streams_text_in_several_chunks(q8_engine):
    chunks = list(q8_engine(PROMPTS[0], temperature=0, max_new_tokens=16))

    assert len(chunks) > 1
    assert "".join(chunks).strip()


def test_q8_0_greedy_output_is_repeatable(q8_engine):
    assert generate(q8_engine, PROMPTS[0]) == generate(q8_engine, PROMPTS[0])


@pytest.mark.needs_batching
def test_q8_0_concurrent_greedy_requests_equal_sequential(q8_engine):
    expected = [generate(q8_engine, messages) for messages in PROMPTS[:2]]

    chunks, _ = run_concurrently(q8_engine, [(messages, {}) for messages in PROMPTS[:2]])

    assert ["".join(c) for c in chunks] == expected


# -- 4-bit (SPEC S1.16, #67) -----------------------------------------------------------------------

# Q4K packs 256 weights per block: SmolLM2-135M (hidden size 576) cannot take it, Qwen3-0.6B (1024) can.
Q4_MODEL = os.environ.get("GEMSTONE_TEST_Q4_MODEL", "Qwen/Qwen3-0.6B")
Q4_TEXTS = [m[0]["content"] for m in PROMPTS]
Q4_MIN_TOP1_AGREEMENT = 3  # of the 4 prompts
Q4_GREEDY_TOKENS = 16
# The bound (SPEC S1.16): q4_k's next-token choice agrees with the float model on 3 of 4 prompts, and q4_k
# is not further from the float model than q4_0, the 4-bit format torchnative measured as degrading
# generation (SmolLM2: tokens diverge at the 7th and loop). Both are measured in the same run on the same
# model, so the reference moves with the model and the torchnative build. q8_0 is measured as the
# near-lossless end of the scale.
# "Not further" is judged on every position of every prompt, not on the last position only: the per-position
# difference KL(q4_k) - KL(q4_0) is paired (same position, same reference), and q4_k fails when its mean is
# above zero by more than Q4_STANDARD_ERRORS standard errors. Run 44946628389 compared four last-position
# KL values (q4_k 0.237, q4_0 0.107), which cannot order two formats; torchnative's own check found its Q4_K
# equal to llama.cpp's reference and closer to the float output than q4_0 on a Qwen3-0.6B layer (fable-tn,
# 2026-11-22). Positions of one prompt are correlated, so the standard error is optimistic: the test leans
# towards failing, not towards passing.
Q4_STANDARD_ERRORS = 2.0
# Two more checks that a broken Q4_K fails (fable-tn, 2026-11-22 11:50, llama.cpp reference arithmetic on
# Qwen3-0.6B over 256 positions: q4_k top-1 agreement 0.828, q4_0 0.812; q8_0 KL about 1/40 of q4_0's in
# run 44946628389): q4_k's top-1 agreement over all positions is at least 0.75, and q8_0 is far closer to
# the float model than either 4-bit format (its mean KL below a quarter of the smaller of the two).
Q4_MIN_TOP1_ALL = 0.75
Q8_MAX_KL_FRACTION = 0.25


def _measure(engine, tokenizer, texts):
    import torch

    logits, continuations = [], []
    with torch.no_grad():
        for text in texts:
            ids = tokenizer(text, return_tensors="pt").input_ids.to(engine.model.device)
            logits.append(engine.model(ids).logits[0].float().cpu())  # every position: [tokens, vocab]
            out = engine.model.generate(ids, max_new_tokens=Q4_GREEDY_TOKENS, do_sample=False)
            continuations.append(out[0, ids.shape[1]:].tolist())
    return logits, continuations


def _distance(reference, measured):
    import torch

    (ref_logits, ref_tokens), (logits, tokens) = reference, measured
    ref_all, all_ = torch.cat(ref_logits), torch.cat(logits)
    rms = float(((all_ - ref_all).pow(2).mean() / ref_all.pow(2).mean()).sqrt())
    p = torch.log_softmax(ref_all, -1)
    kl = (p.exp() * (p - torch.log_softmax(all_, -1))).sum(-1)  # nats per position, KL(float || quantised)
    last = torch.stack([l[-1] for l in logits]).argmax(-1) == torch.stack([l[-1] for l in ref_logits]).argmax(-1)
    prefix = []
    for a, b in zip(ref_tokens, tokens):
        n = 0
        while n < min(len(a), len(b)) and a[n] == b[n]:
            n += 1
        prefix.append(n)
    return dict(
        rms=rms,
        kl=kl,
        top1=int(last.sum()),
        top1_all=float((all_.argmax(-1) == ref_all.argmax(-1)).float().mean()),
        greedy_prefix=prefix,
    )


def test_q4_k_stays_within_the_stated_bound_of_the_float_model():
    import torch
    from transformers import AutoTokenizer
    from api.src.main.engine import Engine

    options = dict(dtype=torch.float32, device=TEST_DEVICE, batching=False, local_files_only=not ALLOW_DOWNLOAD)
    tokenizer = AutoTokenizer.from_pretrained(Q4_MODEL, local_files_only=not ALLOW_DOWNLOAD)

    def measure(quantization=None):
        engine = Engine(Q4_MODEL, quantization=quantization, **options)
        try:
            assert engine.quantization == quantization
            return _measure(engine, tokenizer, Q4_TEXTS)
        finally:
            engine.close()

    reference = measure()
    results = {fmt: _distance(reference, measure(fmt)) for fmt in ("q8_0", "q4_0", "q4_k")}

    q4_k, q4_0 = results["q4_k"], results["q4_0"]
    diff = q4_k["kl"] - q4_0["kl"]  # paired, per position
    mean, se = float(diff.mean()), float(diff.std() / diff.numel() ** 0.5)

    positions = q4_k["kl"].numel()
    lines = [f"{Q4_MODEL}, float32 reference, {len(Q4_TEXTS)} prompts ({positions} positions), "
             f"{Q4_GREEDY_TOKENS} greedy tokens", "",
             "| format | logit relative RMS | mean KL (nats) | top-1, last position | top-1, all positions | greedy prefix |",
             "|---|---|---|---|---|---|"]
    for fmt, r in results.items():
        lines.append(f"| {fmt} | {r['rms']:.4f} | {float(r['kl'].mean()):.4f} | {r['top1']}/{len(Q4_TEXTS)} | "
                     f"{r['top1_all']:.3f} | {r['greedy_prefix']} |")
    lines += ["", f"KL(q4_k) - KL(q4_0), paired over {positions} positions: mean {mean:+.4f}, "
                  f"standard error {se:.4f} (bound: mean <= {Q4_STANDARD_ERRORS:g} standard errors)"]
    measured = "\n".join(lines)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 4-bit quality (#67)\n\n{measured}\n\n")
    print(measured)

    assert q4_k["top1"] >= Q4_MIN_TOP1_AGREEMENT, measured
    assert mean <= Q4_STANDARD_ERRORS * se, measured
    assert q4_k["top1_all"] >= Q4_MIN_TOP1_ALL, measured
    q8_kl = float(results["q8_0"]["kl"].mean())
    assert q8_kl <= Q8_MAX_KL_FRACTION * min(float(q4_k["kl"].mean()), float(q4_0["kl"].mean())), measured
