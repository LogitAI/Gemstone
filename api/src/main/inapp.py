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
"""
import threading
from typing import Iterator, List, Optional

from . import registry as registry_module
from .models.config import ChatHistory
from .registry import DEFAULT_KEEP_ALIVE


EOS = "<EOS>"


def _registry(registry):
    return registry if registry is not None else registry_module.registry


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
