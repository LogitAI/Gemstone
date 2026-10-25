"""
The chat app's WebSocket protocol (SPEC S1.4) on the new engine: tokens stream and end with
<EOS>, and a client that disconnects mid-stream frees the model for the next request (#35, #36).
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.models.base import BaseModel
from api.src.main.registry import Registry
from api.src.main.models.config import ChatHistory
from api.src.main.utils import FunctionCalling


class TinyModel(BaseModel):
    model_id = "test"
    supported_tools = FunctionCalling.DISABLED

    def chat(self, chat_history: ChatHistory, user_prompt: str, **kwargs):
        kwargs.setdefault("temperature", 0)
        kwargs.setdefault("max_new_tokens", 16)
        return super().chat(chat_history, user_prompt, **kwargs)


@pytest.fixture
def client(engine, monkeypatch):
    from api.tests.test_ollama_api import QWEN, FakeStore

    # The session's model ("default") resolves to Qwen3; the registry loads the test engine for it
    # and wraps it in TinyModel (SPEC S1.14).
    # `close`: the session engine outlives this registry (later tests batch on it), so unloading must not close it.
    reg = Registry(loader=lambda hf_id: engine, store=FakeStore(QWEN), model_class=lambda hf_id: TinyModel,
                   close=lambda engine: None)
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as c:
        yield c
    reg.unload()


def open_chat(client, prompt="The capital of France is"):
    session_id = client.post("/api/sessions/").json()["session_id"]
    ws = client.websocket_connect("/api/chat/streaming")
    ws.__enter__()
    ws.send_text(json.dumps({"session_id": session_id}))
    ws.send_text(json.dumps([]))
    ws.send_text(prompt)
    return ws


def test_streams_tokens_then_eos(client):
    ws = open_chat(client)
    frames = []
    while True:
        frame = ws.receive_text()
        frames.append(frame)
        if frame == "<EOS>":
            break
    ws.__exit__(None, None, None)

    reply = "".join(frames[:-1])
    assert frames[-1] == "<EOS>"
    assert reply.strip()
    assert "ERROR:" not in reply, reply  # an engine failure arrives as an ERROR message, not as a reply


def test_disconnect_mid_stream_stops_generation_and_frees_the_model(client, engine, monkeypatch):
    produced = []
    original = engine.model.generate

    def recording_generate(*args, **kwargs):
        output = original(*args, **kwargs)
        sequences = getattr(output, "sequences", output)
        produced.append(sequences.shape[1] - kwargs["input_ids"].shape[1])
        return output

    monkeypatch.setattr(engine.model, "generate", recording_generate)
    monkeypatch.setattr(TinyModel, "chat", lambda self, history, prompt, **kw: BaseModel.chat(
        self, history, prompt, **{"temperature": 0, "max_new_tokens": 400, "min_new_tokens": 400, **kw}))

    ws = open_chat(client)
    ws.receive_text()
    ws.__exit__(None, None, None)  # client goes away mid-stream

    done = threading.Event()

    def second_request():
        second = open_chat(client)
        while second.receive_text() != "<EOS>":
            pass
        second.__exit__(None, None, None)
        done.set()

    threading.Thread(target=second_request, daemon=True).start()

    assert done.wait(timeout=300)
    assert produced[0] < 200  # the abandoned generation stopped well before its 400 tokens


def test_websocket_reuses_session_kv_prefix_across_turns(client, engine, monkeypatch):
    # SPEC S1.4 (#211): a WebSocket chat with a session_id keeps `past_key_values` (DynamicCache)
    # on the session, crops it to the longest common prefix with the next turn's prompt tokens, and
    # prefills only the suffix while producing the same greedy reply as a fresh generation.
    from api.src.main import settings

    prefilled = []
    original_prefill = engine.model._prefill

    def recording_prefill(input_ids, generation_config, model_kwargs, *args, **kwargs):
        cache = model_kwargs.get("past_key_values")
        past_len = cache.get_seq_length() if cache is not None else 0
        prefilled.append((past_len, input_ids.shape[1] - past_len))
        return original_prefill(input_ids, generation_config, model_kwargs, *args, **kwargs)

    monkeypatch.setattr(engine.model, "_prefill", recording_prefill)

    session_id = client.post("/api/sessions/").json()["session_id"]

    def turn(sid, history, prompt):
        with client.websocket_connect("/api/chat/streaming") as ws:
            ws.send_text(json.dumps({"session_id": sid}))
            ws.send_text(json.dumps(history))
            ws.send_text(prompt)
            frames = []
            while (frame := ws.receive_text()) != "<EOS>":
                frames.append(frame)
        return "".join(frames)

    reply1 = turn(session_id, [], "The capital of France is")
    session = settings.Session(session_id=session_id)
    assert session.kv_cache.past_key_values is not None
    assert session.kv_cache.seq_length() > 0

    history2 = [{"role": "user", "content": "The capital of France is"}, {"role": "assistant", "content": reply1}]
    reply2 = turn(session_id, history2, "The capital of Germany is")

    # Compare turn 2 with a fresh session (cold cache) given the exact same history and time prefix.
    fresh_id = client.post("/api/sessions/").json()["session_id"]
    settings.Session(session_id=fresh_id).created_prefix = session.created_prefix
    reply2_fresh = turn(fresh_id, history2, "The capital of Germany is")

    assert prefilled[0][0] == 0 and prefilled[0][1] > 0  # turn 1: cold cache, prefills all prompt tokens
    assert prefilled[1][0] > 0  # turn 2: reused the common prefix from the session's DynamicCache
    assert prefilled[1][1] < prefilled[2][1]  # turn 2 prefilled strictly fewer tokens than a cold turn 2
    assert reply2 == reply2_fresh

    # When an earlier turn is rendered differently on the next turn (e.g. a template dropping <think>),
    # the cache is cropped back to the shorter common prefix and still succeeds.
    altered_history = [
        {"role": "user", "content": "The capital of France is"},
        {"role": "assistant", "content": "Paris."},
    ]
    reply3 = turn(session_id, altered_history, "The capital of Italy is")
    assert reply3.strip() and "ERROR:" not in reply3
    assert 0 < prefilled[3][0] < session.kv_cache.seq_length()
