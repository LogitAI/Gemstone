"""
A streaming reply releases its model lease even when its body is never sent (SPEC S1.14; #93).

The OpenAI (S1.10) and Ollama (S1.15) streaming routes lease the model before they return a
`StreamingResponse`. If the client goes away before the response starts, or the response is
dropped without being sent, the body is never iterated; the lease must still be released exactly
once, so a model switch does not wait forever. The ASGI app is driven by hand (TestClient cannot
disconnect before a response starts). A fake engine and a fake store stand in for the model.
"""
import asyncio
import gc
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from api.src.main import ollama_api, openai_api, registry as registry_module, server
from api.src.main.registry import Registry
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore


SWITCH_TIMEOUT = 5.0  # seconds a model switch may take once the lease is released

PROMPT = [{"role": "user", "content": "Hi"}]
STREAM_ROUTES = {
    "openai": ("/v1/chat/completions", {"model": "qwen3", "stream": True, "messages": PROMPT}),
    "ollama-chat": ("/api/chat", {"model": "qwen3", "messages": PROMPT}),
    "ollama-generate": ("/api/generate", {"model": "qwen3", "prompt": "Hi"}),
}
ENDPOINTS = {
    "openai": openai_api.chat_completions,
    "ollama-chat": ollama_api.chat,
    "ollama-generate": ollama_api.generate,
}


@pytest.fixture
def setup(monkeypatch):
    engines = {}

    def loader(hf_id):
        engines[hf_id] = FakeEngine(hf_id, ["Hello", " there", "!"])
        return engines[hf_id]

    # One model slot: a switch to another model has to wait for the streaming model's lease.
    reg = Registry(loader=loader, store=FakeStore(QWEN, SMOL), max_loaded=1)
    released = []
    release = reg.release

    def counting_release(lease, keep_alive=None):
        released.append(lease)
        release(lease, keep_alive)

    monkeypatch.setattr(reg, "release", counting_release)
    monkeypatch.setattr(registry_module, "registry", reg)
    return dict(registry=reg, released=released, engines=engines, release=release)


def only(reg):
    """ The one resident model (`hf_id`, `active`), or None. """
    models = reg.loaded()
    assert len(models) <= 1
    return models[0] if models else None


def scope_for(path, body, spec_version):
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "client": ("testclient", 50000), "server": ("testserver", 80),
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    }


def request_messages(body):
    pending = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive():
        if pending:
            return pending.pop(0)
        return {"type": "http.disconnect"}  # the client is already gone
    return receive


async def gone_before_start_24(path, body):
    """ ASGI 2.4: the server raises OSError from `send` once the client has disconnected. """
    sent = []

    async def send(message):
        sent.append(message["type"])
        raise OSError("client went away")

    try:
        await server.app(scope_for(path, body, "2.4"), request_messages(body), send)
    except Exception:
        pass
    return sent


async def gone_before_start_23(path, body):
    """ ASGI 2.3: `http.disconnect` arrives while the response start is still being sent. """
    sent = []
    never = asyncio.Event()

    async def send(message):
        sent.append(message["type"])
        await never.wait()

    try:
        await server.app(scope_for(path, body, "2.3"), request_messages(body), send)
    except Exception:
        pass
    return sent


def assert_released_once_and_switch_completes(setup):
    reg, released = setup["registry"], setup["released"]
    gc.collect()
    deadline = time.monotonic() + SWITCH_TIMEOUT
    while (only(reg) is None or only(reg)["active"]) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert only(reg) is not None and only(reg)["hf_id"] == QWEN
    assert only(reg)["active"] == 0, "the lease on the streaming model was never released"

    switched = {}

    def switch():
        switched["lease"] = reg.acquire(SMOL)

    thread = threading.Thread(target=switch, daemon=True)  # a leaked lease blocks it forever
    thread.start()
    thread.join(timeout=SWITCH_TIMEOUT)
    assert not thread.is_alive(), "a model switch did not complete: the lease leaked"
    setup["release"](switched["lease"])
    assert only(reg)["hf_id"] == SMOL

    gc.collect()
    assert len(released) == 1, f"the lease was released {len(released)} times, not once"


@pytest.mark.parametrize("route", list(STREAM_ROUTES))
@pytest.mark.parametrize("disconnect", [gone_before_start_24, gone_before_start_23], ids=["asgi-2.4", "asgi-2.3"])
def test_a_client_gone_before_the_response_starts_releases_the_lease(setup, route, disconnect):
    path, body = STREAM_ROUTES[route]
    sent = asyncio.run(asyncio.wait_for(disconnect(path, json.dumps(body).encode()), timeout=SWITCH_TIMEOUT))
    assert "http.response.body" not in sent  # the body was never sent
    assert_released_once_and_switch_completes(setup)


