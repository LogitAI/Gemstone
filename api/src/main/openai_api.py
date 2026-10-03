"""
OpenAI-compatible API (SPEC S1.10): `GET /v1/models` and `POST /v1/chat/completions`.

The routes call a model's engine directly; the engine comes from the registry (SPEC S1.14), which
the chat app's WebSocket and the Ollama API share, so a model is loaded once for all of them. Unlike the chat app's WebSocket (S1.4, S1.6), they never
run tools: a `<tool_call>` the model emits is returned to the client as `tool_calls`, and the
client sends the result back as a `tool` message in its next request.

Reasoning between `<think>` and `</think>` is removed from `content` and returned as
`reasoning_content`, the extension DeepSeek, vLLM and others use.
"""
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union
import inspect
import json
import threading
import time
import uuid

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel as Schema, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool

from . import registry as registry_module
from .leases import LeasedStreamingResponse, acquire, once
from .registry import ModelBusy, in_catalogue, parse_keep_alive


router = APIRouter(prefix="/v1")

THINK, THINK_END = "<think>", "</think>"
TOOL_CALL, TOOL_CALL_END = "<tool_call>", "</tool_call>"

# Sampling parameters the engine takes; a model's `chat` signature supplies its defaults.
SAMPLING_PARAMETERS = ("temperature", "top_p", "top_k", "min_p", "typical_p", "repeat_penalty")

Event = Tuple[str, Any]  # ("content" | "reasoning", str) or ("tool_call", dict)


# --- request ------------------------------------------------------------------------------------

class ChatCompletionRequest(Schema):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: List[Dict[str, Any]]
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None  # extension, as in vLLM
    min_p: Optional[float] = None  # extension, as in vLLM
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    seed: Optional[int] = None
    stop: Optional[Union[str, List[str]]] = None
    stream: bool = False
    stream_options: Optional[Dict[str, Any]] = None
    n: Optional[int] = None


class APIError(Exception):
    def __init__(self, status: int, message: str, type: str = "invalid_request_error",
                 param: Optional[str] = None, code: Optional[str] = None):
        super().__init__(message)
        self.status, self.body = status, dict(message=message, type=type, param=param, code=code)

    def response(self) -> JSONResponse:
        return JSONResponse(status_code=self.status, content={"error": self.body})


def _text(content: Any) -> str:
    """ OpenAI content is a string, null, or a list of parts; the engine takes a string. """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    raise APIError(400, "Message content must be a string or a list of content parts.", param="messages")


