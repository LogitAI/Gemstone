"""
Model management for the Ollama-compatible API (SPEC S1.14): model names, the local model store
(the Hugging Face cache) and residency.

Residency: one model is resident at a time. Loading another one waits until the resident model has
no generation in flight, then unloads it. After a request the model stays loaded for its
`keep_alive` (default 5 minutes; 0 unloads at once; negative keeps it loaded until another model
replaces it), and an idle model is never unloaded while a generation is running.

The registry is independent of the chat app's sessions (`settings.Session`): they hold their own
model objects.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional
import gc
import json
import os
import re
import sys
import threading
import time


# Ollama-style and Gemstone names that map to a Hugging Face model id. Any other name containing
# a "/" is taken to be a Hugging Face id itself (an optional ":latest" tag is ignored).
MODEL_ALIASES = {
    "qwen3": "Qwen/Qwen3-0.6B",          # Gemstone id (settings.MODEL_LIST, models/qwen3)
    "default": "Qwen/Qwen3-0.6B",        # Gemstone alias of qwen3
    "qwen3:0.6b": "Qwen/Qwen3-0.6B",     # Ollama library name
    "qwen3:latest": "Qwen/Qwen3-0.6B",
    "smollm2:135m": "HuggingFaceTB/SmolLM2-135M-Instruct",  # Ollama's smollm2:135m is the instruct model
}

DEFAULT_KEEP_ALIVE = 300.0  # seconds, as Ollama
FOREVER = "9999-12-31T23:59:59Z"  # reported as expires_at for a negative keep_alive

# Files fetched by /api/pull: weights as safetensors plus config, tokenizer and chat template.
PULL_PATTERNS = ["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja", "*.tiktoken"]


def resolve_model_name(name: str) -> str:
    """ Map a request's model name to a Hugging Face model id, or raise LookupError. """
    if not isinstance(name, str) or not name.strip():
        raise LookupError("model is required")
    name = name.strip()
    if name in MODEL_ALIASES:
        return MODEL_ALIASES[name]
    if name.lower() in MODEL_ALIASES:
        return MODEL_ALIASES[name.lower()]
    repo, _, tag = name.partition(":")
    if "/" in repo and tag in ("", "latest") and re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return repo
    raise LookupError(f"model '{name}' not found")


_DURATION = re.compile(r"(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|ms|s|m|h)")
_UNITS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_keep_alive(value) -> float:
    """
    Ollama's keep_alive: a number of seconds, or a Go duration string ("5m", "1h30m", "250ms").
    None means the default (5 minutes); 0 unloads at once; a negative value keeps the model loaded.
    """
    if value is None:
        return DEFAULT_KEEP_ALIVE
    if isinstance(value, bool):
        raise ValueError(f"invalid keep_alive: {value!r}")
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        pass
    sign = -1 if text.startswith("-") else 1
    body = text.lstrip("+-")
    if not body or _DURATION.sub("", body):
        raise ValueError(f"invalid keep_alive duration: {value!r}")
    seconds = sum(float(n) * _UNITS[u] for n, u in _DURATION.findall(body))
    return sign * (int(seconds) if seconds == int(seconds) else seconds)


@dataclass
class LocalModel:
    """ A model in the local store. """
    hf_id: str
    size: int
    modified_at: str
    digest: str
    path: Optional[str]
    family: str = ""
    context_length: Optional[int] = None
    chat_template: str = ""

    @property
    def capabilities(self) -> List[str]:
        caps = ["completion"]
        if "tools" in self.chat_template:
            caps.append("tools")
        if "<think>" in self.chat_template:
            caps.append("thinking")
        return caps

    def details(self) -> dict:
        return dict(
            parent_model="", format="safetensors", family=self.family,
            families=[self.family] if self.family else [], parameter_size="", quantization_level="",
        )


