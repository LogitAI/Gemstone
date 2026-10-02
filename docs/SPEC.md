# Gemstone — Specification

What Gemstone does: the behavioural contract between the client (`app/`), the model-serving API
(`api/`) and the user. Every item serves a goal in [`INTENT.md`](INTENT.md) (`G1`–`G9`) and carries
a status:

| Status | Meaning |
|---|---|
| `implemented` | The behaviour exists in code that was read for this document. |
| `partial` | Some of it exists, or it exists but reading the code shows a defect. |
| `planned` | Stated as a goal; no code yet on `develop`. |

**Evidence.** Each item names the code it was read from and its test. As of this revision there are
**no behavioural tests**: the only Kotlin test, `app/src/commonTest/kotlin/gemstone/ComposeAppCommonTest.kt`,
asserts `1 + 2 == 3`, and the Python API has none (`api/src/test/` holds static web assets). So
`implemented` below means *read in code*, never *verified by a test*. Closing that gap is the
first TDD task; the test column says `none` until then.

Status was established by reading the `develop` branch at commit `2be0e37` (2026-10-02).

---

## 1. Model-serving API (`api/`)

### S1.1 Server process — `implemented` · G5

- `python -m api run server [host] [port]` starts a uvicorn server on `0.0.0.0:23100` by default,
  with auto-reload and a 300 s WebSocket ping interval and timeout.
- Code: `api/__main__.py`, `api/src/main/server.py`. Test: none.
- Note: `python -m api` with fewer than two arguments raises `IndexError`.

### S1.2 Model catalogue — `implemented` · G5

- `GET /api/models` returns the model table keyed by id: `llama3`, `qwen3`, and `default` (an alias
  of `qwen3`), each with `model_name` and `model_description`.
- Code: `api/src/main/settings.py` (`MODEL_LIST`). Test: none.

### S1.3 Sessions — `partial` · G5

- `POST /api/models/{model_id}/sessions/` and `POST /api/sessions/` (model `default`) create a
  session and return `{model_id, session_id, message}`. The session id is
  `<model_id>_<YYYYmmddHHMMSS>`.
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
  - Two sessions created for the same model within one second get the same id.
- Code: `api/src/main/server.py`, `api/src/main/settings.py` (`Session`). Test: none.

### S1.4 Streaming chat over WebSocket — `implemented` · G4

`WS /api/chat/streaming`. After the server accepts, the client sends exactly three text frames:

1. `{"session_id": "<id>"}`
2. the chat history as a JSON array of messages (`role`, `content`, optional `tool_calls`,
   `tool_call_id`)
3. the user prompt as plain text

The server then sends one text frame per generated chunk, then `<EOS>`, then closes. An unknown
session closes the socket with code `1008`. Reasoning arrives between `<think>` and `</think>`
frames; tool activity arrives as `<tool_call> … </tool_call>` frames (S1.6).

- Code: `api/src/main/server.py` (`chat_with_streaming`), `api/src/main/models/base.py`. Test: none.

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
  scraping fallback).
- Defect found by reading: call ids have one-second resolution, so two tool calls staged in the
  same second share an id.
- Code: `api/src/main/utils/__init__.py`, `api/src/main/utils/*.py`, `api/src/main/models/base.py`.
  Test: none.

### S1.7 Models — `implemented` (weights format changes with the backend transition) · G5

| Id | Weights | Backend (being removed) | Context | Tools |
|---|---|---|---|---|
| `qwen3` (default) | `Qwen/Qwen3-14B-GGUF`, `*Q4_K_M.gguf` | GGUF (default) or BIN | 40 960 | yes |
| `llama3` | `lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF`, `*Q4_K_M.gguf` | GGUF | 131 072 | no |

Each model carries its own system prompt and sampling defaults. Code:
`api/src/main/models/qwen3/model.py`, `api/src/main/models/llama3/model.py`. Test: none.

### S1.8 Inference backends — `being removed` (transition) · G5

The multiple-backend structure is dropped. These backends stay in the code until the torchnative
serving system (S1.11) replaces them.


- **GGUF** (`llama-cpp-python`) — `being removed` (works today). Downloads from the Hugging Face Hub; tries GPU
  layer counts `-1, 50, 45, … 0` until one fits; on Windows always uses CPU (0 layers).
  Default backend. Code: `api/src/main/backend/gguf.py`.
- **BIN** (`transformers` + `bitsandbytes`) — `being removed` (works today). Quantises to 4-bit NF4 on first load
  and caches the result under `api/src/main/backend/.cache/`. Code: `api/src/main/backend/bin.py`.
- **GPTQ** — `being removed` (never worked). `api/src/main/backend/gptq.py` is a scratch script (it
  contains a bare `pip install` line) and is not importable; it will not be completed.
- A backend whose library is missing is replaced by a dummy and a warning is printed.
- Test: none.

### S1.9 Bundled web clients — `implemented` · G1

- `GET /` serves the prebuilt Wasm client (`api/src/test/webpack/gemstone.html`); `/webpack/*` and
  `/composeResources/*` serve its assets.
- `GET /chat` serves a minimal Brython test page (`api/src/test/static/`).
- Code: `api/src/main/server.py`. Test: none.

### S1.10 OpenAI-compatible API — `planned` · G5

The README states the plan. No code.

### S1.11 torchnative serving engine — `planned` · G5

A single serving system built on torchnative replaces S1.8; the goal is local, private LLM serving
— an Ollama replacement. vLLM will not be used. Engine detail is a proposal, pending confirmation:
transformers 5.x continuous batching with a paged KV cache as the engine, kernels in torchnative.
No code on `develop`.

### S1.12 Continuous batching — `planned` · G5

Serve concurrent requests in one batch. Part of S1.11; no code.

### S1.13 Paged attention (paged KV cache) — `planned` · G5

Paged KV cache for the serving engine. Part of S1.11; no code.

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
hard-coded in the client (`Qwen3`, `Llama3`) instead of read from `GET /api/models`.
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

Not on `develop`. The maintainer has uncommitted work for it (Gradle tasks
`generateNativeResourceConfig`, `nativeCompile`, `nativeDist`, `metadataCopy`; Windows x64 only so
far; sending a chat message in the native build is unverified). Status changes when that work is
committed.

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
