"""
The serving engine (SPEC S1.11): one transformers-based engine for every model.

It uses only the public `torch` / `transformers` API, so it runs on whatever `import torch`
resolves to: upstream PyTorch, or torchnative, which replaces `import torch` on device.
"""
from typing import Dict, Generator, List, Optional, Union
import threading
import os


class Engine:
    """
    Loads one causal LM and streams chat completions from it.

    One generation runs at a time per engine; concurrent callers wait their turn (#36).
    A generation stops when its `cancel` event is set or its stream is closed (#35).
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
    ):
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
        self._lock = threading.Lock()

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
        **kwargs
    ) -> Generator[str, None, None]:
        """
        Stream the reply to `messages` as text chunks.

        `stream` is accepted for compatibility with the model layer and ignored: the reply is
        always produced as a stream. `max_new_tokens <= 0` means "up to the context length".
        Extra keyword arguments go to `generate` unchanged.
        """
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList, TextIteratorStreamer

        stop = threading.Event()

        class _Stop(StoppingCriteria):
            def __call__(self, input_ids, scores, **_):
                return stop.is_set() or (cancel is not None and cancel.is_set())

        with self._lock:
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tools=tools or None,
                chat_template=self.chat_template,
                add_generation_prompt=True,
                tokenize=False,
            )
            inputs = self.tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(self.model.device)
            prompt_length = inputs["input_ids"].shape[1]
            if max_new_tokens <= 0:
                if self.context_length is None:
                    raise ValueError("max_new_tokens must be positive when the context length is unknown.")
                max_new_tokens = self.context_length - prompt_length
            if max_new_tokens <= 0:
                raise ValueError(f"The prompt ({prompt_length} tokens) exceeds the token limit ({self.context_length}).")

            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
            do_sample = temperature > 0
            generation_kwargs = dict(
                **inputs,
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


def _quantization_config(name: str):
    """ Quantisation through transformers' HfQuantizer slot. Only torchnative provides one today. """
    try:
        from torchnative.quant import TorchnativeConfig
    except ImportError as e:
        raise ValueError(f"Quantisation '{name}' needs torchnative (uv sync --extra torchnative).") from e
    return TorchnativeConfig(name)
