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


# --------------------------------------------------------------------------------------------- #
# #74: a filter whose argument is neutral for every row is an identity and is skipped, and the
# top_k threshold comes from a partial selection. Neither may change a token.
# --------------------------------------------------------------------------------------------- #

class _State:
    def __init__(self, request_id, kwargs):
        self.request_id = request_id
        self.logit_processor_kwargs = kwargs
        self.initial_tokens = [1, 2, 3]

    def generated_len(self):
        return 0


class _Request:
    def __init__(self, request_id, kwargs):
        self.state = _State(request_id, kwargs)


def _requests(*kwargs_per_row):
    return [_Request(f"test-74-{i}", dict(kwargs)) for i, kwargs in enumerate(kwargs_per_row)]


def _scores(rows=3, vocab=64):
    """ Scores with ties and already-filtered tokens, where a filter that is not an identity shows. """
    import torch

    torch.manual_seed(5)
    scores = torch.randn(rows, vocab) * 4
    scores[0, :8] = scores[0, 8]
    if rows > 1:
        scores[1, :16] = float("-inf")
    return scores


def test_a_neutral_argument_makes_each_filter_an_identity():
    # The invariant behind the skip: at its neutral value every filter returns the scores unchanged,
    # ties and -inf included, so skipping it cannot change a token.
    import torch

    scores = _scores()

    def rows(value, dtype=torch.float32):
        return torch.full((scores.size(0), 1), value, dtype=dtype)

    neutral = engine_module._NEUTRAL
    assert neutral["temperature"](1.0) and not neutral["temperature"](0.7)
    assert neutral["top_k"](0) and not neutral["top_k"](40)
    assert neutral["top_p"](1.0) and not neutral["top_p"](0.95)
    assert neutral["min_p"](0.0) and not neutral["min_p"](0.05)
    assert neutral["seed"](-1) and not neutral["seed"](0)
    assert torch.equal(engine_module._temperature(scores, rows(1.0)), scores)
    assert torch.equal(engine_module._top_k(scores, rows(0, torch.int64)), scores)
    assert torch.equal(engine_module._top_p(scores, rows(1.0)), scores)
    assert torch.equal(engine_module._min_p(scores, rows(0.0)), scores)
    assert torch.equal(engine_module._draw(scores, rows(-1.0)), scores)


def test_batch_filters_skip_a_batch_that_is_neutral_for_them():
    # Red before #74: every filter returned a new tensor computed over the whole vocabulary.
    scores = _scores(rows=2)
    requests = _requests(engine_module._GREEDY, engine_module._GREEDY)

    for cls in engine_module._SAMPLERS:
        processor = cls()
        arg = processor.prepare_tensor_args(requests)

        assert processor.neutral, cls.__name__
        assert processor(scores, arg) is scores, cls.__name__


def test_batch_filters_apply_when_one_row_is_not_neutral():
    import torch

    scores = _scores(rows=2)
    sampled = dict(temperature=0.5, top_k=4, top_p=0.9, min_p=0.1, seed=3)
    requests = _requests(engine_module._GREEDY, sampled)
    try:
        uniform = engine_module._SAMPLER_STATE.draw("test-74-1", 3, 0)
        expected = {
            "Temperature": engine_module._temperature(scores, torch.tensor([[1.0], [0.5]])),
            "TopK": engine_module._top_k(scores, torch.tensor([[0], [4]])),
            "TopP": engine_module._top_p(scores, torch.tensor([[1.0], [0.9]])),
            "MinP": engine_module._min_p(scores, torch.tensor([[0.0], [0.1]])),
            "Draw": engine_module._draw(scores, torch.tensor([[-1.0], [uniform]])),
        }
        for cls in engine_module._SAMPLERS:
            processor = cls()
            arg = processor.prepare_tensor_args(requests)

            assert not processor.neutral, cls.__name__
            actual = processor(scores, arg)
            assert actual is not scores, cls.__name__
            assert torch.equal(actual, expected[cls.__name__]), cls.__name__
    finally:
        engine_module._SAMPLER_STATE.forget("test-74-1")


def test_top_k_threshold_from_a_partial_selection_equals_the_full_sort(monkeypatch):
    # Red before #74: `_top_k` took no `largest` and always sorted the whole row.
    import torch

    scores = _scores(rows=4, vocab=100)
    k = torch.tensor([[5], [1], [40], [7]])
    full_sort = torch.sort(scores, dim=-1, descending=True)[0]
    expected = scores.masked_fill(scores < full_sort.gather(-1, k - 1), float("-inf"))

    def no_sort(*args, **kwargs):
        raise AssertionError("the whole row was sorted")

    monkeypatch.setattr(torch, "sort", no_sort)
    assert torch.equal(engine_module._top_k(scores, k, 40), expected)
    monkeypatch.undo()

    # A row that is off (0) needs the whole row: the sort path stays, with the same result.
    off = torch.tensor([[5], [0], [40], [7]])
    expected_off = scores.masked_fill(scores < full_sort.gather(-1, off.clamp(min=1) - 1), float("-inf"))
    expected_off[1] = scores[1]
    assert torch.equal(engine_module._top_k(scores, off, 0), expected_off)
    assert torch.equal(engine_module._top_k(scores, off), expected_off)


def test_the_exclusive_sampler_skips_its_neutral_filters(monkeypatch):
    # Red before #74: the exclusive sampler ran every filter whatever its argument.
    import torch

    def not_called(*args, **kwargs):
        raise AssertionError("a neutral filter ran")

    scores = _scores(rows=1)
    input_ids = torch.zeros(1, 3, dtype=torch.long)
    sampler = engine_module._ExclusiveSampler(
        temperature=1.0, top_k=0, top_p=1.0, min_p=0.0, typical_p=1.0, seed=7, prompt_length=3,
    )
    expected = engine_module._draw(scores, torch.full((1, 1), sampler.uniform(0)))
    for name in ("_temperature", "_top_k", "_top_p", "_min_p", "_typical_p"):
        monkeypatch.setattr(engine_module, name, not_called)

    assert torch.equal(sampler(input_ids, scores), expected)
    monkeypatch.undo()

    # Filters that are not neutral still run, in the batch's order.
    sampler = engine_module._ExclusiveSampler(
        temperature=0.5, top_k=4, top_p=0.9, min_p=0.1, typical_p=1.0, seed=7, prompt_length=3,
    )
    filtered = engine_module._temperature(scores, torch.full((1, 1), 0.5))
    filtered = engine_module._top_k(filtered, torch.full((1, 1), 4, dtype=torch.int64))
    filtered = engine_module._top_p(filtered, torch.full((1, 1), 0.9))
    filtered = engine_module._min_p(filtered, torch.full((1, 1), 0.1))
    expected = engine_module._draw(filtered, torch.full((1, 1), sampler.uniform(0)))

    assert torch.equal(sampler(input_ids, scores), expected)
