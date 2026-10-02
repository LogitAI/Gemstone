# Gemstone — Specification

What Gemstone does: the behavioural contract between the client (`app/`), the model-serving API
(`api/`) and the user. Every item serves a goal in [`INTENT.md`](INTENT.md) (`G1`–`G9`) and carries
a status:

| Status | Meaning |
|---|---|
| `implemented` | The behaviour exists in code that was read for this document. |
| `partial` | Some of it exists, or it exists but reading the code shows a defect. |
| `planned` | Stated as a goal; no code yet on `develop`. |

**Evidence.** Each item names the code it was read from and its test. Python tests live in
`api/tests/` (pytest) and cover the engine (S1.11), the WebSocket stream (S1.4) and the backend
removal (S1.8). Everything else has **no behavioural test** yet: the only Kotlin test,
`app/src/commonTest/kotlin/gemstone/ComposeAppCommonTest.kt`, asserts `1 + 2 == 3`. So
`implemented` without a named test means *read in code*, never *verified by a test*.

Status was established by reading the `develop` branch at commit `2be0e37` (2026-10-02). S1.11–S1.16
and S3.4 also record the maintainer's decisions of the same day ([`serving/engine.md`](serving/engine.md)).

---

## 1. Model-serving API (`api/`)

### S1.1 Server process — `implemented` · G5

- `python -m api run server [host] [port]` starts a uvicorn server on `0.0.0.0:23100` by default,
  with auto-reload and a 300 s WebSocket ping interval and timeout.
- Code: `api/__main__.py`, `api/src/main/server.py`. Test: none.
- Note: `python -m api` with fewer than two arguments raises `IndexError`.

### S1.2 Model catalogue — `implemented` · G5

