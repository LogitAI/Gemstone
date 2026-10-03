"""
Model management (SPEC S1.14): model names, the catalogue, the local model store (the Hugging Face
cache), derived models (aliases) and residency.

The registry is the single owner of loaded models. The chat app's WebSocket (S1.4), the
OpenAI-compatible API (S1.10) and the Ollama-compatible API (S1.15) all take their engine, and the
model class wrapped around it, from `registry`; sessions (S1.3) hold a model name only.

Residency: several models may be resident at once, up to `max_loaded` models
(`GEMSTONE_MAX_LOADED_MODELS`, default 3, as Ollama's `OLLAMA_MAX_LOADED_MODELS`) and, if set, a
memory budget (`GEMSTONE_MAX_MODEL_MEMORY`). Loading a model that does not fit evicts the least
recently used model that has no generation in flight; a model with an active lease is never
evicted. When every resident model is busy, the load waits for one to become idle, for at most
`load_timeout` (`GEMSTONE_LOAD_TIMEOUT`, default 5 minutes, as `OLLAMA_LOAD_TIMEOUT`), then fails
with `ModelBusy`. After a request a model stays loaded for its own `keep_alive` (default 5 minutes;
0 unloads at once; negative keeps it until it is evicted), and an idle model is never unloaded
while a generation is running.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple
import gc
import importlib
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time

from .engine import ModelBusy  # noqa: F401  (a load, or a request, that waited for a busy model; defined with the engine)


@dataclass(frozen=True)
class CatalogueEntry:
    """ A Gemstone model: its weights, the name the app shows, and its model class. """
    hf_id: str
    model_name: str
    model_description: str
    module: str  # api/src/main/models/<module>, whose `Model` is the model class


# Gemstone's own models (S1.7), listed by GET /api/models and /v1/models.
CATALOGUE = dict(
    qwen3=CatalogueEntry("Qwen/Qwen3-0.6B", "Qwen 3", "Qwen 3 0.6B", "qwen3"),
)
CATALOGUE["default"] = CATALOGUE["qwen3"]

# Ollama-style and Gemstone names that map to a Hugging Face model id. Any other name containing
# a "/" is taken to be a Hugging Face id itself (an optional ":latest" tag is ignored).
MODEL_ALIASES = {
    **{name: entry.hf_id for name, entry in CATALOGUE.items()},  # qwen3, default
    "qwen3:0.6b": "Qwen/Qwen3-0.6B",     # Ollama library name
    "qwen3:latest": "Qwen/Qwen3-0.6B",
    "smollm2:135m": "HuggingFaceTB/SmolLM2-135M-Instruct",  # Ollama's smollm2:135m is the instruct model
}

DEFAULT_KEEP_ALIVE = 300.0  # seconds, as Ollama
DEFAULT_MAX_LOADED = 3  # resident models, as Ollama's OLLAMA_MAX_LOADED_MODELS (3 per GPU)
DEFAULT_LOAD_TIMEOUT = 300  # seconds a load may wait for a busy model, as OLLAMA_LOAD_TIMEOUT
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


def in_catalogue(name: str) -> bool:
    """ True for a Gemstone model name (`qwen3`, `default`). """
    return isinstance(name, str) and name.strip().lower() in CATALOGUE


_plain_classes: dict = {}


def model_class_for(hf_id: str) -> type:
    """
    The model class that wraps `hf_id`'s engine: the Gemstone class (system prompt, sampling
    defaults, server-side tools) for a catalogue model, otherwise a plain `BaseModel` subclass
    with no tools.
    """
    for entry in CATALOGUE.values():
        if entry.hf_id == hf_id:
            return importlib.import_module(f"{__package__}.models.{entry.module}").Model
    if hf_id not in _plain_classes:
        from .models.base import BaseModel
        from .utils import FunctionCalling
        _plain_classes[hf_id] = type("PlainModel", (BaseModel,), dict(
            model_id=hf_id, supported_tools=FunctionCalling.DISABLED))
    return _plain_classes[hf_id]


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


_BYTES = re.compile(r"(\d+(?:\.\d*)?|\.\d+)\s*([kmgt]i?b|b)?", re.IGNORECASE)
_BYTE_UNITS = {"b": 1, "kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12,
               "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40}


def parse_bytes(value) -> int:
    """ A byte count: an integer, or a number with a unit (`8GB` = 8e9, `8GiB` = 8 * 2**30). """
    match = _BYTES.fullmatch(str(value).strip())
    if match is None:
        raise ValueError(f"invalid byte size: {value!r}")
    number, unit = match.groups()
    return int(float(number) * _BYTE_UNITS[(unit or "b").lower()])


def _env(name: str, parse, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return parse(value)
    except ValueError as e:
        raise ValueError(f"{name}: {e}") from e


_ALIAS_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*(?::[a-z0-9._-]+)?")


def alias_key(name) -> Optional[str]:
    """
    The stored form of a derived model's name (`name` becomes `name:latest`), or None when `name`
    cannot be one: it must be an Ollama-style `name[:tag]` without a "/" (so it never shadows a
    Hugging Face id).
    """
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    if not _ALIAS_NAME.fullmatch(name):
        return None
    return name if ":" in name else name + ":latest"


def is_builtin_name(name: str) -> bool:
    """ A name `resolve_model_name` already knows (`qwen3`, `smollm2:135m`, ...); never an alias. """
    for candidate in (name, alias_key(name)):
        try:
            if candidate:
                resolve_model_name(candidate)
                return True
        except LookupError:
            pass
    return False


# Ollama parameters a derived model may set (`/api/create`), with their types. They become the
# default `options` of every request on that model.
DERIVED_PARAMETERS = {
    "temperature": float, "top_p": float, "top_k": int, "min_p": float, "typical_p": float,
    "repeat_penalty": float, "seed": int, "num_predict": int, "stop": list,
    "num_ctx": int, "repeat_last_n": int, "presence_penalty": float, "frequency_penalty": float,
}


def format_parameters(parameters: dict) -> str:
    """ Parameters as Ollama's `/api/show` prints them: one `name value` line each, sorted. """
    lines = []
    for name in sorted(parameters):
        value = parameters[name]
        for v in (value if isinstance(value, list) else [value]):
            lines.append(f"{name} {json.dumps(v) if isinstance(v, str) else v}")
    return "\n".join(lines)