class HFStore:
    """ The local Hugging Face cache (`HF_HOME`) as the model store. """

    def list(self) -> List[LocalModel]:
        return [m for m in (self._describe(r) for r in self._repos()) if m is not None]

    def get(self, hf_id: str) -> Optional[LocalModel]:
        for repo in self._repos():
            if repo.repo_id == hf_id:
                return self._describe(repo)
        return None

    def delete(self, hf_id: str) -> bool:
        from huggingface_hub import scan_cache_dir
        try:
            cache = scan_cache_dir()
        except Exception:
            return False
        hashes = [rev.commit_hash for repo in cache.repos
                  if repo.repo_type == "model" and repo.repo_id == hf_id for rev in repo.revisions]
        if not hashes:
            return False
        cache.delete_revisions(*hashes).execute()
        return True

    def download(self, hf_id: str) -> None:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import RepositoryNotFoundError
        try:
            snapshot_download(hf_id, allow_patterns=PULL_PATTERNS)
        except RepositoryNotFoundError as e:
            raise LookupError(f"model '{hf_id}' not found on the Hugging Face Hub") from e

    @staticmethod
    def _repos() -> Iterable:
        from huggingface_hub import scan_cache_dir
        try:
            return [r for r in scan_cache_dir().repos if r.repo_type == "model"]
        except Exception:  # no cache yet
            return []

    @staticmethod
    def _describe(repo) -> Optional[LocalModel]:
        revisions = sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True)
        if not revisions:
            return None
        rev = revisions[0]
        path = str(rev.snapshot_path)
        config = _read_json(os.path.join(path, "config.json"))
        tokenizer_config = _read_json(os.path.join(path, "tokenizer_config.json"))
        template = tokenizer_config.get("chat_template") or ""
        jinja = os.path.join(path, "chat_template.jinja")
        if not template and os.path.exists(jinja):
            with open(jinja, encoding="utf-8") as f:
                template = f.read()
        if isinstance(template, list):  # named templates
            template = next((t.get("template", "") for t in template if t.get("name") == "default"), "")
        return LocalModel(
            hf_id=repo.repo_id,
            size=repo.size_on_disk,
            modified_at=_iso(datetime.fromtimestamp(rev.last_modified, timezone.utc)),
            digest=rev.commit_hash,
            path=path,
            family=config.get("model_type", ""),
            context_length=config.get("max_position_embeddings"),
            chat_template=template if isinstance(template, str) else "",
        )


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def load_engine(hf_id: str):
    """ Load a model from the local cache only; /api/pull is how a model gets there. """
    from .engine import Engine
    return Engine(hf_id, local_files_only=True)


@dataclass(eq=False)
class Resident:
    hf_id: str
    engine: object
    info: Optional[LocalModel]
    active: int = 0
    keep_alive: float = DEFAULT_KEEP_ALIVE
    expires_at: Optional[datetime] = None  # None while active, or forever
    timer: Optional[threading.Timer] = field(default=None, repr=False)


@dataclass
class Lease:
    """ One request's hold on the resident model. """
    resident: Resident
    load_duration: float  # seconds spent loading for this request

    @property
    def engine(self):
        return self.resident.engine


