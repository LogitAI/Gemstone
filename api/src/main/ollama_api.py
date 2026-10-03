"""
The Ollama-compatible API (SPEC S1.15), so existing Ollama clients can use Gemstone.

Endpoints: POST /api/chat, POST /api/generate, POST /api/embed, POST /api/embeddings (legacy),
GET /api/tags, POST /api/show, POST /api/pull, POST /api/copy, POST /api/create, DELETE /api/delete,
GET /api/ps, GET /api/version; POST /api/push answers 501. Streaming responses are NDJSON;
`"stream": false` returns one JSON object. Errors are `{"error": "..."}` with an HTTP status, as
Ollama does.

Tool calls pass through (SPEC S1.10): a `<tool_call>{json}</tool_call>` block the model emits is
returned as `message.tool_calls` and never executed. Model names resolve through
`Registry.resolve` (derived models included); residency and keep_alive are handled by
`registry.Registry`.
"""
from importlib import metadata
from typing import Iterator, List, Optional
import itertools
import json
import threading
import time

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import registry as registry_module
from .leases import LeasedStreamingResponse, acquire, once
from .registry import (DERIVED_PARAMETERS, ModelBusy, ModelUnavailable, UnsupportedModel, alias_key, format_parameters, is_builtin_name, now_iso,
                       parse_keep_alive)


router = APIRouter()

NDJSON = "application/x-ndjson"

# Ollama option -> Engine keyword. Unset options keep the engine's defaults.
OPTION_MAP = {
    "temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "min_p": "min_p",
    "typical_p": "typical_p", "repeat_penalty": "repeat_penalty", "seed": "seed",
}
# Accepted and ignored: the context length is fixed when the model loads, and the rest have no
# counterpart in the engine yet.
IGNORED_OPTIONS = {"num_ctx", "num_keep", "num_batch", "num_gpu", "main_gpu", "use_mmap", "num_thread",
                   "repeat_last_n", "presence_penalty", "frequency_penalty", "penalize_newline",
                   "mirostat", "mirostat_tau", "mirostat_eta", "tfs_z", "low_vram", "numa"}


def _registry():
    return registry_module.registry


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


class _BadRequest(Exception):
    pass


class _NotImplemented(Exception):
    """ A feature Gemstone does not offer (501). """


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise _BadRequest("invalid JSON body")
    if not isinstance(body, dict):
        raise _BadRequest("the request body must be a JSON object")
    return body


def _model_of(body: dict) -> str:
    name = body.get("model") or body.get("name")
    if not isinstance(name, str) or not name:
        raise _BadRequest("model is required")
    return name


# -- output parsing --------------------------------------------------------------------------------

THINK, THINK_END, TOOL, TOOL_END = "<think>", "</think>", "<tool_call>", "</tool_call>"


def _held_suffix(text: str, tags) -> int:
    """ Length of the longest suffix of `text` that could start one of `tags`. """
    for n in range(min(len(text), max(map(len, tags)) - 1), 0, -1):
        if any(t.startswith(text[-n:]) for t in tags):
            return n
    return 0


