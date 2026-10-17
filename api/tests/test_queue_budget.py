"""
Default requests batch (SPEC S1.11, S1.12; issue #111), and a request that waits for the model gives
up after a bounded time.

A fake tokenizer and a fake batcher stand in for the model, so nothing is loaded.
"""
import threading
import time

import pytest
import torch  # noqa: F401  imported up front: the exclusive path imports them, which is slow cold
import transformers  # noqa: F401

from api.src.main.engine import Engine, ModelBusy, _Batcher, _Gate
from api.src.main.registry import ModelBusy as RegistryModelBusy

CONTEXT = 40960
KV = 8192


class FakeTokenizer:
    def __init__(self, prompt_length):
        self.prompt_length = prompt_length

    def apply_chat_template(self, messages, **_):
        return "prompt"

    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(range(self.prompt_length))}


class FakeBatcher:
    def __init__(self, kv_cache_tokens=KV):
        self.kv_cache_tokens = kv_cache_tokens
        self.budgets = []

    def fits(self, total_tokens):
        return total_tokens <= self.kv_cache_tokens

    def stream(self, input_ids, max_new_tokens, params, cancel):
        self.budgets.append(max_new_tokens)
        yield "batched"


def make_engine(prompt_length=10, context=CONTEXT, batcher=True, queue_timeout=None):
    engine = Engine.__new__(Engine)
    engine.tokenizer = FakeTokenizer(prompt_length)
    engine.chat_template = None
    engine.context_length = context
    engine._gate = _Gate()
    engine._batcher = FakeBatcher() if batcher else None
    engine.queue_timeout = queue_timeout
    engine.exclusive_budgets = []

    def exclusive(input_ids, max_new_tokens, *rest):
        engine.exclusive_budgets.append(max_new_tokens)
        yield "exclusive"

    engine._generate_exclusive = exclusive
    return engine


def run(engine, **kwargs):
    return "".join(engine([{"role": "user", "content": "hi"}], **kwargs))


# --- the token budget -------------------------------------------------------------------------- #

def test_unset_budget_is_capped_to_the_kv_cache_so_the_request_batches():
    engine = make_engine(prompt_length=10)

    assert run(engine, max_new_tokens=0) == "batched"

    assert engine._batcher.budgets == [KV - 10]
    assert engine.exclusive_budgets == []


def test_unset_budget_follows_a_context_smaller_than_the_cache():
    engine = make_engine(prompt_length=10, context=4096)

    assert run(engine, max_new_tokens=-1) == "batched"

    assert engine._batcher.budgets == [4096 - 10]


def test_an_explicit_budget_larger_than_the_cache_stays_exclusive():
    engine = make_engine(prompt_length=10)

    assert run(engine, max_new_tokens=20000) == "exclusive"

    assert engine.exclusive_budgets == [20000]
    assert engine._batcher.budgets == []


def test_an_explicit_budget_that_fits_batches():
    engine = make_engine(prompt_length=10)

    assert run(engine, max_new_tokens=100) == "batched"

    assert engine._batcher.budgets == [100]


def test_a_prompt_larger_than_the_cache_runs_exclusive_up_to_the_context():
    engine = make_engine(prompt_length=KV + 100)

    assert run(engine, max_new_tokens=0) == "exclusive"

    assert engine.exclusive_budgets == [CONTEXT - KV - 100]


def test_an_unset_budget_of_a_request_the_batch_cannot_serve_is_the_whole_context():
    engine = make_engine(prompt_length=10)

    assert run(engine, max_new_tokens=0, repeat_penalty=1.1) == "exclusive"

    assert engine.exclusive_budgets == [CONTEXT - 10]


def test_an_unset_budget_without_batching_is_the_whole_context():
    engine = make_engine(prompt_length=10, batcher=False)

    assert run(engine, max_new_tokens=0) == "exclusive"

    assert engine.exclusive_budgets == [CONTEXT - 10]


def test_a_prompt_over_the_context_is_still_refused():
    engine = make_engine(prompt_length=CONTEXT + 1)

    with pytest.raises(ValueError, match="exceeds the token limit"):
        run(engine, max_new_tokens=0)


# --- the bounded wait -------------------------------------------------------------------------- #

def test_the_busy_error_is_the_registrys():
    assert ModelBusy is RegistryModelBusy


def hold(gate_context):
    """ Hold a gate context manager on a thread until released; returns (release, thread). """
    entered, release = threading.Event(), threading.Event()

    def run_holder():
        with gate_context:
            entered.set()
            release.wait(60)

    thread = threading.Thread(target=run_holder, daemon=True)
    thread.start()
    assert entered.wait(5)
    return release, thread


def test_an_exclusive_wait_gives_up_with_model_busy():
    gate = _Gate()
    release, thread = hold(gate.exclusive())
    try:
        started = time.monotonic()
        with pytest.raises(ModelBusy):
            with gate.exclusive(timeout=0.2):
                pytest.fail("the gate was taken")
        assert 0.15 <= time.monotonic() - started < 5
    finally:
        release.set()
        thread.join(5)


