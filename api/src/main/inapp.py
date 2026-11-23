"""
The in-app entry point (SPEC S3.1, #42; S3.2, #190).

The client's `InAppEngine` calls these two functions through Python Multiplatform when it runs
Gemstone's engine inside the app instead of talking to a server. They use the same registry, model
classes and leases as the server, and they never touch the network:

- `models()` lists the models stored on the device: the local store's models (under their catalogue
  name when they have one) and the derived models of them. A catalogue name whose weights are not on
  the device is left out, because using it would need a download.
- `chat(model_id, history, message)` yields the frames the WebSocket stream sends (S1.4): text,
  `<think>`, `</think>`, `<tool_call>...</tool_call>`, then `<EOS>`. The lease is released however the
  stream ends, including when the app stops reading and closes the generator. A model that is not on
  the device raises `LookupError`.
- `configure(...)` sets the app's own model directory, residency limits, thread count, device and
  weight format; until it is called, the server's registry and environment apply.
"""
import os
import threading
from typing import Iterator, List, Optional, Union

from . import registry as registry_module
from .models.config import ChatHistory
from .registry import DEFAULT_KEEP_ALIVE, AliasStore, HFStore, Registry, model_class_for, parse_bytes


EOS = "<EOS>"

_lock = threading.Lock()
_configured: Optional[Registry] = None


def configure(models_dir: Optional[str] = None, *, max_memory: Union[int, str, None] = None,
              max_loaded: int = 1, threads: Optional[int] = None, device: Optional[str] = None,
              quantization: Optional[str] = None, engine_class=None) -> Registry:
    """
    The registry `models()` and `chat()` use from now on (SPEC S3.1). `models_dir` is laid out like
    `HF_HOME` (repositories in `hub/`, derived models in `gemstone/aliases.json`); None keeps the
    default Hugging Face cache. `max_memory` is bytes or a size such as "3GB" (0 = no budget).
    `threads` sets torch's intra-op thread count. `device` and `quantization` are the engine options;
    a model class's own options win. A malformed value raises ValueError naming the setting, and the
    earlier configuration stays. The earlier registry's models are unloaded. `engine_class` replaces
    `engine.Engine` (tests).
    """
    from .settings import parse_device
    device = parse_device(device, "device")
    try:
        memory = 0 if max_memory is None else max(0, parse_bytes(max_memory))
    except ValueError as e:
        raise ValueError(f"max_memory={max_memory!r} is not a size: use bytes or a number with a unit, e.g. 3GB.") from e
    if not isinstance(max_loaded, int) or max_loaded < 1:
        raise ValueError(f"max_loaded={max_loaded!r}: at least one model must be loadable.")
    if threads is not None and (not isinstance(threads, int) or threads < 1):
        raise ValueError(f"threads={threads!r}: use a positive thread count, or None for torch's default.")

    hub = os.path.join(models_dir, "hub") if models_dir is not None else None
    aliases = AliasStore(os.path.join(models_dir, "gemstone", "aliases.json")) if models_dir is not None else None
    options = {"device": device, **({"quantization": quantization} if quantization else {})}

    def load(hf_id: str):
        engine = engine_class
        if engine is None:
            from .engine import Engine as engine
        cls = model_class_for(hf_id)
        return engine(hf_id, context_length=cls.context_length or None, local_files_only=True,
                      cache_dir=hub, **{**options, **cls.engine_options})

    if threads is not None:
        import torch
        torch.set_num_threads(threads)
    new = Registry(loader=load, store=HFStore(hub), max_loaded=max_loaded, max_memory=memory, aliases=aliases)
    global _configured
    with _lock:
        old, _configured = _configured, new
    if old is not None:
        old.unload()
    return new


def current() -> Registry:
    """ The registry `models()` and `chat()` use: the configured one, else the server's. """
    with _lock:
        return _configured if _configured is not None else registry_module.registry


def reset() -> None:
    """ Back to the server's registry, unloading the configured one's models (tests). """
    global _configured
    with _lock:
        old, _configured = _configured, None
    if old is not None:
        old.unload()


def _registry(registry):
    return registry if registry is not None else current()


def models(registry=None) -> List[dict]:
    """ The models stored on the device: `id`, `hf_id`, `model_name`, `model_description`. """
    reg = _registry(registry)
    return [m for m in reg.models() if reg.store.get(m["hf_id"]) is not None]


def chat(model_id: str, history: List[dict], message: str, registry=None,
         cancel: Optional[threading.Event] = None) -> Iterator[str]:
    """
    The reply to `message` after `history` (`[{"role", "content"}]`), one protocol frame per item,
    ending with `<EOS>`. `cancel`, when set, stops generation early.
    """
    reg = _registry(registry)
    hf_id = reg.resolve(model_id)
    if reg.store.get(hf_id) is None:
        raise LookupError(f"model '{model_id}' is not on this device")
    lease = reg.acquire(hf_id, DEFAULT_KEEP_ALIVE, False)  # fetch=False: never download
    cancel = cancel if cancel is not None else threading.Event()
    tokens = None
    try:
        chat_history = ChatHistory()
        chat_history.extend(history)
        tokens = lease.model.chat(chat_history, message, cancel=cancel)
        for token in tokens:
            if token:
                yield token
        yield EOS
    finally:
        cancel.set()
        try:
            if tokens is not None:
                tokens.close()
        finally:
            reg.release(lease)