class OutputParser:
    """
    Splits the engine's raw text stream into content, thinking and tool calls.

    `think=True` returns reasoning as `thinking`; `think=False` drops it; `think=None` (unset) leaves
    the `<think>` block in the content as the model wrote it.
    """

    def __init__(self, think: Optional[bool]):
        self.think = think
        self.state = "content"
        self.buffer = ""
        self.strip_next = False

    def feed(self, text: str) -> List[tuple]:
        self.buffer += text
        events = []
        while True:
            if self.state == "content":
                starts = [(self.buffer.find(t), t) for t in (THINK, TOOL)]
                starts = [(i, t) for i, t in starts if i >= 0]
                if starts:
                    i, tag = min(starts)
                    self._content(events, self.buffer[:i])
                    self.buffer = self.buffer[i + len(tag):]
                    if tag == THINK:
                        self.state = "think"
                        if self.think is None:
                            events.append(("content", THINK))
                    else:
                        self.state = "tool"
                    continue
                keep = _held_suffix(self.buffer, (THINK, TOOL))
                self._content(events, self.buffer[:len(self.buffer) - keep])
                self.buffer = self.buffer[len(self.buffer) - keep:]
                return events
            end = THINK_END if self.state == "think" else TOOL_END
            i = self.buffer.find(end)
            if self.state == "think":
                if i >= 0:
                    self._thinking(events, self.buffer[:i])
                    self.buffer = self.buffer[i + len(end):]
                    self.state = "content"
                    if self.think is None:
                        events.append(("content", THINK_END))
                    else:
                        self.strip_next = True
                    continue
                keep = _held_suffix(self.buffer, (THINK_END,))
                self._thinking(events, self.buffer[:len(self.buffer) - keep])
                self.buffer = self.buffer[len(self.buffer) - keep:]
                return events
            if i < 0:
                return events
            events.append(self._tool_call(self.buffer[:i]))
            self.buffer = self.buffer[i + len(end):]
            self.state = "content"
            self.strip_next = True

    def flush(self) -> List[tuple]:
        events = []
        rest, self.buffer = self.buffer, ""
        if self.state == "content":
            self._content(events, rest)
        elif self.state == "think":
            self._thinking(events, rest)
        elif rest:
            events.append(("content", TOOL + rest))  # an unterminated tool call stays visible
        return events

    def _content(self, events, text):
        if self.strip_next:
            text = text.lstrip()
            if text:
                self.strip_next = False
        if text:
            events.append(("content", text))

    def _thinking(self, events, text):
        if not text:
            return
        if self.think is None:
            events.append(("content", text))
        elif self.think:
            events.append(("thinking", text))

    @staticmethod
    def _tool_call(raw: str) -> tuple:
        try:
            call = json.loads(raw)
            name = call["name"]
            arguments = call.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return ("content", TOOL + raw + TOOL_END)  # not a valid call: leave it as text
        return ("tool_call", {"function": {"name": name, "arguments": arguments}})


class StopFilter:
    """ Best-effort `stop` sequences: cuts the raw output at the first one. """

    def __init__(self, stops: List[str]):
        self.stops = [s for s in stops if s]
        self.buffer = ""
        self.hit = False

    def feed(self, text: str) -> str:
        if not self.stops:
            return text
        self.buffer += text
        found = [i for i in (self.buffer.find(s) for s in self.stops) if i >= 0]
        if found:
            self.hit = True
            out, self.buffer = self.buffer[:min(found)], ""
            return out
        keep = max(len(s) for s in self.stops) - 1
        cut = max(len(self.buffer) - keep, 0)
        out, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return out

    def flush(self) -> str:
        out, self.buffer = self.buffer, ""
        return out


# -- generation ------------------------------------------------------------------------------------

def _engine_kwargs(options) -> tuple:
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise _BadRequest("options must be an object")
    kwargs = {OPTION_MAP[k]: v for k, v in options.items() if k in OPTION_MAP and v is not None}
    num_predict = options.get("num_predict")
    kwargs["max_new_tokens"] = num_predict if isinstance(num_predict, int) and num_predict > 0 else 0
    stop = options.get("stop") or []
    if isinstance(stop, str):
        stop = [stop]
    return kwargs, list(stop)


def _count_tokens(engine, text: str) -> Optional[int]:
    tokenizer = getattr(engine, "tokenizer", None)
    if tokenizer is None:
        return None
    try:
        return len(tokenizer(text, add_special_tokens=False)["input_ids"])
    except Exception:
        return None


def _prompt_tokens(engine, messages, tools) -> Optional[int]:
    tokenizer = getattr(engine, "tokenizer", None)
    if tokenizer is None:
        return None
    try:
        prompt = tokenizer.apply_chat_template(
            messages, tools=tools or None, chat_template=getattr(engine, "chat_template", None),
            add_generation_prompt=True, tokenize=False)
    except Exception:
        return None
    return _count_tokens(engine, prompt)


