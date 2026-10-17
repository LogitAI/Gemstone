"""
The batch's attention switch (SPEC S1.13, #184). From transformers 5.19 the `paged|` prefix of
`paged|sdpa` is dropped on `set_attn_implementation` and continuous batching runs on plain `sdpa`;
the engine must accept that instead of reporting batching unavailable. No model is loaded.
"""
from types import SimpleNamespace

from api.src.main.engine import _Batcher, _Gate


class FakeManager:
    logit_processor = None

    def warmup(self):
        pass


class FakeModel:
    """ A model whose transformers drops the `paged|` prefix (5.19+ behaviour for sdpa). """
    device = SimpleNamespace(type="meta")
    generation_config = SimpleNamespace(eos_token_id=0)

    def __init__(self, drop_prefix):
        self.config = SimpleNamespace(_attn_implementation="sdpa")
        self.drop_prefix = drop_prefix

    def set_attn_implementation(self, name):
        self.config._attn_implementation = name.split("|", 1)[1] if self.drop_prefix and name != "paged|eager" else name

    def init_continuous_batching(self, **kwargs):
        return FakeManager()


def batcher(model, attn="sdpa_paged"):
    return _Batcher(
        model, tokenizer=None, gate=_Gate(), attn_implementation=attn, kv_cache_tokens=128, page_size=64,
        max_batch_tokens=64, max_batch_requests=2,
    )


def test_batching_accepts_a_transformers_that_drops_the_paged_prefix():
    model = FakeModel(drop_prefix=True)
    b = batcher(model)
    assert b._paged == "sdpa"
    assert model.config._attn_implementation == "sdpa"  # the original is restored after set-up


def test_batching_keeps_the_paged_name_where_transformers_keeps_it():
    model = FakeModel(drop_prefix=False)
    assert batcher(model)._paged == "paged|sdpa"
    assert batcher(model, "eager_paged")._paged == "paged|eager"
