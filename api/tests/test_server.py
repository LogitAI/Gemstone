"""
The chat app's WebSocket protocol (SPEC S1.4) on the new engine: tokens stream and end with
<EOS>, and a client that disconnects mid-stream frees the model for the next request (#35, #36).
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from api.src.main import server, settings
from api.src.main.models.base import BaseModel
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
    model = TinyModel(engine=engine)
    monkeypatch.setattr(settings.Session, "load_model", classmethod(lambda cls, model_name: model))
    with TestClient(server.app) as c:
        yield c
    model.clean_up()


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

    assert frames[-1] == "<EOS>"
    assert "".join(frames[:-1]).strip()


def test_disconnect_mid_stream_stops_generation_and_frees_the_model(client, engine, monkeypatch):
    produced = []
    original = engine.model.generate

    def recording_generate(*args, **kwargs):
        output = original(*args, **kwargs)
        produced.append(output.shape[1] - kwargs["input_ids"].shape[1])
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
