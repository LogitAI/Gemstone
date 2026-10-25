from datetime import datetime
from typing import Optional
from uuid import uuid4
import logging
import os
import threading
import time

from .models.config import time_prefix

logger = logging.getLogger("gemstone.session")

# Idle seconds after which a session expires (GEMSTONE_SESSION_TTL; 0 = never). Default: 24 hours.
DEFAULT_SESSION_TTL = 24 * 3600.0
# Total bytes across all sessions' DynamicCache instances before the oldest session's KV cache is dropped.
DEFAULT_MAX_SESSION_KV_MEMORY = 512 * 1000 * 1000
clock = time.monotonic  # replaced by a fake clock in tests


def configure_logging() -> None:
    """
    Set the level of the `gemstone` loggers from GEMSTONE_LOG_LEVEL (default INFO; DEBUG also logs
    prompts and answers) and make sure they reach the console in uvicorn's format.
    """
    name = os.environ.get("GEMSTONE_LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelName(name)
    log = logging.getLogger("gemstone")
    log.setLevel(level if isinstance(level, int) else logging.INFO)
    if not any(getattr(h, "_gemstone", False) for h in log.handlers):
        handler = logging.StreamHandler()
        handler._gemstone = True
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
        log.addHandler(handler)
        log.propagate = False


def session_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("GEMSTONE_SESSION_TTL", DEFAULT_SESSION_TTL)))
    except ValueError:
        return DEFAULT_SESSION_TTL


def max_session_kv_memory() -> int:
    value = os.environ.get("GEMSTONE_MAX_SESSION_KV_MEMORY")
    if value is None or not value.strip():
        return DEFAULT_MAX_SESSION_KV_MEMORY
    from .registry import parse_bytes
    try:
        return max(0, parse_bytes(value))
    except ValueError:
        return DEFAULT_MAX_SESSION_KV_MEMORY


def _cache_memory_bytes(cache) -> int:
    total = 0
    for layer in getattr(cache, "layers", ()) or ():
        for tensor in (getattr(layer, "keys", None), getattr(layer, "values", None)):
            if tensor is not None and hasattr(tensor, "numel") and hasattr(tensor, "element_size"):
                total += int(tensor.numel() * tensor.element_size())
    return total