def human_number(n: int) -> str:
    """ A parameter count as Ollama writes it: `596.05M`, `8B`, `1.5B`. """
    for size, unit, digits in ((10**9, "B", 1), (10**6, "M", 2), (10**3, "K", 0)):
        if n >= size:
            value = n / size
            return f"{value:.0f}{unit}" if value == int(value) else f"{value:.{digits}f}{unit}"
    return str(n)


# Bytes per element of the safetensors dtypes.
_DTYPE_BYTES = {"F64": 8, "I64": 8, "U64": 8, "F32": 4, "I32": 4, "U32": 4, "F16": 2, "BF16": 2,
                "I16": 2, "U16": 2, "I8": 1, "U8": 1, "BOOL": 1, "F8_E4M3": 1, "F8_E5M2": 1}


def safetensors_stats(path: Optional[str]) -> Tuple[Optional[int], Optional[int], str]:
    """
    (parameter count, weight bytes, main dtype) of the `*.safetensors` files in a snapshot directory,
    read from their headers only (the first few kilobytes of each file). (None, None, "") if none.
    """
    if not path or not os.path.isdir(path):
        return None, None, ""
    params = size = 0
    by_dtype: Dict[str, int] = {}
    found = False
    for name in sorted(os.listdir(path)):
        if not name.endswith(".safetensors"):
            continue
        try:
            with open(os.path.join(path, name), "rb") as f:
                (length,) = struct.unpack("<Q", f.read(8))
                header = json.loads(f.read(length))
        except (OSError, ValueError, struct.error):
            continue
        found = True
        for key, tensor in header.items():
            if key == "__metadata__" or not isinstance(tensor, dict):
                continue
            count = 1
            for dim in tensor.get("shape", []):
                count *= dim
            dtype = tensor.get("dtype", "")
            params += count
            size += count * _DTYPE_BYTES.get(dtype, 0)
            by_dtype[dtype] = by_dtype.get(dtype, 0) + count
    if not found:
        return None, None, ""
    return params, size, max(by_dtype, key=by_dtype.get) if by_dtype else ""


