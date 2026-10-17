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


def test_request_ids_are_unique_across_threads():
    # The sampler state is keyed by request id, so two batched requests must never share one (#180,
    # docs/serving/free-threading.md FT2). The GIL build cannot show the race; 3.15t can.
    import threading

    ids, start = [], threading.Barrier(16)

    def take():
        start.wait()
        mine = [engine_module._next_request_id() for _ in range(2000)]
        ids.extend(mine)

    threads = [threading.Thread(target=take) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ids) == 16 * 2000 and len(set(ids)) == len(ids)


def test_typical_p_filter_matches_transformers():
    # The exclusive path filters typical_p itself (#182); it must keep the same tokens as
    # transformers' TypicalLogitsWarper, the filter `generate` applied before.
    import torch
    from transformers.generation.logits_process import TypicalLogitsWarper

    torch.manual_seed(3)
    scores = torch.randn(2, 64) * 3
    for mass in (0.2, 0.5, 0.9):
        expected = TypicalLogitsWarper(mass)(None, scores.clone())
        actual = engine_module._typical_p(scores.clone(), torch.full((2, 1), mass))
        assert torch.equal(torch.isinf(actual), torch.isinf(expected)), mass
