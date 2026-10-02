"""
One model residency shared by every entry point (SPEC S1.2, S1.3, S1.14; issue #89).

The chat app's WebSocket (S1.4), the OpenAI-compatible API (S1.10) and the Ollama-compatible API
(S1.15) all take their engine from the one registry, so a model is loaded once, `/api/ps` shows
what is really in memory, and a model switch from one API waits for a generation running in
another. A fake engine and a fake store stand in for the model, so nothing is loaded or downloaded.
"""
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server, settings
from api.src.main.models.base import BaseModel
from api.src.main.registry import Registry
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore


@pytest.fixture
def setup(monkeypatch):
    engines = []
    loads = []

    def loader(hf_id):
        loads.append(hf_id)
        engine = FakeEngine(hf_id, ["Hello", " there", "!"])
        engines.append(engine)
        return engine

    store = FakeStore(QWEN, SMOL)
    reg = Registry(loader=loader, store=store)
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as client:
        yield dict(client=client, engines=engines, loads=loads, store=store, registry=reg)
    reg.unload()


def ws_chat(client, session_id, prompt="Hi"):
    with client.websocket_connect("/api/chat/streaming") as ws:
        ws.send_text(json.dumps({"session_id": session_id}))
        ws.send_text(json.dumps([]))
        ws.send_text(prompt)
        frames = []
        while (frame := ws.receive_text()) != "<EOS>":
            frames.append(frame)
    return "".join(frames)


def new_session(client, model_id="qwen3"):
    return client.post(f"/api/models/{model_id}/sessions/").json()["session_id"]


def openai_chat(client, model="qwen3"):
    return client.post("/v1/chat/completions", json={
        "model": model, "messages": [{"role": "user", "content": "Hi"}]}).json()


def ollama_chat(client, model="qwen3:0.6b"):
    return client.post("/api/chat", json={
        "model": model, "stream": False, "messages": [{"role": "user", "content": "Hi"}]}).json()


def ps(client):
    return [m["model"] for m in client.get("/api/ps").json()["models"]]


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


# -- one engine for every entry point ----------------------------------------------------------------

def test_websocket_openai_and_ollama_share_one_engine(setup):
    c = setup["client"]
    assert "Hello there!" in ws_chat(c, new_session(c))
    assert openai_chat(c)["choices"][0]["message"]["content"] == "Hello there!"
    assert ollama_chat(c)["message"]["content"] == "Hello there!"

    assert setup["loads"] == [QWEN]  # loaded once, by the WebSocket
    [engine] = setup["engines"]
    assert len(engine.calls) == 3  # all three requests ran on it


def test_creating_a_session_loads_nothing_and_closing_it_keeps_the_model(setup):
    c = setup["client"]
    session_id = new_session(c)
    assert setup["loads"] == []
    assert ps(c) == []

    ws_chat(c, session_id)
    c.delete(f"/api/sessions/{session_id}")
    assert ps(c) == [QWEN]  # the registry's keep_alive owns the lifetime, not the session


def test_sessions_on_one_model_share_its_engine(setup):
    c = setup["client"]
    ws_chat(c, new_session(c))
    ws_chat(c, new_session(c, "default"))
    assert setup["loads"] == [QWEN]


def test_websocket_uses_the_model_class_and_keeps_its_system_prompt(setup):
    from api.src.main.models.qwen3.model import system_prompt

    c = setup["client"]
    ws_chat(c, new_session(c))
    sent = setup["engines"][0].calls[0]
    assert system_prompt in sent["messages"][0]["content"]
    assert sent["tools"]  # the app path keeps Gemstone's server-side tools (S1.6)
    assert sent["temperature"] == 0.6  # Qwen3's sampling default


# -- /api/ps reports every entry point's model ------------------------------------------------------

def test_ps_reports_a_model_loaded_by_the_websocket(setup):
    c = setup["client"]
    ws_chat(c, new_session(c))
    [entry] = c.get("/api/ps").json()["models"]
    assert entry["model"] == QWEN
    assert not entry["expires_at"].startswith("9999-")  # the default keep_alive (5 minutes) applies


def test_ps_reports_a_model_loaded_by_the_openai_api(setup):
    c = setup["client"]
    openai_chat(c, SMOL)
    assert ps(c) == [SMOL]