def is_causal_lm(config: dict) -> bool:
    """
    Whether a `config.json` describes a causal language model, the only kind Gemstone chats with.
    Decided from the file alone, with no network and no model load: `architectures` names the
    class (`...ForCausalLM`, `...LMHeadModel`); without it, `model_type` must be one that
    transformers' `AutoModelForCausalLM` maps.
    """
    architectures = config.get("architectures")
    if isinstance(architectures, list) and architectures:
        return any(isinstance(a, str) and a.endswith(("ForCausalLM", "LMHeadModel")) for a in architectures)
    model_type = config.get("model_type")
    if not isinstance(model_type, str) or not model_type:
        return False
    try:
        from transformers.models.auto.modeling_auto import MODEL_FOR_CAUSAL_LM_MAPPING_NAMES
    except ImportError:
        return False
    return model_type in MODEL_FOR_CAUSAL_LM_MAPPING_NAMES


def is_complete(path: str) -> bool:
    """
    Whether a snapshot directory holds a loadable model: `config.json` and every weight file. A
    sharded model (`model.safetensors.index.json`) needs each shard its index names, otherwise
    at least one `*.safetensors` file must exist. Snapshot files are symlinks into `blobs/`, so a
    link whose blob is gone counts as missing.
    """
    if not os.path.exists(os.path.join(path, "config.json")):
        return False
    index = os.path.join(path, "model.safetensors.index.json")
    if os.path.exists(index):
        shards = set(_read_json(index).get("weight_map", {}).values())
        return bool(shards) and all(os.path.exists(os.path.join(path, s)) for s in shards)
    try:
        return any(n.endswith(".safetensors") and os.path.exists(os.path.join(path, n))
                   for n in os.listdir(path))
    except OSError:
        return False


class UnsupportedModel(ValueError):
    """ A Hugging Face repository Gemstone cannot load (no safetensors weights); a pull refuses it (#119). """


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
    generation: dict = field(default_factory=dict)  # generation_config.json
    parameter_count: Optional[int] = None  # from the safetensors headers
    weight_bytes: Optional[int] = None  # the weights' size at their stored dtype
    dtype: str = ""  # the main safetensors dtype (BF16, F32, ...)
    causal_lm: bool = True  # config.json describes a causal LM (`is_causal_lm`); `HFStore.list` lists only those

    @property
    def capabilities(self) -> List[str]:
        caps = ["completion"]
        if "tools" in self.chat_template:
            caps.append("tools")
        if "<think>" in self.chat_template:
            caps.append("thinking")
        return caps

    @property
    def parameters(self) -> dict:
        """ The sampling defaults of generation_config.json, under their Ollama names. """
        names = dict(temperature="temperature", top_p="top_p", top_k="top_k", min_p="min_p",
                     typical_p="typical_p", repetition_penalty="repeat_penalty")
        return {names[k]: v for k, v in self.generation.items() if k in names and v is not None}

    def details(self, parent_model: str = "") -> dict:
        return dict(
            parent_model=parent_model, format="safetensors", family=self.family,
            families=[self.family] if self.family else [],
            parameter_size=human_number(self.parameter_count) if self.parameter_count else "",
            quantization_level=self.dtype,
        )


