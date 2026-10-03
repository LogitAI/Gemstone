# Gemstone — Specification

What Gemstone does: the behavioural contract between the client (`app/`), the model-serving API
(`api/`) and the user. Every item serves a goal in [`INTENT.md`](INTENT.md) (`G1`–`G9`) and carries
a status:

| Status | Meaning |
|---|---|
| `implemented` | The behaviour exists in code that was read for this document. |
| `partial` | Some of it exists, or it exists but reading the code shows a defect. |
| `planned` | Stated as a goal; no code yet on `develop`. |
| `removed` | Existed, and was removed on purpose; the item records what replaced it. |

**Evidence.** Each item names the code it was read from and its test. Python tests live in
`api/tests/` (pytest) and cover the engine (S1.11), the WebSocket stream (S1.4), the
OpenAI-compatible API (S1.10), the Ollama-compatible API with its model management (S1.14,
S1.15), the residency shared by all three, with several resident models and derived models
(S1.14, `test_multi_resident.py`), the tool-result cache (S1.6) and the backend
removal (S1.8), plus batching, queue limits, first-use download, errors, tool robustness, security
and client compatibility (each item names its module). Real-model tests (marker `real_model`) run in
CI. Kotlin tests in `app/src/commonTest` cover the network layer (`ModelCatalogTest`,
`ServerAddressTest`, `ChatCloseEventTest`); the UI has none. `implemented` without a named test means
*read in code*, never *verified by a test*.

Status was first established by reading `develop` at commit `2be0e37` (2026-10-02) and is updated
with every change since. S1.11–S1.16 and S3.4 also record the maintainer's decisions of that day ([`serving/engine.md`](serving/engine.md)).

---

## 1. Model-serving API (`api/`)

### S1.1 Server process — `implemented` · G5

