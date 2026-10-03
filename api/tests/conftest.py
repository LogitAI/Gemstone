"""
Shared fixtures for the API tests.

The tests run a real model, not a mock, so the engine is exercised end to end on whatever
`import torch` resolves to (upstream PyTorch or torchnative).

Model: `GEMSTONE_TEST_MODEL` (default `HuggingFaceTB/SmolLM2-135M`, about 270 MB). It is read from
the local Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`) and never downloaded,
unless `GEMSTONE_TEST_ALLOW_DOWNLOAD=1` is set. CI sets that flag and caches `HF_HOME`, so CI and
local runs use the same checkpoint.
"""
import os

import pytest


TEST_MODEL = os.environ.get("GEMSTONE_TEST_MODEL", "HuggingFaceTB/SmolLM2-135M")
ALLOW_DOWNLOAD = os.environ.get("GEMSTONE_TEST_ALLOW_DOWNLOAD") == "1"

# SmolLM2-135M is a base model and ships without a chat template.
CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{{ message['role'] }}: {{ message['content'] }}\n"
    "{% endfor %}"
    "{% if add_generation_prompt %}assistant:{% endif %}"
)


@pytest.fixture(scope="session")
def engine():
    import torch
    from api.src.main.engine import Engine

    try:
        return Engine(
            TEST_MODEL,
            # float32: the equality tests (batched == sequential, seeded batch-invariance) are
            # stated in float32. In the checkpoint's bfloat16, top logits tie exactly often enough
            # that a one-ulp difference between batch shapes flips the argmax.
            dtype=torch.float32,
            chat_template=CHAT_TEMPLATE,
            local_files_only=not ALLOW_DOWNLOAD,
        )
    except OSError as e:  # not in the local cache
        pytest.fail(
            f"Test model {TEST_MODEL} is not in the Hugging Face cache and downloads are off. "
            f"Set GEMSTONE_TEST_ALLOW_DOWNLOAD=1 to fetch it. ({e})"
        )


def pytest_collection_modifyitems(config, items):
    """ Mark every test that loads the real model, so it is deselected by default (see pyproject). """
    for item in items:
        if "engine" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.real_model)