class Registry:
    def __init__(self, loader: Optional[Callable[[str], object]] = None, store=None):
        self.loader = loader or load_engine
        self.store = store if store is not None else HFStore()
        self._cond = threading.Condition()
        self._resident: Optional[Resident] = None
        self._loading = False

    # -- residency ---------------------------------------------------------------------------------

    def acquire(self, hf_id: str, keep_alive: float = DEFAULT_KEEP_ALIVE) -> Lease:
        """
        Load `hf_id` if it is not resident (unloading the resident model once it is idle) and hold
        it for one request. Raises LookupError when the model is not in the local store.
        Every acquire must be followed by `release`.
        """
        with self._cond:
            while True:
                r = self._resident
                if r is not None and r.hf_id == hf_id:
                    r.active += 1
                    r.keep_alive = keep_alive
                    self._cancel_timer(r)
                    return Lease(r, 0.0)
                if self._loading or (r is not None and r.active > 0):
                    self._cond.wait()
                    continue
                if r is not None:
                    self._unload_locked()
                self._loading = True
                break

        start = time.perf_counter()
        try:
            info = self.store.get(hf_id)
            if info is None:
                raise LookupError(f"model '{hf_id}' not found, try pulling it first")
            engine = self.loader(hf_id)
        except BaseException:
            with self._cond:
                self._loading = False
                self._cond.notify_all()
            raise
        with self._cond:
            self._resident = Resident(hf_id, engine, info, active=1, keep_alive=keep_alive)
            self._loading = False
            self._cond.notify_all()
            return Lease(self._resident, time.perf_counter() - start)

    def release(self, lease: Lease, keep_alive: Optional[float] = None) -> None:
        """ End a request's hold; the model then lives for its keep_alive. """
        with self._cond:
            r = lease.resident
            r.active -= 1
            if keep_alive is not None:
                r.keep_alive = keep_alive
            if r is self._resident and r.active == 0:
                self._schedule_locked(r)
            self._cond.notify_all()

    def set_keep_alive(self, hf_id: str, keep_alive: float) -> bool:
        """ Re-arm the keep_alive of a resident model (0 unloads it once idle). False if not resident. """
        with self._cond:
            r = self._resident
            if r is None or r.hf_id != hf_id:
                return False
            r.keep_alive = keep_alive
            if r.active == 0:
                self._schedule_locked(r)
            return True

    def unload(self, hf_id: Optional[str] = None) -> bool:
        """ Unload the resident model (if it is `hf_id`), waiting for its generations to finish. """
        with self._cond:
            while True:
                r = self._resident
                if r is None or (hf_id is not None and r.hf_id != hf_id):
                    return False
                if r.active == 0:
                    self._unload_locked()
                    return True
                self._cond.wait()

    def wait_unloaded(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._resident is None, timeout=timeout)

    def loaded(self) -> List[dict]:
        """ The resident models with their expiry, for /api/ps. """
        with self._cond:
            r = self._resident
            if r is None:
                return []
            if r.keep_alive < 0:
                expires = FOREVER
            elif r.active > 0 or r.expires_at is None:
                expires = _iso(datetime.now(timezone.utc) + timedelta(seconds=r.keep_alive))
            else:
                expires = _iso(r.expires_at)
            return [dict(hf_id=r.hf_id, info=r.info, expires_at=expires)]

    def _schedule_locked(self, r: Resident) -> None:
        self._cancel_timer(r)
        if r.keep_alive == 0:
            self._unload_locked()
        elif r.keep_alive < 0:
            r.expires_at = None
        else:
            r.expires_at = datetime.now(timezone.utc) + timedelta(seconds=r.keep_alive)
            r.timer = threading.Timer(r.keep_alive, self._expire, args=(r,))
            r.timer.daemon = True
            r.timer.start()

    def _expire(self, r: Resident) -> None:
        with self._cond:
            if (self._resident is r and r.active == 0 and r.expires_at is not None
                    and datetime.now(timezone.utc) >= r.expires_at - timedelta(milliseconds=50)):
                self._unload_locked()

    @staticmethod
    def _cancel_timer(r: Resident) -> None:
        if r.timer is not None:
            r.timer.cancel()
            r.timer = None
        r.expires_at = None

    def _unload_locked(self) -> None:
        r = self._resident
        if r is None:
            return
        self._cancel_timer(r)
        self._resident = None
        r.engine = None
        self._cond.notify_all()
        gc.collect()
        torch = sys.modules.get("torch")  # free accelerator memory only if torch is already in use
        if torch is not None:
            for backend in ("cuda", "mps"):
                try:
                    mod = getattr(torch, backend)
                    if mod.is_available():
                        mod.empty_cache()
                except Exception:
                    pass

    # -- the local store ---------------------------------------------------------------------------

    def delete(self, hf_id: str) -> bool:
        """ Unload `hf_id` if resident (after its generations) and remove it from the store. """
        self.unload(hf_id)
        return self.store.delete(hf_id)


registry = Registry()