def test_a_shared_wait_gives_up_with_model_busy():
    gate = _Gate()
    release, thread = hold(gate.exclusive())
    try:
        with pytest.raises(ModelBusy):
            with gate.shared(lambda: None, lambda: None, timeout=0.2):
                pytest.fail("the gate was taken")
    finally:
        release.set()
        thread.join(5)


def test_a_wait_without_a_timeout_still_succeeds_when_the_gate_frees():
    gate = _Gate()
    release, thread = hold(gate.exclusive())
    threading.Timer(0.2, release.set).start()

    with gate.exclusive(timeout=5):
        pass
    thread.join(5)


def test_an_exclusive_that_gave_up_no_longer_holds_off_sharers():
    gate = _Gate()
    release, thread = hold(gate.shared(lambda: None, lambda: None))
    outcome = []

    def waiting_exclusive():
        try:
            with gate.exclusive(timeout=0.5):
                pass
        except ModelBusy:
            outcome.append("busy")

    waiter = threading.Thread(target=waiting_exclusive, daemon=True)
    waiter.start()
    time.sleep(0.1)  # the exclusive is waiting: a new sharer queues behind it
    started = time.monotonic()
    with gate.shared(lambda: None, lambda: None, timeout=5):
        waited = time.monotonic() - started
    waiter.join(5)
    release.set()
    thread.join(5)

    assert outcome == ["busy"]
    assert waited < 3


def test_a_request_that_waits_past_the_queue_timeout_is_busy():
    engine = make_engine(batcher=False, queue_timeout=0.2)
    engine._generate_exclusive = Engine._generate_exclusive.__get__(engine)
    release, thread = hold(engine._gate.exclusive())
    try:
        with pytest.raises(ModelBusy):
            run(engine, max_new_tokens=8)
    finally:
        release.set()
        thread.join(5)


def test_a_batched_request_waiting_behind_an_exclusive_one_gives_up():
    batcher = _Batcher.__new__(_Batcher)  # only its gate is used before the request joins the batch
    batcher._gate = _Gate()
    batcher.queue_timeout = 0.2
    release, thread = hold(batcher._gate.exclusive())
    try:
        with pytest.raises(ModelBusy):
            list(batcher.stream([1, 2, 3], 8, {}, None))
    finally:
        release.set()
        thread.join(5)


def test_the_queue_timeout_comes_from_the_environment(monkeypatch):
    from api.src.main.engine import queue_timeout_default

    monkeypatch.setenv("GEMSTONE_QUEUE_TIMEOUT", "90s")
    assert queue_timeout_default() == 90
    monkeypatch.delenv("GEMSTONE_QUEUE_TIMEOUT")
    assert queue_timeout_default() == 300


# --- a request that waited too long is a 503 / 1013 ------------------------------------------- #

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from api.src.main import registry as registry_module  # noqa: E402
from api.src.main import server  # noqa: E402
from api.src.main.registry import Registry  # noqa: E402
from api.tests.test_ollama_api import QWEN, FakeEngine, FakeStore  # noqa: E402


class BusyEngine(FakeEngine):
    """ An engine whose request waited past its queue timeout. """

    def __call__(self, messages, tools=None, cancel=None, **kwargs):
        raise ModelBusy("server busy: the model was not free within 1s.")
        yield  # a generator, as the engine is: the error comes at the first step


@pytest.fixture
def busy_client(monkeypatch):
    reg = Registry(loader=lambda hf_id: BusyEngine(hf_id, ["x"]), store=FakeStore(QWEN))
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as client:
        yield client
    reg.unload()


MESSAGES = [{"role": "user", "content": "Hi"}]


@pytest.mark.parametrize("stream", [False, True])
def test_ollama_answers_503_when_the_queue_timeout_passes(busy_client, stream):
    r = busy_client.post("/api/chat", json={"model": QWEN, "stream": stream, "messages": MESSAGES})

    assert r.status_code == 503 and "busy" in r.json()["error"]
    assert busy_client.get("/api/ps").json()["models"][0]["expires_at"]  # the lease was released


@pytest.mark.parametrize("stream", [False, True])
def test_openai_answers_503_when_the_queue_timeout_passes(busy_client, stream):
    r = busy_client.post("/v1/chat/completions", json={"model": QWEN, "stream": stream, "messages": MESSAGES})

    assert r.status_code == 503 and r.json()["error"]["code"] == "server_busy"


def test_the_websocket_closes_1013_when_the_queue_timeout_passes(busy_client):
    import json

    session_id = busy_client.post("/api/models/qwen3/sessions/").json()["session_id"]
    with busy_client.websocket_connect("/api/chat/streaming") as ws:
        ws.send_text(json.dumps({"session_id": session_id}))
        ws.send_text(json.dumps([]))
        ws.send_text("Hi")
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()

    assert closed.value.code == 1013
