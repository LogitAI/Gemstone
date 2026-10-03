"""
q8_0 weights on torchnative (SPEC S1.11): the engine loads the model through torchnative's
`TorchnativeConfig("q8_0")`, streams, and its output does not depend on how requests are scheduled.
Not collected on upstream PyTorch (`torchnative_only`); skipped on a torchnative build without
q8_0 (`needs_quantization`), or, for the concurrent test, without batching (`needs_batching`).
"""
import pytest

from api.tests.conftest import ALLOW_DOWNLOAD, CHAT_TEMPLATE, TEST_MODEL
from api.tests.test_batching import PROMPTS, generate, run_concurrently

pytestmark = [pytest.mark.real_model, pytest.mark.needs_quantization, pytest.mark.torchnative_only]


@pytest.fixture(scope="module")
def q8_engine():
    import torch
    from api.src.main.engine import Engine

    engine = Engine(
        TEST_MODEL,
        dtype=torch.float32,
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