def _clean_messages(messages) -> list:
    """ Keep what the chat template understands; images are not supported and dropped. """
    if not isinstance(messages, list):
        raise _BadRequest("messages must be a list")
    clean = []
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str):
            raise _BadRequest("each message needs a role")
        out = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("tool_calls"):
            out["tool_calls"] = m["tool_calls"]
        if m.get("tool_name"):
            out["name"] = m["tool_name"]
        clean.append(out)
    return clean


def _generate(lease, messages, tools, kwargs, stops, think, cancel, started, release) -> Iterator[tuple]:
    """
    Run one generation on the leased engine. Yields ("content" | "thinking" | "tool_call", value)
    events, then ("done", stats). Releases the lease when it ends or is closed.
    """
    engine = lease.engine
    parser = OutputParser(think)
    stop_filter = StopFilter(stops)
    raw = []
    first_token = None
    chunks = None
    try:
        prompt_eval_count = _prompt_tokens(engine, messages, tools)
        chunks = engine(messages, tools=tools or None, cancel=cancel, **kwargs)
        for chunk in chunks:
            if first_token is None:
                first_token = time.perf_counter()
            text = stop_filter.feed(chunk)
            raw.append(text)
            yield from parser.feed(text)
            if stop_filter.hit:
                break
        tail = stop_filter.flush()
        raw.append(tail)
        yield from parser.feed(tail)
        yield from parser.flush()
        chunks.close()
        end = time.perf_counter()
        eval_count = _count_tokens(engine, "".join(raw))
        max_new = kwargs.get("max_new_tokens", 0)
        reason = "length" if (not stop_filter.hit and eval_count is not None and 0 < max_new <= eval_count) else "stop"
        first = first_token or end
        stats = dict(
            done_reason=reason,
            total_duration=_ns(end - started),
            load_duration=_ns(lease.load_duration),
            prompt_eval_count=prompt_eval_count,
            prompt_eval_duration=_ns(first - (started + lease.load_duration)),
            eval_count=eval_count,
            eval_duration=_ns(end - first),
        )
        yield ("done", {k: v for k, v in stats.items() if v is not None})
    finally:
        cancel.set()
        if chunks is not None:
            chunks.close()
        release()


def _ns(seconds: float) -> int:
    return max(int(seconds * 1e9), 0)


def _closer(items: Iterator, cancel: Optional[threading.Event] = None, release=None):
    """
    Ends a stream once: `cancel` stops the generation, the iterator is closed, then `release` runs
    (it covers an iterator that never started). If a worker is still inside `next`, closing fails
    and the iterator's own cleanup releases when that call returns.
    """
    def close():
        if cancel is not None:
            cancel.set()
        try:
            items.close()
        except ValueError:  # still running in a worker
            return
        if release is not None:
            release()
    return once(close)


async def _stream(items: Iterator[dict], close):
    """ Drive a blocking iterator off the event loop and send it as NDJSON; `close` ends it. """
    end = object()
    try:
        while (item := await run_in_threadpool(next, items, end)) is not end:
            yield json.dumps(item) + "\n"
    finally:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(close)


