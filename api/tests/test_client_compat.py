"""
Compatibility with stock Ollama and OpenAI clients (SPEC S1.10, S1.15; issue #118): health checks,
the version, Ollama library names, first-use fetch on the Ollama routes, and the thinking switch.

No model is loaded and nothing is downloaded: a fake engine and a fake model store.
"""
import pathlib
import re
import threading

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.engine import Engine
from api.src.main.registry import Registry, resolve_model_name
from api.src.main.version import VERSION
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore

ROOT = pathlib.Path(__file__).resolve().parents[2]
USER = [{"role": "user", "content": "Hi"}]


@pytest.fixture(autouse=True)
def no_key(monkeypatch):
    monkeypatch.delenv("GEMSTONE_API_KEY", raising=False)


def serve(monkeypatch, *cached):
    engine = FakeEngine(QWEN, ["Hello", " there", "!"])
    store = FakeStore(*cached)
    reg = Registry(loader=lambda hf_id: engine, store=store, close=lambda e: None)
    monkeypatch.setattr(registry_module, "registry", reg)
    return engine, store, reg


@pytest.fixture
def cached(monkeypatch):
    engine, store, reg = serve(monkeypatch, QWEN, SMOL)
    with TestClient(server.app) as client:
        yield dict(client=client, engine=engine, store=store)
    reg.unload()


@pytest.fixture
def fresh(monkeypatch):
    """ A fresh install: nothing in the local store. """
    engine, store, reg = serve(monkeypatch)
    with TestClient(server.app) as client:
        yield dict(client=client, engine=engine, store=store)
    reg.unload()


# -- health ----------------------------------------------------------------------------------------

def test_head_root_is_200(cached):
    r = cached["client"].head("/")
    assert r.status_code == 200 and r.content == b""


def test_head_root_and_version_need_no_key(cached, monkeypatch):
    monkeypatch.setenv("GEMSTONE_API_KEY", "s3cret")
    assert cached["client"].head("/").status_code == 200
    assert cached["client"].head("/api/version").status_code == 200
    assert cached["client"].get("/api/tags").status_code == 401


def test_get_root_still_serves_the_web_client(cached):
    assert any(getattr(r, "path", None) == "/" and "GET" in r.methods for r in server.app.routes)


# -- version ---------------------------------------------------------------------------------------

def test_version_is_the_pyproject_version_without_installing():
    declared = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert VERSION == declared


def test_api_version_is_real_semver(cached):
    body = cached["client"].get("/api/version").json()
    assert body == {"version": VERSION} and body["version"] != "0.0.0"
    assert re.fullmatch(r"\d+\.\d+\.\d+", body["version"])


# -- Ollama names ----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["qwen3", "qwen3:latest", "qwen3:0.6b", "Qwen3:0.6B", "qwen3:0.6b-fp16", "default"])
def test_ollama_names_for_the_catalogue_model(name):
    assert resolve_model_name(name) == QWEN


def test_unknown_ollama_name_says_what_exists(cached):
    r = cached["client"].post("/api/chat", json={"model": "llama3.2", "messages": USER})
    assert r.status_code == 404
    error = r.json()["error"]
    assert "llama3.2" in error and "qwen3" in error and "Org/Model" in error and "Hugging Face" in error
    assert cached["store"].downloads == []


def test_pull_of_unknown_ollama_name_is_404_with_the_same_hint(cached):
    r = cached["client"].post("/api/pull", json={"model": "llama3.2", "stream": False})
    assert r.status_code == 404 and "Org/Model" in r.json()["error"]


def test_openai_unknown_name_says_what_exists(cached):
    r = cached["client"].post("/v1/chat/completions", json={"model": "llama3.2", "messages": USER})
    assert r.status_code == 404
    assert "Org/Model" in r.json()["error"]["message"] and "qwen3" in r.json()["error"]["message"]


# -- first use -------------------------------------------------------------------------------------

