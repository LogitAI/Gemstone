"""
Capability-based skips (SPEC S1.11): on torchnative a missing capability skips with a reason that
names it; on upstream PyTorch nothing is ever skipped for a capability. No model is loaded.
"""
import pytest

from api.tests import conftest
from api.tests.conftest import capability_skip_reason, substrate


class Engine:
    def __init__(self, batching, why=None):
        self.batching = batching
        self.batching_unavailable = why


def test_substrate_is_named_with_a_version():
    kind, version = substrate()

    assert kind in ("torch", "torchnative")
    assert version


@pytest.mark.parametrize("capability", ["batching", "quantization"])
def test_nothing_skips_on_upstream_pytorch(monkeypatch, capability):
    monkeypatch.setattr(conftest, "substrate", lambda: ("torch", "2.9.0"))
    monkeypatch.setattr(conftest, "quantization_unavailable", lambda fmt="q8_0": "missing")

    assert capability_skip_reason(capability, Engine(False, "no batching")) is None


def test_missing_batching_on_torchnative_skips_with_the_reason_and_version(monkeypatch):
    monkeypatch.setattr(conftest, "substrate", lambda: ("torchnative", "0.1.0b4"))

    reason = capability_skip_reason("batching", Engine(False, "psutil is not installed"))

    assert "continuous batching" in reason and "0.1.0b4" in reason and "psutil is not installed" in reason
    assert capability_skip_reason("batching", Engine(True)) is None


def test_missing_quantization_on_torchnative_skips_naming_q8_0(monkeypatch):
    monkeypatch.setattr(conftest, "substrate", lambda: ("torchnative", "0.1.0b4"))
    monkeypatch.setattr(conftest, "quantization_unavailable", lambda fmt="q8_0": "no TorchnativeConfig")

    reason = capability_skip_reason("quantization")

    assert "q8_0" in reason and "0.1.0b4" in reason and "no TorchnativeConfig" in reason


def test_capability_markers_are_registered():
    # Every `needs_*` marker is registered, so a typo cannot silently turn a skip into a pass.
    import tomllib
    from pathlib import Path

    markers = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert {m.split(":")[0] for m in markers} >= {"needs_batching", "needs_quantization", "torchnative_only"}
