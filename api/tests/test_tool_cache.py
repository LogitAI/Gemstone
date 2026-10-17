"""
Per-session tool-result cache (SPEC S1.6, issue #82; behaviour from PR #53 by @Mir47-47).

The server keeps each tool result in the session; the client's history only carries the placeholder
`<cached_result:<call id>>`. A later turn reads the result back through the `get_cache_data` tool.
A scripted fake engine stands in for the model, so no weights are loaded.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server, settings
from api.src.main.models.base import BaseModel
from api.src.main.models.config import ChatHistory
from api.src.main.utils import FunctionCalling, FunctionSchema
from api.src.main.utils import FunctionCallResult

PLACEHOLDER = re.compile(r"<cached_result:([^>]+)>")


def call(name, **arguments):
    """ A Qwen3-format tool call, split the way a token stream would split it. """
    return ["<tool_call>", json.dumps(dict(name=name, arguments=arguments)), "</tool_call>"]


class FakeEngine:
    """
    Turn 1 ("weather"): call `lookup`, then answer from its result.
    Turn 2 ("again"): find the placeholder in the history, call `get_cache_data` with its id, then
    answer from the returned data. Calling `lookup` again would be a failure.
    """

    def __init__(self):
        self.messages_seen = []

    def __call__(self, messages, **kwargs):
        assert "tool_call_caches" not in kwargs
        self.messages_seen.append(messages)
        last = messages[-1]
        if last["role"] == "tool":
            yield from ["Answer: ", last["content"]]
        elif "again" in last["content"]:
            ids = [m.group(1) for msg in messages for m in PLACEHOLDER.finditer(msg.get("content") or "")]
            yield from call("get_cache_data", tool_call_cache_id=ids[-1])
        else:
            yield from call("lookup", city="Seoul")


@pytest.fixture
def lookups():
    return []


@pytest.fixture
def model(lookups):
    def lookup(city):
        lookups.append(city)
        return f"{city}: 21C, clear"

    cache_schema = next(s for s in FunctionCalling.DEFAULT.schemas if s["name"] == "get_cache_data")

    class CacheModel(BaseModel):
        model_id = "fake"
        supported_tools = FunctionCalling(
            schemas=[
                FunctionSchema(
                    name="lookup", description="Look a city up",
                    parameters=dict(type="object", properties=dict(city=dict(type="string")), required=["city"]),
                ),
                cache_schema,
            ],
            implementations=dict(
                lookup=lookup,
                get_cache_data=FunctionCalling.DEFAULT.implementations["get_cache_data"],
            ),
        )

    return CacheModel(engine=FakeEngine())


def run_turn(model, history, prompt, caches):
    return "".join(model.chat(history, prompt, tool_call_caches=caches))


def test_second_turn_reads_the_first_result_through_get_cache_data(model, lookups):
    caches = {}
    server_history = ChatHistory()
    run_turn(model, server_history, "weather", caches)
    assert lookups == ["Seoul"]
    assert list(caches.values()) == ["Seoul: 21C, clear"]

    # The client got placeholders only; build its history from the tool messages it was given.
    turn2 = ChatHistory()
    for message in server_history:
        turn2.extend([dict(message, content=PLACEHOLDER_FOR(message))] if message["role"] == "tool" else [message])
    out = run_turn(model, turn2, "again", caches)

    assert lookups == ["Seoul"]  # the original tool was not called again
    assert "Seoul: 21C, clear" in out


def PLACEHOLDER_FOR(message):
    return f"<cached_result:{message['tool_call_id']}>"


def test_call_ids_are_unique_within_one_second():
    seen = []
    result = FunctionCallResult()
    result.register_tools([], {})
    for _ in range(50):
        result.stage(json.dumps(dict(name="nope", arguments={})))
        seen.append(result.job_list[-1]["id"])
    assert len(set(seen)) == 50


def test_get_cache_data_reports_a_missing_id():
    from api.src.main.utils.cache import get_cache_data

    assert "not found" in get_cache_data("call_missing", {})
    assert get_cache_data("a", {"a": "value"}) == "value"


def test_qwen3_prompt_prefers_the_cache():
    from api.src.main.models.qwen3.model import system_prompt

    assert "get_cache_data" in system_prompt


def test_cache_is_dropped_when_the_session_closes(model):
    session = settings.Session(model_id="default")
    session.tool_call_caches["call_x"] = "result"
    caches = session.tool_call_caches
    settings.Session.close(session.session_id)
    assert caches == {}
    with pytest.raises(ValueError):
        settings.Session.close(session.session_id)


def test_websocket_second_turn_uses_the_session_cache(model, lookups, monkeypatch):
    from api.tests.test_ollama_api import QWEN, FakeStore

    reg = registry_module.Registry(
        loader=lambda hf_id: model.runtime, store=FakeStore(QWEN), model_class=lambda hf_id: type(model))
    monkeypatch.setattr(registry_module, "registry", reg)

    def turn(client, session_id, history, prompt):
        with client.websocket_connect("/api/chat/streaming") as ws:
            ws.send_text(json.dumps({"session_id": session_id}))
            ws.send_text(json.dumps(history))
            ws.send_text(prompt)
            frames = []
            while (frame := ws.receive_text()) != "<EOS>":
                frames.append(frame)
        return frames

    with TestClient(server.app) as client:
        session_id = client.post("/api/sessions/").json()["session_id"]
        frames = turn(client, session_id, [], "weather")
        records = [json.loads(m) for m in re.findall(r"<tool_call>\n(\{.*?\})\n</tool_call>", "".join(frames), re.DOTALL)]
        history = next(r["history"] for r in records if "history" in r)
        assert all(PLACEHOLDER.search(m["content"]) for m in history if m["role"] == "tool")

        out = "".join(turn(client, session_id, history, "again"))
        assert lookups == ["Seoul"]
        assert "Seoul: 21C, clear" in out

        caches = settings.Session(session_id=session_id).tool_call_caches
        client.delete(f"/api/sessions/{session_id}")
        assert caches == {}


def test_sessions_created_in_the_same_second_do_not_share_a_cache():
    first = settings.Session(model_id="qwen3")
    second = settings.Session(model_id="qwen3")
    try:
        first.tool_call_caches["call_x"] = "private result"

        assert first.session_id != second.session_id
        assert "call_x" not in second.tool_call_caches
    finally:
        for s in (first, second):
            settings.Session._Session__sessions.pop(s.session_id, None)