async def _run(body: dict, messages: list, tools, render, finish):
    """ Shared by /api/chat and /api/generate. `render` maps an event to a stream line. """
    started = time.perf_counter()
    name = _model_of(body)
    keep_alive = parse_keep_alive(body.get("keep_alive"))
    reg = _registry()
    hf_id = reg.resolve(name)
    derived = reg.derived(name) or {}
    options = body.get("options")
    if options is not None and not isinstance(options, dict):
        raise _BadRequest("options must be an object")
    # A derived model's parameters are defaults; the request's options win (as in Ollama).
    kwargs, stops = _engine_kwargs({**(derived.get("parameters") or {}), **(options or {})})
    if messages and derived.get("system") and not any(m["role"] == "system" for m in messages):
        messages = [{"role": "system", "content": derived["system"]}, *messages]
    think = body.get("think")
    if think is not None and not isinstance(think, bool):
        think = bool(think)  # "low" / "medium" / "high" levels are treated as on

    if not messages:  # load or unload only
        if keep_alive == 0:
            await run_in_threadpool(reg.unload, hf_id)
            return JSONResponse(finish(name, "unload"))
        lease = await acquire(reg, hf_id, keep_alive)
        reg.release(lease)
        return JSONResponse(finish(name, "load"))

    lease = await acquire(reg, hf_id, keep_alive)
    cancel = threading.Event()
    release = once(lambda: reg.release(lease))
    events = _generate(lease, messages, tools, kwargs, stops, think, cancel, started, release)

    if body.get("stream", True) is False:
        try:
            collected = await run_in_threadpool(list, events)
        except ModelBusy:
            raise  # 503: the model stayed busy past the queue timeout (the generator released the lease)
        except Exception as e:
            return _error(500, str(e))
        return JSONResponse(render(name, collected, stream=False))

    # The first step runs before the response starts, so a request that waited past the queue
    # timeout is still an HTTP 503 (the generator released the lease when it failed).
    end = object()
    failure = None
    try:
        first = await run_in_threadpool(next, events, end)
    except ModelBusy:
        raise
    except Exception as e:  # reported on the stream, as before
        first, failure = end, e

    def lines():
        try:
            if failure is not None:
                raise failure
            for event in ([] if first is end else itertools.chain([first], events)):
                line = render(name, [event], stream=True)
                if line is not None:
                    yield line
        except Exception as e:
            yield {"error": str(e)}
        finally:
            events.close()

    # The response, not only its body, closes the stream: a body that never starts (the client went
    # away first) or a response dropped unsent still releases the lease (#93).
    items = lines()
    close = _closer(items, cancel, release)
    return LeasedStreamingResponse(_stream(items, close), close, media_type=NDJSON)


def _merge(events) -> tuple:
    content, thinking, calls, stats = [], [], [], None
    for kind, value in events:
        if kind == "content":
            content.append(value)
        elif kind == "thinking":
            thinking.append(value)
        elif kind == "tool_call":
            calls.append(value)
        elif kind == "done":
            stats = value
    return "".join(content), "".join(thinking), calls, stats


def _chat_render(name, events, stream):
    content, thinking, calls, stats = _merge(events)
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    if calls:
        message["tool_calls"] = calls
    if stream and stats is None and not (content or thinking or calls):
        return None
    line = {"model": name, "created_at": now_iso(), "message": message, "done": stats is not None}
    if stats is not None:
        line.update(stats)
    return line


def _generate_render(name, events, stream):
    content, thinking, calls, stats = _merge(events)
    if calls:  # /api/generate has no tool calls; show them as text
        content += "".join(TOOL + json.dumps(c["function"]) + TOOL_END for c in calls)
    if stream and stats is None and not (content or thinking):
        return None
    line = {"model": name, "created_at": now_iso(), "response": content, "done": stats is not None}
    if thinking:
        line["thinking"] = thinking
    if stats is not None:
        line.update(stats)
    return line


def _chat_finish(name, reason):
    return {"model": name, "created_at": now_iso(), "message": {"role": "assistant", "content": ""},
            "done_reason": reason, "done": True}


def _generate_finish(name, reason):
    return {"model": name, "created_at": now_iso(), "response": "", "done_reason": reason, "done": True}


def _handle(fn):
    """ Map failures to Ollama's `{"error": ...}` responses. """
    async def wrapper(request: Request):
        try:
            return await fn(request)
        except _BadRequest as e:
            return _error(400, str(e))
        except _NotImplemented as e:
            return _error(501, str(e))
        except LookupError as e:
            return _error(404, str(e.args[0]) if e.args else "model not found")
        except (ModelBusy, ModelUnavailable) as e:
            return _error(503, str(e))
        except ValueError as e:
            return _error(400, str(e))
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# -- endpoints -------------------------------------------------------------------------------------

