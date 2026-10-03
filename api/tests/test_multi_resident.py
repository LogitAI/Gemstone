"""
Several resident models with eviction (SPEC S1.14) and the rest of Ollama's model management
(SPEC S1.15): copy, create (derived models), delete of a derived model, embed, push, pull progress
and show details. Issue #75.

A fake engine and a fake store stand in for the model, so nothing is loaded or downloaded. The
only torch code here runs a tiny hand-made module (the embedding pooling) and reads a hand-written
safetensors header.
"""
import json
import pathlib
import shutil
import struct
import threading
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.registry import AliasStore, ModelBusy, Registry, parse_bytes
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore, ndjson


ROOT = pathlib.Path(__file__).resolve().parents[2]
THIRD = "someone/third-model"
FOURTH = "someone/fourth-model"
PROMPT = [{"role": "user", "content": "Hi"}]


def make_setup(monkeypatch, *, memory=None, estimates=None, **limits):
    """ A registry with fake engines; `memory` is what each engine reports, `estimates` the store's guess. """
    engines, loads = {}, []
    memory = memory or {}

    def loader(hf_id):
        loads.append(hf_id)
        engine = FakeEngine(hf_id, ["Hello", " there", "!"])
        engine.memory_bytes = memory.get(hf_id)
        engines.setdefault(hf_id, []).append(engine)
        return engine

    store = FakeStore(QWEN, SMOL, THIRD, FOURTH)
    for hf_id, size in (estimates or {}).items():
        store.models[hf_id].weight_bytes = size
    limits.setdefault("load_timeout", 5.0)
    reg = Registry(loader=loader, store=store, aliases=AliasStore(None), **limits)
    monkeypatch.setattr(registry_module, "registry", reg)
    return dict(registry=reg, engines=engines, loads=loads, store=store)


@pytest.fixture
def make(monkeypatch):
    made = []

    def factory(**kwargs):
        s = make_setup(monkeypatch, **kwargs)
        made.append(s)
        return s
    yield factory
    for s in made:
        s["registry"].unload()


@pytest.fixture
def client():
    with TestClient(server.app) as c:
        yield c


def resident(reg):
    return sorted(m["hf_id"] for m in reg.loaded())


def hold(reg, hf_id, keep_alive=300):
    return reg.acquire(hf_id, keep_alive)


def touch(reg, hf_id):
    reg.release(reg.acquire(hf_id))


# -- several resident models ------------------------------------------------------------------------

def test_two_models_are_resident_at_once_and_both_serve(make, client):
    s = make()
    for model in ("qwen3", "smollm2:135m", "qwen3", "smollm2:135m"):
        r = client.post("/api/chat", json={"model": model, "stream": False, "messages": PROMPT})
        assert r.json()["message"]["content"] == "Hello there!"
    assert s["loads"] == [QWEN, SMOL]  # each loaded once, neither evicted
    assert sorted(m["model"] for m in client.get("/api/ps").json()["models"]) == sorted([QWEN, SMOL])
    assert len(s["engines"][QWEN][0].calls) == 2 and len(s["engines"][SMOL][0].calls) == 2


def test_over_the_count_limit_the_least_recently_used_model_is_evicted(make):
    s = make(max_loaded=2)
    reg = s["registry"]
    touch(reg, QWEN)
    touch(reg, SMOL)
    touch(reg, QWEN)  # SMOL is now the least recently used
    touch(reg, THIRD)
    assert resident(reg) == sorted([QWEN, THIRD])
    assert s["engines"][SMOL][0].closed_engine  # an evicted engine is closed (its batch loop stopped)
    assert not s["engines"][QWEN][0].closed_engine


def test_over_the_memory_budget_the_least_recently_used_model_is_evicted(make):
    sizes = {QWEN: 100, SMOL: 100, THIRD: 100}
    s = make(max_loaded=5, max_memory=250, memory=sizes, estimates=sizes)
    reg = s["registry"]
    touch(reg, QWEN)
    touch(reg, SMOL)
    assert resident(reg) == sorted([QWEN, SMOL])
    touch(reg, THIRD)  # 300 > 250: QWEN, the least recently used, goes first
    assert resident(reg) == sorted([SMOL, THIRD])


