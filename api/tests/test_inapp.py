"""
The in-app entry point (SPEC S3.1, #42; S3.2, #190): what the client's `InAppEngine` calls through
Python Multiplatform. It lists only the models stored on the device, streams a chat in the WebSocket
protocol (S1.4) ending with `<EOS>`, releases the model lease however the stream ends, and never
downloads. A fake store and engine stand in for the Hugging Face cache and the model.
"""
import pytest

from api.src.main import inapp
from api.src.main.registry import Registry
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore


@pytest.fixture
def setup():
    engines = {}

    def loader(hf_id):
        engines[hf_id] = FakeEngine(hf_id, ["Hello", " there", "!"])
        return engines[hf_id]

    store = FakeStore(QWEN)  # only Qwen3-0.6B is on the device
    return dict(registry=Registry(loader=loader, store=store, max_loaded=1), store=store, engines=engines)


def active(reg):
    return sum(m["active"] for m in reg.loaded())


def test_models_are_the_ones_stored_on_the_device(setup):
    models = inapp.models(setup["registry"])

    assert models, "a model in the local store is listed"
    assert {m["hf_id"] for m in models} == {QWEN}
    assert all(m["id"] and m["model_name"] and m["model_description"] for m in models)


def test_a_chat_streams_the_websocket_frames_and_ends_with_eos(setup):
    reg = setup["registry"]
    history = [{"role": "user", "content": "Earlier"}, {"role": "assistant", "content": "Before"}]

    frames = list(inapp.chat(QWEN, history, "Hi", registry=reg))

    assert frames[-1] == "<EOS>"
    assert "".join(frames[:-1]) == "Hello there!"
    sent = setup["engines"][QWEN].calls[0]["messages"]
    assert [m["content"] for m in sent if m["role"] in ("user", "assistant")][-3:] == ["Earlier", "Before", "Hi"]
    assert active(reg) == 0, "the lease is released after the stream ends"


def test_a_stream_closed_early_releases_the_lease(setup):
    reg = setup["registry"]
    stream = inapp.chat(QWEN, [], "Hi", registry=reg)

    assert next(stream) == "Hello"
    assert active(reg) == 1
    stream.close()  # the app stopped reading: the user cancelled
    assert active(reg) == 0


def test_a_model_not_on_the_device_is_refused_and_not_downloaded(setup):
    with pytest.raises(LookupError):
        list(inapp.chat(SMOL, [], "Hi", registry=setup["registry"]))

    assert setup["store"].downloads == []
    assert setup["engines"] == {}