def _engine_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ OpenAI messages → the chat-template messages the engine takes. """
    converted = []
    for i, message in enumerate(messages):
        role = message.get("role")
        if role not in ("system", "developer", "user", "assistant", "tool"):
            raise APIError(400, f"messages[{i}] has an invalid role: {role!r}.", param=f"messages[{i}].role")
        out = {"role": "system" if role == "developer" else role, "content": _text(message.get("content"))}
        if role == "assistant":
            if isinstance(message.get("reasoning_content"), str):
                out["reasoning_content"] = message["reasoning_content"]
            calls = []
            for call in message.get("tool_calls") or []:
                function = dict(call.get("function") or {})
                arguments = function.get("arguments")
                if isinstance(arguments, str):  # chat templates expect a mapping
                    try:
                        function["arguments"] = json.loads(arguments) if arguments.strip() else {}
                    except json.JSONDecodeError:
                        pass
                calls.append({"id": call.get("id"), "type": "function", "function": function})
            if calls:
                out["tool_calls"] = calls
        if role == "tool":
            out["tool_call_id"] = message.get("tool_call_id")
        if message.get("name"):
            out["name"] = message["name"]
        converted.append(out)
    return converted


def _select_tools(request: ChatCompletionRequest) -> Optional[List[Dict[str, Any]]]:
    tools, choice = request.tools or None, request.tool_choice
    if not tools or choice == "none":
        return None
    if isinstance(choice, dict):  # {"type": "function", "function": {"name": ...}}
        name = (choice.get("function") or {}).get("name")
        tools = [t for t in tools if (t.get("function") or {}).get("name") == name]
        if not tools:
            raise APIError(400, f"tool_choice names an unknown tool: {name!r}.", param="tool_choice")
    return tools


def _model_defaults(model) -> Dict[str, Any]:
    """ The sampling defaults a model declares on its `chat` method. """
    try:
        parameters = inspect.signature(model.chat).parameters
    except (AttributeError, TypeError, ValueError):
        return {}
    return {
        name: parameters[name].default for name in SAMPLING_PARAMETERS
        if name in parameters and parameters[name].default is not inspect.Parameter.empty
    }


# --- output parsing -----------------------------------------------------------------------------

def _held_back(text: str, needles: List[str]) -> int:
    """ Length of the longest suffix of `text` that could still grow into one of `needles`. """
    longest = 0
    for needle in needles:
        for size in range(min(len(needle) - 1, len(text)), longest, -1):
            if text.endswith(needle[:size]):
                longest = size
                break
    return longest


class _Trim:
    """ Drops leading and trailing whitespace of one channel while it streams. """

    def __init__(self):
        self.started, self.held = False, ""

    def __call__(self, text: str) -> str:
        if not self.started:
            text = text.lstrip()
            if not text:
                return ""
            self.started = True
        text = self.held + text
        kept = text.rstrip()
        self.held = text[len(kept):]
        return kept


class OutputParser:
    """
    Splits the model's raw text, chunk by chunk, into content, reasoning and tool calls.
    A tag split across chunks is held back until it is complete.
    """

    def __init__(self, parse_tools: bool):
        self.parse_tools = parse_tools
        self.state = "content"  # "content" | "reasoning" | "tool"
        self.buffer = ""
        self.trim = {"content": _Trim(), "reasoning": _Trim()}

    def _emit(self, channel: str, text: str) -> List[Event]:
        text = self.trim[channel](text)
        return [(channel, text)] if text else []

    def _tool_call(self, block: str) -> List[Event]:
        try:
            call = json.loads(block)
            name = call["name"]
            arguments = call.get("arguments", call.get("parameters", {}))
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            return self._emit("content", TOOL_CALL + block + TOOL_CALL_END)
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        return [("tool_call", {"name": name, "arguments": arguments})]

    def feed(self, text: str) -> List[Event]:
        self.buffer += text
        events: List[Event] = []
        while True:
            if self.state == "tool":
                end = self.buffer.find(TOOL_CALL_END)
                if end < 0:
                    return events
                events += self._tool_call(self.buffer[:end].strip())
                self.buffer = self.buffer[end + len(TOOL_CALL_END):]
                self.state = "content"
                continue

            if self.state == "reasoning":
                tags = {THINK_END: "content"}
            else:
                tags = {THINK: "reasoning", THINK_END: "content"}  # a stray </think> is dropped
                if self.parse_tools:
                    tags[TOOL_CALL] = "tool"
            found = [(self.buffer.find(tag), tag) for tag in tags if tag in self.buffer]
            if not found:
                keep = _held_back(self.buffer, list(tags))
                ready, self.buffer = self.buffer[:len(self.buffer) - keep], self.buffer[len(self.buffer) - keep:]
                return events + self._emit(self.state, ready)
            index, tag = min(found)
            events += self._emit(self.state, self.buffer[:index])
            self.buffer = self.buffer[index + len(tag):]
            self.state = tags[tag]

    def finish(self) -> List[Event]:
        rest, self.buffer = self.buffer, ""
        if self.state == "tool":  # generation ended inside a tool call
            return self._tool_call(rest.strip())
        return self._emit(self.state, rest)


class _StopMatcher:
    """ Ends the raw output at the first stop sequence, holding back a partial match. """

    def __init__(self, stops: List[str]):
        self.stops = [s for s in stops if s]
        self.buffer = ""
        self.stopped = False

    def feed(self, text: str) -> str:
        if not self.stops:
            return text
        self.buffer += text
        hits = [i for i in (self.buffer.find(s) for s in self.stops) if i >= 0]
        if hits:
            self.stopped = True
            ready, self.buffer = self.buffer[:min(hits)], ""
            return ready
        keep = _held_back(self.buffer, self.stops)
        ready, self.buffer = self.buffer[:len(self.buffer) - keep], self.buffer[len(self.buffer) - keep:]
        return ready

    def finish(self) -> str:
        rest, self.buffer = self.buffer, ""
        return rest


# --- generation ---------------------------------------------------------------------------------

class Generation:
    """
    One engine generation, stepped from worker threads. `step` returns the next batch of events,
    or None once the reply is complete; `close` stops generation and releases the engine.
    """

    def __init__(self, engine, messages, tools, parameters: Dict[str, Any], stops: List[str]):
        self.engine, self.messages, self.tools = engine, messages, tools
        self.max_new_tokens = parameters.get("max_new_tokens", 0)
        self.cancel = threading.Event()
        self.tokens: Iterator[str] = engine(messages, tools=tools, cancel=self.cancel, **parameters)
        self.parser = OutputParser(parse_tools=bool(tools))
        self.stopper = _StopMatcher(stops)
        self.raw: List[str] = []
        self.lock = threading.Lock()
        self.done = False
        self.tool_calls = 0
        self.usage: Optional[Dict[str, int]] = None
        self.finish_reason: Optional[str] = None

    def step(self) -> Optional[List[Event]]:
        with self.lock:
            if self.done:
                return None
            while True:
                chunk = next(self.tokens, None)
                if chunk is None:
                    return self._finish(self.parser.feed(self.stopper.finish()))
                text = self.stopper.feed(chunk)
                self.raw.append(text)
                events = self.parser.feed(text)
                if self.stopper.stopped:
                    self.cancel.set()
                    self.tokens.close()
                    return self._finish(events)
                if events:
                    self.tool_calls += sum(kind == "tool_call" for kind, _ in events)
                    return events

    def _finish(self, events: List[Event]) -> List[Event]:
        self.done = True
        events += self.parser.finish()
        self.tool_calls += sum(kind == "tool_call" for kind, _ in events)
        self.usage = self._usage()
        if self.tool_calls:
            self.finish_reason = "tool_calls"
        elif (not self.stopper.stopped and self.max_new_tokens > 0
              and self.usage["completion_tokens"] >= self.max_new_tokens):
            self.finish_reason = "length"
        else:
            self.finish_reason = "stop"
        return events

    def _usage(self) -> Dict[str, int]:
        prompt = completion = 0
        tokenizer = getattr(self.engine, "tokenizer", None)
        if tokenizer is not None:
            try:
                completion = len(tokenizer.encode("".join(self.raw), add_special_tokens=False))
                text = tokenizer.apply_chat_template(
                    self.messages, tools=self.tools, chat_template=getattr(self.engine, "chat_template", None),
                    add_generation_prompt=True, tokenize=False,
                )
                prompt = len(tokenizer.encode(text, add_special_tokens=False))
            except Exception:  # usage is informative; never fail a reply over it
                pass
        return dict(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion)

    def run(self) -> List[Event]:
        events: List[Event] = []
        while (batch := self.step()) is not None:
            events += batch
        return events

    def close(self):
        self.cancel.set()
        with self.lock:  # waits for a step in flight, which the cancel ends soon
            self.tokens.close()


def _tool_call_object(call: Dict[str, str]) -> Dict[str, Any]:
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": call["name"], "arguments": call["arguments"]},
    }


def _engine_error(e: Exception) -> APIError:
    if isinstance(e, ValueError) and "exceeds the token limit" in str(e):
        return APIError(400, str(e), code="context_length_exceeded", param="messages")
    return APIError(500, f"Generation failed: {e}", type="server_error")


# --- routes -------------------------------------------------------------------------------------

@router.get("/models")
def list_models():
    """ The models the registry knows (SPEC S1.2). """
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": created, "owned_by": "gemstone"}
            for m in registry_module.registry.models()
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    try:
        return await _chat_completions(request)
    except APIError as e:
        return e.response()


async def _chat_completions(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise APIError(400, "The request body is not valid JSON.")
    try:
        req = ChatCompletionRequest.model_validate(body)
    except ValidationError as e:
        first = e.errors()[0]
        raise APIError(400, f"{'.'.join(map(str, first['loc']))}: {first['msg']}",
                       param=".".join(map(str, first["loc"])))
    if req.n not in (None, 1):
        raise APIError(400, "Only n=1 is supported.", param="n")
    try:
        keep_alive = parse_keep_alive(body.get("keep_alive"))  # extension, as in Ollama
    except ValueError as e:
        raise APIError(400, str(e), param="keep_alive")
    registry = registry_module.registry
    try:
        hf_id = registry.resolve(req.model)  # derived models (`/api/create`, `/api/copy`) too
    except LookupError:
        raise APIError(404, f"The model '{req.model}' does not exist.", param="model", code="model_not_found")

    derived = registry.derived(req.model) or {}
    messages = _engine_messages(req.messages)
    if derived.get("system") and not any(m["role"] == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": derived["system"]})
    tools = _select_tools(req)

    # Hold the model in the registry until the reply ends; a catalogue model missing from the cache
    # is fetched on first use.
    try:
        lease = await acquire(registry, hf_id, keep_alive, in_catalogue(req.model))
    except LookupError:
        raise APIError(404, f"The model '{req.model}' does not exist locally; pull it first.",
                       param="model", code="model_not_found")
    except ModelBusy as e:
        raise APIError(503, str(e), type="server_error", code="server_busy")
    release = once(lambda: registry.release(lease))
    try:
        return await _complete(req, lease, release, messages, tools, derived.get("parameters") or {})
    except BaseException:
        release()
        raise


async def _complete(req: ChatCompletionRequest, lease, release, messages, tools, derived: Dict[str, Any]):
    """
    Run the completion on the leased model. `release` runs exactly once: before a non-streaming or
    failed reply returns, or, for a stream, once the response is done with however it ends (#93).
    `derived` holds a derived model's Ollama parameters, which sit between the model's defaults
    and the request's own values.
    """
    model, engine = lease.model, lease.engine

    parameters = _model_defaults(model)
    parameters.update({k: v for k, v in derived.items() if k in SAMPLING_PARAMETERS or k == "seed"})
    for name in ("temperature", "top_p", "top_k", "min_p"):
        if getattr(req, name) is not None:
            parameters[name] = getattr(req, name)
    num_predict = derived.get("num_predict") if isinstance(derived.get("num_predict"), int) else 0
    parameters["max_new_tokens"] = (req.max_completion_tokens or req.max_tokens  # 0: up to the context length
                                    or max(num_predict, 0))
    if req.seed is not None:
        parameters["seed"] = req.seed
    stops = [req.stop] if isinstance(req.stop, str) else list(req.stop or derived.get("stop") or [])

    generation = Generation(engine, messages, tools, parameters, stops)

    def finish():
        try:
            generation.close()
        finally:
            release()
    finish = once(finish)  # stop generation and free the engine, then release the lease

    try:
        return await _respond(req, generation, finish)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(finish)
        raise


async def _respond(req: ChatCompletionRequest, generation: Generation, finish):
    """ The reply: a `chat.completion`, or a stream whose response runs `finish` once it ends. """
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if not req.stream:
        try:
            events = await run_in_threadpool(generation.run)
        except Exception as e:
            raise _engine_error(e)
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(finish)
        content = "".join(v for k, v in events if k == "content")
        reasoning = "".join(v for k, v in events if k == "reasoning")
        message: Dict[str, Any] = {"role": "assistant", "content": content or None}
        if reasoning:
            message["reasoning_content"] = reasoning
        calls = [_tool_call_object(v) for k, v in events if k == "tool_call"]
        if calls:
            message["tool_calls"] = calls
        return {
            "id": completion_id, "object": "chat.completion", "created": created, "model": req.model,
            "choices": [{"index": 0, "message": message, "finish_reason": generation.finish_reason}],
            "usage": generation.usage,
        }

    # Streaming. The first step runs before the response starts, so a failure to start
    # (an over-long prompt, say) is still an HTTP error.
    try:
        first = await run_in_threadpool(generation.step)
    except Exception as e:  # `_complete` releases
        raise _engine_error(e)

    include_usage = bool((req.stream_options or {}).get("include_usage"))

    def chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None) -> str:
        body = {
            "id": completion_id, "object": "chat.completion.chunk", "created": created, "model": req.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    async def stream():
        tool_index = 0
        try:
            yield chunk({"role": "assistant", "content": ""})
            events = first
            while events is not None:
                for kind, value in events:
                    if kind == "content":
                        yield chunk({"content": value})
                    elif kind == "reasoning":
                        yield chunk({"reasoning_content": value})
                    else:
                        yield chunk({"tool_calls": [{"index": tool_index, **_tool_call_object(value)}]})
                        tool_index += 1
                events = await run_in_threadpool(generation.step)
            yield chunk({}, generation.finish_reason)
            if include_usage:
                usage = {
                    "id": completion_id, "object": "chat.completion.chunk", "created": created,
                    "model": req.model, "choices": [], "usage": generation.usage,
                }
                yield f"data: {json.dumps(usage)}\n\n"
        except Exception as e:  # mid-stream failure: report it in-band
            yield f"data: {json.dumps({'error': _engine_error(e).body}, ensure_ascii=False)}\n\n"
        finally:
            # Also reached when the client disconnects: stop generation and free the engine (#35).
            # A body that never starts is covered by the response itself (#93).
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(finish)
        yield "data: [DONE]\n\n"

    return LeasedStreamingResponse(stream(), finish, media_type="text/event-stream",
                                   headers={"Cache-Control": "no-cache"})
