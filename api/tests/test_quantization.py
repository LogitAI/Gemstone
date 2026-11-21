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
Q4_MAX_RELATIVE_RMS = 0.15  # agent proposal, not confirmed (SPEC S1.16)
Q4_MIN_TOP1_AGREEMENT = 3  # of the 4 prompts


def _next_token_logits(engine, tokenizer, texts):
    import torch

    rows = []
    with torch.no_grad():
        for text in texts:
            ids = tokenizer(text, return_tensors="pt").input_ids.to(engine.model.device)
            rows.append(engine.model(ids).logits[0, -1].float().cpu())
    return torch.stack(rows)


def test_q4_k_stays_within_the_stated_bound_of_the_float_model():
    import torch
    from transformers import AutoTokenizer
    from api.src.main.engine import Engine

    options = dict(dtype=torch.float32, device=TEST_DEVICE, batching=False, local_files_only=not ALLOW_DOWNLOAD)
    tokenizer = AutoTokenizer.from_pretrained(Q4_MODEL, local_files_only=not ALLOW_DOWNLOAD)
    reference = Engine(Q4_MODEL, **options)
    try:
        expected = _next_token_logits(reference, tokenizer, Q4_TEXTS)
    finally:
        reference.close()
    quantized = Engine(Q4_MODEL, quantization="q4_k", **options)
    try:
        assert quantized.quantization == "q4_k"
        actual = _next_token_logits(quantized, tokenizer, Q4_TEXTS)
    finally:
        quantized.close()

    relative_rms = float(((actual - expected).pow(2).mean() / expected.pow(2).mean()).sqrt())
    top1 = int((actual.argmax(-1) == expected.argmax(-1)).sum())
    measured = f"{Q4_MODEL} q4_k vs float32: next-token logit relative RMS {relative_rms:.4f}, top-1 agreement {top1}/{len(Q4_TEXTS)}"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### 4-bit quality (#67)\n\n{measured}\n\n")
    assert relative_rms <= Q4_MAX_RELATIVE_RMS, measured
    assert top1 >= Q4_MIN_TOP1_AGREEMENT, measured