class HFStore:
    """ The local Hugging Face cache (`HF_HOME`) as the model store. """

    # Safetensors headers by (repo id, snapshot path): a snapshot directory is named by its commit hash.
    _stats: Dict[Tuple[str, str], tuple] = {}

    def list(self) -> List[LocalModel]:
        """ The complete causal-LM models in the cache; other repos (bert, vision, ...) are not chat models (#119). """
        return [m for m in (self._describe(r) for r in self._repos()) if m is not None and m.causal_lm]

    def get(self, hf_id: str) -> Optional[LocalModel]:
        """ The model in the cache, or None when it is absent or only partly fetched (`is_complete`). """
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
        for _ in self.download_iter(hf_id):
            pass

    def download_iter(self, hf_id: str) -> Iterator[dict]:
        """
        Download the repository's `PULL_PATTERNS` files one by one, yielding Ollama pull progress
        (`status`, `digest`, `total`, `completed`) before and after each file. The Hub reports file
        sizes, so the progress is per file, not per byte.

        Each file is fetched at the revision `main`, not at the commit hash: huggingface_hub writes
        `refs/main` only for a named revision, and loading offline with the default revision
        resolves through that ref. (`snapshot_download` would do the same but report no per-file
        progress.) A push to `main` in the middle of a pull can leave the files in two snapshots;
        `is_complete` then rejects the newest one and the next acquire fetches it whole.
        """
        import huggingface_hub
        from huggingface_hub.errors import RepositoryNotFoundError
        try:
            info = huggingface_hub.HfApi().model_info(hf_id, files_metadata=True)
        except RepositoryNotFoundError as e:
            raise LookupError(f"model '{hf_id}' not found on the Hugging Face Hub") from e
        files = [(f.rfilename, f.size or 0) for f in (info.siblings or [])
                 if any(fnmatch(f.rfilename, p) for p in PULL_PATTERNS)]
        if not any(name.endswith(".safetensors") for name, _ in files):
            raise UnsupportedModel(
                f"model '{hf_id}' has no *.safetensors weights, the only format Gemstone loads "
                f"(it may ship .bin, GGUF or other weights): nothing was downloaded")
        for name, size in files:
            event = dict(status=f"pulling {name}", digest=name, total=size, completed=0)
            yield dict(event)
            huggingface_hub.hf_hub_download(hf_id, name, revision="main")
            yield {**event, "completed": size}

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
        if not is_complete(path):
            return None
        config = _read_json(os.path.join(path, "config.json"))
        tokenizer_config = _read_json(os.path.join(path, "tokenizer_config.json"))
        template = tokenizer_config.get("chat_template") or ""
        jinja = os.path.join(path, "chat_template.jinja")
        if not template and os.path.exists(jinja):
            with open(jinja, encoding="utf-8") as f:
                template = f.read()
        if isinstance(template, list):  # named templates
            template = next((t.get("template", "") for t in template if t.get("name") == "default"), "")
        key = (repo.repo_id, path)  # the snapshot path holds the cache location and the revision hash
        if key not in HFStore._stats:
            HFStore._stats[key] = safetensors_stats(path)
        params, weight_bytes, dtype = HFStore._stats[key]
        return LocalModel(
            hf_id=repo.repo_id,
            size=repo.size_on_disk,
            modified_at=_iso(datetime.fromtimestamp(rev.last_modified, timezone.utc)),
            digest=rev.commit_hash,
            path=path,
            family=config.get("model_type", ""),
            context_length=config.get("max_position_embeddings"),
            chat_template=template if isinstance(template, str) else "",
            generation=_read_json(os.path.join(path, "generation_config.json")),
            parameter_count=params,
            weight_bytes=weight_bytes,
            dtype=dtype,
            causal_lm=is_causal_lm(config),
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


class AliasStore:
    """
    Derived models (`/api/copy`, `/api/create`): name -> {"from": Hugging Face id, "system",
    "parameters", "created_at"}. They are a few hundred bytes of JSON in one file, by default
    `$HF_HOME/gemstone/aliases.json` (`GEMSTONE_ALIASES` overrides it): next to the cache whose
    models they name, so they move with `HF_HOME`, but outside its `hub/` directory, which
    `huggingface_hub.scan_cache_dir` expects to hold only repositories. `path=None` keeps them in
    memory (tests).
    """
    _DEFAULT = object()

    def __init__(self, path=_DEFAULT):
        if path is AliasStore._DEFAULT:
            path = os.environ.get("GEMSTONE_ALIASES") or self.default_path()
        self.path = path
        self._lock = threading.Lock()
        self._memory: dict = {}

    @staticmethod
    def default_path() -> str:
        from huggingface_hub import constants
        return os.path.join(constants.HF_HOME, "gemstone", "aliases.json")

    def all(self) -> dict:
        with self._lock:
            return dict(self._read())

    def get(self, name: str) -> Optional[dict]:
        return self.all().get(name)

    def set(self, name: str, record: dict) -> None:
        with self._lock:
            data = dict(self._read())
            data[name] = record
            self._write(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = dict(self._read())
            if data.pop(name, None) is None:
                return False
            self._write(data)
            return True

    def _read(self) -> dict:
        if self.path is None:
            return self._memory
        data = _read_json(self.path)
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict) -> None:
        if self.path is None:
            self._memory = data
            return
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".aliases-", suffix=".json")  # replaced atomically
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def load_engine(hf_id: str):
    """
    Load a model from the local cache only; /api/pull (or a first use of a catalogue model) is how
    a model gets there. The model class's context length and engine options apply.
    """
    from .engine import Engine
    cls = model_class_for(hf_id)
    return Engine(hf_id, context_length=cls.context_length or None, local_files_only=True, **cls.engine_options)


