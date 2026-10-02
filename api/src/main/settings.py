from datetime import datetime
from uuid import uuid4
import os


# The model catalogue lives in the registry (registry.CATALOGUE, SPEC S1.2).

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../test/static")
WEBPACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../test/webpack")


class Session:
    """
    Session Manager. A session holds a model name and its tool-result cache, never an engine:
    the registry (registry.py, SPEC S1.14) loads, shares and unloads models.
    """
    __sessions: dict[str, 'Session'] = {}
    _initialized = False

    def __new__(cls, model_id: str = None, session_id: str = None):
        """ Create a new session or return an existing one """
        if session_id and session_id in cls.__sessions:
            return cls.__sessions[session_id]
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
            print("INFO:     Session", self.session_id, "is CREATED for model", model_id)
            self.__sessions[self.session_id] = self
            print("INFO:     Current sessions:", list(self.__sessions))
            self.tool_call_caches: dict[str, str] = {}  # tool call id -> result (S1.6)

    @classmethod
    def close(cls, session_id: str):
        """ Close the session. The model stays resident for its keep_alive (S1.14). """
        if session_id in cls.__sessions:
            print("INFO:     Session", session_id, "is DELETED for model", cls.__sessions[session_id].model_id)
            cls.__sessions[session_id].tool_call_caches.clear()
            del cls.__sessions[session_id]
            print("INFO:     Current sessions:", list(cls.__sessions))
        else:
            raise ValueError(f"Session {session_id} not found")