@router.post("/api/chat")
@_handle
async def chat(request: Request):
    """ Ollama chat: messages in, an assistant message (content, thinking, tool_calls) out. """
    body = await _body(request)
    messages = _clean_messages(body.get("messages") or [])
    tools = body.get("tools") or None
    if tools is not None and not isinstance(tools, list):
        raise _BadRequest("tools must be a list")
    return await _run(body, messages, tools, _chat_render, _chat_finish)


@router.post("/api/generate")
@_handle
async def generate(request: Request):
    """ Ollama generate: a prompt (and optional system prompt) through the chat template. """
    body = await _body(request)
    if body.get("raw"):
        raise _BadRequest("raw mode is not supported: prompts always go through the chat template")
    prompt = body.get("prompt") or ""
    messages = []
    if prompt:
        if body.get("system"):
            messages.append({"role": "system", "content": body["system"]})
        messages.append({"role": "user", "content": prompt})
    return await _run(body, messages, None, _generate_render, _generate_finish)


def _tag(name: str, info, modified_at: Optional[str] = None, parent: str = "") -> dict:
    return dict(name=name, model=name, modified_at=modified_at or info.modified_at, size=info.size,
                digest=info.digest, details=info.details(parent))


def _local(hf_id: str):
    info = _registry().store.get(hf_id)
    if info is None:
        raise LookupError(f"model '{hf_id}' not found")
    return info


@router.get("/api/tags")
async def tags():
    """ Models in the local store (the Hugging Face cache), then the derived models of them. """
    reg = _registry()
    models = await run_in_threadpool(reg.store.list)
    by_id = {m.hf_id: m for m in models}
    entries = [_tag(m.hf_id, m) for m in models]
    for name, record in sorted(reg.aliases.all().items()):
        info = by_id.get(record.get("from"))
        if info is not None:
            entries.append(_tag(name, info, record.get("created_at"), info.hf_id))
    return {"models": entries}


def _modelfile(hf_id: str, system: str, parameters: dict) -> str:
    """ A Modelfile that describes the model (Ollama's `modelfile` field); for reading only. """
    lines = [f"FROM {hf_id}"]
    if system:
        lines.append(f'SYSTEM """{system}"""')
    lines += [f"PARAMETER {line}" for line in format_parameters(parameters).splitlines()]
    return "\n".join(lines) + "\n"


@router.post("/api/show")
@_handle
async def show(request: Request):
    """ Details of one local model or derived model. """
    name = _model_of(await _body(request))
    reg = _registry()
    hf_id = reg.resolve(name)
    derived = reg.derived(name)
    info = await run_in_threadpool(_local, hf_id)
    parameters = {**info.parameters, **((derived or {}).get("parameters") or {})}
    system = (derived or {}).get("system") or ""
    model_info = {"general.architecture": info.family}
    if info.parameter_count:
        model_info["general.parameter_count"] = info.parameter_count
    if info.family and info.context_length:
        model_info[f"{info.family}.context_length"] = info.context_length
    body = dict(modelfile=_modelfile(hf_id, system, parameters), parameters=format_parameters(parameters),
                template=info.chat_template, details=info.details(hf_id if derived else ""),
                model_info=model_info, capabilities=info.capabilities,
                modified_at=(derived or {}).get("created_at") or info.modified_at)
    if system:
        body["system"] = system
    return body


@router.get("/api/ps")
async def ps():
    """ Loaded models, latest expiry first, with their memory estimate and when they unload. """
    models = []
    for entry in _registry().loaded():
        info = entry["info"]
        models.append(dict(
            name=entry["hf_id"], model=entry["hf_id"], size=entry["size"],
            digest=info.digest if info else "", details=info.details() if info else {},
            expires_at=entry["expires_at"], size_vram=0,
        ))
    return {"models": models}