@pytest.mark.parametrize("route", list(STREAM_ROUTES))
def test_a_streaming_response_dropped_unsent_releases_the_lease(setup, route):
    """ A response that is created but never sent (a middleware drops it, say) is not a leak. """
    path, body = STREAM_ROUTES[route]
    raw = json.dumps(body).encode()

    async def build():
        request = Request(scope_for(path, raw, "2.4"), request_messages(raw))
        response = await ENDPOINTS[route](request)
        assert response.status_code == 200 and hasattr(response, "body_iterator")
        assert only(setup["registry"])["active"] == 1  # leased until the response is done with

    asyncio.run(asyncio.wait_for(build(), timeout=SWITCH_TIMEOUT))
    assert_released_once_and_switch_completes(setup)


@pytest.mark.parametrize("route", list(STREAM_ROUTES))
@pytest.mark.parametrize("spec_version", ["2.4", "2.3"])
def test_the_response_releases_as_it_ends_not_when_it_is_collected(setup, route, spec_version):
    """ Release does not wait for garbage collection: the response is still referenced here. """
    path, body = STREAM_ROUTES[route]
    raw = json.dumps(body).encode()
    reg = setup["registry"]

    async def send(message):
        if spec_version == "2.4":
            raise OSError("client went away")
        await asyncio.Event().wait()  # 2.3: blocked until the disconnect cancels it

    async def run():
        request = Request(scope_for(path, raw, spec_version), request_messages(raw))
        response = await ENDPOINTS[route](request)
        assert only(reg)["active"] == 1
        try:
            await response(scope_for(path, raw, spec_version), disconnect, send)
        except Exception:
            pass
        assert only(reg)["active"] == 0, "released only once the response was garbage-collected"
        if route == "openai":  # its first step ran before the response: that generation is closed too
            assert setup["engines"][QWEN].closed.is_set()
        assert len(setup["released"]) == 1
        return response  # keep it alive until the check above is done

    async def disconnect():
        return {"type": "http.disconnect"}

    assert asyncio.run(asyncio.wait_for(run(), timeout=SWITCH_TIMEOUT)) is not None
    assert_released_once_and_switch_completes(setup)


@pytest.mark.parametrize("route", list(STREAM_ROUTES))
@pytest.mark.parametrize("stream", [True, False], ids=["stream", "no-stream"])
def test_a_finished_reply_releases_the_lease_once(setup, route, stream):
    path, body = STREAM_ROUTES[route]
    with TestClient(server.app) as client:
        response = client.post(path, json={**body, "stream": stream})
        assert response.status_code == 200
        assert "Hello" in response.text
    assert_released_once_and_switch_completes(setup)


@pytest.mark.parametrize("route", list(STREAM_ROUTES))
def test_an_unknown_model_takes_no_lease(setup, route):
    path, body = STREAM_ROUTES[route]
    with TestClient(server.app) as client:
        assert client.post(path, json={**body, "model": "someone/not-pulled"}).status_code == 404
    assert setup["released"] == [] and only(setup["registry"]) is None


def test_a_lease_granted_after_the_request_was_cancelled_is_released(setup):
    """ The acquire helper: a request cancelled while it waits for a switch does not keep the model. """
    from api.src.main import leases

    reg = setup["registry"]
    held = reg.acquire(QWEN)  # the switch to SMOL waits for this lease

    async def run():
        waiting = asyncio.ensure_future(leases.acquire(reg, SMOL))
        await asyncio.sleep(0.1)
        waiting.cancel()
        threading.Timer(0.1, setup["release"], args=(held,)).start()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(asyncio.wait_for(run(), timeout=SWITCH_TIMEOUT))
    # asyncio's cancellation abandons the worker thread, which is granted SMOL once QWEN is free.
    deadline = time.monotonic() + SWITCH_TIMEOUT
    while not (only(reg) and only(reg)["hf_id"] == SMOL and setup["released"]) \
            and time.monotonic() < deadline:
        time.sleep(0.01)
    assert only(reg)["hf_id"] == SMOL
    assert only(reg)["active"] == 0, "the lease granted to the cancelled request leaked"
    assert len(setup["released"]) == 1