def test_the_engine_reported_footprint_replaces_the_estimate(make):
    s = make(max_loaded=5, max_memory=250, memory={QWEN: 100, SMOL: 200}, estimates={QWEN: 100, SMOL: 50})
    reg = s["registry"]
    touch(reg, QWEN)
    touch(reg, SMOL)  # estimated 50 (fits), but reports 200 once loaded: 300 > 250
    assert resident(reg) == [SMOL]
    assert [m["size"] for m in reg.loaded()] == [200]


def test_a_model_larger_than_the_budget_still_loads_alone(make):
    s = make(max_memory=10, memory={QWEN: 100}, estimates={QWEN: 100})
    touch(s["registry"], QWEN)
    assert resident(s["registry"]) == [QWEN]


def test_a_busy_model_is_never_evicted(make):
    s = make(max_loaded=2)
    reg = s["registry"]
    busy = hold(reg, QWEN)  # least recently used, but generating
    touch(reg, SMOL)
    touch(reg, THIRD)
    assert resident(reg) == sorted([QWEN, THIRD])  # SMOL went, though QWEN is older
    assert s["engines"][QWEN][0].closed_engine is False
    reg.release(busy)


def test_when_every_model_is_busy_the_load_waits_then_proceeds_on_release(make):
    s = make(max_loaded=2)
    reg = s["registry"]
    first, second = hold(reg, QWEN), hold(reg, SMOL)
    granted = {}
    t = threading.Thread(target=lambda: granted.update(lease=reg.acquire(THIRD)), daemon=True)
    t.start()
    t.join(timeout=0.4)
    assert t.is_alive()  # waiting for a model to become idle
    assert s["loads"] == [QWEN, SMOL]

    # A model that is already resident is still served while the load waits.
    again = hold(reg, SMOL)
    reg.release(again)

    reg.release(first)
    t.join(timeout=5)
    assert not t.is_alive()
    assert s["loads"] == [QWEN, SMOL, THIRD]
    assert resident(reg) == sorted([SMOL, THIRD])  # the model released first was evicted
    reg.release(granted["lease"])
    reg.release(second)


def test_when_every_model_stays_busy_the_load_times_out(make, client):
    s = make(max_loaded=2, load_timeout=0.2)
    reg = s["registry"]
    first, second = hold(reg, QWEN), hold(reg, SMOL)
    with pytest.raises(ModelBusy):
        reg.acquire(THIRD)

    r = client.post("/api/chat", json={"model": THIRD, "stream": False, "messages": PROMPT})
    assert r.status_code == 503 and "busy" in r.json()["error"]
    r = client.post("/v1/chat/completions", json={"model": THIRD, "messages": PROMPT})
    assert r.status_code == 503 and "busy" in r.json()["error"]["message"]
    assert s["loads"] == [QWEN, SMOL]

    reg.release(first)  # a timed-out load leaves nothing behind: the next one goes through
    touch(reg, THIRD)
    assert resident(reg) == sorted([SMOL, THIRD])
    reg.release(second)


def test_keep_alive_expires_each_model_on_its_own(make, client):
    s = make()
    client.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": "50ms"})
    client.post("/api/generate", json={"model": SMOL, "prompt": "Hi", "stream": False, "keep_alive": -1})
    assert s["registry"].wait_unloaded(timeout=5, hf_id=QWEN)
    assert resident(s["registry"]) == [SMOL]


def test_keep_alive_zero_unloads_only_that_model(make, client):
    s = make()
    client.post("/api/chat", json={"model": "qwen3", "messages": []})
    client.post("/api/chat", json={"model": SMOL, "messages": []})
    client.post("/api/chat", json={"model": "qwen3", "messages": [], "keep_alive": 0})
    assert resident(s["registry"]) == [SMOL]


def test_ps_lists_every_resident_model_with_expiry_and_size(make, client):
    make(memory={QWEN: 1500, SMOL: 700})
    client.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": "10m"})
    client.post("/api/generate", json={"model": SMOL, "prompt": "Hi", "stream": False, "keep_alive": -1})
    models = client.get("/api/ps").json()["models"]
    assert [m["model"] for m in models] == [SMOL, QWEN]  # latest expiry first, as Ollama sorts
    assert models[0]["expires_at"].startswith("9999-")
    assert not models[1]["expires_at"].startswith("9999-")
    assert [m["size"] for m in models] == [700, 1500]