def _download_steps(store, hf_id: str) -> Iterator[dict]:
    """ The store's per-file pull progress, or nothing (a store without it downloads in one go). """
    steps = getattr(store, "download_iter", None)
    if steps is None:
        store.download(hf_id)
        return iter(())
    return steps(hf_id)


@router.post("/api/pull")
@_handle
async def pull(request: Request):
    """ Download a model from the Hugging Face Hub into the local cache, reporting each file. """
    body = await _body(request)
    hf_id = _registry().resolve(_model_of(body))
    store = _registry().store

    if body.get("stream", True) is False:
        try:
            await run_in_threadpool(store.download, hf_id)
        except UnsupportedModel as e:
            return _error(400, str(e))
        except LookupError as e:
            return _error(500, str(e))
        except Exception as e:
            return _error(503, str(ModelUnavailable.from_error(hf_id, e)))
        return {"status": "success"}

    def lines():
        yield {"status": "pulling manifest"}
        try:
            yield from _download_steps(store, hf_id)
        except (LookupError, UnsupportedModel) as e:
            yield {"error": str(e)}
            return
        except Exception as e:
            yield {"error": str(ModelUnavailable.from_error(hf_id, e))}
            return
        yield {"status": "success"}

    items = lines()
    return StreamingResponse(_stream(items, _closer(items)), media_type=NDJSON)


@router.post("/api/push")
async def push():
    """ Not offered: Gemstone's models come from the Hugging Face Hub, not an Ollama registry. """
    return _error(501, "push is not supported: Gemstone's models live on the Hugging Face Hub; "
                       "upload a model there with `hf upload` instead")


def _alias_name(name) -> str:
    key = alias_key(name)
    if key is None:
        raise _BadRequest(f"invalid model name {name!r}: use name[:tag] with letters, digits, '.', '_' or '-' "
                          f"and no '/' (Hugging Face ids name themselves)")
    if is_builtin_name(name):
        raise _BadRequest(f"'{name}' is a built-in model name")
    return key


def _source(name) -> tuple:
    """ (Hugging Face id, derived record or {}) of a model in the local store. """
    if not isinstance(name, str) or not name:
        raise _BadRequest("from is required: the model to derive from")
    reg = _registry()
    hf_id = reg.resolve(name)
    _local(hf_id)
    return hf_id, reg.derived(name) or {}


@router.post("/api/copy")
@_handle
async def copy(request: Request):
    """ Name a local model (or derived model) anew; the copy shares the weights. """
    body = await _body(request)
    source, destination = body.get("source"), body.get("destination")
    if not isinstance(source, str) or not source:
        raise _BadRequest("source is required")
    hf_id, derived = await run_in_threadpool(_source, source)
    key = _alias_name(destination)
    record = {**derived, "from": hf_id, "created_at": now_iso()}
    await run_in_threadpool(_registry().aliases.set, key, record)
    return Response(status_code=200)


# Ollama create fields Gemstone does not take, and why.
CREATE_UNSUPPORTED = {
    "template": "templates are not supported: Ollama's are Go templates, and Gemstone always formats "
                "prompts with the model's Hugging Face (Jinja) chat template",
    "files": "model files are not supported: Gemstone loads safetensors from the Hugging Face cache "
             "(pull the model instead)",
    "adapters": "adapters are not supported",
    "quantize": "quantizing is not supported",
    "modelfile": "Modelfiles are not supported: send 'from' with 'system' and 'parameters' instead",
    "messages": "preset messages are not supported",
}


