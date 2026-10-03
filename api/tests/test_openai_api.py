"""
The OpenAI-compatible API (SPEC S1.10): `/v1/models` and `/v1/chat/completions`, streaming and
not, with tool calls passed through to the client instead of executed.

Most tests use a scripted fake engine, so they load no model. The last test runs SmolLM2-135M
through the `engine` fixture and checks the API streams what the engine generates.
"""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.registry import Registry
from api.tests.test_ollama_api import QWEN, FakeStore


class FakeTokenizer:
    """ One token per whitespace-separated word, enough for usage and `length`. """

    def apply_chat_template(self, messages, tools=None, chat_template=None, add_generation_prompt=True, tokenize=False):
        return " ".join(str(m.get("content") or "") for m in messages)

    def encode(self, text, add_special_tokens=False):
        return text.split()


class FakeEngine:
    """ Yields scripted text chunks and records every call. """

    def __init__(self, chunks=("Hello", " there", "!")):
        self.chunks = list(chunks)
        self.calls = []
        self.tokenizer = FakeTokenizer()
        self.chat_template = None

    def __call__(self, messages, tools=None, cancel=None, **kwargs):
        self.calls.append(dict(messages=messages, tools=tools, cancel=cancel, **kwargs))
        yield from self.chunks


def serve(monkeypatch, engine):
    """ Every model name the registry is asked for loads `engine` (SPEC S1.14). """
    reg = Registry(loader=lambda hf_id: engine, store=FakeStore(QWEN),
                   close=lambda engine: None)  # the session engine outlives this registry
    monkeypatch.setattr(registry_module, "registry", reg)
    return reg


@pytest.fixture
def fake(monkeypatch):
    engine = FakeEngine()
    serve(monkeypatch, engine)
    return engine


@pytest.fixture
def client(fake):
    with TestClient(server.app) as c:
        yield c


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
    },
}

# A Qwen3-style reply: reasoning, then a tool call whose tags are split across chunks.
TOOL_CALL_CHUNKS = [
    "<think>", "\nThe user wants", " the weather.\n", "</think>", "\n\n",
    "<tool", "_call>", '\n{"name": "get_weather", ',
    '"arguments": {"city": "Seoul"}}', "\n</tool_", "call>",
]


def chat(client, **body):
    body.setdefault("model", "qwen3")
    body.setdefault("messages", [{"role": "user", "content": "Hi"}])
    return client.post("/v1/chat/completions", json=body)


