"""
The Ollama-compatible API (SPEC S1.15), so existing Ollama clients can use Gemstone.

Endpoints: POST /api/chat, POST /api/generate, GET /api/tags, POST /api/show, POST /api/pull,
DELETE /api/delete, GET /api/ps, GET /api/version. Streaming responses are NDJSON; `"stream": false`
returns one JSON object. Errors are `{"error": "..."}` with an HTTP status, as Ollama does.

Tool calls pass through (SPEC S1.10): a `<tool_call>{json}</tool_call>` block the model emits is
returned as `message.tool_calls` and never executed. Model names resolve through
`registry.resolve_model_name`; residency and keep_alive are handled by `registry.Registry`.
"""
from importlib import metadata
from typing import Iterator, List, Optional
import json
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import registry as registry_module
from .registry import now_iso, parse_keep_alive, resolve_model_name


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


def _once(fn):
    lock, done = threading.Lock(), []

    def wrapper():
        with lock:
            if done:
                return
            done.append(True)
        fn()
    return wrapper


async def _stream(items: Iterator[dict], cancel: Optional[threading.Event] = None, release=None):
    """
    Drive a blocking iterator off the event loop and send it as NDJSON. When the client goes away,
    `cancel` stops the generation; `release` runs once the iterator is closed. If a worker is still
    inside `next`, closing fails and the iterator's own cleanup releases when that call returns.
    """
    end = object()
    try:
        while (item := await run_in_threadpool(next, items, end)) is not end:
            yield json.dumps(item) + "\n"
    finally:
        if cancel is not None:
            cancel.set()
        try:
            await run_in_threadpool(items.close)
        except ValueError:  # still running in a worker
            pass
        else:
            if release is not None:
                release()  # covers an iterator that never started


async def _run(body: dict, messages: list, tools, render, finish):
    """ Shared by /api/chat and /api/generate. `render` maps an event to a stream line. """
    started = time.perf_counter()
    name = _model_of(body)
    keep_alive = parse_keep_alive(body.get("keep_alive"))
    kwargs, stops = _engine_kwargs(body.get("options"))
    think = body.get("think")
    if think is not None and not isinstance(think, bool):
        think = bool(think)  # "low" / "medium" / "high" levels are treated as on
    hf_id = resolve_model_name(name)
    reg = _registry()

    if not messages:  # load or unload only
        if keep_alive == 0:
            await run_in_threadpool(reg.unload, hf_id)
            return JSONResponse(finish(name, "unload"))
        lease = await run_in_threadpool(reg.acquire, hf_id, keep_alive)
        reg.release(lease)
        return JSONResponse(finish(name, "load"))

    lease = await run_in_threadpool(reg.acquire, hf_id, keep_alive)
    cancel = threading.Event()
    release = _once(lambda: reg.release(lease))
    events = _generate(lease, messages, tools, kwargs, stops, think, cancel, started, release)

    if body.get("stream", True) is False:
        try:
            collected = await run_in_threadpool(list, events)
        except Exception as e:
            return _error(500, str(e))
        return JSONResponse(render(name, collected, stream=False))

    def lines():
        try:
            for event in events:
                line = render(name, [event], stream=True)
                if line is not None:
                    yield line
        except Exception as e:
            yield {"error": str(e)}
        finally:
            events.close()

    return StreamingResponse(_stream(lines(), cancel, release), media_type=NDJSON)


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
        except LookupError as e:
            return _error(404, str(e.args[0]) if e.args else "model not found")
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


def _tag(info) -> dict:
    return dict(name=info.hf_id, model=info.hf_id, modified_at=info.modified_at, size=info.size,
                digest=info.digest, details=info.details())


@router.get("/api/tags")
async def tags():
    """ Models in the local store (the Hugging Face cache). """
    models = await run_in_threadpool(_registry().store.list)
    return {"models": [_tag(m) for m in models]}


@router.post("/api/show")
@_handle
async def show(request: Request):
    """ Details of one local model. """
    hf_id = resolve_model_name(_model_of(await _body(request)))
    info = await run_in_threadpool(_registry().store.get, hf_id)
    if info is None:
        raise LookupError(f"model '{hf_id}' not found")
    model_info = {"general.architecture": info.family}
    if info.family and info.context_length:
        model_info[f"{info.family}.context_length"] = info.context_length
    return dict(modelfile="", parameters="", template=info.chat_template, details=info.details(),
                model_info=model_info, capabilities=info.capabilities, modified_at=info.modified_at)


@router.get("/api/ps")
async def ps():
    """ Loaded models and when they unload. """
    models = []
    for entry in _registry().loaded():
        info = entry["info"]
        models.append(dict(
            name=entry["hf_id"], model=entry["hf_id"], size=info.size if info else 0,
            digest=info.digest if info else "", details=info.details() if info else {},
            expires_at=entry["expires_at"], size_vram=0,
        ))
    return {"models": models}


@router.post("/api/pull")
@_handle
async def pull(request: Request):
    """ Download a model from the Hugging Face Hub into the local cache. """
    body = await _body(request)
    hf_id = resolve_model_name(_model_of(body))
    store = _registry().store

    if body.get("stream", True) is False:
        try:
            await run_in_threadpool(store.download, hf_id)
        except LookupError as e:
            return _error(500, str(e))
        except Exception as e:
            return _error(500, f"pull failed: {e}")
        return {"status": "success"}

    def lines():
        yield {"status": "pulling manifest"}
        yield {"status": f"downloading {hf_id}"}
        try:
            store.download(hf_id)
        except Exception as e:
            yield {"error": str(e)}
            return
        yield {"status": "success"}

    return StreamingResponse(_stream(lines()), media_type=NDJSON)


@router.delete("/api/delete")
@_handle
async def delete(request: Request):
    """ Unload a model and remove it from the local cache. """
    hf_id = resolve_model_name(_model_of(await _body(request)))
    if not await run_in_threadpool(_registry().delete, hf_id):
        raise LookupError(f"model '{hf_id}' not found")
    return Response(status_code=200)


@router.get("/api/version")
async def version():
    """ The Gemstone version (clients use it to check that the server is up). """
    try:
        return {"version": metadata.version("gemstone")}
    except metadata.PackageNotFoundError:
        return {"version": "0.0.0"}