class ModelUnavailable(Exception):
    """
    A model that should be fetched could not be: the machine is offline, or the download failed
    (#114). Not a LookupError: the model exists, it just cannot be had now. `str()` is the full
    advice; `reason` is a short form for a WebSocket close frame.
    """

    def __init__(self, message: str, reason: str, offline: bool):
        super().__init__(message)
        self.reason, self.offline = reason, offline

    @classmethod
    def offline(cls, hf_id: str) -> "ModelUnavailable":
        return cls(f"Model '{hf_id}' is not downloaded and the server is offline, so it cannot be downloaded now. "
                   f"Connect to the internet once so it can download, or pull it while online.",
                   f"Model '{hf_id}' is offline and not downloaded. Connect once to download it.", True)

    @classmethod
    def failed(cls, hf_id: str, cause: BaseException) -> "ModelUnavailable":
        detail = (str(cause) or type(cause).__name__)[:200]
        return cls(f"The download of model '{hf_id}' failed: {detail}. Check the connection and free disk space, "
                   f"then retry or pull it again.",
                   f"The download of model '{hf_id}' failed. Retry or pull it again.", False)

    @classmethod
    def from_error(cls, hf_id: str, error: BaseException) -> "ModelUnavailable":
        """ Classify a failure while fetching `hf_id`: offline or connection errors, else a failed download. """
        import httpx
        import requests
        from huggingface_hub import constants
        from huggingface_hub.errors import LocalEntryNotFoundError, OfflineModeIsEnabled
        seen, e = [], error
        while e is not None and e not in seen:  # the cause chain: the Hub client wraps connection errors
            seen.append(e)
            if isinstance(e, (OfflineModeIsEnabled, LocalEntryNotFoundError, requests.ConnectionError,
                              requests.Timeout, httpx.TransportError, ConnectionError, TimeoutError)):
                return cls.offline(hf_id)
            e = e.__cause__ or e.__context__
        return cls.offline(hf_id) if constants.HF_HUB_OFFLINE else cls.failed(hf_id, error)


@dataclass(eq=False)
class Resident:
    hf_id: str
    engine: object
    info: Optional[LocalModel]
    model: object = None  # the model class instance wrapping `engine`
    active: int = 0
    keep_alive: float = DEFAULT_KEEP_ALIVE
    expires_at: Optional[datetime] = None  # None while active, or forever
    timer: Optional[threading.Timer] = field(default=None, repr=False)
    memory: int = 0  # bytes: what the engine reports, else the store's estimate
    last_used: float = 0.0  # time.monotonic() of the last acquire or release


@dataclass
class Lease:
    """ One request's hold on a resident model. """
    resident: Resident
    load_duration: float  # seconds spent loading for this request

    @property
    def engine(self):
        return self.resident.engine

    @property
    def model(self):
        """ The model class instance (prompt, defaults, tools) around the engine. """
        return self.resident.model


def estimate_memory(info: Optional[LocalModel]) -> int:
    """ A model's footprint before it is loaded: its safetensors weights, else its size on disk. """
    if info is None:
        return 0
    return info.weight_bytes or info.size or 0