def sse_events(response):
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [line[len("data: "):] for line in response.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    return [json.loads(e) for e in events[:-1]]


def joined(chunks, field):
    return "".join(c["choices"][0]["delta"].get(field) or "" for c in chunks if c["choices"])


# --- /v1/models --------------------------------------------------------------------------------

def test_models_lists_the_catalogue_in_openai_format(client):
    response = client.get("/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert {m["id"] for m in body["data"]} == {m["id"] for m in registry_module.registry.models()}
    assert {"qwen3", "default"} <= {m["id"] for m in body["data"]}
    assert all(m["object"] == "model" and "owned_by" in m and "created" in m for m in body["data"])


# --- non-streaming ------------------------------------------------------------------------------

def test_non_streaming_returns_a_chat_completion(client, fake):
    response = chat(client)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["model"] == "qwen3"
    choice = body["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"] == "Hello there!"
    assert choice["finish_reason"] == "stop"
    assert body["usage"]["completion_tokens"] == 2
    assert body["usage"]["total_tokens"] == body["usage"]["prompt_tokens"] + 2


def test_reasoning_is_moved_out_of_content(client, fake):
    fake.chunks = ["<think>", "\nPondering.\n", "</think>", "\n\n", "Answer."]

    message = chat(client).json()["choices"][0]["message"]

    assert message["content"] == "Answer."
    assert message["reasoning_content"] == "Pondering."


def test_tool_call_is_returned_not_executed(client, fake, monkeypatch):
    from api.src.main.models import base
    executed = []
    monkeypatch.setattr(base.BaseModel, "parse_tool_calling", lambda *a, **k: executed.append(a))
    fake.chunks = TOOL_CALL_CHUNKS

    body = chat(client, tools=[WEATHER_TOOL]).json()

    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    message = choice["message"]
    assert message["content"] is None
    assert message["reasoning_content"] == "The user wants the weather."
    [call] = message["tool_calls"]
    assert call["type"] == "function"
    assert call["id"].startswith("call_")
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Seoul"}
    assert fake.calls[0]["tools"] == [WEATHER_TOOL]
    assert executed == []


def test_text_around_a_tool_call_is_content(client, fake):
    fake.chunks = ["Let me check.", "<tool_call>", '{"name": "get_weather", "arguments": {"city": "Seoul"}}',
                   "</tool_call>", '<tool_call>{"name": "get_weather", "arguments": {"city": "Busan"}}</tool_call>']

    message = chat(client, tools=[WEATHER_TOOL]).json()["choices"][0]["message"]

    assert message["content"] == "Let me check."
    cities = [json.loads(c["function"]["arguments"])["city"] for c in message["tool_calls"]]
    assert cities == ["Seoul", "Busan"]
    assert len({c["id"] for c in message["tool_calls"]}) == 2


def test_tool_choice_none_sends_no_tools_and_parses_no_calls(client, fake):
    fake.chunks = ['<tool_call>{"name": "get_weather", "arguments": {}}</tool_call>']

    body = chat(client, tools=[WEATHER_TOOL], tool_choice="none").json()

    assert fake.calls[0]["tools"] is None
    assert body["choices"][0]["finish_reason"] == "stop"
    message = body["choices"][0]["message"]
    assert not message.get("tool_calls")
    assert "get_weather" in message["content"]


def test_tool_result_round_trip_reaches_the_engine(client, fake):
    messages = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Weather in Seoul?"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "Seoul"}'},
        }]},
        {"role": "tool", "tool_call_id": "call_1", "content": "Sunny, 21 C"},
    ]

    response = chat(client, messages=messages, tools=[WEATHER_TOOL])

    assert response.status_code == 200
    sent = fake.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[0]["content"] == "Be brief."
    call = sent[2]["tool_calls"][0]
    assert call["function"]["name"] == "get_weather"
    assert call["function"]["arguments"] == {"city": "Seoul"}  # chat templates expect a mapping
    assert sent[3]["tool_call_id"] == "call_1"
    assert sent[3]["content"] == "Sunny, 21 C"


def test_content_parts_are_joined(client, fake):
    chat(client, messages=[{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}])

    assert fake.calls[0]["messages"][0]["content"] == "ab"


def test_sampling_parameters_are_forwarded(client, fake):
    chat(client, temperature=0.3, top_p=0.5, max_tokens=7, seed=42)

    call = fake.calls[0]
    assert call["temperature"] == 0.3
    assert call["top_p"] == 0.5
    assert call["max_new_tokens"] == 7
    assert call["seed"] == 42
    assert isinstance(call["cancel"], threading.Event)


def test_max_completion_tokens_wins_over_max_tokens(client, fake):
    chat(client, max_tokens=7, max_completion_tokens=9)

    assert fake.calls[0]["max_new_tokens"] == 9


def test_reaching_the_token_limit_finishes_with_length(client, fake):
    fake.chunks = ["one", " two", " three"]

    body = chat(client, max_tokens=3).json()

    assert body["choices"][0]["finish_reason"] == "length"


def test_stop_sequence_ends_the_reply(client, fake):
    fake.chunks = ["Hello", " wor", "ld. END", " ignored"]

    body = chat(client, stop=["END"]).json()

    assert body["choices"][0]["message"]["content"] == "Hello world."
    assert body["choices"][0]["finish_reason"] == "stop"


# --- streaming ----------------------------------------------------------------------------------

