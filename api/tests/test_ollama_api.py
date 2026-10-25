"""
The Ollama-compatible API (SPEC S1.15) and its minimal model management (SPEC S1.14).

Every test except the last runs on a fake engine and a fake model store, so no model is loaded
and nothing is downloaded. The last test drives the real engine (the `engine` fixture of
`conftest.py`, SmolLM2-135M) through `/api/chat` end to end.
"""
import json
import pathlib
import re
import threading

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.registry import LocalModel, Registry, parse_keep_alive, resolve_model_name


ROOT = pathlib.Path(__file__).resolve().parents[2]
QWEN = "Qwen/Qwen3-0.6B"
SMOL = "HuggingFaceTB/SmolLM2-135M-Instruct"


class FakeTokenizer:
    """ One token per whitespace-separated word; enough to check the count fields. """

    def apply_chat_template(self, messages, tools=None, chat_template=None, add_generation_prompt=True, tokenize=False, **_):
        return " ".join(str(m.get("content", "")) for m in messages)

    def __call__(self, text, add_special_tokens=False, **_):
        return {"input_ids": text.split()}


class FakeEngine:
    def __init__(self, model_id, chunks):
        self.model_id = model_id
        self.chunks = chunks
        self.calls = []
        self.tokenizer = FakeTokenizer()
        self.chat_template = None
        self.closed = threading.Event()
        self.gate = None  # a threading.Event the generation waits on after its first chunk
        self.memory_bytes = None  # the footprint the engine reports (None: the registry estimates it)
        self.closed_engine = False  # `close` ran: the registry unloaded this engine

    def close(self):
        self.closed_engine = True

    def __call__(self, messages, tools=None, cancel=None, **kwargs):
        self.calls.append(dict(messages=messages, tools=tools, **kwargs))
        try:
            for i, chunk in enumerate(self.chunks):
                if cancel is not None and cancel.is_set():
                    return
                yield chunk
                if i == 0 and self.gate is not None:
                    self.gate.wait(timeout=10)
        finally:
            self.closed.set()


class FakeStore:
    """ Stands in for the Hugging Face cache. """

    def __init__(self, *ids):
        self.models = {i: self._entry(i) for i in ids}
        self.downloads = []
        self.files = {}  # hf_id -> [(file name, size)] that a pull reports progress for

    @staticmethod
    def _entry(hf_id):
        return LocalModel(
            hf_id=hf_id, size=1234, modified_at="2026-10-01T00:00:00Z", digest="abc123", path=None,
            family="qwen3" if "Qwen" in hf_id else "llama", context_length=40960,
            chat_template="{{ tools }} <think>",
        )

    def list(self):
        return list(self.models.values())

    def get(self, hf_id):
        return self.models.get(hf_id)

    def delete(self, hf_id):
        return self.models.pop(hf_id, None) is not None

    def download_iter(self, hf_id):
        self.downloads.append(hf_id)
        if hf_id == "nobody/does-not-exist":
            raise LookupError(f"Repository {hf_id} not found on the Hugging Face Hub.")
        for name, size in self.files.get(hf_id, []):
            yield dict(status=f"pulling {name}", digest=name, total=size, completed=0)
            yield dict(status=f"pulling {name}", digest=name, total=size, completed=size)
        self.models[hf_id] = self._entry(hf_id)

    def download(self, hf_id):
        for _ in self.download_iter(hf_id):
            pass


DEFAULT_CHUNKS = ["Hello", " there", "!"]


@pytest.fixture
def setup(monkeypatch):
    engines = {}
    script = {"chunks": DEFAULT_CHUNKS}
    loads = []

    def loader(hf_id):
        loads.append(hf_id)
        engines[hf_id] = FakeEngine(hf_id, script["chunks"])
        return engines[hf_id]

    store = FakeStore(QWEN, SMOL)
    reg = Registry(loader=loader, store=store)
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as client:
        yield dict(client=client, engines=engines, script=script, loads=loads, store=store, registry=reg)
    reg.unload()


