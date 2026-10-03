"""
Offline and download failures answer with a clear, shaped error on every entry point (SPEC S1.4,
S1.10, S1.14, S1.15; #114).

A catalogue model that is not in the cache is downloaded on first use. When that fails because the
machine is offline or the download breaks, the registry raises `ModelUnavailable`, whose message
names the model and says whether it is offline or the download failed, and what to do. The WebSocket
closes with 1011 and a short reason, OpenAI answers 503 in its error shape, Ollama answers 503
`{"error": ...}`. An unknown model stays a 404 / close 1008. A fake store and loader stand in for
the Hub.
"""
import json

import httpx
import pytest
import requests
from fastapi.testclient import TestClient
from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
from starlette.websockets import WebSocketDisconnect

from api.src.main import registry as registry_module
from api.src.main import server, settings
from api.src.main.registry import ModelUnavailable, Registry
from api.tests.test_ollama_api import QWEN, FakeEngine, FakeStore

OFFLINE = [
    OfflineModeIsEnabled("Cannot reach https://huggingface.co: offline mode is enabled."),
    LocalEntryNotFoundError("An error happened while trying to locate the file on the Hub."),
    requests.ConnectionError("Max retries exceeded"),
    httpx.ConnectError("name resolution failed"),
]
BROKEN = [OSError("No space left on device"), RuntimeError("checksum mismatch")]


class EmptyStore(FakeStore):
    """ An empty cache whose download raises `error`. """

    def __init__(self, error):
        super().__init__()
        self.error = error

    def download_iter(self, hf_id):
        raise self.error
        yield

    def download(self, hf_id):
        raise self.error


def make(monkeypatch, error):
    reg = Registry(loader=lambda hf_id: FakeEngine(hf_id, ["x"]), store=EmptyStore(error))
    monkeypatch.setattr(registry_module, "registry", reg)
    return reg


@pytest.mark.parametrize("error", OFFLINE, ids=lambda e: type(e).__name__)
def test_an_offline_fetch_says_so(error):
    reg = Registry(loader=lambda i: None, store=EmptyStore(error))
    with pytest.raises(ModelUnavailable) as raised:
        reg.acquire(QWEN, fetch=True)
    message = str(raised.value)
    assert QWEN in message and "offline" in message and "download" in message
    assert "connect" in message.lower()
    assert not isinstance(raised.value, LookupError)


@pytest.mark.parametrize("error", BROKEN, ids=lambda e: type(e).__name__)
def test_a_failed_download_says_so(error):
    reg = Registry(loader=lambda i: None, store=EmptyStore(error))
    with pytest.raises(ModelUnavailable) as raised:
        reg.acquire(QWEN, fetch=True)
    message = str(raised.value)
    assert QWEN in message and "download" in message and "failed" in message
    assert "offline" not in message


def test_a_missing_repository_stays_a_lookup_error():
    reg = Registry(loader=lambda i: None, store=EmptyStore(LookupError("model 'x' not found on the Hub")))
    with pytest.raises(LookupError):
        reg.acquire(QWEN, fetch=True)


def test_a_failed_fetch_leaves_the_registry_usable(monkeypatch):
    reg = Registry(loader=lambda i: None, store=EmptyStore(OfflineModeIsEnabled("offline")))
    with pytest.raises(ModelUnavailable):
        reg.acquire(QWEN, fetch=True)
    with pytest.raises(ModelUnavailable):  # not stuck behind a "load in progress"
        reg.acquire(QWEN, fetch=True)


@pytest.mark.parametrize("error", OFFLINE + BROKEN, ids=lambda e: type(e).__name__)
def test_openai_answers_503_in_its_error_shape(monkeypatch, error):
    make(monkeypatch, error)
    with TestClient(server.app) as c:
        r = c.post("/v1/chat/completions", json={"model": "qwen3", "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "model_unavailable" and body["type"] == "server_error"
    assert "qwen3" in body["message"] or QWEN in body["message"]
    assert "offline" in body["message"] or "download" in body["message"]


def test_openai_unknown_model_stays_404(monkeypatch):
    make(monkeypatch, OfflineModeIsEnabled("offline"))
    with TestClient(server.app) as c:
        r = c.post("/v1/chat/completions", json={"model": "nope/unknown", "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 404 and r.json()["error"]["code"] == "model_not_found"


def test_ollama_chat_answers_503_with_an_error_line(monkeypatch):
    reg = make(monkeypatch, OfflineModeIsEnabled("offline"))

    def acquire(*args, **kwargs):
        raise ModelUnavailable.offline(QWEN)

    monkeypatch.setattr(reg, "acquire", acquire)
    with TestClient(server.app) as c:
        r = c.post("/api/chat", json={"model": "qwen3", "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 503
    assert QWEN in r.json()["error"] and "offline" in r.json()["error"]


@pytest.mark.parametrize("error", OFFLINE + BROKEN, ids=lambda e: type(e).__name__)
def test_ollama_pull_names_the_cause(monkeypatch, error):
    make(monkeypatch, error)
    with TestClient(server.app) as c:
        r = c.post("/api/pull", json={"model": "qwen3", "stream": False})
        streamed = c.post("/api/pull", json={"model": "qwen3"})
    assert r.status_code == 503
    assert "offline" in r.json()["error"] or "download" in r.json()["error"]
    last = json.loads(streamed.text.strip().splitlines()[-1])
    assert "offline" in last["error"] or "download" in last["error"]


def ws_open(client, model_id):
    session_id = settings.Session(model_id=model_id).session_id
    with client.websocket_connect("/api/chat/streaming") as ws:
        ws.send_text(json.dumps({"session_id": session_id}))
        ws.send_text(json.dumps([]))
        ws.send_text("Hi")
        ws.receive_text()


@pytest.mark.parametrize("error", OFFLINE + BROKEN, ids=lambda e: type(e).__name__)
def test_websocket_closes_1011_with_a_short_reason(monkeypatch, error):
    make(monkeypatch, error)
    with TestClient(server.app) as c:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws_open(c, "qwen3")
    assert closed.value.code == 1011  # not 1013: that one means "busy, retry"
    assert len(closed.value.reason.encode()) <= 123
    assert "offline" in closed.value.reason or "download" in closed.value.reason


def test_websocket_reason_fits_even_for_a_long_model_name(monkeypatch):
    reg = make(monkeypatch, OfflineModeIsEnabled("offline"))
    reg.store.models = {}
    long = "org/" + "m" * 200

    def acquire(*args, **kwargs):
        raise ModelUnavailable.offline(long)

    monkeypatch.setattr(reg, "acquire", acquire)
    with TestClient(server.app) as c:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws_open(c, "qwen3")
    assert closed.value.code == 1011 and len(closed.value.reason.encode()) <= 123


def test_websocket_unknown_model_stays_1008(monkeypatch):
    make(monkeypatch, OfflineModeIsEnabled("offline"))
    with TestClient(server.app) as c:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws_open(c, "someone/not-pulled")
    assert closed.value.code == 1008
