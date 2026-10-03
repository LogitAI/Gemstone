"""
A model fetched on first use must be loadable offline, and a half-fetched copy must not count as
present (SPEC S1.14, issue #110).

The fake Hub below writes the cache the way `huggingface_hub.hf_hub_download` does: blobs, a
snapshot of symlinks, and `refs/<revision>` only when the revision is not itself a commit hash.
No network, no model. The last test fetches a real model and runs only in CI
(`GEMSTONE_TEST_ALLOW_DOWNLOAD=1`).
"""
import json
import os
import pathlib
import shutil
import uuid
from types import SimpleNamespace

import pytest

from api.src.main.registry import AliasStore, HFStore, Registry

ROOT = pathlib.Path(__file__).resolve().parents[2]
REPO = "someone/fresh-model"
SHA = "a" * 40
CI_DOWNLOAD = os.environ.get("GEMSTONE_TEST_ALLOW_DOWNLOAD") == "1"


@pytest.fixture
def hub(monkeypatch):
    """ A temp cache and a fake Hub; `hub.files` maps a file name to its bytes, `hub.fetched` logs downloads. """
    import huggingface_hub
    from huggingface_hub import constants
    from huggingface_hub.utils import _cache_manager as cache_manager

    home = ROOT / ".tmp" / f"first-use-{uuid.uuid4().hex}"
    cache = home / "hub"
    cache.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(cache_manager, "HF_HUB_CACHE", str(cache))  # imported by value
    state = SimpleNamespace(cache=cache, files={}, fetched=[], fail_on=None)

    class Api:
        def model_info(self, repo_id, files_metadata=False, **_):
            siblings = [SimpleNamespace(rfilename=n, size=len(b)) for n, b in state.files.items()]
            return SimpleNamespace(sha=SHA, siblings=siblings)

    def hf_hub_download(repo_id, filename, revision=None, **_):
        if filename == state.fail_on:
            raise ConnectionError("interrupted")
        state.fetched.append(filename)
        folder = cache / ("models--" + repo_id.replace("/", "--"))
        blob = folder / "blobs" / f"{abs(hash((repo_id, filename)))}"
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(state.files[filename])
        link = folder / "snapshots" / SHA / filename
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(os.path.relpath(blob, link.parent))
        if revision != SHA:  # what huggingface_hub does
            (folder / "refs").mkdir(exist_ok=True)
            (folder / "refs" / revision).write_text(SHA)
        return str(link)

    monkeypatch.setattr(huggingface_hub, "HfApi", Api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hf_hub_download)
    yield state
    shutil.rmtree(home, ignore_errors=True)


def safetensors_file(n=1):
    raw = json.dumps({"w": {"dtype": "F32", "shape": [n], "data_offsets": [0, 4 * n]}}).encode()
    return len(raw).to_bytes(8, "little") + raw + b"\0" * (4 * n)


def single(hub):
    hub.files = {"config.json": b'{"model_type": "llama"}', "model.safetensors": safetensors_file(),
                 "README.md": b"ignored"}


def sharded(hub, missing=()):
    names = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    index = {"weight_map": {"a": names[0], "b": names[1]}}
    hub.files = {"config.json": b'{"model_type": "llama"}',
                 "model.safetensors.index.json": json.dumps(index).encode(),
                 **{n: safetensors_file() for n in names if n not in missing}}


def test_download_writes_refs_main_so_the_default_revision_loads_offline(hub):
    from huggingface_hub import try_to_load_from_cache

    single(hub)
    HFStore().download(REPO)
    found = try_to_load_from_cache(REPO, "config.json")
    assert isinstance(found, str) and found.endswith("config.json") and os.path.exists(found)
    assert (hub.cache / "models--someone--fresh-model" / "refs" / "main").read_text() == SHA
    assert HFStore().get(REPO) is not None


def test_a_snapshot_without_weights_is_not_present(hub):
    single(hub)
    del hub.files["model.safetensors"]
    HFStore().download(REPO)
    assert HFStore().get(REPO) is None
    assert REPO not in [m.hf_id for m in HFStore().list()]


def test_a_sharded_snapshot_missing_a_shard_is_not_present(hub):
    sharded(hub, missing=["model-00002-of-00002.safetensors"])
    HFStore().download(REPO)
    assert HFStore().get(REPO) is None
    hub.files["model-00002-of-00002.safetensors"] = safetensors_file()
    HFStore().download(REPO)
    assert HFStore().get(REPO) is not None


def test_acquire_with_fetch_refetches_an_interrupted_pull(hub):
    single(hub)
    hub.fail_on = "model.safetensors"
    with pytest.raises(ConnectionError):
        HFStore().download(REPO)  # config.json landed, the weights did not
    assert "config.json" in hub.fetched and HFStore().get(REPO) is None

    hub.fail_on, hub.fetched = None, []
    registry = Registry(loader=lambda hf_id: SimpleNamespace(hf_id=hf_id), store=HFStore(),
                        model_class=lambda hf_id: (lambda engine: engine), aliases=AliasStore(None),
                        close=lambda engine: None)
    lease = registry.acquire(REPO, fetch=True)
    registry.release(lease)
    assert "model.safetensors" in hub.fetched
    assert HFStore().get(REPO) is not None


@pytest.mark.skipif(not CI_DOWNLOAD, reason="downloads a real model; set GEMSTONE_TEST_ALLOW_DOWNLOAD=1")
def test_real_pull_into_an_empty_hf_home_loads_offline(monkeypatch):
    from huggingface_hub import constants
    from huggingface_hub.utils import _cache_manager as cache_manager
    from transformers import AutoConfig, AutoTokenizer

    home = ROOT / ".tmp" / f"first-use-real-{uuid.uuid4().hex}"
    cache = home / "hub"
    cache.mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(cache))
    monkeypatch.setattr(cache_manager, "HF_HUB_CACHE", str(cache))
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    repo = "HuggingFaceTB/SmolLM2-135M"
    try:
        store = HFStore()
        assert store.get(repo) is None
        store.download(repo)
        assert store.get(repo) is not None
        assert AutoConfig.from_pretrained(repo, local_files_only=True, cache_dir=str(cache)).model_type
        assert AutoTokenizer.from_pretrained(repo, local_files_only=True, cache_dir=str(cache)) is not None
    finally:
        shutil.rmtree(home, ignore_errors=True)
