from datetime import datetime
from uuid import uuid4
import logging
import os
import time

logger = logging.getLogger("gemstone.session")

# Idle seconds after which a session expires (GEMSTONE_SESSION_TTL; 0 = never). Default: 24 hours.
DEFAULT_SESSION_TTL = 24 * 3600.0
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


# The model catalogue lives in the registry (registry.CATALOGUE, SPEC S1.2).

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
WEBPACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webpack")


class Session:
    """
    Session Manager. A session holds a model name and its tool-result cache, never an engine:
    the registry (registry.py, SPEC S1.14) loads, shares and unloads models.
    """
    __sessions: dict[str, 'Session'] = {}
    _initialized = False

    def __new__(cls, model_id: str = None, session_id: str = None):
        """ Create a new session or return an existing one """
        cls.sweep()
        if session_id and session_id in cls.__sessions:
            session = cls.__sessions[session_id]
            session.last_used = clock()
            return session
        else:
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
            self.__sessions[self.session_id] = self
            logger.info("Session %s is CREATED for model %s (%d open)", self.session_id, model_id, len(self.__sessions))
            self.tool_call_caches: dict[str, str] = {}  # tool call id -> result (S1.6)

    @classmethod
    def sweep(cls) -> None:
        """ Drop the sessions idle longer than the TTL, with their tool caches. """
        ttl = session_ttl()
        if ttl <= 0:
            return
        now = clock()
        for sid in [sid for sid, s in cls.__sessions.items() if now - s.last_used > ttl]:
            logger.info("Session %s EXPIRED after %.0f s idle", sid, ttl)
            cls.__sessions.pop(sid).tool_call_caches.clear()

    @classmethod
    def close(cls, session_id: str):
        """ Close the session. The model stays resident for its keep_alive (S1.14). """
        if session_id in cls.__sessions:
            cls.__sessions[session_id].tool_call_caches.clear()
            logger.info("Session %s is DELETED for model %s", session_id, cls.__sessions.pop(session_id).model_id)
        else:
            raise ValueError(f"Session {session_id} not found")
