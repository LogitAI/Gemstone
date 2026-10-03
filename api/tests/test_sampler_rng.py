"""
The per-request sampler's uniform draws (SPEC S1.12) come from the request's own seed and need no
torch RNG, so seeded sampling works on substrates without torch.Generator (torchnative #30).
"""
import pytest

from api.src.main import engine as engine_module


def test_draws_are_reproducible_per_seed_and_need_no_torch_generator(monkeypatch):
    import torch

    def refuse(*args, **kwargs):
        raise NotImplementedError("torch.Generator is not available on this substrate")

    monkeypatch.setattr(torch, "Generator", refuse)
    state = engine_module._SamplerState()

    first = [state.draw("a", 7, i) for i in range(100)]
    again = [state.draw("b", 7, i) for i in reversed(range(100))][::-1]
    other = [state.draw("c", 11, i) for i in range(100)]

    assert first == again  # same seed, same draw at the same token index, in any order of access
    assert first != other
    assert all(0.0 <= u < 1.0 for u in first)


def test_forgetting_a_request_drops_its_stream():
    state = engine_module._SamplerState()
    state.draw("a", 7, 0)
    state.forget("a")

    assert "a" not in state._draws