def test_streaming_sends_chunks_then_done(client, fake):
    response = chat(client, stream=True)

    assert response.status_code == 200
    chunks = sse_events(response)
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert joined(chunks, "content") == "Hello there!"
    assert len([c for c in chunks if c["choices"] and c["choices"][0]["delta"].get("content")]) > 1
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(c["choices"][0]["finish_reason"] is None for c in chunks[:-1])


def test_streaming_tool_call_is_a_tool_call_delta(client, fake):
    fake.chunks = TOOL_CALL_CHUNKS

    chunks = sse_events(chat(client, stream=True, tools=[WEATHER_TOOL]))

    assert joined(chunks, "content") == ""
    assert joined(chunks, "reasoning_content") == "The user wants the weather."
    deltas = [d for c in chunks for d in c["choices"][0]["delta"].get("tool_calls") or []]
    [call] = deltas
    assert call["index"] == 0
    assert call["id"].startswith("call_")
    assert call["type"] == "function"
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Seoul"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_streaming_usage_chunk_when_asked(client, fake):
    chunks = sse_events(chat(client, stream=True, stream_options={"include_usage": True}))

    assert chunks[-1]["choices"] == []
    assert chunks[-1]["usage"]["completion_tokens"] == 2
    assert all("usage" not in c or c["usage"] is None for c in chunks[:-1])


def test_disconnect_mid_stream_cancels_generation(monkeypatch):
    """ Drive the ASGI app by hand: TestClient cannot disconnect in the middle of a response. """
    started = threading.Event()
    closed = threading.Event()
    seen = {}

    def endless(messages, tools=None, cancel=None, **kwargs):
        seen["cancel"] = cancel
        started.set()
        try:
            for _ in range(5000):  # about 50 s if nothing stops it
                if cancel.is_set():
                    return
                yield "tick "
                time.sleep(0.01)
        finally:
            closed.set()

    class EndlessEngine(FakeEngine):
        def __call__(self, messages, **kwargs):
            return endless(messages, **kwargs)

    engine = EndlessEngine()
    reg = serve(monkeypatch, engine)

    body = json.dumps({"model": "qwen3", "stream": True, "messages": [{"role": "user", "content": "Hi"}]}).encode()

    async def run():
        first_chunk = asyncio.Event()
        pending = [{"type": "http.request", "body": body, "more_body": False}]

        async def receive():
            if pending:
                return pending.pop(0)
            await first_chunk.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                if first_chunk.is_set():
                    raise OSError("client went away")
                first_chunk.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions", "query_string": b"", "root_path": "",
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            "client": ("testclient", 50000), "server": ("testserver", 80),
        }
        try:
            await asyncio.wait_for(server.app(scope, receive, send), timeout=30)
        except OSError:
            pass

    started_at = time.monotonic()
    asyncio.run(run())

    assert started.is_set()
    assert closed.wait(timeout=10)
    assert seen["cancel"].is_set()
    assert time.monotonic() - started_at < 20
    assert reg.loaded() and all(m["active"] == 0 for m in reg.loaded())  # the lease was released


# --- errors -------------------------------------------------------------------------------------

def test_unknown_model_is_404_in_openai_error_format(client):
    response = chat(client, model="no-such-model")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "model_not_found"
    assert "no-such-model" in error["message"]
    assert error["type"] == "invalid_request_error"


def test_malformed_request_is_400_in_openai_error_format(client):
    response = client.post("/v1/chat/completions", json={"model": "qwen3"})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


# --- a real model --------------------------------------------------------------------------------

def test_real_model_stream_equals_engine_output(engine, monkeypatch):
    serve(monkeypatch, engine)
    messages = [{"role": "user", "content": "The capital of France is"}]
    expected = "".join(engine(messages, temperature=0, max_new_tokens=16)).strip()

    with TestClient(server.app) as c:
        chunks = sse_events(chat(c, messages=messages, stream=True, temperature=0, max_tokens=16))

    assert expected
    assert joined(chunks, "content") == expected
    assert chunks[-1]["choices"][0]["finish_reason"] in ("stop", "length")