class SessionKVCache:
    """
    One session's `DynamicCache` (`past_key_values`) and the token sequence it covers (SPEC S1.4, #211).
    On the next turn, `prepare` crops the cache back to the longest common prefix with the new prompt
    tokens (leaving at least one suffix token to prefill).
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.hf_id: Optional[str] = None
        self.token_ids: list[int] = []
        self.past_key_values = None
        self.memory_bytes: int = 0

    def seq_length(self) -> int:
        if self.past_key_values is None:
            return 0
        return int(self.past_key_values.get_seq_length())

    def clear(self) -> None:
        self.hf_id = None
        self.token_ids = []
        self.past_key_values = None
        self.memory_bytes = 0

    def store(self, hf_id: str, token_ids: list[int], past_key_values) -> None:
        seq_len = past_key_values.get_seq_length() if past_key_values is not None else 0
        if past_key_values is None or seq_len <= 0:
            self.clear()
            return
        self.hf_id = hf_id
        self.token_ids = list(token_ids[:seq_len])
        self.past_key_values = past_key_values
        self.memory_bytes = _cache_memory_bytes(past_key_values)

    def prepare(self, hf_id: str, prompt_ids: list[int]):
        if self.past_key_values is None or self.hf_id != hf_id or not prompt_ids:
            self.clear()
            return None
        seq_len = min(self.seq_length(), len(self.token_ids))
        max_common = min(seq_len, len(prompt_ids) - 1)
        common = 0
        while common < max_common and self.token_ids[common] == prompt_ids[common]:
            common += 1
        if common <= 0:
            self.clear()
            return None
        if self.seq_length() > common:
            self.past_key_values.crop(-(self.seq_length() - common))
            self.token_ids = self.token_ids[:common]
            self.memory_bytes = _cache_memory_bytes(self.past_key_values)
        return self.past_key_values


# The model catalogue lives in the registry (registry.CATALOGUE, SPEC S1.2).

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
WEBPACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webpack")


class Session:
    """
    Session Manager. A session holds a model name and its tool-result cache, never an engine:
    the registry (registry.py, SPEC S1.14) loads, shares and unloads models.
    """
    __sessions: dict[str, 'Session'] = {}
    # Routes run on worker threads and the WebSocket on the event loop: the map changes only under
    # this lock, so it holds without the GIL too (#180)
    __lock = threading.Lock()
    _initialized = False

    def __new__(cls, model_id: str = None, session_id: str = None):
        """ Create a new session or return an existing one """
        cls.sweep()
        with cls.__lock:
            session = cls.__sessions.get(session_id) if session_id else None
            if session is not None:
                session.last_used = clock()
                return session
        return super().__new__(cls)

    def __init__(self, model_id: str = None, session_id: str = None):
        """ Create a new session for the specified model """
        if not self._initialized:
            self._initialized = True

            if model_id is None:
                raise ValueError("Model ID must be specified")
            self.model_id = model_id
            # The random suffix keeps sessions created in the same second apart (each has its own cache)
            self.session_id = f"{model_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
            self.last_used = clock()
            self.created_prefix = time_prefix()
            self.tool_call_caches: dict[str, str] = {}  # tool call id -> result (S1.6)
            self.kv_cache = SessionKVCache()  # per-session DynamicCache (S1.4, #211)
            with self.__lock:
                self.__sessions[self.session_id] = self
                count = len(self.__sessions)
            logger.info("Session %s is CREATED for model %s (%d open)", self.session_id, model_id, count)

    @classmethod
    def sweep(cls) -> None:
        """ Drop the sessions idle longer than the TTL, with their tool and KV caches. """
        ttl = session_ttl()
        if ttl <= 0:
            return
        now = clock()
        with cls.__lock:
            expired = [(sid, cls.__sessions.pop(sid)) for sid, s in list(cls.__sessions.items()) if now - s.last_used > ttl]
        for sid, session in expired:
            logger.info("Session %s EXPIRED after %.0f s idle", sid, ttl)
            session.tool_call_caches.clear()
            kv = getattr(session, "kv_cache", None)
            if kv is not None:
                kv.clear()

    @classmethod
    def close(cls, session_id: str):
        """ Close the session. The model stays resident for its keep_alive (S1.14). """
        with cls.__lock:
            session = cls.__sessions.pop(session_id, None)
        if session is None:
            raise ValueError(f"Session {session_id} not found")
        session.tool_call_caches.clear()
        kv = getattr(session, "kv_cache", None)
        if kv is not None:
            kv.clear()
        logger.info("Session %s is DELETED for model %s", session_id, session.model_id)

    @classmethod
    def clear_model_caches(cls, hf_id: Optional[str] = None) -> None:
        """ Free the KV caches of sessions using `hf_id` (or every model when None) on unload/eviction (S1.14). """
        with cls.__lock:
            sessions = list(cls.__sessions.values())
        for session in sessions:
            kv = getattr(session, "kv_cache", None)
            if kv is not None and (hf_id is None or kv.hf_id == hf_id):
                kv.clear()

    @classmethod
    def enforce_kv_budget(cls) -> None:
        """ Drop the oldest sessions' KV caches until total session KV memory fits `GEMSTONE_MAX_SESSION_KV_MEMORY`. """
        budget = max_session_kv_memory()
        if budget <= 0:
            return
        with cls.__lock:
            sessions = sorted(
                (s for s in cls.__sessions.values() if getattr(getattr(s, "kv_cache", None), "memory_bytes", 0) > 0),
                key=lambda s: s.last_used,
            )
        total = sum(s.kv_cache.memory_bytes for s in sessions)
        while total > budget and sessions:
            oldest = sessions.pop(0)
            total -= oldest.kv_cache.memory_bytes
            oldest.kv_cache.clear()