def test_limits_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("GEMSTONE_MAX_LOADED_MODELS", "2")
    monkeypatch.setenv("GEMSTONE_MAX_MODEL_MEMORY", "8GiB")
    monkeypatch.setenv("GEMSTONE_LOAD_TIMEOUT", "90s")
    reg = Registry(store=FakeStore())
    assert (reg.max_loaded, reg.max_memory, reg.load_timeout) == (2, 8 * 2**30, 90)


def test_default_limits(monkeypatch):
    for name in ("GEMSTONE_MAX_LOADED_MODELS", "GEMSTONE_MAX_MODEL_MEMORY", "GEMSTONE_LOAD_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    reg = Registry(store=FakeStore())
    assert (reg.max_loaded, reg.max_memory, reg.load_timeout) == (3, 0, 300)


@pytest.mark.parametrize("text, value", [
    ("0", 0), ("1024", 1024), ("1KB", 1000), ("1KiB", 1024), ("1.5GB", 1_500_000_000), ("8gib", 8 * 2**30),
])
def test_parse_bytes(text, value):
    assert parse_bytes(text) == value


@pytest.mark.parametrize("text", ["", "lots", "-1", "1XB"])
def test_parse_bytes_rejects_nonsense(text):
    with pytest.raises(ValueError):
        parse_bytes(text)


# -- copy and delete (aliases) ------------------------------------------------------------------------

def test_copy_makes_an_alias_that_shares_the_engine(make, client):
    s = make()
    assert client.post("/api/copy", json={"source": "qwen3", "destination": "my-qwen"}).status_code == 200
    tags = {m["name"]: m for m in client.get("/api/tags").json()["models"]}
    assert "my-qwen:latest" in tags
    assert tags["my-qwen:latest"]["details"]["parent_model"] == QWEN
    assert tags["my-qwen:latest"]["digest"] == "abc123"

    for model in ("qwen3", "my-qwen", "my-qwen:latest"):
        r = client.post("/api/chat", json={"model": model, "stream": False, "messages": PROMPT})
        assert r.json()["message"]["content"] == "Hello there!"
        assert r.json()["model"] == model
    assert s["loads"] == [QWEN]  # one engine for the model and its alias


def test_copy_errors(make, client):
    make()
    r = client.post("/api/copy", json={"source": "someone/not-pulled", "destination": "x"})
    assert r.status_code == 404 and set(r.json()) == {"error"}
    for bad in ("a/b", "qwen3", "smollm2:135m", "", "white space"):
        r = client.post("/api/copy", json={"source": "qwen3", "destination": bad})
        assert r.status_code == 400 and set(r.json()) == {"error"}, bad


def test_delete_removes_an_alias_but_not_the_weights(make, client):
    s = make()
    client.post("/api/copy", json={"source": "qwen3", "destination": "my-qwen"})
    assert client.request("DELETE", "/api/delete", json={"model": "my-qwen"}).status_code == 200
    assert QWEN in s["store"].models
    r = client.post("/api/chat", json={"model": "my-qwen", "stream": False, "messages": PROMPT})
    assert r.status_code == 404
    assert client.request("DELETE", "/api/delete", json={"model": "my-qwen"}).status_code == 404


def test_aliases_persist_in_a_json_file():
    directory = ROOT / ".tmp" / f"aliases-{uuid.uuid4().hex}"
    try:
        path = directory / "aliases.json"
        AliasStore(str(path)).set("my-qwen:latest", {"from": QWEN, "system": "Hi"})
        assert json.loads(path.read_text(encoding="utf-8"))["my-qwen:latest"]["from"] == QWEN
        assert AliasStore(str(path)).get("my-qwen:latest") == {"from": QWEN, "system": "Hi"}
        AliasStore(str(path)).delete("my-qwen:latest")
        assert AliasStore(str(path)).get("my-qwen:latest") is None
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_the_default_alias_file_lives_under_hf_home(monkeypatch):
    monkeypatch.delenv("GEMSTONE_ALIASES", raising=False)
    from huggingface_hub import constants
    path = pathlib.Path(AliasStore().path)
    assert path.name == "aliases.json" and path.parent.parent == pathlib.Path(constants.HF_HOME)


# -- create (derived models) --------------------------------------------------------------------------

CREATE = {"model": "terse", "from": "qwen3", "system": "Be terse.",
          "parameters": {"temperature": 0.1, "num_predict": 5, "stop": ["STOP"]}}


def test_create_derives_a_model_with_its_system_prompt_and_parameters(make, client):
    s = make()
    lines = ndjson(client.post("/api/create", json=CREATE))
    assert lines[-1] == {"status": "success"}

    client.post("/api/chat", json={"model": "terse", "stream": False, "messages": PROMPT})
    call = s["engines"][QWEN][0].calls[-1]
    assert call["messages"][0] == {"role": "system", "content": "Be terse."}
    assert call["temperature"] == 0.1 and call["max_new_tokens"] == 5

    # The request's own options and system message win over the derived model's.
    client.post("/api/chat", json={"model": "terse", "stream": False, "options": {"temperature": 0.9},
                                   "messages": [{"role": "system", "content": "Mine."}, *PROMPT]})
    call = s["engines"][QWEN][0].calls[-1]
    assert call["temperature"] == 0.9 and call["max_new_tokens"] == 5
    assert [m["content"] for m in call["messages"] if m["role"] == "system"] == ["Mine."]

    client.post("/api/generate", json={"model": "terse", "prompt": "Hi", "stream": False})
    assert s["engines"][QWEN][0].calls[-1]["messages"][0] == {"role": "system", "content": "Be terse."}
    assert s["loads"] == [QWEN]


def test_a_derived_model_stop_parameter_cuts_the_output(make, client):
    s = make()
    client.post("/api/create", json={**CREATE, "stream": False})
    s["registry"].loader = lambda hf_id: FakeEngine(hf_id, ["one ", "STOP", " two"])
    body = client.post("/api/chat", json={"model": "terse", "stream": False, "messages": PROMPT}).json()
    assert body["message"]["content"] == "one "


def test_the_openai_api_applies_a_derived_model(make, client):
    s = make()
    client.post("/api/create", json={**CREATE, "stream": False})
    r = client.post("/v1/chat/completions", json={"model": "terse", "messages": PROMPT})
    assert r.status_code == 200 and r.json()["model"] == "terse"
    call = s["engines"][QWEN][0].calls[-1]
    assert call["messages"][0] == {"role": "system", "content": "Be terse."}
    assert call["temperature"] == 0.1 and call["max_new_tokens"] == 5


def test_create_from_a_derived_model_inherits_and_overrides(make, client):
    s = make()
    client.post("/api/create", json={**CREATE, "stream": False})
    r = client.post("/api/create", json={"model": "terser", "from": "terse", "parameters": {"temperature": 0.0},
                                         "stream": False})
    assert r.status_code == 200
    client.post("/api/chat", json={"model": "terser", "stream": False, "messages": PROMPT})
    call = s["engines"][QWEN][0].calls[-1]
    assert call["messages"][0]["content"] == "Be terse."
    assert call["temperature"] == 0.0 and call["max_new_tokens"] == 5


def test_show_describes_a_derived_model(make, client):
    make()
    client.post("/api/create", json={**CREATE, "stream": False})
    body = client.post("/api/show", json={"model": "terse"}).json()
    assert body["system"] == "Be terse."
    assert "temperature 0.1" in body["parameters"] and 'stop "STOP"' in body["parameters"]
    assert body["modelfile"].startswith(f"FROM {QWEN}")
    assert 'SYSTEM """Be terse."""' in body["modelfile"]
    assert body["details"]["parent_model"] == QWEN


@pytest.mark.parametrize("extra", [
    {"template": "{{ .Prompt }}"}, {"files": {"model.gguf": "sha256:abc"}}, {"adapters": {"a": "sha256:b"}},
    {"quantize": "q4_K_M"}, {"modelfile": "FROM qwen3"}, {"messages": PROMPT},
])
def test_create_refuses_what_it_cannot_do(make, client, extra):
    make()
    r = client.post("/api/create", json={**CREATE, **extra, "stream": False})
    assert r.status_code == 501 and set(r.json()) == {"error"}
    assert client.post("/api/show", json={"model": "terse"}).status_code == 404


def test_create_errors(make, client):
    make()
    r = client.post("/api/create", json={"model": "x", "from": "someone/not-pulled", "stream": False})
    assert r.status_code == 404
    r = client.post("/api/create", json={"model": "x", "stream": False})
    assert r.status_code == 400 and "from" in r.json()["error"]
    r = client.post("/api/create", json={"model": "x", "from": "qwen3", "parameters": {"bogus": 1}, "stream": False})
    assert r.status_code == 400 and "bogus" in r.json()["error"]


# -- embeddings ---------------------------------------------------------------------------------------

class EmbeddingEngine(FakeEngine):
    def embed(self, texts, truncate=True):
        self.embedded = list(texts)
        return [[float(len(t)), 1.0] for t in texts]


def test_embed_returns_one_vector_per_input(make, client):
    s = make()
    s["registry"].loader = lambda hf_id: EmbeddingEngine(hf_id, [])
    body = client.post("/api/embed", json={"model": "qwen3", "input": ["ab", "abcd efg"]}).json()
    assert body["model"] == "qwen3"
    assert body["embeddings"] == [[2.0, 1.0], [8.0, 1.0]]
    assert body["prompt_eval_count"] == 3  # one token per word in the fake tokenizer
    assert isinstance(body["total_duration"], int) and isinstance(body["load_duration"], int)

    one = client.post("/api/embed", json={"model": "qwen3", "input": "abc"}).json()
    assert one["embeddings"] == [[3.0, 1.0]]


def test_legacy_embeddings_returns_one_vector(make, client):
    s = make()
    s["registry"].loader = lambda hf_id: EmbeddingEngine(hf_id, [])
    assert client.post("/api/embeddings", json={"model": "qwen3", "prompt": "abc"}).json() == {"embedding": [3.0, 1.0]}


def test_embed_releases_the_model(make, client):
    s = make(max_loaded=1)
    s["registry"].loader = lambda hf_id: EmbeddingEngine(hf_id, [])
    client.post("/api/embed", json={"model": "qwen3", "input": "abc", "keep_alive": 0})
    assert resident(s["registry"]) == []


def test_embed_on_an_engine_without_embeddings_is_501(make, client):
    make()
    r = client.post("/api/embed", json={"model": "qwen3", "input": "abc"})
    assert r.status_code == 501 and set(r.json()) == {"error"}
    assert client.post("/api/embed", json={"model": "nobody/missing", "input": "abc"}).status_code == 404
    assert client.post("/api/embed", json={"model": "qwen3", "input": 3}).status_code == 400


def test_engine_embed_mean_pools_the_last_hidden_state_and_normalises():
    import torch
    from types import SimpleNamespace
    from api.src.main.engine import Engine, _Gate

    class Tokenizer:
        def __call__(self, text, add_special_tokens=True, **_):
            return {"input_ids": [int(w) for w in text.split()]}

    class Base(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.table = torch.nn.Embedding(10, 2)
            with torch.no_grad():
                self.table.weight.copy_(torch.tensor([[float(i), 1.0] for i in range(10)]))

        def forward(self, input_ids=None, **_):
            return SimpleNamespace(last_hidden_state=self.table(input_ids))

    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = Base()

        @property
        def base_model(self):
            return self.model

        @property
        def device(self):
            return torch.device("cpu")

    engine = Engine.__new__(Engine)
    engine.tokenizer, engine.model, engine._gate, engine.context_length = Tokenizer(), Model(), _Gate(), 3
    [a, b] = engine.embed(["1 3", "2 4 6 8"])
    assert a == pytest.approx([2 / 5 ** 0.5, 1 / 5 ** 0.5])  # mean (2, 1), unit length
    assert b == pytest.approx([4 / 17 ** 0.5, 1 / 17 ** 0.5])  # truncated to 3 tokens: mean (4, 1)
    with pytest.raises(ValueError):
        engine.embed(["2 4 6 8"], truncate=False)


# -- push, pull progress, show ------------------------------------------------------------------------

def test_push_is_not_implemented(make, client):
    make()
    r = client.post("/api/push", json={"model": "qwen3"})
    assert r.status_code == 501 and "Hugging Face" in r.json()["error"]


def test_pull_streams_progress_per_file(make, client):
    s = make()
    s["store"].files["someone/new-model"] = [("config.json", 10), ("model.safetensors", 300)]
    lines = ndjson(client.post("/api/pull", json={"model": "someone/new-model"}))
    files = [l for l in lines if "total" in l]
    assert {l["status"] for l in files} == {"pulling config.json", "pulling model.safetensors"}
    final = {l["status"]: l for l in files}
    assert final["pulling model.safetensors"]["completed"] == 300 == final["pulling model.safetensors"]["total"]
    assert lines[-1] == {"status": "success"}


def test_hf_store_pulls_the_pattern_files_one_by_one(monkeypatch):
    import huggingface_hub
    from types import SimpleNamespace
    from api.src.main.registry import HFStore

    siblings = [SimpleNamespace(rfilename=n, size=s) for n, s in [
        ("config.json", 10), ("model.safetensors", 300), ("pytorch_model.bin", 300), ("README.md", 5)]]

    class Api:
        def model_info(self, repo_id, files_metadata=False, **_):
            assert files_metadata
            return SimpleNamespace(sha="rev1", siblings=siblings)

    fetched = []
    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download",
                        lambda repo_id, filename, revision=None, **_: fetched.append((filename, revision)))
    events = list(HFStore().download_iter("someone/model"))
    assert fetched == [("config.json", "rev1"), ("model.safetensors", "rev1")]
    assert events[-1] == {"status": "pulling model.safetensors", "digest": "model.safetensors",
                          "total": 300, "completed": 300}


def test_show_reports_parameters_and_details_of_a_hugging_face_model(make, client):
    s = make()
    info = s["store"].models[QWEN]
    info.generation = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "do_sample": True}
    info.parameter_count, info.dtype = 596_049_920, "BF16"
    body = client.post("/api/show", json={"model": "qwen3"}).json()
    assert body["parameters"].splitlines() == ["temperature 0.6", "top_k 20", "top_p 0.95"]
    assert body["details"]["parameter_size"] == "596.05M"
    assert body["details"]["quantization_level"] == "BF16"
    assert body["modelfile"].startswith(f"FROM {QWEN}")


