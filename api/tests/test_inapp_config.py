"""
The in-app configuration (SPEC S3.1, #42): the app calls `inapp.configure(...)` with its own model
directory, residency limits, thread count, device and weight format, and `models()` / `chat()` use them
instead of the server's environment. A fake engine records what it was built with; the model directory is
a temp Hugging Face layout written by test_model_list's helpers; the default Hugging Face cache is a
second temp directory holding another model, so a listing that reads it is caught. No model, no network.
"""
import shutil
import uuid
from types import SimpleNamespace

import pytest

from api.src.main import inapp
from api.tests.test_model_list import ROOT, hub, cached  # noqa: F401  (hub is a fixture)
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine

QWEN_CONFIG = {"architectures": ["Qwen3ForCausalLM"], "model_type": "qwen3"}


class RecordingEngine:
    """ Stands in for `engine.Engine`: records each construction and returns a fake engine. """

    def __init__(self):
        self.built, self.engines = [], []

    def __call__(self, hf_id, **options):
        self.built.append(dict(hf_id=hf_id, **options))
        self.engines.append(FakeEngine(hf_id, ["Hi", " there"]))
        return self.engines[-1]


@pytest.fixture
def models_dir(hub):  # the default cache (`hub`) holds SmolLM2; the app's directory holds Qwen3
    cached(hub, SMOL, {"architectures": ["LlamaForCausalLM"], "model_type": "llama"})
    home = ROOT / ".tmp" / f"inapp-models-{uuid.uuid4().hex}"
    (home / "hub").mkdir(parents=True)
    cached(SimpleNamespace(cache=home / "hub"), QWEN, QWEN_CONFIG)
    yield home
    shutil.rmtree(home, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset():
    yield
    inapp.reset()


def test_models_are_read_from_the_apps_directory_only(models_dir):
    inapp.configure(models_dir=str(models_dir), engine_class=RecordingEngine())

    assert {m["hf_id"] for m in inapp.models()} == {QWEN}


def test_a_chat_loads_from_the_apps_directory_with_the_configured_options(models_dir):
    engines = RecordingEngine()
    inapp.configure(models_dir=str(models_dir), device="mps", quantization="q8_0", engine_class=engines)

    frames = list(inapp.chat(QWEN, [], "Hello"))

    assert frames[-1] == "<EOS>"
    [built] = engines.built
    assert built["hf_id"] == QWEN
    assert built["cache_dir"] == str(models_dir / "hub")
    assert built["local_files_only"] is True
    assert built["device"] == "mps"
    assert built["quantization"] == "q8_0"


def test_a_model_outside_the_apps_directory_is_refused(models_dir):
    engines = RecordingEngine()
    inapp.configure(models_dir=str(models_dir), engine_class=engines)

    with pytest.raises(LookupError):
        list(inapp.chat(SMOL, [], "Hello"))  # it is in the default cache, not in the app's directory
    assert engines.built == []


def test_residency_limits_reach_the_registry(models_dir):
    reg = inapp.configure(models_dir=str(models_dir), max_memory="3GB", max_loaded=2, engine_class=RecordingEngine())

    assert reg.max_memory == 3_000_000_000
    assert reg.max_loaded == 2


def test_threads_set_torchs_thread_count(models_dir, monkeypatch):
    import torch
    calls = []
    monkeypatch.setattr(torch, "set_num_threads", calls.append)

    inapp.configure(models_dir=str(models_dir), threads=3, engine_class=RecordingEngine())

    assert calls == [3]


@pytest.mark.parametrize("setting, value", [
    ("device", "gpu"),
    ("threads", 0),
    ("max_memory", "lots"),
    ("max_loaded", 0),
])
def test_a_malformed_value_names_the_setting_and_changes_nothing(models_dir, setting, value):
    before = inapp.configure(models_dir=str(models_dir), engine_class=RecordingEngine())

    with pytest.raises(ValueError, match=setting):
        inapp.configure(models_dir=str(models_dir), engine_class=RecordingEngine(), **{setting: value})

    assert inapp.current() is before


def test_reconfiguring_unloads_what_the_earlier_registry_loaded(models_dir):
    engines = RecordingEngine()
    inapp.configure(models_dir=str(models_dir), engine_class=engines)
    list(inapp.chat(QWEN, [], "Hello"))
    assert inapp.current().loaded(), "the model stays resident for its keep_alive after the chat"

    inapp.configure(models_dir=str(models_dir), engine_class=RecordingEngine())

    assert inapp.current().loaded() == []
    assert engines.engines[0].closed_engine, "the earlier registry's engine was unloaded"


@pytest.mark.real_model
def test_the_real_engine_chats_from_the_configured_directory(engine):
    """
    The demonstration #42 asks for, on the Python side: `configure` and `chat` drive the real
    `engine.Engine` (not a fake) on the test model, read from a configured directory with downloads off.
    The directory is `HF_HOME`, where the `engine` fixture has put the test model. Only the chat template
    is added, because SmolLM2-135M is a base model and ships without one.
    """
    import functools
    import pathlib

    from huggingface_hub import constants

    from api.src.main.engine import Engine
    from api.tests.conftest import CHAT_TEMPLATE, TEST_DEVICE, TEST_MODEL

    hub_dir = pathlib.Path(constants.HF_HUB_CACHE)
    assert hub_dir.name == "hub", f"the test needs the cache laid out as HF_HOME/hub, not {hub_dir}"
    real = functools.partial(Engine, chat_template=CHAT_TEMPLATE)
    inapp.configure(models_dir=str(hub_dir.parent), device=TEST_DEVICE, engine_class=real)

    assert TEST_MODEL in {m["hf_id"] for m in inapp.models()}
    frames = list(inapp.chat(TEST_MODEL, [], "The capital of France is"))

    assert frames[-1] == "<EOS>"
    assert "".join(frames[:-1]).strip(), "the real engine streamed some text before <EOS>"
    [resident] = inapp.current().loaded()
    assert resident["hf_id"] == TEST_MODEL
    assert resident["device"] == TEST_DEVICE, "the configured device reached the real engine"