def ndjson(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


# -- names and durations ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("qwen3", QWEN), ("default", QWEN), ("qwen3:0.6b", QWEN), ("qwen3:latest", QWEN),
    ("Qwen/Qwen3-0.6B", QWEN), ("Qwen/Qwen3-0.6B:latest", QWEN),
    ("smollm2:135m", SMOL), ("someone/some-model", "someone/some-model"),
])
def test_model_names_resolve_to_hugging_face_ids(name, expected):
    assert resolve_model_name(name) == expected


@pytest.mark.parametrize("name", ["", "llama3", "qwen3:72b", "Qwen/Qwen3-0.6B:q4_K_M"])
def test_unknown_model_names_are_rejected(name):
    with pytest.raises(LookupError):
        resolve_model_name(name)


@pytest.mark.parametrize("value, seconds", [
    (None, 300), ("5m", 300), ("1h30m", 5400), ("10s", 10), ("1.5s", 1.5), (0, 0), ("0", 0),
    (30, 30), (-1, -1), ("-1m", -60), ("250ms", 0.25),
])
def test_keep_alive_parses_durations(value, seconds):
    assert parse_keep_alive(value) == seconds


# -- /api/chat -------------------------------------------------------------------------------------

def test_chat_streams_ndjson_by_default(setup):
    r = setup["client"].post("/api/chat", json={"model": "qwen3", "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = ndjson(r)
    assert [l["done"] for l in lines] == [False] * (len(lines) - 1) + [True]
    assert "".join(l["message"]["content"] for l in lines) == "Hello there!"
    assert all(l["model"] == "qwen3" and l["message"]["role"] == "assistant" and "created_at" in l for l in lines)
    final = lines[-1]
    assert final["done_reason"] == "stop"
    assert final["prompt_eval_count"] == 1 and final["eval_count"] == 2
    for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration"):
        assert isinstance(final[key], int) and final[key] >= 0


def test_chat_non_streaming_returns_one_object(setup):
    r = setup["client"].post("/api/chat", json={
        "model": "qwen3", "stream": False, "messages": [{"role": "user", "content": "Hi"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["done"] is True and body["done_reason"] == "stop"
    assert body["message"] == {"role": "assistant", "content": "Hello there!"}


def test_chat_passes_tool_calls_through_without_running_them(setup, monkeypatch):
    from api.src.main import utils
    ran = []
    monkeypatch.setattr(utils.FunctionCallResult, "register_tools", lambda *a, **k: ran.append(a))
    setup["script"]["chunks"] = [
        "<think>", "need weather", "</think>", "\n\n",
        "<tool_call>", '\n{"name": "get_weather", ', '"arguments": {"location": "Daejeon"}}\n', "</tool_call>",
    ]
    tools = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}]
    body = {"model": "qwen3", "messages": [{"role": "user", "content": "Weather?"}], "tools": tools, "think": True}

    lines = ndjson(setup["client"].post("/api/chat", json=body))
    calls = [c for l in lines for c in l["message"].get("tool_calls", [])]
    assert calls == [{"function": {"name": "get_weather", "arguments": {"location": "Daejeon"}}}]
    assert "".join(l["message"].get("thinking", "") for l in lines) == "need weather"
    assert "<tool_call>" not in "".join(l["message"]["content"] for l in lines)

    single = setup["client"].post("/api/chat", json={**body, "stream": False}).json()
    assert single["message"]["tool_calls"] == calls
    assert single["message"]["thinking"] == "need weather"
    assert single["message"]["content"] == ""

    assert setup["engines"][QWEN].calls[0]["tools"] == tools
    assert ran == []  # Gemstone does not execute the tool


def test_chat_think_false_hides_reasoning_and_unset_keeps_it_raw(setup):
    setup["script"]["chunks"] = ["<think>", "hmm", "</think>", "\n\nYes."]
    msg = [{"role": "user", "content": "Q"}]
    hidden = setup["client"].post("/api/chat", json={"model": "qwen3", "messages": msg, "stream": False, "think": False}).json()
    assert hidden["message"] == {"role": "assistant", "content": "Yes."}
    raw = setup["client"].post("/api/chat", json={"model": "qwen3", "messages": msg, "stream": False}).json()
    assert raw["message"]["content"] == "<think>hmm</think>\n\nYes."


def test_chat_forwards_options_to_the_engine(setup):
    setup["client"].post("/api/chat", json={
        "model": "qwen3", "stream": False,
        "messages": [{"role": "system", "content": "Be brief."},
                     {"role": "user", "content": "Hi", "images": ["aGVsbG8="]}],
        "options": {"seed": 42, "temperature": 0.3, "top_p": 0.8, "top_k": 7, "min_p": 0.1,
                    "typical_p": 0.9, "repeat_penalty": 1.2, "num_predict": 64, "num_ctx": 4096},
    })
    call = setup["engines"][QWEN].calls[0]
    assert call["seed"] == 42 and call["temperature"] == 0.3 and call["top_p"] == 0.8
    assert call["top_k"] == 7 and call["min_p"] == 0.1 and call["typical_p"] == 0.9
    assert call["repeat_penalty"] == 1.2 and call["max_new_tokens"] == 64
    assert "num_ctx" not in call
    assert call["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]


def test_chat_num_predict_unset_or_negative_means_up_to_the_context(setup):
    for options in ({}, {"num_predict": -1}):
        setup["client"].post("/api/chat", json={
            "model": "qwen3", "stream": False, "messages": [{"role": "user", "content": "Hi"}], "options": options})
    assert [c["max_new_tokens"] for c in setup["engines"][QWEN].calls] == [0, 0]


def test_chat_reports_length_when_num_predict_is_reached(setup):
    body = setup["client"].post("/api/chat", json={
        "model": "qwen3", "stream": False, "messages": [{"role": "user", "content": "Hi"}],
        "options": {"num_predict": 2}}).json()
    assert body["done_reason"] == "length" and body["eval_count"] == 2


def test_chat_stop_sequences_cut_the_output(setup):
    setup["script"]["chunks"] = ["one two ", "thr", "ee four"]
    body = setup["client"].post("/api/chat", json={
        "model": "qwen3", "stream": False, "messages": [{"role": "user", "content": "count"}],
        "options": {"stop": ["three"]}}).json()
    assert body["message"]["content"] == "one two "
    assert body["done_reason"] == "stop"
    assert setup["engines"][QWEN].closed.is_set()


def test_chat_with_unknown_model_is_a_404_in_ollama_format(setup):
    for name in ("llama3", "someone/not-pulled"):
        r = setup["client"].post("/api/chat", json={"model": name, "messages": [{"role": "user", "content": "Hi"}]})
        assert r.status_code == 404
        assert set(r.json()) == {"error"} and name in r.json()["error"]
    assert setup["loads"] == []


def test_chat_with_a_bad_body_is_a_400_in_ollama_format(setup):
    r = setup["client"].post("/api/chat", content=b"not json")
    assert r.status_code == 400 and set(r.json()) == {"error"}
    r = setup["client"].post("/api/chat", json={"messages": []})
    assert r.status_code == 400 and "model" in r.json()["error"]


# -- residency -------------------------------------------------------------------------------------

def test_empty_chat_loads_the_model_and_keep_alive_zero_unloads_it(setup):
    c = setup["client"]
    loaded = c.post("/api/chat", json={"model": "qwen3", "messages": []}).json()
    assert loaded["done"] is True and loaded["done_reason"] == "load"
    assert [m["model"] for m in c.get("/api/ps").json()["models"]] == [QWEN]

    unloaded = c.post("/api/chat", json={"model": "qwen3", "messages": [], "keep_alive": 0}).json()
    assert unloaded["done_reason"] == "unload"
    assert c.get("/api/ps").json()["models"] == []


def test_keep_alive_zero_unloads_after_the_request(setup):
    c = setup["client"]
    c.post("/api/chat", json={"model": "qwen3", "stream": False, "keep_alive": 0,
                              "messages": [{"role": "user", "content": "Hi"}]})
    assert c.get("/api/ps").json()["models"] == []


def test_ps_reports_the_resident_model_and_its_expiry(setup):
    c = setup["client"]
    c.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": "10m"})
    models = c.get("/api/ps").json()["models"]
    assert len(models) == 1
    m = models[0]
    assert m["name"] == QWEN and m["model"] == QWEN and m["size"] == 1234 and m["digest"] == "abc123"
    assert re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", m["expires_at"])
    assert m["details"]["family"] == "qwen3"

    c.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": -1})
    assert c.get("/api/ps").json()["models"][0]["expires_at"].startswith("9999-")


def test_keep_alive_expiry_unloads_the_model(setup):
    setup["client"].post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": "50ms"})
    assert setup["registry"].wait_unloaded(timeout=5)
    assert setup["client"].get("/api/ps").json()["models"] == []


def test_with_a_limit_of_one_loading_a_second_model_unloads_the_first(setup):
    setup["registry"].max_loaded = 1  # several models stay resident by default (test_multi_resident.py)
    c = setup["client"]
    c.post("/api/chat", json={"model": "qwen3", "messages": []})
    c.post("/api/chat", json={"model": "smollm2:135m", "messages": []})
    assert [m["model"] for m in c.get("/api/ps").json()["models"]] == [SMOL]
    assert setup["loads"] == [QWEN, SMOL]


def test_a_model_is_not_unloaded_mid_generation(setup):
    setup["registry"].max_loaded = 1  # the second model can only load by evicting the first
    c = setup["client"]
    c.post("/api/chat", json={"model": "qwen3", "messages": []})
    setup["engines"][QWEN].gate = gate = threading.Event()
    result = {}

    def first():
        result["first"] = c.post("/api/chat", json={
            "model": "qwen3", "stream": False, "messages": [{"role": "user", "content": "Hi"}]}).json()

    t = threading.Thread(target=first)
    t.start()
    while not setup["engines"][QWEN].calls:
        pass

    second = threading.Thread(target=lambda: c.post("/api/chat", json={"model": "smollm2:135m", "messages": []}))
    second.start()
    second.join(timeout=0.5)
    assert second.is_alive()  # waiting for the generation on qwen3
    assert setup["loads"] == [QWEN]

    gate.set()
    t.join(timeout=10)
    second.join(timeout=10)
    assert result["first"]["message"]["content"] == "Hello there!"
    assert setup["loads"] == [QWEN, SMOL]


# -- /api/generate ---------------------------------------------------------------------------------

def test_generate_streams_and_uses_system_and_prompt(setup):
    lines = ndjson(setup["client"].post("/api/generate", json={
        "model": "qwen3", "prompt": "Hi", "system": "Be brief.", "options": {"seed": 1}}))
    assert "".join(l["response"] for l in lines) == "Hello there!"
    assert lines[-1]["done"] is True and lines[-1]["done_reason"] == "stop"
    call = setup["engines"][QWEN].calls[0]
    assert call["messages"] == [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]
    assert call["seed"] == 1


def test_generate_non_streaming_and_empty_prompt_loads(setup):
    c = setup["client"]
    body = c.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False}).json()
    assert body["response"] == "Hello there!" and body["done"] is True
    assert c.post("/api/generate", json={"model": "qwen3"}).json()["done_reason"] == "load"


def test_generate_raw_is_refused(setup):
    r = setup["client"].post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "raw": True})
    assert r.status_code == 400 and "raw" in r.json()["error"]


# -- model management ------------------------------------------------------------------------------

def test_tags_lists_local_models(setup):
    models = setup["client"].get("/api/tags").json()["models"]
    assert sorted(m["name"] for m in models) == sorted([QWEN, SMOL])
    m = next(m for m in models if m["name"] == QWEN)
    assert m["model"] == QWEN and m["size"] == 1234 and m["digest"] == "abc123"
    assert m["modified_at"] == "2026-10-01T00:00:00Z"
    assert m["details"]["format"] == "safetensors" and m["details"]["family"] == "qwen3"


def test_show_describes_a_local_model(setup):
    body = setup["client"].post("/api/show", json={"model": "qwen3:0.6b"}).json()
    assert body["details"]["family"] == "qwen3"
    assert body["template"] == "{{ tools }} <think>"
    assert body["model_info"]["general.architecture"] == "qwen3"
    assert body["model_info"]["qwen3.context_length"] == 40960
    assert set(body["capabilities"]) == {"completion", "tools", "thinking"}

    r = setup["client"].post("/api/show", json={"model": "someone/not-pulled"})
    assert r.status_code == 404 and set(r.json()) == {"error"}


def test_delete_removes_the_model_and_unloads_it(setup):
    c = setup["client"]
    c.post("/api/chat", json={"model": "qwen3", "messages": []})
    r = c.request("DELETE", "/api/delete", json={"model": "qwen3"})
    assert r.status_code == 200
    assert QWEN not in setup["store"].models
    assert c.get("/api/ps").json()["models"] == []
    r = c.request("DELETE", "/api/delete", json={"model": "qwen3"})
    assert r.status_code == 404 and set(r.json()) == {"error"}


def test_pull_streams_status_and_downloads(setup):
    lines = ndjson(setup["client"].post("/api/pull", json={"model": "someone/new-model"}))
    assert lines[0]["status"] == "pulling manifest"
    assert lines[-1] == {"status": "success"}
    assert setup["store"].downloads == ["someone/new-model"]
    assert "someone/new-model" in [m["name"] for m in setup["client"].get("/api/tags").json()["models"]]


def test_pull_non_streaming_and_failure(setup):
    c = setup["client"]
    assert c.post("/api/pull", json={"model": "qwen3", "stream": False}).json() == {"status": "success"}
    assert setup["store"].downloads == [QWEN]

    r = c.post("/api/pull", json={"model": "nobody/does-not-exist", "stream": False})
    assert r.status_code == 500 and "not found" in r.json()["error"]
    lines = ndjson(c.post("/api/pull", json={"model": "nobody/does-not-exist"}))
    assert "error" in lines[-1]

    r = c.post("/api/pull", json={"model": "llama3"})
    assert r.status_code == 404 and set(r.json()) == {"error"}


def test_version(setup):
    assert re.match(r"\d+\.\d+\.\d+", setup["client"].get("/api/version").json()["version"])


# -- the legacy routes are gone (SPEC S1.5) --------------------------------------------------------

def test_legacy_chat_and_hello_routes_are_gone(setup):
    c = setup["client"]
    assert c.get("/api/hello").status_code == 404
    r = c.post("/api/chat?user_prompt=Hello", headers={"Authorization": "qwen3_20260101000000"})
    assert r.status_code == 400 and set(r.json()) == {"error"}
    for route in server.app.routes:
        params = getattr(getattr(route, "dependant", None), "query_params", [])
        assert "user_prompt" not in [p.name for p in params]


def test_no_client_or_document_uses_the_legacy_routes():
    offenders = []
    kotlin = re.compile(r"/api/chat(?!/streaming)|/api/hello|\"Authorization\"")
    for path in (ROOT / "app" / "src").rglob("*.kt"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if kotlin.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")

    legacy_doc = re.compile(r"/api/hello|user_prompt|non-streaming[^\n]*/api/chat\b(?!/)|비스트리밍")
    docs = [ROOT / "README.md", ROOT / "docs" / "locale" / "README_ko.md", *(ROOT / "docs" / "guide").glob("*.html")]
    for path in docs:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if legacy_doc.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{n}")
    assert offenders == []


# -- real model (CI) -------------------------------------------------------------------------------

def test_chat_on_the_real_engine(engine, monkeypatch):
    store = FakeStore(engine.model_id)
    reg = Registry(loader=lambda hf_id: engine, store=store,
                   close=lambda engine: None)  # the session engine outlives this registry
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as client:
        body = client.post("/api/chat", json={
            "model": engine.model_id, "stream": False, "keep_alive": 0,
            "messages": [{"role": "user", "content": "The capital of France is"}],
            "options": {"temperature": 0, "num_predict": 8},
        }).json()
    assert body["done"] is True and body["done_reason"] in ("stop", "length")
    assert body["message"]["content"].strip()
    assert 0 < body["eval_count"] <= 9 and body["prompt_eval_count"] > 0
