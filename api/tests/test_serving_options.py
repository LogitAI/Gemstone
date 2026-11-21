"""
The server's device and weight format (SPEC S1.17, S1.16): `GEMSTONE_DEVICE`, `GEMSTONE_QUANTIZATION`,
the `cuda` extra (#164) and what `GET /api/ps` reports. Fake engines; nothing is loaded or downloaded.
"""
import pathlib
import tomllib

import pytest

from api.src.main import registry as registry_module
from api.src.main import settings
from api.tests.test_multi_resident import client, make  # noqa: F401  fixtures
from api.tests.test_ollama_api import QWEN, SMOL

ROOT = pathlib.Path(__file__).resolve().parents[2]


# -- GEMSTONE_DEVICE -------------------------------------------------------------------------------

@pytest.mark.parametrize("value, device", [
    (None, "cpu"), ("", "cpu"), ("cpu", "cpu"), (" CUDA ", "cuda"), ("cuda:1", "cuda:1"), ("mps", "mps"),
])
def test_device_defaults_to_cpu_and_takes_the_listed_values(monkeypatch, value, device):
    if value is None:
        monkeypatch.delenv("GEMSTONE_DEVICE", raising=False)
    else:
        monkeypatch.setenv("GEMSTONE_DEVICE", value)
    assert settings.serving_device() == device


@pytest.mark.parametrize("value", ["gpu", "cuda:", "cuda:x", "xpu", "cpu:0"])
def test_a_malformed_device_is_refused_naming_the_accepted_ones(monkeypatch, value):
    monkeypatch.setenv("GEMSTONE_DEVICE", value)
    with pytest.raises(ValueError) as e:
        settings.serving_device()
    assert value in str(e.value) and "cuda:<index>" in str(e.value) and "mps" in str(e.value)


def test_the_server_refuses_to_start_on_a_malformed_device(monkeypatch):
    import api.__main__ as entry

    started = []
    monkeypatch.setattr(entry, "run_uvicorn", lambda *a, **k: started.append(a))
    monkeypatch.setenv("GEMSTONE_DEVICE", "gpu")
    with pytest.raises(SystemExit) as e:
        entry.main(["run", "server"])
    assert "GEMSTONE_DEVICE" in str(e.value.code) and not started


def test_an_unavailable_device_names_what_to_install():
    import torch
    from api.src.main.engine import check_device

    check_device("cpu")
    if torch.cuda.is_available():
        check_device("cuda")
    else:
        with pytest.raises(ValueError, match=r"uv sync --python 3\.13 --extra cuda"):
            check_device("cuda")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        check_device("mps")
    else:
        with pytest.raises(ValueError, match="mps"):
            check_device("mps")


# -- GEMSTONE_QUANTIZATION -------------------------------------------------------------------------

@pytest.mark.parametrize("value, fmt", [(None, None), ("", None), (" ", None), ("Q4_K", "q4_k"), ("q8_0", "q8_0")])
def test_quantization_is_off_unless_set(monkeypatch, value, fmt):
    if value is None:
        monkeypatch.delenv("GEMSTONE_QUANTIZATION", raising=False)
    else:
        monkeypatch.setenv("GEMSTONE_QUANTIZATION", value)
    assert settings.serving_quantization() == fmt


def test_an_unknown_format_names_the_accepted_ones():
    from api.src.main.engine import _quantization_config

    try:
        import torchnative.quant  # noqa: F401
    except ImportError:
        with pytest.raises(ValueError, match="uv sync --extra torchnative"):
            _quantization_config("q4_k")
        return
    with pytest.raises(ValueError) as e:
        _quantization_config("q9_z")
    assert "q9_z" in str(e.value) and "q4_k" in str(e.value)


# -- every engine the registry loads ---------------------------------------------------------------

def test_engine_options_apply_the_settings_and_the_model_class_wins(monkeypatch):
    monkeypatch.setenv("GEMSTONE_DEVICE", "cuda")
    monkeypatch.setenv("GEMSTONE_QUANTIZATION", "q4_k")
    assert settings.engine_options({}) == {"device": "cuda", "quantization": "q4_k"}
    assert settings.engine_options({"device": "cpu", "dtype": "x"}) == {"device": "cpu", "quantization": "q4_k", "dtype": "x"}
    monkeypatch.delenv("GEMSTONE_QUANTIZATION")
    assert settings.engine_options({}) == {"device": "cuda"}


def test_the_registry_loads_every_engine_with_the_settings(monkeypatch):
    from api.src.main import engine as engine_module

    seen = []

    class Recorder:
        def __init__(self, model_id, **kwargs):
            seen.append((model_id, kwargs))

    monkeypatch.setattr(engine_module, "Engine", Recorder)
    monkeypatch.setenv("GEMSTONE_DEVICE", "cuda:0")
    monkeypatch.setenv("GEMSTONE_QUANTIZATION", "q4_k")
    registry_module.load_engine("someone/plain-model")

    (model_id, kwargs), = seen
    assert model_id == "someone/plain-model"
    assert kwargs["device"] == "cuda:0" and kwargs["quantization"] == "q4_k" and kwargs["local_files_only"]


# -- GET /api/ps -----------------------------------------------------------------------------------

def test_ps_reports_gpu_placement_and_the_format(make, client):  # noqa: F811
    s = make(memory={QWEN: 1500, SMOL: 700})
    client.post("/api/generate", json={"model": "qwen3", "prompt": "Hi", "stream": False, "keep_alive": -1})
    client.post("/api/generate", json={"model": SMOL, "prompt": "Hi", "stream": False, "keep_alive": "10m"})
    gpu = s["engines"][QWEN][0]
    gpu.device, gpu.quantization = "cuda:0", "q4_k"

    models = {m["model"]: m for m in client.get("/api/ps").json()["models"]}
    assert models[QWEN]["size_vram"] == 1500 and models[QWEN]["details"]["quantization_level"] == "Q4_K"
    assert models[SMOL]["size_vram"] == 0
    assert models[SMOL]["details"]["quantization_level"] == s["store"].get(SMOL).dtype


# -- the cuda extra --------------------------------------------------------------------------------

def test_the_cuda_extra_installs_cuda_torch_and_conflicts_with_the_others():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["optional-dependencies"]["cuda"] == ["torch"]
    conflicts = project["tool"]["uv"]["conflicts"]
    assert [{"extra": "torch"}, {"extra": "torchnative"}, {"extra": "cuda"}] in conflicts
    index = {i["name"]: i["url"] for i in project["tool"]["uv"]["index"]}
    assert index["pytorch-cu132"] == "https://download.pytorch.org/whl/cu132"

    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    cuda_torch = [p for p in lock["package"] if p["name"] == "torch" and "cu132" in p["source"].get("registry", "")]
    assert cuda_torch, "uv.lock has no torch from the CUDA 13.2 index"
    wheels = [w["url"] for p in cuda_torch for w in p.get("wheels", [])]
    assert any("linux" in w for w in wheels) and any("win" in w for w in wheels)