def test_safetensors_headers_give_the_parameter_count_and_bytes():
    from api.src.main.registry import safetensors_stats

    directory = ROOT / ".tmp" / f"safetensors-{uuid.uuid4().hex}"
    try:
        directory.mkdir(parents=True)
        for name, tensors in {"a.safetensors": {"w": ("BF16", [4, 3])}, "b.safetensors": {"v": ("BF16", [5])}}.items():
            header = {k: {"dtype": d, "shape": sh, "data_offsets": [0, 0]} for k, (d, sh) in tensors.items()}
            header["__metadata__"] = {"format": "pt"}
            raw = json.dumps(header).encode()
            (directory / name).write_bytes(struct.pack("<Q", len(raw)) + raw)
        assert safetensors_stats(str(directory)) == (17, 34, "BF16")
        assert safetensors_stats(str(directory / "missing")) == (None, None, "")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def test_engine_memory_estimate_counts_weights_and_the_kv_cache():
    import torch
    from types import SimpleNamespace
    from api.src.main.engine import memory_bytes

    model = torch.nn.Linear(4, 3, bias=False).to(torch.float32)  # 12 weights x 4 bytes
    model.config = SimpleNamespace(num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2,
                                   hidden_size=8, head_dim=None)
    assert memory_bytes(model, kv_cache_tokens=0) == 48
    # 10 tokens x 2 layers x (K and V) x 2 kv heads x head_dim 2 x 4 bytes
    assert memory_bytes(model, kv_cache_tokens=10) == 48 + 10 * 2 * 2 * 2 * 2 * 4