@pytest.mark.parametrize("route, body", [
    ("/api/chat", {"messages": USER}),
    ("/api/generate", {"prompt": "Hi"}),
    ("/api/embed", {"input": "Hi"}),
])
@pytest.mark.parametrize("name", ["qwen3", "qwen3:latest", "qwen3:0.6b"])
def test_ollama_routes_fetch_a_catalogue_model_on_first_use(fresh, route, body, name):
    fresh["engine"].embed = lambda texts, truncate=True: [[0.0] for _ in texts]
    r = fresh["client"].post(route, json={"model": name, "stream": False, **body})
    assert r.status_code == 200, r.text
    assert fresh["store"].downloads == [QWEN]


def test_ollama_does_not_fetch_other_models(fresh):
    for name in ("someone/not-pulled", "smollm2:135m"):
        r = fresh["client"].post("/api/chat", json={"model": name, "messages": USER})
        assert r.status_code == 404
    assert fresh["store"].downloads == []


# -- thinking --------------------------------------------------------------------------------------

def ollama_chat(client, **body):
    r = client.post("/api/chat", json={"model": "qwen3", "messages": USER, "stream": False, **body})
    assert r.status_code == 200, r.text


def test_ollama_think_false_disables_thinking_in_the_template(cached):
    ollama_chat(cached["client"], think=False)
    assert cached["engine"].calls[-1]["chat_template_kwargs"] == {"enable_thinking": False}


def test_ollama_think_true_enables_it_and_unset_changes_nothing(cached):
    ollama_chat(cached["client"], think=True)
    assert cached["engine"].calls[-1]["chat_template_kwargs"] == {"enable_thinking": True}
    ollama_chat(cached["client"])
    assert "chat_template_kwargs" not in cached["engine"].calls[-1]


def test_ollama_generate_passes_think_too(cached):
    cached["client"].post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "think": False})
    assert cached["engine"].calls[-1]["chat_template_kwargs"] == {"enable_thinking": False}


def openai_chat(client, **body):
    r = client.post("/v1/chat/completions", json={"model": "qwen3", "messages": USER, **body})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("effort, expected", [("none", False), ("minimal", False), ("low", True), ("high", True)])
def test_openai_reasoning_effort_sets_enable_thinking(cached, effort, expected):
    openai_chat(cached["client"], reasoning_effort=effort)
    assert cached["engine"].calls[-1]["chat_template_kwargs"] == {"enable_thinking": expected}


def test_openai_chat_template_kwargs_pass_through_and_win(cached):
    openai_chat(cached["client"], reasoning_effort="none", chat_template_kwargs={"enable_thinking": True, "x": 1})
    assert cached["engine"].calls[-1]["chat_template_kwargs"] == {"enable_thinking": True, "x": 1}


def test_openai_without_a_reasoning_field_changes_nothing(cached):
    openai_chat(cached["client"])
    assert "chat_template_kwargs" not in cached["engine"].calls[-1]


def test_openai_bad_chat_template_kwargs_is_400(cached):
    r = cached["client"].post("/v1/chat/completions",
                              json={"model": "qwen3", "messages": USER, "chat_template_kwargs": "no"})
    assert r.status_code == 400


# -- the engine ------------------------------------------------------------------------------------

def test_engine_gives_chat_template_kwargs_to_the_template_and_stays_batchable():
    seen = {}

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            seen.update(kwargs)
            return "prompt"

        def __call__(self, text, add_special_tokens=False):
            return {"input_ids": [1, 2]}

    engine = Engine.__new__(Engine)
    engine.tokenizer, engine.chat_template, engine._batcher, engine.context_length = Tokenizer(), None, None, 100
    engine._generate_exclusive = lambda *args: iter(["ok"])
    assert list(engine(USER, max_new_tokens=5, chat_template_kwargs={"enable_thinking": False})) == ["ok"]
    assert seen["enable_thinking"] is False and seen["add_generation_prompt"] is True
