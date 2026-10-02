"""
The GGUF / BIN / GPTQ backends are gone (SPEC S1.8, #84): nothing in the server references or
imports llama-cpp or bitsandbytes.
"""
import sys
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src" / "main"
LEGACY = ("llama_cpp", "llama-cpp", "bitsandbytes", "auto_gptq", "BackendType", "CoreRuntime")


def test_no_source_file_mentions_a_legacy_backend():
    offenders = [
        f"{path.relative_to(SOURCE)}: {name}"
        for path in SOURCE.rglob("*.py")
        for name in LEGACY
        if name in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_the_backend_package_is_gone():
    assert not (SOURCE / "backend").exists()


def test_loading_the_server_and_models_imports_no_legacy_library():
    import api.src.main.server  # noqa: F401
    import api.src.main.models.qwen3  # noqa: F401

    assert "llama_cpp" not in sys.modules
    assert "bitsandbytes" not in sys.modules
