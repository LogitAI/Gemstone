"""
The serving engine (SPEC S1.11): one transformers-based engine for every model.

It uses only the public `torch` / `transformers` API, so it runs on whatever `import torch`
resolves to: upstream PyTorch, or torchnative, which replaces `import torch` on device.

Concurrent requests are served by continuous batching (S1.12) on a paged KV cache (S1.13):
transformers' `ContinuousBatchingManager` with the `paged|sdpa` (default) or `paged|eager`
attention, both plain torch ops. Sampling is done per request by Gemstone's own logits
processors (`_SAMPLERS` below), so temperature / top-k / top-p / min-p / seed are per request and a
seeded sample does not depend on which other requests share the batch.

Requests the batch cannot serve take the exclusive path, which is the M1 engine unchanged: one
`model.generate` at a time, with no batched request running. See `Engine.__call__`.
"""
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Union
import itertools
import logging
import os
import queue
import random
import threading


logger = logging.getLogger(__name__)

DEFAULT_QUEUE_TIMEOUT = 300  # seconds a request may wait for the model before it is refused as busy


class ModelBusy(TimeoutError):
    """
    A request waited `queue_timeout` for the model (or a load waited `load_timeout` for room, see
    the registry) and it stayed busy: the APIs answer 503 and the WebSocket closes with 1013.
    """


def queue_timeout_default() -> float:
    """ `GEMSTONE_QUEUE_TIMEOUT` (seconds or a duration such as "5m"; 0 or negative: wait without a limit), else 5 minutes. """
    value = os.environ.get("GEMSTONE_QUEUE_TIMEOUT")
    if value is None or not value.strip():
        return DEFAULT_QUEUE_TIMEOUT
    from .registry import parse_keep_alive  # the same duration syntax as the other timeouts
    try:
        return parse_keep_alive(value)
    except ValueError as e:
        raise ValueError(f"GEMSTONE_QUEUE_TIMEOUT: {e}") from e

# Attention implementations of the batched path. Both are plain torch ops (gather + SDPA, or
# gather + matmul/softmax), so they are the ones torchnative can run.
PAGED_ATTENTION = {
    "sdpa_paged": "paged|sdpa",
    "eager_paged": "paged|eager",
}

# Logits-processor kwargs of a greedy request: neutral values for every sampler, and a negative
# seed, which makes the final draw keep the scores so the manager's argmax picks the top token.
_GREEDY = dict(temperature=1.0, top_k=0, top_p=1.0, min_p=0.0, seed=-1)


