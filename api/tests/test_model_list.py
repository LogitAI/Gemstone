"""
The model list holds only chat-capable causal LMs, and a pull without loadable weights fails
(SPEC S1.2, S1.14, issue #119).

A temp Hugging Face cache and a fake Hub; no network, no model. The cache paths of
`huggingface_hub` bind at import, so both `constants` and `_cache_manager` are patched.
"""
import json
import os
import pathlib
import shutil
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.registry import AliasStore, HFStore, Registry, UnsupportedModel

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHA = "b" * 40


@pytest.fixture
def hub(monkeypatch):
    import huggingface_hub
    from huggingface_hub import constants
    from huggingface_hub.utils import _cache_manager as cache_manager

    home = ROOT / ".tmp" / f"model-list-{uuid.uuid4().hex}"
    cache = home / "hub"
    cache.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(cache_manager, "HF_HUB_CACHE", str(cache))
    state = SimpleNamespace(cache=cache, repo_files={}, fetched=[], infos=0)

    class Api:
        def model_info(self, repo_id, files_metadata=False, **_):
            state.infos += 1
            files = state.repo_files[repo_id]
            return SimpleNamespace(sha=SHA, siblings=[SimpleNamespace(rfilename=n, size=len(b)) for n, b in files.items()])

    def hf_hub_download(repo_id, filename, revision=None, **_):
        state.fetched.append((repo_id, filename))
        write(state, repo_id, {filename: state.repo_files[repo_id][filename]})

    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hf_hub_download)
    yield state
    shutil.rmtree(home, ignore_errors=True)


def write(hub, repo_id, files):
    folder = hub.cache / ("models--" + repo_id.replace("/", "--"))
    for name, data in files.items():
        blob = folder / "blobs" / f"{abs(hash((repo_id, name)))}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        link = folder / "snapshots" / SHA / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(os.path.relpath(blob, link.parent))
    (folder / "refs").mkdir(exist_ok=True)
    (folder / "refs" / "main").write_text(SHA)


def safetensors(n=1):
    raw = json.dumps({"w": {"dtype": "F32", "shape": [n], "data_offsets": [0, 4 * n]}}).encode()
    return len(raw).to_bytes(8, "little") + raw + b"\0" * (4 * n)


def cached(hub, repo_id, config):
    write(hub, repo_id, {"config.json": json.dumps(config).encode(), "model.safetensors": safetensors()})


CAUSAL = [{"architectures": ["LlamaForCausalLM"], "model_type": "llama"},
          {"architectures": ["GPT2LMHeadModel"], "model_type": "gpt2"},
          {"model_type": "qwen3"}]  # no architectures: the model type decides
NOT_CAUSAL = [{"architectures": ["BertForMaskedLM"], "model_type": "bert"},
              {"architectures": ["ViTForImageClassification"], "model_type": "vit"},
              {"model_type": "vit"}, {}]


@pytest.mark.parametrize("config", CAUSAL)
def test_causal_lm_repos_are_listed(hub, config):
    cached(hub, "someone/lm", config)
    assert [m.hf_id for m in HFStore().list()] == ["someone/lm"]


@pytest.mark.parametrize("config", NOT_CAUSAL)
def test_other_repos_are_not_listed(hub, config):
    cached(hub, "someone/other", config)
    assert HFStore().list() == []


def test_registry_models_hides_other_repos_but_keeps_catalogue_and_aliases(hub):
    cached(hub, "someone/lm", CAUSAL[0])
    cached(hub, "google/bert", NOT_CAUSAL[0])
    aliases = AliasStore(None)
    aliases.set("mine:latest", {"from": "someone/lm"})
    aliases.set("bert-copy:latest", {"from": "google/bert"})
    reg = Registry(loader=lambda hf_id: None, store=HFStore(), aliases=aliases)
    ids = [m["id"] for m in reg.models()]
    assert "someone/lm" in ids and "mine:latest" in ids and "qwen3" in ids and "default" in ids
    assert "google/bert" not in ids and "bert-copy:latest" not in ids


def test_api_models_and_tags_hide_other_repos(hub, monkeypatch):
    cached(hub, "someone/lm", CAUSAL[0])
    cached(hub, "google/bert", NOT_CAUSAL[0])
    reg = Registry(loader=lambda hf_id: None, store=HFStore(), aliases=AliasStore(None))
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as c:
        assert "someone/lm" in c.get("/api/models").json() and "google/bert" not in c.get("/api/models").json()
        assert "google/bert" not in [m["id"] for m in c.get("/v1/models").json()["data"]]
        assert [m["name"] for m in c.get("/api/tags").json()["models"]] == ["someone/lm"]


def test_listing_does_not_reread_safetensors_headers_per_call(hub, monkeypatch):
    cached(hub, "someone/lm", CAUSAL[0])
    calls = []
    real = registry_module.safetensors_stats
    monkeypatch.setattr(registry_module, "safetensors_stats", lambda p: calls.append(p) or real(p))
    store = HFStore()
    store.list(), store.list(), store.list()
    assert len(calls) == 1


# -- pull ------------------------------------------------------------------------------------------

def test_pull_of_a_repo_without_safetensors_fails_before_downloading(hub):
    hub.repo_files["someone/bin-only"] = {"config.json": b"{}", "pytorch_model.bin": b"x", "model.gguf": b"y"}
    with pytest.raises(UnsupportedModel, match="safetensors"):
        HFStore().download("someone/bin-only")
    assert hub.fetched == []


def test_pull_endpoint_reports_a_repo_without_weights(hub, monkeypatch):
    hub.repo_files["someone/bin-only"] = {"config.json": b"{}", "pytorch_model.bin": b"x"}
    reg = Registry(loader=lambda hf_id: None, store=HFStore(), aliases=AliasStore(None))
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as c:
        r = c.post("/api/pull", json={"model": "someone/bin-only", "stream": False})
        assert r.status_code == 400 and "safetensors" in r.json()["error"]
        lines = [json.loads(l) for l in c.post("/api/pull", json={"model": "someone/bin-only"}).text.splitlines() if l]
        assert "safetensors" in lines[-1]["error"] and lines[-1].get("status") != "success"
    assert hub.fetched == [] and HFStore().list() == []


def test_pull_of_a_safetensors_repo_still_works(hub):
    hub.repo_files["someone/lm"] = {"config.json": b'{"architectures": ["LlamaForCausalLM"]}',
                                    "model.safetensors": safetensors()}
    HFStore().download("someone/lm")
    assert [m.hf_id for m in HFStore().list()] == ["someone/lm"]
