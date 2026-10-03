"""
Shared fixtures for the API tests.

The tests run a real model, not a mock, so the engine is exercised end to end on whatever
`import torch` resolves to (upstream PyTorch or torchnative).

Model: `GEMSTONE_TEST_MODEL` (default `HuggingFaceTB/SmolLM2-135M`, about 270 MB). It is read from
the local Hugging Face cache (`HF_HOME`, default `~/.cache/huggingface`) and never downloaded,
unless `GEMSTONE_TEST_ALLOW_DOWNLOAD=1` is set. CI sets that flag and caches `HF_HOME`, so CI and
local runs use the same checkpoint.

Substrate and capabilities (SPEC S1.11): `import torch` is upstream PyTorch or torchnative. On
torchnative a test that needs a capability the installed build lacks is skipped with a reason that
names the capability (markers `needs_batching`, `needs_quantization`); on upstream PyTorch nothing
is ever skipped for a capability. Tests marked `torchnative_only` are not collected on upstream.
Anything else fails loudly on either substrate.
"""
import importlib.metadata
import os

import pytest


TEST_MODEL = os.environ.get("GEMSTONE_TEST_MODEL", "HuggingFaceTB/SmolLM2-135M")
ALLOW_DOWNLOAD = os.environ.get("GEMSTONE_TEST_ALLOW_DOWNLOAD") == "1"
# Device of the real-model engines (`cpu` by default; e.g. `mps` to check Apple GPUs locally).
TEST_DEVICE = os.environ.get("GEMSTONE_TEST_DEVICE", "cpu")

# SmolLM2-135M is a base model and ships without a chat template.
CHAT_TEMPLATE = (
    "{% for message in messages %}"
    "{{ message['role'] }}: {{ message['content'] }}\n"
    "{% endfor %}"
    "{% if add_generation_prompt %}assistant:{% endif %}"
)


def substrate():
    """ `("torchnative", version)` when `import torch` is torchnative, else `("torch", torch version)`. """
    try:
        return "torchnative", importlib.metadata.version("torchnative")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        return "torch", importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return "torch", "unknown"


def is_torchnative():
    return substrate()[0] == "torchnative"


def quantization_unavailable(fmt="q8_0"):
    """ Why torchnative cannot quantise to `fmt` (None: it can). """
    try:
        from torchnative.quant import FORMATS, TorchnativeConfig  # noqa: F401
    except ImportError as e:
        return f"torchnative.quant.TorchnativeConfig is missing ({e})"
    if fmt not in FORMATS():
        return f"torchnative.quant does not list the {fmt} format"
    return None


def capability_skip_reason(capability, engine=None):
    """
    The reason to skip a test that needs `capability`, or None to run it. Always None on upstream
    PyTorch: there a missing capability is a failure, not a skip.
    """
    kind, version = substrate()
    if kind != "torchnative":
        return None
    if capability == "batching":
        if engine is not None and not engine.batching:
            return f"continuous batching unavailable on torchnative {version}: {engine.batching_unavailable}"
    elif capability == "quantization":
        why = quantization_unavailable("q8_0")
        if why:
            return f"q8_0 quantisation unavailable on torchnative {version}: {why}"
    return None


@pytest.fixture(autouse=True)
def _capabilities(request):
    """ Skip a `needs_*` test, naming the missing capability, when torchnative lacks it. """
    if not is_torchnative():
        return
    if request.node.get_closest_marker("needs_quantization"):
        reason = capability_skip_reason("quantization")
        if reason:
            pytest.skip(reason)
    if request.node.get_closest_marker("needs_batching"):
        # Batching is a property of the engine under test, so the engine is built first.
        for name in ("q8_engine", "engine"):
            if name in request.fixturenames:
                reason = capability_skip_reason("batching", request.getfixturevalue(name))
                if reason:
                    pytest.skip(reason)
                break


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
            device=TEST_DEVICE,
            chat_template=CHAT_TEMPLATE,
            local_files_only=not ALLOW_DOWNLOAD,
        )
    except OSError as e:  # not in the local cache
        pytest.fail(
            f"Test model {TEST_MODEL} is not in the Hugging Face cache and downloads are off. "
            f"Set GEMSTONE_TEST_ALLOW_DOWNLOAD=1 to fetch it. ({e})"
        )


def pytest_collection_modifyitems(config, items):
    """
    Mark every test that loads the real model, so it is deselected by default (see pyproject), and
    drop the `torchnative_only` tests when `import torch` is upstream PyTorch.
    """
    for item in items:
        if "engine" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.real_model)
    if not is_torchnative():
        dropped = [item for item in items if item.get_closest_marker("torchnative_only")]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = [item for item in items if item not in dropped]