def test_openai_api_takes_keep_alive(setup):
    c = setup["client"]
    c.post("/v1/chat/completions", json={
        "model": "qwen3", "keep_alive": 0, "messages": [{"role": "user", "content": "Hi"}]})
    assert ps(c) == []


# -- switching models waits for generations in other APIs -------------------------------------------

def test_ollama_switch_waits_for_a_websocket_generation(setup):
    c = setup["client"]
    session_id = new_session(c)
    c.post("/api/chat", json={"model": "qwen3", "messages": []})  # load qwen3
    gate = setup["engines"][0].gate = threading.Event()
    result = {}

    ws = threading.Thread(target=lambda: result.update(ws=ws_chat(c, session_id)))
    ws.start()
    wait_for(lambda: setup["engines"][0].calls)

    switch = threading.Thread(target=lambda: c.post("/api/chat", json={"model": "smollm2:135m", "messages": []}))
    switch.start()
    switch.join(timeout=0.5)
    assert switch.is_alive()  # waits for the WebSocket generation on qwen3
    assert setup["loads"] == [QWEN]
    assert ps(c) == [QWEN]

    gate.set()
    ws.join(timeout=10)
    switch.join(timeout=10)
    assert "Hello there!" in result["ws"]
    assert setup["loads"] == [QWEN, SMOL]
    assert ps(c) == [SMOL]


def test_websocket_switch_waits_for_an_openai_generation(setup):
    c = setup["client"]
    session_id = new_session(c, "default")
    c.post("/api/chat", json={"model": SMOL, "messages": []})  # load smollm2 through Ollama
    gate = setup["engines"][0].gate = threading.Event()
    result = {}

    first = threading.Thread(target=lambda: result.update(openai=openai_chat(c, SMOL)))
    first.start()
    wait_for(lambda: setup["engines"][0].calls)

    ws = threading.Thread(target=lambda: result.update(ws=ws_chat(c, session_id)))
    ws.start()
    ws.join(timeout=0.5)
    assert ws.is_alive()  # waits for the OpenAI generation on smollm2
    assert setup["loads"] == [SMOL]

    gate.set()
    first.join(timeout=10)
    ws.join(timeout=10)
    assert result["openai"]["choices"][0]["message"]["content"] == "Hello there!"
    assert "Hello there!" in result["ws"]
    assert setup["loads"] == [SMOL, QWEN]
    assert ps(c) == [QWEN]


# -- the catalogue comes from the registry ----------------------------------------------------------

def test_api_models_lists_the_registry_models_in_the_app_shape(setup):
    body = setup["client"].get("/api/models").json()
    assert {"qwen3", "default"} <= set(body)
    assert body["qwen3"] == {"model_name": "Qwen 3", "model_description": "Qwen 3 0.6B"}
    assert SMOL in body  # a pulled Hugging Face model can be used too
    assert all(set(v) == {"model_name", "model_description"} for v in body.values())
    assert set(body) == {m["id"] for m in registry_module.registry.models()}


def test_v1_models_lists_the_same_models(setup):
    c = setup["client"]
    ids = {m["id"] for m in c.get("/v1/models").json()["data"]}
    assert ids == set(c.get("/api/models").json())


def test_model_list_is_gone_from_settings():
    assert not hasattr(settings, "MODEL_LIST")


# -- model classes ------------------------------------------------------------------------------------

def test_names_map_to_their_model_class():
    from api.src.main.models.qwen3 import Qwen3Model

    assert registry_module.model_class_for(QWEN) is Qwen3Model
    plain = registry_module.model_class_for(SMOL)
    assert issubclass(plain, BaseModel) and not issubclass(plain, Qwen3Model)
    assert plain.model_id == SMOL
    assert plain.supported_tools.schemas == []


def test_model_classes_are_not_singletons():
    class Plain(BaseModel):
        model_id = "fake"

    first, second = Plain(engine=object()), Plain(engine=object())
    assert first is not second and first.runtime is not second.runtime


def test_a_session_on_a_model_not_in_the_store_closes_the_socket(setup):
    from starlette.websockets import WebSocketDisconnect

    c = setup["client"]
    session_id = settings.Session(model_id="someone/not-pulled").session_id
    with pytest.raises(WebSocketDisconnect) as closed:
        ws_chat(c, session_id)
    assert closed.value.code == 1008
    assert setup["loads"] == []