def _parameters(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _BadRequest("parameters must be an object")
    out = {}
    for name, value in raw.items():
        kind = DERIVED_PARAMETERS.get(name)
        if kind is None:
            raise _BadRequest(f"unknown parameter '{name}'")
        try:
            if kind is list:
                value = [value] if isinstance(value, str) else list(value)
                if not all(isinstance(v, str) for v in value):
                    raise ValueError
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError
            else:
                value = kind(value)
        except (TypeError, ValueError):
            raise _BadRequest(f"invalid value for parameter '{name}': {value!r}")
        out[name] = value
    return out


@router.post("/api/create")
@_handle
async def create(request: Request):
    """
    A derived model: `from` a local model, with its own `system` prompt and `parameters`, stored
    as an alias that shares the weights. Templates, files, adapters, quantizing and Modelfiles
    answer 501.
    """
    body = await _body(request)
    name = _model_of(body)
    for field in CREATE_UNSUPPORTED:
        if body.get(field):
            raise _NotImplemented(CREATE_UNSUPPORTED[field])
    key = _alias_name(name)
    hf_id, base = await run_in_threadpool(_source, body.get("from"))
    parameters = {**(base.get("parameters") or {}), **_parameters(body.get("parameters"))}
    system = body.get("system")
    if system is not None and not isinstance(system, str):
        raise _BadRequest("system must be a string")
    record = {"from": hf_id, "created_at": now_iso()}
    if system or base.get("system"):
        record["system"] = system or base["system"]
    if parameters:
        record["parameters"] = parameters
    await run_in_threadpool(_registry().aliases.set, key, record)

    if body.get("stream", True) is False:
        return {"status": "success"}
    lines = [{"status": f"using {hf_id}"}, {"status": "writing manifest"}, {"status": "success"}]
    return StreamingResponse(iter([json.dumps(line) + "\n" for line in lines]), media_type=NDJSON)


@router.delete("/api/delete")
@_handle
async def delete(request: Request):
    """ Remove a derived model, or unload a model and remove it from the local cache. """
    name = _model_of(await _body(request))
    reg = _registry()
    key = alias_key(name)
    if key is not None and await run_in_threadpool(reg.aliases.delete, key):
        return Response(status_code=200)
    hf_id = reg.resolve(name)
    if not await run_in_threadpool(reg.delete, hf_id):
        raise LookupError(f"model '{hf_id}' not found")
    return Response(status_code=200)


# -- embeddings ------------------------------------------------------------------------------------

async def _embed(body: dict, inputs: List[str]) -> tuple:
    """ (vectors, token count, load seconds) for `inputs` on the model `body` names. """
    name = _model_of(body)
    keep_alive = parse_keep_alive(body.get("keep_alive"))
    truncate = body.get("truncate", True) is not False
    reg = _registry()
    lease = await acquire(reg, reg.resolve(name), keep_alive)
    try:
        embed = getattr(lease.engine, "embed", None)
        if embed is None:
            raise _NotImplemented(f"model '{name}' cannot produce embeddings")
        vectors = await run_in_threadpool(embed, inputs, truncate) if inputs else []
        count = sum(_count_tokens(lease.engine, text) or 0 for text in inputs)
    finally:
        reg.release(lease)
    return vectors, count, lease.load_duration


@router.post("/api/embed")
@_handle
async def embed(request: Request):
    """
    Embeddings: the mean of the model's last hidden state over each input's tokens, unit length.
    A chat model's embeddings are usable for similarity but weaker than a dedicated embedding model's.
    """
    started = time.perf_counter()
    body = await _body(request)
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
        raise _BadRequest("input must be a string or a list of strings")
    vectors, count, load = await _embed(body, inputs)
    return {"model": _model_of(body), "embeddings": vectors, "total_duration": _ns(time.perf_counter() - started),
            "load_duration": _ns(load), "prompt_eval_count": count}


@router.post("/api/embeddings")
@_handle
async def embeddings(request: Request):
    """ The legacy single-prompt endpoint: `prompt` in, one `embedding` out. """
    body = await _body(request)
    prompt = body.get("prompt") or ""
    if not isinstance(prompt, str):
        raise _BadRequest("prompt must be a string")
    vectors, _, _ = await _embed(body, [prompt] if prompt else [])
    return {"embedding": vectors[0] if vectors else []}


@router.get("/api/version")
async def version():
    """ The Gemstone version (clients use it to check that the server is up). """
    try:
        return {"version": metadata.version("gemstone")}
    except metadata.PackageNotFoundError:
        return {"version": "0.0.0"}