- `GET /api/models` returns the model table keyed by id: `qwen3`, and `default` (an alias of
  `qwen3`), each with `model_name` and `model_description`. (`llama3` was removed with the
  GGUF backend, #84.)
- Code: `api/src/main/settings.py` (`MODEL_LIST`). Test: none.

### S1.3 Sessions — `partial` · G5

- `POST /api/models/{model_id}/sessions/` and `POST /api/sessions/` (model `default`) create a
  session and return `{model_id, session_id, message}`. The session id is
  `<model_id>_<YYYYmmddHHMMSS>_<8 hex>`, unique even within one second.
- `DELETE /api/sessions/{session_id}` (also accepted as `POST`) closes a session and unloads the
  model when nothing else holds it.
- The model is loaded lazily on first use, and each model class is a process-wide singleton, so
  sessions on the same model share one loaded model.
- Defects found by reading:
  - An unknown `model_id` is not rejected at creation; it fails only when the model is first used.
  - Error paths `return` an `HTTPException` instead of raising it, so the client receives HTTP 200
    with an error body.
  - Deleting an unknown session raises `ValueError`, but the endpoint catches `KeyError`, so the
    response is HTTP 500 instead of 404.
- Code: `api/src/main/server.py`, `api/src/main/settings.py` (`Session`). Test: session-id
  uniqueness only (`api/tests/test_tool_cache.py`).

### S1.4 Streaming chat over WebSocket — `implemented` · G4

`WS /api/chat/streaming`. After the server accepts, the client sends exactly three text frames:

1. `{"session_id": "<id>"}`
2. the chat history as a JSON array of messages (`role`, `content`, optional `tool_calls`,
   `tool_call_id`)
3. the user prompt as plain text

The server then sends one text frame per generated chunk, then `<EOS>`, then closes. An unknown
session closes the socket with code `1008`. Reasoning arrives between `<think>` and `</think>`
frames; tool activity arrives as `<tool_call> … </tool_call>` frames (S1.6). Generation runs off
the event loop; if the client disconnects mid-stream, generation stops and the model is free for
the next request (#35).

- Code: `api/src/main/server.py` (`chat_with_streaming`), `api/src/main/models/base.py`.
  Test: `api/tests/test_server.py`.

### S1.5 Non-streaming chat — `partial` · G5

- `POST /api/chat?user_prompt=…` with the session id in the `Authorization` header and an optional
  history body; `GET /api/hello` loads the default model and answers "Hello?".
- Defect found by reading: `BaseModel.chat` contains `yield`, so it is always a generator; with
  `stream=False` it returns a generator object rather than a string. Both endpoints are therefore
  expected to fail at serialisation. Unverified by execution.
- Code: `api/src/main/server.py`, `api/src/main/models/base.py`. Test: none.

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
- Code: `api/src/main/utils/__init__.py`, `api/src/main/utils/*.py`, `api/src/main/models/base.py`.
  Test: `api/tests/test_tool_cache.py` (cache only).

### S1.7 Models — `implemented` · G5

| Id | Weights | Context | Tools |
|---|---|---|---|
| `qwen3` (default) | `Qwen/Qwen3-0.6B` (Hugging Face, safetensors) | 40 960 | yes |

Each model carries its own system prompt and sampling defaults, and runs on the engine of S1.11.
Code: `api/src/main/models/qwen3/model.py`. Test: none for the Qwen3 checkpoint itself (it needs
a download; tests use SmolLM2-135M through the same model layer, `api/tests/test_server.py`).

### S1.8 Inference backends — `removed` · G5

The GGUF (`llama-cpp-python`), BIN (`transformers` + `bitsandbytes`) and GPTQ backends, the
`CoreRuntime` registry and `BackendType` were removed in #84, along with the `llama3` model and the
unused `utils/embedding.py`. The engine of S1.11 replaces them.
Test: `api/tests/test_backends_removed.py`.

### S1.9 Bundled web clients — `implemented` · G1

- `GET /` serves the prebuilt Wasm client (`api/src/test/webpack/gemstone.html`); `/webpack/*` and
  `/composeResources/*` serve its assets.
- `GET /chat` serves a minimal Brython test page (`api/src/test/static/`).
- Code: `api/src/main/server.py`. Test: none.

### S1.10 OpenAI-compatible API — `planned` · G5

The README states the plan. This API is how external clients attach to Gemstone, including agent
harnesses (`INTENT.md` § 4).

**Tool calls pass through (decided 2026-10-03).** When the request carries `tools` and the model
emits a tool call, the response returns it as `tool_calls`. Streaming returns it as tool-call
deltas. Gemstone does not execute it. The client runs the tool and sends the result back as a
`tool` message in its next request, as with the OpenAI and Ollama APIs. A harness's tools (file
edits, shell) live on the client's machine, so a provider that executed tool calls itself could
not serve it.

This differs on purpose from the chat app's WebSocket (S1.4, S1.6), where the server executes
Gemstone's built-in tools and streams the results. Both stay.

Concurrent requests from several clients are served by continuous batching (S1.12).
No code.

### S1.11 torchnative serving engine — `partial` · G5

One serving system built on torchnative replaces S1.8. The goal is local, private LLM serving: an
Ollama replacement.

Decided (2026-10-02):

- One engine. `CoreRuntime`, `BackendType` and the GGUF / BIN / GPTQ runtimes are removed, along
  with `llama-cpp-python` and `bitsandbytes`.
- Models load through `transformers` (`from_pretrained`) on torchnative.
- The Python requirement is `>=3.13` (torchnative's floor).
- vLLM is not a dependency: a consequence of the torchnative decision, because vLLM assumes the
  CUDA PyTorch runtime and cannot run on torchnative.

Implemented (#84), `api/src/main/engine.py`:

- `Engine(model_id, dtype=, device=, quantization=, chat_template=, ...)` loads a causal LM with
  `from_pretrained`. `quantization="q8_0"` goes through torchnative's `TorchnativeConfig`.
- Calling it with chat messages streams the reply as text chunks. Temperature 0 is greedy and
  equals `transformers` `generate`; `seed` makes sampling reproducible.
- One generation runs at a time per engine; concurrent callers wait and get the same output as
  sequential calls (#36).
- A generation stops when its `cancel` event is set or its stream is closed; the WebSocket
  endpoint does both when the client disconnects (#35).
- The substrate is chosen at install time: `uv sync --extra torch` (upstream PyTorch) or
  `--extra torchnative`.

Tests: `api/tests/test_engine.py`, `api/tests/test_server.py`, on upstream PyTorch with
SmolLM2-135M. Still open for M1: the same tests on torchnative (cpu, mps) once torchnative TN-M1
lands (2026-10-24).

Proposal, pending confirmation: the engine is transformers 5.x continuous batching with a paged KV
cache, kernels live in torchnative, and the engine uses only the public `torch` / `transformers`
API so a GPU server can run the same code on upstream PyTorch. Constraint: custom kernels cannot be
registered from Gemstone, because `torch.library` registrations are no-ops on torchnative. They
must be torchnative operators. See [`serving/engine.md`](serving/engine.md).

No code on `develop`. Test: none.

### S1.12 Continuous batching — `planned` · G5

Concurrent requests to a loaded model are scheduled into one running batch. A request joins and
leaves the batch between decode steps, and its tokens stream back on its own connection. Part of
S1.11. Batching does not change results: greedy output equals sequential generation, and sampled
output with the same seed is identical whether or not the request was batched, because the
OpenAI- and Ollama-compatible APIs (S1.10, S1.15) accept a per-request `seed`. Unverified on
torchnative: transformers' continuous-batching modules import there but have not run. No code.

### S1.13 Paged attention (paged KV cache) — `planned` · G5

The KV cache is allocated in fixed-size blocks shared by all requests of a model, so memory is held
per token in use, not per maximum context. Part of S1.11. The candidate implementations are
transformers' `sdpa_paged` / `eager_paged` (plain torch ops), with a torchnative kernel later for
speed. No code.

### S1.14 Model management — `planned` · G5

Follows from *Ollama replacement*. The exact scope is to be confirmed (`INTENT.md` § 5):

- pull a model from Hugging Face, list local models, show one, remove one;
- list loaded models (`ps`), keep a model loaded for a configurable keep-alive, hold several
  models at once, and evict under memory pressure.

Replaces the hard-coded `MODEL_LIST` (S1.2). No code.

### S1.15 Ollama-compatible API — `planned` · G5

Follows from *Ollama replacement*, so existing Ollama clients can use Gemstone. Served next to the
OpenAI-compatible API (S1.10). The endpoint set is to be confirmed. No code.

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
  `GEMSTONE_SERVER_HOST` and `GEMSTONE_SERVER_PORT` (JVM system property or environment variable;
  the port value includes the colon, e.g. `:23100`).
- Desktop command line: `--server/-s host[:port]`, `--host/-h`, `--port/-p`, `--help`.
- Web uses the host the page was served from, so the web client must be served by the API (S1.9).
- Code: `app/src/cioMain/.../HttpClientFactory.cio.kt`, `app/src/wasmJsMain/.../HttpClientFactory.js.kt`,
  `app/src/desktopMain/kotlin/gemstone/Main.desktop.kt`. Test: none.

### S2.3 Chat screen — `implemented` · G4, G6

- Sends the prompt over the WebSocket protocol of S1.4 and renders the reply as it streams, with
  incremental Markdown.
- Shows the model's reasoning as a collapsible block with elapsed seconds.
- Shows each tool call as a chip labelled `<function>(): <call id>`, and appends the server's
  `history` record to the local chat history so the next prompt carries the tool results.
- Defect found by reading: a chip's completion flag is created as `false` and never set to
  `true` — the `result` record has the same key as the `call` record and is ignored.
- Code: `app/src/commonMain/kotlin/gemstone/framework/network/websocket/ChatWebSocketClient.kt`,
  `.../ui/viewmodel/ChatViewModel.kt`, `.../ui/compose/screen/chat/ChatScreen.kt`. Test: none.

### S2.4 Layout and navigation — `implemented` · G1

In landscape the sidebar sits next to the chat; in portrait the sidebar is a start screen that
navigates to the chat, with a swipe-back gesture to return. Code: `.../screen/chat/MainScreen.kt`,
`.../navigation/AppNavigation.kt`. Test: none.

### S2.5 Model selection — `partial` · G5

The sidebar lists models and selecting one opens a new server session for it. The list is
hard-coded in the client (`Qwen3`) instead of read from `GET /api/models`.
Code: `.../ui/viewmodel/AIModelViewModel.kt`, `.../screen/chat/SideScreen.kt`. Test: none.

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
3. **Production routes served from `api/src/test/`** (S1.9). The deployed web client and the Brython
   page live under a `test` directory. This is a layout question rather than a behaviour question,
   but it decides where Python tests can go.
4. **Settings and localisation** (S2.7, S2.9) have no goal of their own; they are filed under G1
   on the assumption that a client meant for every platform is also meant for every user. Confirm,
   and say which languages are in scope (Android declares `en` and `ko`).