class Engine:
    """
    Loads one causal LM and streams chat completions from it.

    Concurrent callers are batched together (continuous batching); see `__call__` for the
    requests that instead run alone. A generation stops when its `cancel` event is set or its
    stream is closed (#35); in a batch only that request is removed.
    """

    def __init__(
        self,
        model_id: str,
        *,
        revision: Optional[str] = None,
        dtype=None,
        device: str = "cpu",
        quantization: Optional[str] = None,
        chat_template: Optional[str] = None,
        context_length: Optional[int] = None,
        cache_dir: Optional[Union[str, os.PathLike[str]]] = None,
        local_files_only: bool = False,
        batching: bool = True,
        attn_implementation: str = "sdpa_paged",
        kv_cache_tokens: int = 8192,
        page_size: int = 64,
        max_batch_tokens: int = 256,
        max_batch_requests: int = 16,
        queue_timeout: Optional[float] = None,
    ):
        """
        `batching=False` turns continuous batching off: every request takes the exclusive path.
        `attn_implementation` is `"sdpa_paged"` or `"eager_paged"`. `kv_cache_tokens` is the size of
        the paged KV cache shared by all batched requests, in tokens, allocated in pages of
        `page_size` tokens. `max_batch_tokens` caps the tokens of one forward pass (longer prompts are
        prefilled in chunks) and `max_batch_requests` the requests decoded together.
        `queue_timeout` (seconds, default `GEMSTONE_QUEUE_TIMEOUT` or 300; 0 or negative: no limit) is
        how long a request waits for the model, behind an exclusive generation or for one to finish,
        before it fails with `ModelBusy`.
        """
        from transformers import AutoModelForCausalLM, AutoTokenizer

        hub_kwargs = dict(revision=revision, cache_dir=cache_dir, local_files_only=local_files_only)
        model_kwargs = dict(hub_kwargs)
        if dtype is not None:
            model_kwargs["dtype"] = dtype
        if quantization is not None:
            model_kwargs["quantization_config"] = _quantization_config(quantization)

        self.model_id = model_id
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, **hub_kwargs)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **model_kwargs).to(device).eval()
        self.chat_template = chat_template
        self.context_length = context_length or getattr(self.model.config, "max_position_embeddings", None)
        self._gate = _Gate()
        self.queue_timeout = queue_timeout if queue_timeout is not None else queue_timeout_default()

        # Continuous batching, or the reason it is off.
        self._batcher: Optional[_Batcher] = None
        self.batching_unavailable: Optional[str] = None
        if not batching:
            self.batching_unavailable = "turned off (batching=False)"
        else:
            try:
                self._batcher = _Batcher(
                    self.model,
                    self.tokenizer,
                    self._gate,
                    attn_implementation=attn_implementation,
                    kv_cache_tokens=kv_cache_tokens,
                    page_size=page_size,
                    max_batch_tokens=max_batch_tokens,
                    max_batch_requests=max_batch_requests,
                )
            except _BatchingUnavailable as e:
                self.batching_unavailable = str(e)
                logger.warning("Continuous batching is off for %s: %s", model_id, e)

        # The footprint the registry budgets for (SPEC S1.14): weights plus the paged KV cache.
        if self._batcher is not None:
            self._batcher.queue_timeout = self._wait()
        self.memory_bytes = memory_bytes(self.model, kv_cache_tokens if self._batcher is not None else 0)

    @property
    def batching(self) -> bool:
        """ Whether concurrent requests are batched (False: every request runs alone). """
        return self._batcher is not None

    def close(self):
        """ Stop the batching loop and release its KV cache. The engine keeps serving on the exclusive path. """
        if self._batcher is not None:
            batcher, self._batcher = self._batcher, None
            batcher.close()

    def _wait(self) -> Optional[float]:
        """ The longest a request waits for the model (None: without a limit). """
        timeout = getattr(self, "queue_timeout", None)
        return timeout if timeout and timeout > 0 else None

    def embed(self, texts: List[str], truncate: bool = True) -> List[List[float]]:
        """
        One embedding per text (Ollama's `/api/embed`, SPEC S1.15): the mean of the model's last
        hidden state over the text's tokens, scaled to unit length. A text longer than the context
        is cut to it, or with `truncate=False` refused (ValueError). Runs on the exclusive path, so
        it never overlaps a batched generation (which switches the model's attention).
        """
        import torch

        encoded = []
        for text in texts:
            ids = list(self.tokenizer(text, add_special_tokens=True)["input_ids"])
            if not ids:
                raise ValueError("cannot embed an empty input")
            if self.context_length and len(ids) > self.context_length:
                if not truncate:
                    raise ValueError(f"the input ({len(ids)} tokens) exceeds the context length ({self.context_length})")
                ids = ids[:self.context_length]
            encoded.append(ids)

        base = getattr(self.model, "base_model", None) or self.model  # the decoder without the LM head
        vectors = []
        with self._gate.exclusive(self._wait()), torch.no_grad():
            for ids in encoded:
                inputs = torch.tensor([ids], dtype=torch.long, device=self.model.device)
                hidden = base(input_ids=inputs).last_hidden_state[0].float()
                vectors.append(torch.nn.functional.normalize(hidden.mean(dim=0), dim=0).tolist())
        return vectors

    def __call__(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, str]]] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
        top_k: int = 40,
        min_p: float = 0.05,
        typical_p: float = 1.0,
        stream: bool = True,
        max_new_tokens: int = 512,
        repeat_penalty: float = 1.0,
        seed: Optional[int] = None,
        cancel: Optional[threading.Event] = None,
        chat_template_kwargs: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        Stream the reply to `messages` as text chunks.

        `chat_template_kwargs` are extra variables for the chat template (`enable_thinking=False`
        switches Qwen3's reasoning off). Unlike `kwargs` they do not make the request exclusive.

        `stream` is accepted for compatibility with the model layer and ignored: the reply is
        always produced as a stream. `max_new_tokens <= 0` means "as many as fit": up to the context
        length, but when the request can join the batch, up to what the paged KV cache holds
        (`min(context length, kv_cache_tokens)` in total with the prompt), so a request that sets
        no limit still batches. A request that sets a larger limit explicitly takes the exclusive
        path. A request that waits for the model longer than `queue_timeout` raises `ModelBusy`.

        The request joins the running batch unless it needs something the batch does not do, in
        which case it takes the exclusive path (`model.generate`, alone, as in M1):
        batching is off or unavailable (`batching_unavailable` says why); `typical_p != 1` or
        `repeat_penalty != 1`; extra keyword arguments (they go to `generate` unchanged); or the
        prompt plus `max_new_tokens` does not fit in the paged KV cache.
        """
        prompt = self.tokenizer.apply_chat_template(
            messages,
            tools=tools or None,
            chat_template=self.chat_template,
            add_generation_prompt=True,
            tokenize=False,
            **(chat_template_kwargs or {}),
        )
        input_ids = self.tokenizer(prompt, add_special_tokens=False)["input_ids"]
        prompt_length = len(input_ids)
        batcher = self._batcher
        batchable = batcher is not None and typical_p == 1.0 and repeat_penalty == 1.0 and not kwargs
        if max_new_tokens <= 0:
            if self.context_length is None:
                raise ValueError("max_new_tokens must be positive when the context length is unknown.")
            max_new_tokens = self.context_length - prompt_length
            if batchable:
                # No limit was set: take what the paged KV cache can hold, so the request batches.
                # A prompt that already fills the cache cannot, and runs exclusive up to the context.
                fitting = min(self.context_length, batcher.kv_cache_tokens) - prompt_length
                if fitting > 0:
                    max_new_tokens = fitting
        if max_new_tokens <= 0:
            raise ValueError(f"The prompt ({prompt_length} tokens) exceeds the token limit ({self.context_length}).")

        if batchable and batcher.fits(prompt_length + max_new_tokens):
            if temperature > 0:
                params = dict(
                    temperature=float(temperature),
                    top_k=int(top_k or 0),
                    top_p=float(top_p if top_p is not None else 1.0),
                    min_p=float(min_p or 0.0),
                    seed=int(seed) if seed is not None else random.SystemRandom().randrange(2**62),
                )
            else:
                params = dict(_GREEDY)
            yield from batcher.stream(input_ids, max_new_tokens, params, cancel)
            return

        yield from self._generate_exclusive(
            input_ids, max_new_tokens, temperature, top_p, top_k, min_p, typical_p, repeat_penalty, seed, cancel,
            kwargs,
        )

    def _generate_exclusive(
        self, input_ids, max_new_tokens, temperature, top_p, top_k, min_p, typical_p, repeat_penalty, seed, cancel,
        kwargs,
    ) -> Generator[str, None, None]:
        """ One `model.generate`, with no other generation running (the M1 engine). """
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

        stop = threading.Event()

        class _Stop(StoppingCriteria):
            def __call__(self, input_ids, scores, **_):
                return stop.is_set() or (cancel is not None and cancel.is_set())

        with self._gate.exclusive(self._wait()):
            inputs = torch.tensor([input_ids], dtype=torch.long, device=self.model.device)
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
            do_sample = temperature > 0
            generation_kwargs = dict(
                input_ids=inputs,
                attention_mask=torch.ones_like(inputs),
                max_new_tokens=max_new_tokens,
                do_sample=do_sample,
                repetition_penalty=repeat_penalty,
                streamer=streamer,
                stopping_criteria=StoppingCriteriaList([_Stop()]),
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
            if do_sample:
                generation_kwargs.update(temperature=temperature, top_p=top_p, top_k=top_k, typical_p=typical_p)
                if min_p:
                    generation_kwargs.update(min_p=min_p)
            generation_kwargs.update(kwargs)

            failure = []

            def run():
                try:
                    if seed is not None:
                        torch.manual_seed(seed)
                    with torch.no_grad():
                        self.model.generate(**generation_kwargs)
                except BaseException as e:  # re-raised in the consumer
                    failure.append(e)
                    streamer.end()

            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            exhausted = False
            try:
                for text in streamer:
                    if text:
                        yield text
                exhausted = True
            finally:
                stop.set()  # the consumer stopped early, or the stream ended
                if not exhausted:
                    for _ in streamer:  # consume up to the end signal so nothing is left queued
                        pass
                worker.join()
            if failure:
                raise failure[0]


def memory_bytes(model, kv_cache_tokens: int = 0) -> int:
    """
    An estimate of a loaded model's memory: its parameters and buffers at their element size, plus
    a paged KV cache of `kv_cache_tokens` tokens (keys and values, every layer, in the weights'
    dtype). Activations and allocator overhead are not counted.
    """
    tensors = list(model.parameters()) + list(model.buffers())
    weights = sum(t.numel() * t.element_size() for t in tensors)
    config = getattr(model, "config", None)
    if not kv_cache_tokens or config is None:
        return weights
    layers = getattr(config, "num_hidden_layers", None)
    heads = getattr(config, "num_attention_heads", None)
    kv_heads = getattr(config, "num_key_value_heads", None) or heads
    head_dim = getattr(config, "head_dim", None) or (
        config.hidden_size // heads if heads and getattr(config, "hidden_size", None) else None)
    if not (layers and kv_heads and head_dim):
        return weights
    element = next((t.element_size() for t in tensors if t.is_floating_point()), 2)
    return weights + kv_cache_tokens * layers * 2 * kv_heads * head_dim * element


class _Gate:
    """
    Shared/exclusive access to the model. Batched requests share it; an exclusive request waits
    until no batched request runs (and the batching loop has stopped), and holds off new ones.
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False
        self._exclusive_waiting = 0

    @contextmanager
    def exclusive(self, timeout: Optional[float] = None):
        """ Wait up to `timeout` seconds (None: without a limit) for the gate, else raise `ModelBusy`. """
        with self._cond:
            self._exclusive_waiting += 1
            try:
                taken = self._cond.wait_for(lambda: not self._exclusive and self._shared == 0, timeout)
            finally:
                self._exclusive_waiting -= 1
                self._cond.notify_all()  # sharers queued behind this waiter may go now if it gave up
            if not taken:
                raise ModelBusy(f"server busy: the model was not free within {timeout:g}s.")
            self._exclusive = True
        try:
            yield
        finally:
            with self._cond:
                self._exclusive = False
                self._cond.notify_all()

    @contextmanager
    def shared(self, on_first, on_last, timeout: Optional[float] = None):
        """
        `on_first` runs when the first sharer enters, `on_last` when the last one leaves (under the
        lock). Waits up to `timeout` seconds (None: without a limit), else raises `ModelBusy`.
        """
        with self._cond:
            if not self._cond.wait_for(lambda: not self._exclusive and self._exclusive_waiting == 0, timeout):
                raise ModelBusy(f"server busy: the model was not free within {timeout:g}s.")
            if self._shared == 0:
                on_first()
            self._shared += 1
        try:
            yield
        finally:
            with self._cond:
                self._shared -= 1
                if self._shared == 0:
                    try:
                        on_last()
                    finally:
                        self._cond.notify_all()


class _BatchingUnavailable(Exception):
    pass


class _Batcher:
    """
    One `ContinuousBatchingManager` per engine.

    The manager's loop runs only while batched requests are in flight. When the last one leaves,
    the loop stops (its paged KV cache is kept for the next session) and the model's original
    attention implementation is restored, so `model.generate` works again on the exclusive path.
    """

    def __init__(
        self, model, tokenizer, gate: _Gate, *, attn_implementation: str, kv_cache_tokens: int, page_size: int,
        max_batch_tokens: int, max_batch_requests: int,
    ):
        from transformers import ContinuousBatchingConfig, GenerationConfig

        if attn_implementation not in PAGED_ATTENTION:
            raise ValueError(f"attn_implementation must be one of {sorted(PAGED_ATTENTION)}, got {attn_implementation!r}.")
        if not hasattr(model, "init_continuous_batching"):
            raise _BatchingUnavailable(f"{type(model).__name__} has no continuous-batching support in transformers.")
        if model.device.type == "cpu":
            try:
                import psutil  # noqa: F401  transformers sizes the CPU cache from it
            except ImportError as e:
                raise _BatchingUnavailable("psutil is not installed; transformers cannot size a CPU KV cache.") from e

        self.model = model
        self.tokenizer = tokenizer
        self._gate = gate
        self._paged = PAGED_ATTENTION[attn_implementation]
        self._original = model.config._attn_implementation
        self.kv_cache_tokens = kv_cache_tokens
        self.queue_timeout: Optional[float] = None  # the engine sets it: how long a request waits for the gate

        eos = model.generation_config.eos_token_id
        self._eos = eos if eos is not None else tokenizer.eos_token_id
        generation_config = GenerationConfig(do_sample=False, eos_token_id=self._eos, max_new_tokens=256)
        cb_config = ContinuousBatchingConfig(
            page_size=page_size,
            num_blocks=max(1, -(-kv_cache_tokens // page_size)),
            max_batch_tokens=max_batch_tokens,
            max_requests_per_batch=max_batch_requests,
            max_blocks_per_request=0,  # the block-table decode path is flash-attention on CUDA only
            max_memory_percent=0.9,
            allow_block_sharing=False,  # prefix reuse would make a reply depend on earlier requests
            use_cuda_graph=False,  # no CUDA graphs, streams or compile: the same code runs on torchnative
            use_async_batching=False,
            default_compile_level=0,
        )

        self._set_attention(self._paged)
        try:
            if model.config._attn_implementation != self._paged:
                raise _BatchingUnavailable(f"{type(model).__name__} cannot switch to {self._paged} attention.")
            self.manager = model.init_continuous_batching(
                generation_config=generation_config, continuous_batching_config=cb_config
            )
            self.manager.logit_processor = _sampling_processors()
            self.manager.warmup()  # creates the batch processor and allocates the paged KV cache now
        except _BatchingUnavailable:
            raise
        except Exception as e:
            raise _BatchingUnavailable(f"could not set up continuous batching: {e!r}") from e
        finally:
            self._set_attention(self._original)

        self._queues: Dict[str, queue.Queue] = {}
        self._queues_lock = threading.Lock()
        self._dispatcher: Optional[threading.Thread] = None

    def fits(self, total_tokens: int) -> bool:
        """ Whether one request of this many tokens fits in the paged KV cache by itself. """
        return total_tokens <= self.kv_cache_tokens

    def close(self):
        with self._gate.exclusive():  # waits until no batched request runs and the loop has stopped
            self.manager.destroy()
            self.manager.batch_processor = None  # drops the paged KV cache
            self.model.destroy_cached_continuous_batching_manager()

    # ------------------------------------------------------------------------------------------ #

    def _set_attention(self, implementation: str):
        if self.model.config._attn_implementation != implementation:
            self.model.set_attn_implementation(implementation)

    def _start(self):
        """ First batched request: switch to paged attention and start the loop and the dispatcher. """
        self._set_attention(self._paged)
        try:
            self.manager.start()
        except BaseException:
            self._set_attention(self._original)
            raise
        self._dispatcher = threading.Thread(target=self._dispatch, args=(self.manager,), daemon=True)
        self._dispatcher.start()

    def _stop(self):
        """ Last batched request left: stop the loop (keeping its cache) and restore the attention. """
        try:
            self.manager.stop(block=True, keep_for_next_session=True)
            if self._dispatcher is not None:
                self._dispatcher.join()
                self._dispatcher = None
        finally:
            self._set_attention(self._original)

    def _dispatch(self, manager):
        """ Route the manager's outputs to the queue of the request they belong to. """
        while True:
            result = manager.get_result(timeout=0.1)
            if result is None:
                if not manager.is_running():
                    return
                continue
            with self._queues_lock:
                target = self._queues.get(result.request_id)
            if target is not None:
                target.put(result)

    def stream(self, input_ids, max_new_tokens, params, cancel) -> Generator[str, None, None]:
        with self._gate.shared(self._start, self._stop, self.queue_timeout):
            request_id = f"gemstone-{next(_REQUEST_IDS)}"  # unique across engines: the sampler state is shared
            with self._queues_lock:
                outputs = self._queues[request_id] = queue.Queue()
            finished = False
            try:
                added = self.manager.add_request(
                    input_ids=list(input_ids),
                    request_id=request_id,
                    max_new_tokens=max_new_tokens,
                    streaming=True,
                    eos_token_id=self._eos,
                    **params,
                )
                if added is None:
                    raise RuntimeError("The continuous-batching loop did not accept the request.")

                sent = ""
                while True:
                    if cancel is not None and cancel.is_set():
                        break
                    try:
                        result = outputs.get(timeout=0.05)
                    except queue.Empty:
                        if not self.manager.is_running():
                            raise RuntimeError("The continuous-batching loop stopped before the request finished.")
                        continue
                    if result.error is not None:
                        finished = True  # failed requests are already out of the batch
                        raise RuntimeError(f"Generation failed: {result.error}")
                    text = self.tokenizer.decode(result.generated_tokens, skip_special_tokens=True)
                    done = result.is_finished()
                    # Hold back an incomplete UTF-8 sequence until its next token arrives.
                    if (done or not text.endswith("�")) and text.startswith(sent) and len(text) > len(sent):
                        chunk, sent = text[len(sent):], text
                        yield chunk
                    if done:
                        finished = True
                        break
            finally:
                if not finished:
                    self.manager.cancel_request(request_id)
                with self._queues_lock:
                    self._queues.pop(request_id, None)
                _SAMPLER_STATE.forget(request_id)


# --------------------------------------------------------------------------------------------- #
# Per-request sampling inside the batch.
#
# transformers' own continuous-batching sampler (`ModelRunner._sample`) draws every row of the
# batch with one `torch.multinomial` on the global RNG, seeded once per manager. A request's
# sample therefore depends on its neighbours and its row position, and there is no per-request
# seed. Gemstone runs the manager greedily (`do_sample=False`, so it takes an argmax) and does the
# sampling in these per-request logits processors instead: the last one draws each row's token
# from that request's own random stream (`random.Random(seed)`) and leaves only that token
# selectable. The stream is plain Python, so it needs no torch RNG (torchnative has no
# `torch.Generator` yet).
# --------------------------------------------------------------------------------------------- #

def _sampling_processors():
    from transformers import LogitsProcessorList
    from transformers.generation.continuous_batching.cb_logits_processors import (
        ContinuousBatchingLogitsProcessorList,
    )

    processors = [cls() for cls in _SAMPLERS]
    batch = ContinuousBatchingLogitsProcessorList(LogitsProcessorList(processors), per_request_processors=False)
    batch.tensors_required = len(processors)  # one int32 argument row per processor
    batch.do_processing = True
    return batch


class _SamplerState:
    """ The uniform draws of each seeded request, generated from its seed, indexed by token. """

    def __init__(self):
        self._lock = threading.Lock()
        self._draws: Dict[str, tuple] = {}

    def draw(self, request_id: str, seed: int, index: int) -> float:
        with self._lock:
            entry = self._draws.get(request_id)
            if entry is None:
                entry = self._draws[request_id] = (random.Random(seed), [])
            generator, draws = entry
            while len(draws) <= index:
                draws.append(generator.random())
            return draws[index]

    def forget(self, request_id: str):
        with self._lock:
            self._draws.pop(request_id, None)


_SAMPLER_STATE = _SamplerState()
_REQUEST_IDS = itertools.count(1)


def _define_samplers():
    import torch
    from transformers.generation.continuous_batching.cb_logits_processors import ContinuousBatchingLogitsProcessor

    def as_float(values):
        return torch.tensor(values, dtype=torch.float32, device="cpu").view(dtype=torch.int32)

    def float_arg(tensor_arg, rows):
        return tensor_arg[:rows].view(dtype=torch.float32).unsqueeze(-1)  # [B, 1]

    class Temperature(ContinuousBatchingLogitsProcessor):
        supported_kwargs = {"temperature": float}
        ignored_kwargs = ()

        def fill_defaults(self, int32_tensor):
            int32_tensor.copy_(torch.ones_like(int32_tensor, dtype=torch.float32).view(dtype=torch.int32))

        def prepare_tensor_args(self, requests):
            return as_float([r.state.logit_processor_kwargs.get("temperature", 1.0) for r in requests])

        def __call__(self, scores, tensor_arg):
            return scores / float_arg(tensor_arg, scores.size(0))

    class TopK(ContinuousBatchingLogitsProcessor):
        supported_kwargs = {"top_k": int}
        ignored_kwargs = ()

        def fill_defaults(self, int32_tensor):
            int32_tensor.fill_(0)

        def prepare_tensor_args(self, requests):
            return torch.tensor(
                [r.state.logit_processor_kwargs.get("top_k", 0) for r in requests], dtype=torch.int32, device="cpu"
            )

        def __call__(self, scores, tensor_arg):
            vocab = scores.size(-1)
            k = tensor_arg[: scores.size(0)].to(torch.int64)
            k = torch.where(k <= 0, torch.full_like(k, vocab), k.clamp(max=vocab))  # 0 = off
            sorted_scores = torch.sort(scores, dim=-1, descending=True)[0]
            thresholds = sorted_scores.gather(-1, (k - 1).unsqueeze(-1))
            return scores.masked_fill(scores < thresholds, float("-inf"))

    class TopP(ContinuousBatchingLogitsProcessor):
        supported_kwargs = {"top_p": float}
        ignored_kwargs = ()

        def fill_defaults(self, int32_tensor):
            int32_tensor.copy_(torch.ones_like(int32_tensor, dtype=torch.float32).view(dtype=torch.int32))

        def prepare_tensor_args(self, requests):
            return as_float([r.state.logit_processor_kwargs.get("top_p", 1.0) for r in requests])

        def __call__(self, scores, tensor_arg):
            top_p = float_arg(tensor_arg, scores.size(0))
            sorted_logits, sorted_indices = torch.sort(scores, descending=False, dim=-1)
            cumulative = sorted_logits.softmax(dim=-1).cumsum(dim=-1)
            remove = (cumulative <= 1 - top_p) & (top_p < 1)
            remove[..., -1:] = False  # always keep the most likely token
            remove = remove.scatter(-1, sorted_indices, remove)
            return scores.masked_fill(remove, float("-inf"))

    class MinP(ContinuousBatchingLogitsProcessor):
        supported_kwargs = {"min_p": float}
        ignored_kwargs = ()

        def fill_defaults(self, int32_tensor):
            int32_tensor.copy_(torch.zeros_like(int32_tensor, dtype=torch.float32).view(dtype=torch.int32))

        def prepare_tensor_args(self, requests):
            return as_float([r.state.logit_processor_kwargs.get("min_p", 0.0) for r in requests])

        def __call__(self, scores, tensor_arg):
            min_p = float_arg(tensor_arg, scores.size(0))
            probs = scores.softmax(dim=-1)
            remove = probs < min_p * probs.max(dim=-1, keepdim=True).values
            return scores.masked_fill(remove, float("-inf"))

    class Draw(ContinuousBatchingLogitsProcessor):
        """ Inverse-CDF draw with the request's own uniform number; a negative seed means greedy. """
        supported_kwargs = {"seed": int}
        ignored_kwargs = ()

        def fill_defaults(self, int32_tensor):
            int32_tensor.copy_(torch.full_like(int32_tensor, -1.0, dtype=torch.float32).view(dtype=torch.int32))

        def prepare_tensor_args(self, requests):
            uniforms = []
            for r in requests:
                state = r.state
                seed = state.logit_processor_kwargs.get("seed", -1)
                if seed < 0:
                    uniforms.append(-1.0)
                    continue
                # Index of the token being drawn. A request the cache had to evict restarts with its
                # tokens so far folded into the prompt; count them so its draws stay aligned.
                true_prompt = getattr(state, "_true_initial_tokens", 0)
                folded = len(state.initial_tokens) - true_prompt if true_prompt else 0
                index = state.generated_len() + folded
                uniforms.append(_SAMPLER_STATE.draw(state.request_id, seed, index))
            return as_float(uniforms)

        def __call__(self, scores, tensor_arg):
            u = float_arg(tensor_arg, scores.size(0))  # [B, 1]
            probs = scores.softmax(dim=-1)
            cdf = probs.cumsum(dim=-1)
            target = u.clamp(min=0.0, max=1.0 - 2.0**-24) * cdf[..., -1:]
            # First token whose cumulative probability exceeds the target: it has non-zero probability.
            token = (cdf <= target).sum(dim=-1, keepdim=True).clamp(max=scores.size(-1) - 1)
            chosen = torch.full_like(scores, float("-inf")).scatter(-1, token, 0.0)
            return torch.where(u < 0, scores, chosen)

    return [Temperature, TopK, TopP, MinP, Draw]


class _LazySamplers:
    """ The sampler classes, defined on first use so importing this module does not import torch. """

    def __init__(self):
        self._classes = None

    def __iter__(self):
        if self._classes is None:
            self._classes = _define_samplers()
        return iter(self._classes)


_SAMPLERS = _LazySamplers()


def _quantization_config(name: str):
    """ Quantisation through transformers' HfQuantizer slot. Only torchnative provides one today. """
    try:
        from torchnative.quant import TorchnativeConfig
    except ImportError as e:
        raise ValueError(f"Quantisation '{name}' needs torchnative (uv sync --extra torchnative).") from e
    return TorchnativeConfig(name)