- `uv run --extra torch python -m api run server [host] [port] [--reload]` starts a uvicorn server on `127.0.0.1:23100` by
  default, with a 300 s WebSocket ping interval and timeout. `GEMSTONE_HOST=host[:port]` overrides the
  default (arguments win over it). Auto-reload only with `--reload` or `GEMSTONE_DEV=1` (#116).
- CORS and the WebSocket accept localhost, `127.0.0.1`, `[::1]` origins (any port) and the server's own
  origin; `GEMSTONE_ORIGINS` (comma-separated, `*` allowed) adds more. A request without an `Origin`
  header always passes. A foreign Origin gets no CORS headers, a state-changing request from it is 403,
  and its WebSocket is refused before accept.
- `GEMSTONE_API_KEY`, when set, requires `Authorization: Bearer <key>` on every HTTP route and on the
  WebSocket (header or `?api_key=`), except the web assets (`/`, `/chat`, `/static`, `/webpack`,
  `/composeResources`), `HEAD /` and `GET /api/version`. When unset on a non-loopback bind, one
  start-up warning is logged; the server is never blocked (Ollama compatibility).
- Code: `api/__main__.py`, `api/src/main/security.py`, `api/src/main/server.py`.
  Test: `api/tests/test_main_args.py`, `api/tests/test_security.py`.
- Note: `python -m api` with fewer than two arguments raises `IndexError`.

### S1.2 Model catalogue — `implemented` · G5

- `GET /api/models` returns the models the registry (S1.14) knows, keyed by id, each with
  `model_name` and `model_description`: Gemstone's catalogue, `qwen3` and `default` (an alias of
  `qwen3`), then every other causal-LM model in the local store under its Hugging Face id (S1.14:
  bert, vision and other non-causal repos in the cache are not listed, #119), and the derived models
  of listed ones. `GET /v1/models` lists the same ids (S1.10). (`llama3` was removed with the GGUF
  backend, #84.)
- Code: `api/src/main/registry.py` (`CATALOGUE`, `Registry.models`), `api/src/main/server.py`
  (`models`). Test: `api/tests/test_residency.py` (`test_api_models_lists_the_registry_models_in_the_app_shape`,
  `test_v1_models_lists_the_same_models`).

### S1.3 Sessions — `partial` · G5

- `POST /api/models/{model_id}/sessions/` and `POST /api/sessions/` (model `default`) create a
  session and return `{model_id, session_id, message}`. The session id is
  `<model_id>_<YYYYmmddHHMMSS>_<8 hex>`, unique even within one second.
- `DELETE /api/sessions/{session_id}` (also accepted as `POST`) closes a session and drops its
  tool-result cache. It does not unload the model: the registry's keep_alive does (S1.14).
- A session holds a model name, never an engine (#89). Creating one loads nothing; the first
  WebSocket chat (S1.4) loads the model through the registry, so sessions on one model, the
  OpenAI API and the Ollama API share one loaded engine. Model classes are not singletons; the
  registry creates one model-class instance per loaded engine.
- Creating a session for an unknown model is `404` with a JSON `detail`: the name must resolve
  and be a catalogue name (fetched on first use, so not yet downloaded is fine) or a model in the
  local store (`Registry.check_known`, which loads and fetches nothing). `{model_id}` may hold a
  "/" (a Hugging Face id). Such an id is also part of the session id, so `{session_id}` of
  `DELETE`/`POST /api/sessions/{session_id}` is a path parameter and may hold "/" too (#117); ids
  are unchanged, so the WebSocket and clients need no change. Deleting an unknown session is `404`. Errors are raised, never returned,
  so every error response has a non-2xx status and a JSON `detail`.
- Code: `api/src/main/server.py`, `api/src/main/settings.py` (`Session`). Tests: session-id
  uniqueness (`api/tests/test_tool_cache.py`); sessions share the registry's engine and closing
  one keeps the model (`api/tests/test_residency.py`); the 404s, the `detail` bodies and the
  create, chat, delete flow and deleting a Hugging Face id's session (`api/tests/test_sessions.py`).

### S1.4 Streaming chat over WebSocket — `implemented` · G4

`WS /api/chat/streaming`. After the server accepts, the client sends exactly three text frames:

1. `{"session_id": "<id>"}`
2. the chat history as a JSON array of messages (`role`, `content`, optional `tool_calls`,
   `tool_call_id`)
3. the user prompt as plain text

The server then sends one text frame per generated chunk, then `<EOS>`, then closes. An unknown
session closes the socket with code `1008`. Reasoning arrives between `<think>` and `</think>`
frames; tool activity arrives as `<tool_call> … </tool_call>` frames (S1.6). Each tag is a frame of its own,
even when the model's streamer emits it glued to whitespace or text (#112). Generation runs off
the event loop; if the client disconnects mid-stream, generation stops and the model is free for
the next request (#35).

The model comes from the registry (S1.14): the chat holds a lease on it for the whole reply
(tool-call rounds included), then the default keep_alive (5 minutes) applies. The model class's
system prompt, sampling defaults and server-side tools wrap the engine. A catalogue model missing
from the local cache is downloaded on first use; any other model must be pulled first
(`POST /api/pull`), otherwise the socket closes with code `1008`.

A model that must be fetched but cannot be (the machine is offline, or the download fails) closes
the socket with code `1011` and a reason of at most 123 bytes that names the model, says whether
it is offline or the download failed, and what to do (connect once, or pull again) (#114). `1011`
(server error) is chosen over `1013` (busy, try again later), which keeps meaning "every resident
model is busy": a retry at once does not help when offline. Close codes the app reads: `1000`
completes the reply, anything else is an error whose reason it shows (S2.3). No frame announces a
running first-time download; that needs a client change (follow-up).

- Code: `api/src/main/server.py` (`chat_with_streaming`), `api/src/main/models/base.py`.
  Tests: `api/tests/test_server.py` (real model), `api/tests/test_residency.py` (fake engine).

### S1.5 Non-streaming chat — `removed` · G5

The session-based `POST /api/chat?user_prompt=…` (session id in the `Authorization` header) and
`GET /api/hello` were removed (decided 2026-10-03). Both were broken: `BaseModel.chat` is always a
generator, so `stream=False` returned a generator object that could not be serialised. No client
called them. `POST /api/chat` is now Ollama's chat endpoint (S1.15); the chat app keeps the
WebSocket of S1.4.
Test: `api/tests/test_ollama_api.py` (`test_legacy_chat_and_hello_routes_are_gone`, and
`test_no_client_or_document_uses_the_legacy_routes`, which scans `app/src`, the READMEs and
`docs/guide/` for callers or descriptions of the old form).

### S1.6 Tool calling — `implemented` · G6

- Models that support tools receive a schema list; when the model emits
  `<tool_call>{"name": …, "arguments": …}</tool_call>`, the server runs the tool on a thread pool,
  appends the result to the history as a `tool` message, and generates again until no more tools
  are called.
- The client sees, as `<tool_call>` frames: a `call` record, a `result` record whose content is the
  placeholder `<cached_result:<call id>>`, and finally a `history` record carrying the assistant and
  tool messages to append to its history.
- Default tools: `get_weather`, `get_weather_forecast` (Open-Meteo), `get_calendar_events`,
  `get_upcoming_holidays` (Nager.Date), `get_exchange_rate` (exchangerate-api.com), `calculate`
  (local), `search_web`, `search_website`, `fetch_webpage` (SerpApi with `SERPAPI_KEY`, Bing
  scraping fallback), `get_cache_data` (local).
- Tool-result cache (#82; idea and first implementation from PR #53 by @Mir47-47): each `Session`
  holds `tool_call_caches` (call id -> full result). The server stores every tool result there when
  the tool finishes; the client's history keeps only the `<cached_result:<call id>>` placeholder.
  `get_cache_data(tool_call_cache_id)` (default tool, `utils/cache.py`) returns the cached result,
  or `Cache '<id>' not found`, so a later turn answers from an earlier result without calling the
  original tool again. The Qwen3 system prompt tells the model to check the cache first. The cache
  is passed to `BaseModel.chat(tool_call_caches=…)` by `chat_with_streaming` and is emptied when the
  session closes. It lives in memory only and is not shared between sessions.
- Call ids are `call_<YYYYmmddHHMMSS>_<8 hex>`, unique even within one second (the former
  one-second-resolution id made same-second calls share an id and would have overwritten each
  other's cache entry). Session ids are unique too (S1.3), so sessions never share a cache.
- Robustness (#113): a malformed tool call (invalid JSON, not an object, no `name`, non-object
  `arguments`) does not raise; it becomes a `tool` result starting `Error: …` that the model sees on
  its next turn. Each tool has a deadline, `GEMSTONE_TOOL_TIMEOUT` seconds (default 30); past it the
  result is `Error: tool '<name>' timed out after <n> seconds.` and the chat goes on (the stuck
  tool's daemon thread is abandoned, its late result ignored). Waiting for tools blocks on a
  condition variable, not a spin loop. `calculate` parses with `ast` (numbers, `+ - * / // **`,
  parentheses, `pi`, `e`, `abs round pow sqrt sin cos tan log log10 exp`), caps expressions at 1000
  characters and integer results at 100 000 bits, so `9**9**9**9` returns `Error: Result too large`
  at once. `BaseModel.chat` ends a failing stream with an `ERROR:` message instead of dying.
- Code: `api/src/main/utils/__init__.py`, `api/src/main/utils/*.py`, `api/src/main/models/base.py`.
  Tests: `api/tests/test_tool_cache.py` (cache), `api/tests/test_tool_robustness.py` (#113).

### S1.7 Models — `implemented` · G5

| Id | Weights | Context | Tools |
|---|---|---|---|
| `qwen3` (default) | `Qwen/Qwen3-0.6B` (Hugging Face, safetensors) | 40 960 | yes |

Each model carries its own system prompt and sampling defaults, and runs on the engine of S1.11.
The registry maps a Hugging Face id to its model class (`registry.model_class_for`): a catalogue
model's weights get its Gemstone class; any other Hugging Face id gets a plain `BaseModel` subclass
with no tools and the base sampling defaults.
Code: `api/src/main/models/qwen3/model.py`, `api/src/main/registry.py`. Test: none for the Qwen3 checkpoint itself (it needs
a download; tests use SmolLM2-135M through the same model layer, `api/tests/test_server.py`).

### S1.8 Inference backends — `removed` · G5

The GGUF (`llama-cpp-python`), BIN (`transformers` + `bitsandbytes`) and GPTQ backends, the
`CoreRuntime` registry and `BackendType` were removed in #84, along with the `llama3` model and the
unused `utils/embedding.py`. The engine of S1.11 replaces them.
Test: `api/tests/test_backends_removed.py`.

### S1.9 Bundled web clients — `implemented` · G1

- `GET /` serves the prebuilt Wasm client (`api/src/main/webpack/gemstone.html`); `/webpack/*` and
  `/composeResources/*` serve its assets.
- `GET /chat` serves a minimal Brython test page (`api/src/main/static/`).
- Code: `api/src/main/server.py`. Test: `api/tests/test_web_assets.py`.

### S1.10 OpenAI-compatible API — `partial` · G5

This API is how external clients attach to Gemstone, including agent harnesses (`INTENT.md` § 4).

**Tool calls pass through (decided 2026-10-03).** When the request carries `tools` and the model
emits a tool call, the response returns it as `tool_calls`. Streaming returns it as tool-call
deltas. Gemstone does not execute it. The client runs the tool and sends the result back as a
`tool` message in its next request, as with the OpenAI and Ollama APIs. A harness's tools (file
edits, shell) live on the client's machine, so a provider that executed tool calls itself could
not serve it.

This differs on purpose from the chat app's WebSocket (S1.4, S1.6), where the server executes
Gemstone's built-in tools and streams the results. Both stay.

Implemented (#65), `api/src/main/openai_api.py`:

- `GET /v1/models` lists the registry's models (S1.2) in OpenAI's list format (`object: "list"`,
  entries with `id`, `object: "model"`, `created`, `owned_by: "gemstone"`).
- `model` is any name the registry resolves (S1.14). The engine is leased from the registry for
  the whole reply, so it is the one the WebSocket and the Ollama API use. The lease is released
  once the reply ends, also when a stream's client is gone before the response starts (S1.14). A catalogue model missing
  from the cache is downloaded on first use; another model that is not pulled is a 404. When that
  download cannot happen (offline, or it fails) the answer is 503 in OpenAI's error shape with
  `type: "server_error"`, `code: "model_unavailable"` and a message naming the model and saying
  "offline" or that the download failed (#114). The
  extension `keep_alive` (as in Ollama, default 5 minutes) sets how long the model stays loaded.
- `POST /v1/chat/completions` accepts `model`, `messages` (`system`, `user`, `assistant` with
  optional `tool_calls`, `tool` with `tool_call_id`; content as a string or a list of text parts),
  `tools`, `tool_choice` (`auto`, `none`, `required`, or one named function), `temperature`,
  `top_p`, `max_tokens` / `max_completion_tokens`, `seed`, `stop`, `stream` and
  `stream_options.include_usage`. The messages go to the model's engine as they are: the model's
  own system prompt and Gemstone's built-in tools are not added. Unset sampling parameters take the
  model's defaults; an unset token limit means "up to the context length".
- Non-streaming returns a `chat.completion`; streaming returns server-sent events, one
  `chat.completion.chunk` per `data:` line (the first carries `role: "assistant"`), ending with
  `data: [DONE]`. With `include_usage`, a last chunk with empty `choices` carries `usage`.
- The model's `<tool_call>{"name": …, "arguments": …}</tool_call>` blocks become `tool_calls`
  (`id` `call_<24 hex>`, `type: "function"`, `function.arguments` a JSON string); streaming sends
  each as one tool-call delta carrying the whole call. Tags split across engine chunks are
  recognised. `finish_reason` is `tool_calls` when any call was returned, else `length` when the
  token limit was reached, else `stop`. With `tool_choice: "none"` the tools are not sent and no
  output is parsed as a call. A block that is not valid JSON is returned as content.
- `reasoning_effort` switches thinking: `none` or `minimal` passes `enable_thinking=False` to the
  chat template (Qwen3 then skips its reasoning), any other value passes `True`. The vLLM extension
  `chat_template_kwargs` (an object) is passed to the template as well and wins over
  `reasoning_effort`. Neither set: nothing is passed and the template's default applies (Qwen3
  thinks). The engine takes these as `chat_template_kwargs=`; unlike other extra keywords they do
  not make a request exclusive (S1.12). An unknown model's 404 message lists the names that exist
  and says Hugging Face ids can be pulled (S1.15).
- Reasoning between `<think>` and `</think>` is removed from `content` and returned as
  `reasoning_content` (message field, or delta field when streaming), the extension used by
  DeepSeek, vLLM and others. Leading and trailing whitespace around content and reasoning is
  dropped.
- `stop` (a string or a list) ends the reply at the first match in the raw model output,
  best effort; the match is not returned.
- A streaming client that disconnects stops its generation (the engine's `cancel` is set and its
  stream closed), as on the WebSocket (#35).
- Errors use OpenAI's shape `{"error": {"message", "type", "param", "code"}}`: an unknown model is
  HTTP 404 (`model_not_found`), a malformed request HTTP 400 (`invalid_request_error`), an
  engine failure before streaming starts HTTP 500.

Not yet: `n` > 1, `logprobs`, `response_format`, `/v1/completions`, `/v1/embeddings`.
`usage` counts tokens by re-tokenising with the engine's tokenizer. Concurrent requests from
several clients wait for one another until continuous batching (S1.12) lands.

Test: `api/tests/test_openai_api.py` (a scripted fake engine, plus one SmolLM2-135M test that
streams a greedy completion and compares it with the engine's own output).

### S1.11 torchnative serving engine — `partial` · G5

One serving system built on torchnative replaces S1.8. The goal is local, private LLM serving: an
Ollama replacement.

Decided (2026-10-02):

- One engine. `CoreRuntime`, `BackendType` and the GGUF / BIN / GPTQ runtimes are removed, along
  with `llama-cpp-python` and `bitsandbytes`.
- Models load through `transformers` (`from_pretrained`) on torchnative.
- The Python requirement is 3.13 (`>=3.13,<3.14`): torchnative's floor, capped until torchnative is
  measured on 3.14. `--extra torchnative` installs torchnative's pre-release from PyPI
  (0.1.0b4 on 2026-10-03); a version floor is not pinned yet (pending the maintainer's decision).
- vLLM is not a dependency: a consequence of the torchnative decision, because vLLM assumes the
  CUDA PyTorch runtime and cannot run on torchnative.

Implemented (#84), `api/src/main/engine.py`:

- `Engine(model_id, dtype=, device=, quantization=, chat_template=, ...)` loads a causal LM with
  `from_pretrained`. `quantization="q8_0"` goes through torchnative's `TorchnativeConfig`; this is the
  November weight format but is unverified until torchnative's #15 gate measures q8_0 with batching.
- Calling it with chat messages streams the reply as text chunks. Temperature 0 is greedy and
  equals `transformers` `generate`; `seed` makes sampling reproducible.
- Concurrent callers are batched (S1.12). A request the batch cannot serve takes the exclusive
  path, where one `generate` runs at a time and concurrent callers wait and get the same output as
  sequential calls (#36). The exclusive path is used when batching is off (`batching=False`) or
  unavailable (`Engine.batching_unavailable` gives the reason: the model has no
  continuous-batching support, cannot switch to paged attention, or `psutil` is missing on CPU),
  or when the request uses `typical_p != 1`, `repeat_penalty != 1`, extra `generate` keyword
  arguments, or more tokens (prompt + `max_new_tokens`) than the paged KV cache holds.
- A request that sets no limit (`max_new_tokens <= 0`, the default of the app, Ollama and OpenAI
  entry points) means "as many as fit". When it can batch, its budget is
  `min(context length, kv_cache_tokens) - prompt length`, so default requests batch (#111) instead
  of asking for the whole context (40 960 tokens for Qwen3), which the cache (8192) never holds.
  A prompt that already fills the cache, a request the batch cannot serve, and an engine without
  batching still get the whole context, on the exclusive path. An explicit `max_new_tokens` larger
  than the cache stays exclusive: nothing is capped silently.
- A request waits at most `queue_timeout` for the model (`Engine(queue_timeout=)`, else
  `GEMSTONE_QUEUE_TIMEOUT` in seconds or as a duration such as `5m`, default 300 s, 0 or negative
  for no limit): an exclusive request behind running batched ones or another exclusive one, and a
  batched request behind an exclusive one. Then it raises `ModelBusy` (the error of the registry's
  load timeout, S1.14), which the Ollama and OpenAI endpoints answer as HTTP 503 and the chat
  WebSocket as close code 1013. The HTTP streaming endpoints decide before the response starts.
- A generation stops when its `cancel` event is set or its stream is closed; the WebSocket
  endpoint does both when the client disconnects (#35).
- The substrate is chosen at install time: `uv sync --extra torch` (upstream PyTorch) or
  `--extra torchnative`.

Capability-based skips: the CI job `.github/workflows/test-torchnative.yml` (manual with a
torchnative version input, weekly, non-blocking) runs `api/tests` on torchnative. `conftest.py`
detects the substrate (torchnative and its version, or upstream PyTorch). On torchnative only,
`needs_batching` skips when `engine.batching` is False (reason: `engine.batching_unavailable` and the
torchnative version; all of `test_batching.py` and the concurrent q8_0 test) and `needs_quantization`
skips when `torchnative.quant` has no `TorchnativeConfig` or q8_0 (`test_quantization.py`, which is
`torchnative_only`: not collected on upstream). On upstream PyTorch nothing skips for a capability
(`test_substrate.py` guards it); every other failure is loud.

Tests: `api/tests/test_queue_budget.py` (fake tokenizer and batcher: the budget rule, the queue
timeout and its 503 / 1013 mapping), `api/tests/test_engine.py`, `api/tests/test_server.py`, on upstream PyTorch with
SmolLM2-135M. Still open for M1: the same tests on torchnative (cpu, mps) once torchnative TN-M1
lands (2026-10-24).

The 2026-10-02 proposal, now implemented (#85, #91) and awaiting verification on torchnative: the engine is transformers 5.x continuous batching with a paged KV
cache, kernels live in torchnative, and the engine uses only the public `torch` / `transformers`
API so a GPU server can run the same code on upstream PyTorch. Constraint: custom kernels cannot be
registered from Gemstone, because `torch.library` registrations are no-ops on torchnative. They
must be torchnative operators. See [`serving/engine.md`](serving/engine.md).

### S1.12 Continuous batching — `partial` · G5

Concurrent requests to a loaded model are scheduled into one running batch. A request joins and
leaves the batch between decode steps, and its tokens stream back on its own connection. Part of
S1.11. Batching does not change results: greedy output equals sequential generation, and sampled
output with the same seed is identical whether or not the request was batched, because the
OpenAI- and Ollama-compatible APIs (S1.10, S1.15) accept a per-request `seed`.

Implemented (#63), `api/src/main/engine.py`:

- One transformers `ContinuousBatchingManager` per engine (`_Batcher`). Its loop runs while
  batched requests are in flight and stops when the last one leaves, keeping its KV cache; the
  model's own attention is restored then, so `model.generate` works between batches.
- Cancelling (`cancel` event) or closing one stream cancels that request in the manager; the other
  requests continue.
- Sampling is per request: `temperature`, `top_k`, `top_p`, `min_p` and `seed`. transformers'
  own batched sampler cannot do this: it draws all rows with one `torch.multinomial` on the global
  RNG, seeded once per manager (`generation/continuous_batching/model_runner.py`,
  `ModelRunner._sample`; `distributed.py`, `set_tp_seed`), so a sample depends on the other
  requests in the batch. The engine runs the manager greedily and samples in its own per-request
  logits processors instead: the last one draws each row's token from that request's own
  random stream (`random.Random(seed)`, plain Python, so no torch RNG is needed — torchnative has
  no `torch.Generator` yet), so the draw sequence of a request depends only on its seed.
  Test: `api/tests/test_sampler_rng.py`.
- Requests the batch does not serve take the exclusive path (S1.11).
- A request must fit the cache on its own (prompt + new tokens <= `kv_cache_tokens`). transformers
  cannot serve a lone request that outgrows the cache: when the cache is full and nothing else can
  be evicted, `ContinuousBatchingManager` raises "No requests can be scheduled and no requests can
  be offloaded" (`continuous_api.py`, `_generation_loop_body`), the loop treats it as critical, and
  every request in flight fails. Several requests that fit alone but not together are handled by
  transformers: the newest are evicted (copied to a CPU pool if `cpu_offload_space` is set, else
  soft-reset and requeued with their tokens so far folded into the prompt,
  `offloading_manager.py`, `offload_requests`) and resume later; the engine's sampler counts for
  that fold.
- No CUDA graphs, CUDA streams, async batching or `torch.compile` are used (all turned off in the
  `ContinuousBatchingConfig`), so the same code is meant to run on torchnative.

Tests: `api/tests/test_batching.py` (two and four concurrent greedy requests equal sequential
ones; requests overlap in time, also with no `max_new_tokens`; a seeded sample is the same batched or alone, next to a greedy
neighbour; cancelling or closing one stream leaves the others correct).
Benchmark: `benchmarks/batching_throughput.py` (1 versus N concurrent requests); not yet measured.

Unverified:

- Bit-level batch invariance of the forward pass. Batched and sequential runs push the same rows
  through matrix products of different shapes, and torch does not promise the same rounding for
  them. The sampler itself is row-independent; the tests check that results agree on SmolLM2-135M
  **in float32**, not that they always will. In bfloat16 (SmolLM2's checkpoint dtype) the top two
  logits tie exactly often enough that a one-ulp difference between batch shapes flips the token:
  CI saw a greedy reply diverge at a step where both candidates scored exactly 21.375. The
  equality criteria are therefore stated for float32; upstream PyTorch is not self-consistent in
  bfloat16 either.
- torchnative: transformers' continuous-batching modules import there but have not run.
- Prefix sharing (reusing the KV blocks of an identical prompt prefix) is off, so that a reply does
  not depend on earlier requests. Turning it on is a later, measured decision.

### S1.13 Paged attention (paged KV cache) — `partial` · G5

The KV cache is allocated in fixed-size blocks shared by all requests of a model, so memory is held
per token in use, not per maximum context. Part of S1.11.

Implemented (#64), `api/src/main/engine.py`: batched requests run on transformers'
`PagedAttentionCache` with `paged|sdpa` attention (`Engine(attn_implementation="sdpa_paged")`,
the default) or `paged|eager` (`"eager_paged"`); both are plain torch ops (gather, matmul or
`scaled_dot_product_attention`, softmax). The cache holds `kv_cache_tokens` tokens (default 8192)
in pages of `page_size` tokens (default 64); one forward pass takes at most `max_batch_tokens`
tokens (default 256; longer prompts are prefilled in chunks) and `max_batch_requests` requests
(default 16). On CPU transformers sizes the cache with `psutil`, which is therefore a dependency.

Tests: `api/tests/test_batching.py` runs on `sdpa_paged`. `eager_paged` has no test on the real
model. A torchnative paged-attention kernel for speed is still to come.

### S1.14 Model management — `partial` · G5

Follows from *Ollama replacement*. Served through the Ollama-compatible API (S1.15); the local
model store is the Hugging Face cache (`HF_HOME`).

Implemented (M3 scope, #66; several resident models and derived models, #75),
`api/src/main/registry.py`:

- **Names.** A request names a model by Gemstone id, Ollama name or Hugging Face id:

  | Name | Hugging Face id |
  |---|---|
  | `qwen3`, `default`, `qwen3:0.6b`, `qwen3:latest` | `Qwen/Qwen3-0.6B` |
  | `smollm2:135m` | `HuggingFaceTB/SmolLM2-135M-Instruct` |
  | `<org>/<repo>` (optional `:latest`) | itself |

  Any other name, or another tag on a Hugging Face id (`…:q4_K_M`), is unknown (404).
- **Pull** (`POST /api/pull`) downloads the repository's safetensors weights, config, tokenizer and
  chat template files into the cache, one file at a time (so progress is per file), each at the
  revision `main`: huggingface_hub then writes `refs/main`, which an offline load of the default
  revision (`local_files_only=True`) resolves through. A model fetched on first use (a catalogue
  name over the WebSocket or the OpenAI API, or `acquire(fetch=True)`) takes the same path.
  A repository whose Hub file list has no `*.safetensors` (only `.bin`, GGUF, ...) is refused
  **before anything is downloaded** (#119): `registry.UnsupportedModel`, answered as an Ollama
  stream line `{"error": ...}` or, with `stream: false`, HTTP 400 `{"error": ...}`.
- **Present means complete (#110).** The store treats a cached model as present, and lists it,
  only when its snapshot holds `config.json` and every weight file: each shard that
  `model.safetensors.index.json` names, or else at least one `*.safetensors`. A copy left partial
  by an interrupted pull is absent: the next pull or fetching `acquire` downloads it again (files
  already complete are reused by the cache), and `DELETE /api/delete` still removes it.
- **Only causal LMs are listed (#119).** `HFStore.list` (so `GET /api/tags`, `/api/models`,
  `/v1/models` and the app sidebar) keeps a complete cached repo only if its snapshot's
  `config.json` describes a causal LM (`is_causal_lm`): `architectures` has an entry ending in
  `ForCausalLM` or `LMHeadModel`; with no `architectures`, `model_type` must be in transformers'
  `AutoModelForCausalLM` mapping. This reads the file only (no network, no model load). Limits:
  it does not check for a chat template (a base model such as `gpt2` is listed), and an
  `architectures` list is trusted over `model_type`. `show` and loading by explicit name are not
  filtered. Safetensors headers are read once per snapshot and cached, not on every call.
- **List** (`GET /api/tags`) and **show** (`POST /api/show`) read the cache: size on disk, revision
  hash as `digest`, `model_type` as family, `max_position_embeddings` as context length, and the
  chat template (capabilities `tools` / `thinking` are inferred from it).
- **Remove** (`DELETE /api/delete`) unloads the model if resident and deletes its cached revisions.
- **Residency (#75).** Several models are resident at once, bounded by a count and an optional
  memory budget:

  | Setting | Default | Meaning |
  |---|---|---|
  | `GEMSTONE_MAX_LOADED_MODELS` | `3` | Most resident models (minimum 1), as Ollama's `OLLAMA_MAX_LOADED_MODELS`. |
  | `GEMSTONE_MAX_MODEL_MEMORY` | unset (no budget) | Total memory of the resident models: bytes, or a number with a unit (`8GB` = 8e9, `8GiB` = 2^33). |
  | `GEMSTONE_LOAD_TIMEOUT` | `5m` | How long a load waits for room (seconds or a Go duration), as `OLLAMA_LOAD_TIMEOUT`. |

  A bad value fails at start-up naming the variable. The same limits are the `Registry`
  constructor's `max_loaded`, `max_memory` and `load_timeout`.

  - **Eviction.** Loading a model that does not fit the count or the budget evicts idle models,
    least recently used first (last acquire or release), until it fits. A model with an active
    lease (a generation in flight, a WebSocket chat, an open stream) is **never** evicted. A model
    alone is never refused for being over the budget: it loads once the others are gone.
  - **Waiting.** When room can only come from busy models, the load waits for one to become idle,
    for at most `GEMSTONE_LOAD_TIMEOUT`. It then fails with `registry.ModelBusy` (a
    `TimeoutError`), which the APIs report as: Ollama HTTP 503 `{"error": ...}`, OpenAI HTTP 503
    (`type: server_error`, `code: server_busy`), the chat WebSocket close code 1013 (try again
    later). Loads are one at a time.
  - **Memory.** Before loading, a model's footprint is estimated from its safetensors headers
    (else its size on disk). After loading, the figure the engine reports replaces it:
    `Engine.memory_bytes` (function `engine.memory_bytes`), the parameters and buffers at their
    element size plus the paged KV cache (keys and values, every layer) when batching is on.
    Activations and allocator overhead are not counted, so the budget is an estimate, not a
    guarantee.
  - **Keep-alive.** A model is loaded only from the cache (no implicit download; a model that is
    not there is a 404 asking to pull it first), except a catalogue model, fetched on first use.
    Each model has its own `keep_alive` (seconds or a Go duration such as `5m`; default 5
    minutes; `0` unloads that model at once, the others stay; negative keeps it until it is
    evicted). An idle model is never unloaded mid-generation. `GET /api/ps` lists every resident
    model, latest expiry first, with `size` (the memory figure above) and `expires_at`
    (`9999-12-31T23:59:59Z` when it never expires; `size_vram` is always 0).
- **Derived models: copy and create (#75).** A derived model is a name for a local model with
  its own `system` prompt and `parameters`; it shares the weights, so it costs no memory beyond
  the base model (a derived and a base name are the same resident model). Records live in one
  JSON file, `$HF_HOME/gemstone/aliases.json` (`GEMSTONE_ALIASES` overrides the path): next to the
  cache so they move with `HF_HOME`, outside `hub/` which `scan_cache_dir` expects to hold only
  repositories. Names are `name[:tag]` (lower case, letters, digits, `.`, `_`, `-`, no `/`;
  `name` means `name:latest`) and never a built-in name (`qwen3`, `smollm2:135m`, ...). A request
  to any API names a derived model like any other; its `system` is added when the request has no
  system message and its `parameters` are defaults under the request's own options (Ollama
  API, OpenAI API for the sampling ones, `num_predict` and `stop`). `/api/tags` lists a derived
  model after the local models when its weights are in the store; `/api/delete` on a derived
  name removes only the record, never the weights.
- **One owner for every entry point (#89).** The registry is the only place engines are loaded.
  The chat app's WebSocket (S1.4), the OpenAI API (S1.10) and the Ollama API (S1.15) all lease
  the model from it, so at most one engine exists, one model is loaded once whichever API asks
  first, `GET /api/ps` reports what is really in memory, and a model switch from any API waits for
  the generations of every other API on the resident model. keep_alive (default 5 minutes)
  applies to all three. Each loaded engine is wrapped in its model class (S1.7), which the
  WebSocket uses for its system prompt, defaults and tools; the two compatible APIs use the
  engine directly. Sessions (S1.3) hold only a model name.
- **Every lease is released exactly once (#93).** A request's lease ends when its reply ends,
  however it ends: completed, failed, cancelled, or the client gone. A streaming reply (OpenAI
  S1.10, Ollama S1.15) leases the model before its response starts; the response releases the
  lease (and stops the generation) when it has been sent, when it ends without its body being read
  (the client disconnected before the response started), or when it is discarded unsent, so a
  later model switch never waits on a reply nobody receives. A request cancelled while it waits
  for the model (behind a switch, say) releases the lease it is granted afterwards. Code:
  `api/src/main/leases.py` (`acquire`, `LeasedStreamingResponse`).
- **Catalogue.** `registry.CATALOGUE` holds Gemstone's models (`qwen3`, `default`) with their
  display names and model class; it replaces `settings.MODEL_LIST`. `Registry.models()` lists it
  plus the other local models, for `GET /api/models` and `GET /v1/models` (S1.2).

Not yet: memory accounting beyond weights and KV cache (activations, allocator overhead), GPU
placement (`size_vram` is 0), pull progress per byte (it is per file; the Hub reports file
sizes), GGUF.

Tests: `api/tests/test_multi_resident.py` (fake engine and store: two models resident and
serving, count and memory eviction in least-recently-used order, the engine-reported footprint,
a model over the budget loading alone, a busy model never evicted, waiting then proceeding on
release, the timeout error, per-model keep_alive, `/api/ps`, the environment variables, byte-size
parsing, aliases and their JSON file, the alias file's default under `HF_HOME`, the memory
estimate of an engine); `api/tests/test_ollama_api.py` (fake engine and fake store; nothing is loaded or
downloaded); `api/tests/test_residency.py` (one engine for WebSocket, OpenAI and Ollama; switches
wait for another API's generation; `/api/ps` after WebSocket and OpenAI loads; the catalogue
listings; model-class mapping); `api/tests/test_lease_release.py` (the ASGI app driven by hand: a
client gone before a stream starts, a stream dropped unsent, finished replies, and a
cancelled acquire each release exactly once, and a switch to another model then completes).

### S1.15 Ollama-compatible API — `partial` · G5

Follows from *Ollama replacement*, so existing Ollama clients can use Gemstone. Served next to the
OpenAI-compatible API (S1.10). Code: `api/src/main/ollama_api.py` (an `APIRouter` included by
`server.py`).

Implemented (M3 scope, #66; the rest of the commands, #75):

- `POST /api/chat`, `POST /api/generate`, `GET /api/tags`, `POST /api/show`, `POST /api/pull`,
  `DELETE /api/delete`, `GET /api/ps`, `GET /api/version` (below).
  `GET /api/ps` shows every resident model whichever API loaded it (S1.14).
- `POST /api/copy`, `POST /api/create`, `POST /api/embed`, `POST /api/embeddings` and
  `POST /api/push` (below).
- Streaming is NDJSON by default; `"stream": false` returns one JSON object. Errors are
  `{"error": "..."}`: 400 for a malformed request, 404 for an unknown or not-pulled model, 501 for what Gemstone does
  not offer, 503 when a load timed out waiting for room (S1.14) or a model cannot be downloaded because the
  machine is offline or the download failed (the message names the model, #114; `/api/pull` too), 500 for a pull of an unknown repository. An error after streaming has started arrives as a final `{"error": ...}` line.
  A stream whose client is gone before it starts still releases the model (S1.14).
- **Health and version (#118).** `HEAD /` answers 200 with no body and needs no API key, as Ollama's
  does; `GET /` keeps serving the bundled web client. `HEAD` and `GET /api/version` return
  `{"version": "<Gemstone version>"}`, the single constant `VERSION` in `api/src/main/version.py`
  (equal to `pyproject.toml`, checked by a test; the project is not installed, so
  `importlib.metadata` cannot be used). It is a plain `MAJOR.MINOR.PATCH` string, which is what Ollama
  clients compare as semver; Gemstone's `1.x` is above every Ollama `0.x`, so a "requires Ollama >=
  0.x" gate passes.
- **Names (#118).** The names that resolve to the catalogue model (`Qwen/Qwen3-0.6B`) are `qwen3`,
  `qwen3:latest`, `qwen3:0.6b`, `qwen3:0.6b-fp16` and `default` (case-insensitive); `smollm2:135m` maps
  to `HuggingFaceTB/SmolLM2-135M-Instruct`; `Org/Model[:latest]` is a Hugging Face id. Nothing else
  is mapped: Ollama library names such as `llama3.2` are not available. Such a name is a 404
  whose message lists the names above and says a Hugging Face id (`Org/Model`) can be pulled. The
  same text is used for `/api/pull`, `/api/show` and the OpenAI API.
- **First use (#118).** `/api/chat`, `/api/generate` (also load-only) and `/api/embed`/`/api/embeddings`
  download a catalogue model that is not in the cache (`qwen3` and the Ollama names that map to it),
  as the app and the OpenAI API do, so `ollama run qwen3` works on a fresh install. Any other model
  that is not pulled stays a 404; an offline or failed download is a 503 (#114).
- **Chat.** `messages` (`role`, `content`, `tool_calls`, `tool_name`); `images` are dropped (no
  vision support). `tools` go to the chat template; a `<tool_call>{json}</tool_call>` block the
  model emits becomes `message.tool_calls: [{function: {name, arguments}}]` and is never executed
  (pass-through, S1.10). A block that is not valid JSON stays in the content as text.
- **Thinking (#118).** `think: false` passes `enable_thinking=False` to the chat template, so a
  Qwen3-style model skips its reasoning, and drops any `<think>` block from the reply; `think: true`
  (or a level string such as `"high"`) passes `enable_thinking=True` and returns the block as
  `message.thinking`; unset passes nothing: the template's default applies (Qwen3 thinks) and the
  `<think>` block stays in `content` as the model wrote it. `/api/chat` and `/api/generate` behave alike.
- **Options.** `temperature`, `top_p`, `top_k`, `min_p`, `typical_p`, `repeat_penalty` and `seed`
  go to the engine unchanged; `num_predict` becomes `max_new_tokens` (unset or ≤ 0 means up to the
  context length); `stop` cuts the output at the first stop string and stops generation (best
  effort, on the raw text). `num_ctx` and the other Ollama options are accepted and ignored. Unset
  options keep the engine's defaults, not Ollama's.
- **Load / unload.** A chat with no messages (or a generate with no prompt) only loads the model
  (`done_reason: "load"`), or with `keep_alive: 0` unloads it (`"unload"`).
- **Generate.** `prompt` and `system` become a system and a user message through the chat
  template. `raw: true` is refused (400): the engine always applies the chat template.
  `suffix`, `template`, `context`, `images` and `format` are ignored.
- **Final fields.** `done_reason` is `stop`, or `length` when `eval_count` reaches `num_predict`.
  `prompt_eval_count` is the templated prompt's token count; `eval_count` re-tokenises the
  generated text (it can differ from the generated token count by a token or two, and excludes the
  end-of-sequence token). Durations are wall-clock nanoseconds: `load_duration` is the load done
  for this request (0 when already resident), `prompt_eval_duration` runs to the first chunk,
  `eval_duration` from the first chunk to the end. Counts are omitted when the engine has no
  tokenizer.

- **Copy** (`POST /api/copy`, `source`, `destination`) names a local model, or a derived model
  (it inherits its system and parameters), anew; 200 with an empty body. 404 when `source` is not
  in the local store; 400 when `destination` is not a valid name or is a built-in name. An
  existing derived `destination` is replaced.
- **Create** (`POST /api/create`) makes a derived model (S1.14) `from` a local model (or a derived
  one, whose settings it inherits) with an optional `system` string and a `parameters` object
  (`temperature`, `top_p`, `top_k`, `min_p`, `typical_p`, `repeat_penalty`, `seed`, `num_predict`,
  `stop`, `num_ctx`, `repeat_last_n`, `presence_penalty`, `frequency_penalty`; another name or a
  mistyped value is 400). It streams `using <id>`, `writing manifest`, `success`, or with
  `"stream": false` `{"status": "success"}`. **Out of scope, 501:** `template` (Ollama's are Go
  templates; Gemstone always formats prompts with the model's Hugging Face Jinja chat template),
  `files`, `adapters`, `quantize`, `modelfile` (send `from`, `system` and `parameters` instead)
  and `messages`; each answers with a message saying why. No weights are created or converted.
- **Embeddings** (`POST /api/embed`: `input` a string or list, `truncate` default true, `keep_alive`;
  response `embeddings`, `total_duration`, `load_duration`, `prompt_eval_count`). The legacy
  `POST /api/embeddings` takes one `prompt` and returns `embedding`. A vector is the mean of the
  model's last hidden state over the text's tokens, scaled to unit length (`Engine.embed`). It is
  usable for similarity but weaker than a dedicated embedding model's. A text over the context
  length is cut to it, or with `truncate: false` is a 400; an empty input is a 400; an engine
  without `embed` is 501. It runs on the engine's exclusive path, so it waits for a batched
  generation to end (S1.12) and loads the model like any request.
- **Push** (`POST /api/push`) is **501**, always: Gemstone's models come from the Hugging Face
  Hub, not an Ollama registry, so there is nowhere to push to; the error says to upload with
  `hf upload`.
- **Pull** streams one `pulling <file>` event per file with `digest`, `total` and `completed`
  (before and after the file, from the Hub's file sizes; not per byte), then `success`.
- **Show** also returns `modelfile` (read-only, a description: `FROM`, `SYSTEM`, `PARAMETER`),
  `parameters`, `system` (derived models) and `general.parameter_count` (from the safetensors
  headers); a derived model's `details.parent_model` is its base. **Delete** of a derived name
  removes the record only.

Status `partial` stays. Not yet: `format` (JSON / schema output), blobs (`/api/blobs`), Modelfile
text and templates, adapters, quantizing, `logprobs`, image input, and concurrent generation (requests on
one model take turns until S1.12).

Tests: `api/tests/test_ollama_api.py` — on a fake engine (streaming and non-streaming chat and
generate, tool-call pass-through, option forwarding, stop, think, tags / show / ps, keep_alive,
model switching and the no-unload-mid-generation rule, delete, pull with a mocked downloader, error
format), and one test on the real engine (SmolLM2-135M, `test_chat_on_the_real_engine`);
`api/tests/test_multi_resident.py` — copy, create (and what it refuses), derived models in the
Ollama and OpenAI APIs, show, embed and the legacy embeddings, push 501, per-file pull progress.

### S1.16 4-bit quantised weights — `planned` · G5

An Ollama replacement has to run models at roughly 4-bit quality. Today torchnative offers
`TorchnativeConfig("q8_0")` through transformers' `HfQuantizer` slot. Its Q4_0 shows 29.5% logit
RMS error and degrades generation, and it has no GGUF reader. This item depends on torchnative.
No code in Gemstone.

## 2. Client (`app/`)

### S2.1 Targets — `partial` · G1

| Target | Source set | Status |
|---|---|---|
| Android (min SDK 24) | `androidMain` | `implemented` |
| Desktop JVM (Jewel decorated window; Dmg, Msi, Deb installers) | `desktopMain` | `implemented` |
| Web (Kotlin/Wasm) | `wasmJsMain` | `implemented` |
| iOS (static framework `Gemstone` for x64, arm64, simulator arm64) | `iosMain` | `partial` |

iOS: the framework targets are configured, but the Xcode build phase runs
`cd "$SRCROOT/.." && ./gradlew :composeApp:embedAndSignAppleFrameworkForXcode` — there is no
`gradlew` at that path and no `:composeApp` module (the module is `:app`). The Xcode build is
expected to fail until that script is corrected.

Code: `app/build.gradle.kts`, `app/src/iosMain/swift/iosApp.xcodeproj/project.pbxproj`. Test: none.

### S2.2 Server address — `implemented` · G5

- Android, iOS and desktop default to `127.0.0.1:23100`, overridable through
  `GEMSTONE_SERVER_HOST` and `GEMSTONE_SERVER_PORT` (JVM system property or environment variable).
- `ServerAddress` parses the host: `host`, `host:port`, `[ipv6]:port` or a full `http(s)://host[:port]`
  URL (path, query and fragment ignored). The port may be written with or without a leading colon
  (`23100`, `:23100`) and an explicit port wins over one in the host. A bare host without a port
  uses 23100; a URL without a port uses the scheme's (80 or 443). `http`/`ws` and `https`/`wss`
  follow the scheme, so an `https` address chats over `wss`. A malformed address is an
  `IllegalArgumentException`.
- `GEMSTONE_API_KEY` (same sources), when set, is sent as `Authorization: Bearer <key>` on every
  HTTP and WebSocket request. A browser cannot set headers on a WebSocket, so the web client sends
  no key. The server side is S1.1.
- Desktop command line: `--server/-s <address>` (anything above), `--host/-h`, `--port/-p`, `--help`.
- Web uses the protocol, host and port of the page (`window.location.origin`; default ports 80/443
  when it omits them), so the web client must be served by the API (S1.9).
- Android has no address setting yet: a server on the same machine is reached with
  `adb reverse tcp:23100 tcp:23100`; serving a LAN means `GEMSTONE_HOST=0.0.0.0` plus
  `GEMSTONE_API_KEY` on the server, which desktop clients reach with `--server`. Pointing an Android
  device at a LAN address needs a settings screen (S2.7).
- Code: `app/src/commonMain/.../network/ServerAddress.kt`, `app/src/cioMain/.../HttpClientFactory.cio.kt`,
  `app/src/wasmJsMain/.../HttpClientFactory.js.kt`, `app/src/desktopMain/kotlin/gemstone/Main.desktop.kt`.
  Test: `app/src/commonTest/.../network/ServerAddressTest.kt`.

### S2.3 Chat screen — `implemented` · G4, G6

- Sends the prompt over the WebSocket protocol of S1.4 and renders the reply as it streams, with
  incremental Markdown.
- Shows the model's reasoning as a collapsible block with elapsed seconds.
- Shows each tool call as a chip labelled `<function>(): <call id>`, and appends the server's
  `history` record to the local chat history so the next prompt carries the tool results.
- Defect found by reading: a chip's completion flag is created as `false` and never set to
  `true` — the `result` record has the same key as the `call` record and is ignored.
- Code: `app/src/commonMain/kotlin/gemstone/framework/network/websocket/ChatWebSocketClient.kt`,
  `.../ui/viewmodel/ChatViewModel.kt`, `.../ui/compose/screen/chat/ChatScreen.kt`.
  Test: `app/src/commonTest/.../network/ChatCloseEventTest.kt` (close code to event).
- A server close with a code other than 1000 (or none) is an error, not a finished reply: the app
  shows the close reason (S1.4: 1008, 1011, 1013) and stores nothing in the chat history. An empty
  reply is shown as an error too and kept out of the history, with its unanswered prompt dropped
  (#114; overlaps #39).

### S2.4 Layout and navigation — `implemented` · G1

In landscape the sidebar sits next to the chat; in portrait the sidebar is a start screen that
navigates to the chat, with a swipe-back gesture to return. Code: `.../screen/chat/MainScreen.kt`,
`.../navigation/AppNavigation.kt`. Test: none.

### S2.5 Model selection — `implemented` · G5

The sidebar lists the models the server offers and selecting one opens a new server session for
it, using the server id (`/api/models/{id}/sessions/`, lowercase, e.g. `qwen3`). The list is read
from `GET /api/models` (`{id: {model_name, model_description}}`) when the sidebar appears; the
sidebar shows `model_name`. The server's `default` alias is hidden from the list; it only picks the
default model (the entry with the same `model_name`, else the first), which the "All" entry uses.
If the server is unreachable the list is empty, "All" still works (it sends the `default` alias)
and a "Server unreachable - retry" button re-fetches the list (`AIModelViewModel.refreshAIModels`).
Code: `.../network/ModelCatalog.kt` (pure parsing), `.../network/http/ModelsApi.kt`,
`.../ui/viewmodel/AIModelViewModel.kt`, `.../screen/chat/SideScreen.kt`.
Test: `ModelCatalogTest` (commonTest).

### S2.6 Chat list and history — `partial` · G4

The sidebar has a "Recent Chats" section; conversations live in memory only and are lost when the
app closes. No persistence layer exists. Test: none.

### S2.7 Settings — `planned` · G1 (assumed)

`SettingsScreen.kt` is an empty file; the user name shown in the sidebar is hard-coded in
`SettingsViewModel.kt`.

### S2.8 Clean Architecture layers — `planned` · G9

`domain/entity/*` and `domain/usecase/chat/*` exist as empty declarations. All behaviour currently
lives in `framework/` (view models and the network client). The README's detailed tree describes
the target, not the current code.

### S2.9 Localisation — `partial` · G1 (assumed)

Android declares `en` and `ko` locales, but only English strings exist, and the reasoning status
line is hard-coded in Korean (`"…초 동안"`, `ChatScreen.kt`).

## 3. Planned capabilities

### S3.1 On-device inference through Python Multiplatform — `planned` · G2, G3

The client embeds the Python model code via Python Multiplatform and runs without the server.
The engine is the torchnative-based system of S1.11.
No code on `develop`.

### S3.2 Offline operation — `planned` · G3

Depends on S3.1. Today every chat goes through the server.

### S3.3 Cross-platform sync — `planned` · G7

No code. The meaning of sync is an open question in `INTENT.md` § 5.

### S3.4 Native desktop executable (GraalVM native-image) — `planned` · G8

Managed long-term by `compose-multiplatform-extended` (decided 2026-10-02). That plugin has no code
yet, so Gemstone keeps its own path in the meantime (on `develop` since #71): Gradle tasks `generateNativeResourceConfig`,
`nativeCompile`, `nativeDist` and `metadataCopy`, plus `NativeRuntime.kt`, the reachability
metadata and [`build/native-desktop.md`](build/native-desktop.md). Windows x64 only so far. Clicks
are not yet registered in the native build (under investigation), and sending a chat message there
is unverified. `./gradlew :app:compileKotlinDesktop` succeeds; the native build itself has not been
run on `develop`. When the plugin takes over, the Gradle
tasks become plugin configuration and only Gemstone-specific metadata stays here.

---

## Outside intent — needs a decision

These exist in code (or in the README) but no goal in `INTENT.md` clearly covers them.

1. **Tools that call third-party web services** (S1.6: SerpApi, Bing scraping, Open-Meteo,
   Nager.Date, exchangerate-api.com). They are useful, and G6 covers tool calling in general, but
   they send user queries to outside services, which sits uneasily with *privacy-first* (G2) and
   *full functionality without internet* (G3). Decide whether they are in scope, opt-in, or
   server-only.
2. **Remote model providers.** The README's project tree names `OpenAIClient.kt`,
   `AnthropicClient.kt` and `HuggingFaceClient.kt`. No such code exists and no goal mentions remote
   models. Either add the goal or drop them from the plan.
3. **Production routes served from `api/src/test/`** (S1.9). *Resolved (#83):* the web client and
   the Brython page now live in `api/src/main/webpack/` and `api/src/main/static/`; Python tests are
   in `api/tests/`.
4. **Settings and localisation** (S2.7, S2.9) have no goal of their own; they are filed under G1
   on the assumption that a client meant for every platform is also meant for every user. Confirm,
   and say which languages are in scope (Android declares `en` and `ko`).