def reported_memory(engine) -> Optional[int]:
    """ The footprint an engine reports (`memory_bytes`, an attribute or a method), if any. """
    value = getattr(engine, "memory_bytes", None)
    if callable(value):
        value = value()
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def close_engine(engine) -> None:
    """ Stop an unloaded engine's background work (the batching loop) so its memory can be freed. """
    close = getattr(engine, "close", None)
    if callable(close):
        try:
            close()
        except Exception as e:  # an unload never fails over this
            print("WARNING:  closing an unloaded engine failed:", repr(e))


class Registry:
    def __init__(self, loader: Optional[Callable[[str], object]] = None, store=None,
                 model_class: Optional[Callable[[str], type]] = None, *, max_loaded: Optional[int] = None,
                 max_memory: Optional[int] = None, load_timeout: Optional[float] = None,
                 aliases: Optional[AliasStore] = None, close: Callable[[object], None] = close_engine):
        """
        `max_loaded`, `max_memory` (bytes, 0 = no budget) and `load_timeout` (seconds) default to
        `GEMSTONE_MAX_LOADED_MODELS`, `GEMSTONE_MAX_MODEL_MEMORY` and `GEMSTONE_LOAD_TIMEOUT`.
        `close` runs on an engine once it is unloaded.
        """
        self.loader = loader or load_engine
        self.store = store if store is not None else HFStore()
        self.model_class = model_class or model_class_for
        self.max_loaded = max(1, max_loaded if max_loaded is not None
                              else _env("GEMSTONE_MAX_LOADED_MODELS", int, DEFAULT_MAX_LOADED))
        self.max_memory = max(0, max_memory if max_memory is not None
                              else _env("GEMSTONE_MAX_MODEL_MEMORY", parse_bytes, 0))
        self.load_timeout = (load_timeout if load_timeout is not None
                             else _env("GEMSTONE_LOAD_TIMEOUT", parse_keep_alive, DEFAULT_LOAD_TIMEOUT))
        self.aliases = aliases if aliases is not None else AliasStore()
        self.close = close
        self._cond = threading.Condition()
        self._residents: Dict[str, Resident] = {}
        self._loading = False

    # -- names, derived models and the catalogue ---------------------------------------------------

    def derived(self, name: str) -> Optional[dict]:
        """ The derived model (`/api/copy`, `/api/create`) called `name`, or None. """
        key = alias_key(name)
        return self.aliases.get(key) if key else None

    def resolve(self, name: str) -> str:
        """ A request's model name to a Hugging Face id, derived models included; LookupError if unknown. """
        record = self.derived(name)
        if record is not None:
            return record["from"]
        return resolve_model_name(name)

    def models(self) -> List[dict]:
        """
        The models a request can name: Gemstone's catalogue, then every other model in the local
        store under its Hugging Face id, then the derived models whose weights are in the store.
        Entries: `id`, `hf_id`, `model_name`, `model_description`.
        """
        entries = [dict(id=name, hf_id=e.hf_id, model_name=e.model_name, model_description=e.model_description)
                   for name, e in CATALOGUE.items()]
        known = {e.hf_id for e in CATALOGUE.values()}
        local = set()
        for info in self.store.list():
            local.add(info.hf_id)
            if info.hf_id not in known:
                entries.append(dict(id=info.hf_id, hf_id=info.hf_id, model_name=info.hf_id,
                                    model_description=f"{info.hf_id} (Hugging Face)"))
        for name, record in sorted(self.aliases.all().items()):
            if record.get("from") in local:
                entries.append(dict(id=name, hf_id=record["from"], model_name=name,
                                    model_description=f"{name} (from {record['from']})"))
        return entries

    def check_known(self, name: str) -> str:
        """
        The Hugging Face id of a model a session may name, without loading it: a catalogue name
        (fetched on first use), a model in the local store, or a derived model of one. Raises
        LookupError otherwise.
        """
        hf_id = self.resolve(name)
        if not in_catalogue(name) and self.store.get(hf_id) is None:
            raise LookupError(f"model '{name}' not found, try pulling it first")
        return hf_id

    # -- residency ---------------------------------------------------------------------------------

    def acquire(self, hf_id: str, keep_alive: float = DEFAULT_KEEP_ALIVE, fetch: bool = False) -> Lease:
        """
        Hold `hf_id` for one request, loading it if it is not resident. A load that does not fit
        the limits first evicts idle models, least recently used first, and waits (up to
        `load_timeout`, then `ModelBusy`) while the models it would have to evict are busy.
        Raises LookupError when the model is not in the local store, unless `fetch` is set: then
        it is downloaded first. Every acquire must be followed by `release`.
        """
        deadline = time.monotonic() + self.load_timeout
        with self._cond:
            while True:
                r = self._residents.get(hf_id)
                if r is not None:
                    r.active += 1
                    r.keep_alive = keep_alive
                    r.last_used = time.monotonic()
                    self._cancel_timer(r)
                    return Lease(r, 0.0)
                if not self._loading:  # one load at a time
                    self._loading = True
                    break
                self._wait_locked(deadline, hf_id)

        start = time.perf_counter()
        try:
            info = self.store.get(hf_id)
            if info is None and fetch:
                try:
                    self.store.download(hf_id)
                except (LookupError, ModelBusy):
                    raise
                except Exception as e:  # offline or a broken download (#114)
                    raise ModelUnavailable.from_error(hf_id, e) from e
                info = self.store.get(hf_id)
            if info is None:
                raise LookupError(f"model '{hf_id}' not found, try pulling it first")
            estimate = estimate_memory(info)
            with self._cond:
                while (victims := self._victims_locked(estimate)) is None:
                    self._wait_locked(deadline, hf_id)
                evicted = [self._detach_locked(v) for v in victims]
            self._close(evicted, "EVICTED")
            engine = self.loader(hf_id)
            model = self.model_class(hf_id)(engine=engine)
        except BaseException:
            with self._cond:
                self._loading = False
                self._cond.notify_all()
            raise
        print("INFO:     Model", hf_id, "is LOADED")
        with self._cond:
            r = Resident(hf_id, engine, info, model, active=1, keep_alive=keep_alive,
                         memory=reported_memory(engine) or estimate, last_used=time.monotonic())
            self._residents[hf_id] = r
            self._loading = False
            # The engine's own report can exceed the estimate: make room after the fact, if idle
            # models allow it.
            evicted = [self._detach_locked(v) for v in self._victims_locked(0, best_effort=True)]
            self._cond.notify_all()
        self._close(evicted, "EVICTED")
        return Lease(r, time.perf_counter() - start)

    def release(self, lease: Lease, keep_alive: Optional[float] = None) -> None:
        """ End a request's hold; the model then lives for its keep_alive. """
        unloaded = []
        with self._cond:
            r = lease.resident
            r.active -= 1
            r.last_used = time.monotonic()
            if keep_alive is not None:
                r.keep_alive = keep_alive
            if self._residents.get(r.hf_id) is r and r.active == 0:
                unloaded = self._schedule_locked(r)
            self._cond.notify_all()
        self._close(unloaded)

    def set_keep_alive(self, hf_id: str, keep_alive: float) -> bool:
        """ Re-arm the keep_alive of a resident model (0 unloads it once idle). False if not resident. """
        unloaded = []
        with self._cond:
            r = self._residents.get(hf_id)
            if r is None:
                return False
            r.keep_alive = keep_alive
            if r.active == 0:
                unloaded = self._schedule_locked(r)
        self._close(unloaded)
        return True

    def unload(self, hf_id: Optional[str] = None) -> bool:
        """
        Unload `hf_id` (every resident model if None), waiting for its generations to finish.
        False if nothing was resident.
        """
        unloaded = []
        try:
            with self._cond:
                while True:
                    targets = [r for r in self._residents.values() if hf_id is None or r.hf_id == hf_id]
                    unloaded += [self._detach_locked(r) for r in targets if r.active == 0]
                    if all(r.active == 0 for r in targets):
                        break
                    self._cond.wait()
        finally:
            self._close(unloaded)
        return bool(unloaded)

    def wait_unloaded(self, timeout: float, hf_id: Optional[str] = None) -> bool:
        """ Wait until `hf_id` (every model if None) is no longer resident. """
        with self._cond:
            return self._cond.wait_for(
                lambda: not self._residents if hf_id is None else hf_id not in self._residents, timeout=timeout)

    def loaded(self) -> List[dict]:
        """
        The resident models for /api/ps: `hf_id`, `info`, `expires_at`, `size` (bytes) and `active`
        (leases held), latest expiry first, as Ollama lists them.
        """
        with self._cond:
            entries = []
            for r in sorted(self._residents.values(), key=lambda r: r.last_used, reverse=True):
                if r.keep_alive < 0:
                    expires = FOREVER
                elif r.active > 0 or r.expires_at is None:
                    expires = _iso(datetime.now(timezone.utc) + timedelta(seconds=r.keep_alive))
                else:
                    expires = _iso(r.expires_at)
                entries.append(dict(hf_id=r.hf_id, info=r.info, expires_at=expires, size=r.memory, active=r.active))
        return sorted(entries, key=lambda e: e["expires_at"], reverse=True)  # stable: ties stay most recent first

    def _victims_locked(self, incoming: int, best_effort: bool = False) -> Optional[List[Resident]]:
        """
        The idle models to evict so that the resident models plus one of `incoming` bytes fit the
        count limit and the memory budget, least recently used first. `incoming=0` checks the
        models already resident (after a load). None when busy models stand in the way (or, with
        `best_effort`, the idle models that can go). A model alone is never over the budget.
        """
        count = len(self._residents) + (1 if incoming else 0)
        memory = sum(r.memory for r in self._residents.values()) + incoming
        idle = sorted((r for r in self._residents.values() if r.active == 0), key=lambda r: r.last_used)
        victims = []
        while count > self.max_loaded or (self.max_memory and memory > self.max_memory and count > 1):
            if not idle:
                return victims if best_effort else None
            v = idle.pop(0)
            victims.append(v)
            count -= 1
            memory -= v.memory
        return victims

    def _wait_locked(self, deadline: float, hf_id: str) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelBusy(f"server busy: could not load '{hf_id}' within {self.load_timeout}s because every "
                            f"loaded model is busy; try again later")
        self._cond.wait(remaining)

    def _schedule_locked(self, r: Resident) -> List[Resident]:
        """ Arm `r`'s keep_alive; returns `r` detached when its keep_alive is 0. """
        self._cancel_timer(r)
        if r.keep_alive == 0:
            return [self._detach_locked(r)]
        if r.keep_alive < 0:
            r.expires_at = None
        else:
            r.expires_at = datetime.now(timezone.utc) + timedelta(seconds=r.keep_alive)
            r.timer = threading.Timer(r.keep_alive, self._expire, args=(r,))
            r.timer.daemon = True
            r.timer.start()
        return []

    def _expire(self, r: Resident) -> None:
        unloaded = []
        with self._cond:
            if (self._residents.get(r.hf_id) is r and r.active == 0 and r.expires_at is not None
                    and datetime.now(timezone.utc) >= r.expires_at - timedelta(milliseconds=50)):
                unloaded = [self._detach_locked(r)]
        self._close(unloaded)

    @staticmethod
    def _cancel_timer(r: Resident) -> None:
        if r.timer is not None:
            r.timer.cancel()
            r.timer = None
        r.expires_at = None

    def _detach_locked(self, r: Resident) -> Resident:
        """ Take an idle model out of residency; `_close` then frees it outside the lock. """
        self._cancel_timer(r)
        if self._residents.get(r.hf_id) is r:
            del self._residents[r.hf_id]
        self._cond.notify_all()
        return r

    def _close(self, residents: List[Resident], why: str = "UNLOADED") -> None:
        """ Free detached models: close their engines, then collect and empty accelerator caches. """
        if not residents:
            return
        for r in residents:
            engine, r.engine, r.model = r.engine, None, None
            if engine is not None:
                self.close(engine)
            print("INFO:     Model", r.hf_id, "is", why)
        del engine
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
