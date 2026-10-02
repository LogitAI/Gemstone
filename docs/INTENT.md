# Gemstone — Intent

This document states why Gemstone exists and what it is for. It is the boundary for
[`SPEC.md`](SPEC.md): the spec may describe only behaviour that serves an intent written here.

Sources, in order of authority:

1. The maintainer's own words in the project README (as of commit `2be0e37`) and `pyproject.toml`.
2. The maintainer's design note on the native desktop build (an untracked working document,
   proposed location `docs/build/native-desktop.md`).
3. What the code in `app/` and `api/` does.

Anything that comes from (3) alone, or from reading between the lines, is marked:

> Inferred — confirm with the maintainer.

---

## 1. One sentence

**Gemstone is an open-source AI chat system that runs the same client on every platform and is
meant to run the model on the user's own device.**

The README calls it a *"Universal AI Chat System with On-Device Intelligence"* and *"an open-source
multi-platform AI Chat System written with Kotlin Compose Multiplatform and Python."*

## 2. Goals

### G1 — One client, every platform

The README lists Web, Android, iOS, Windows, Linux and macOS as supported platforms and names
Compose Multiplatform as the UI technology. The client is written once in Kotlin and shared across
all of them.

### G2 — On-device, privacy-first inference

*"Privacy-first local AI processing with Python Multiplatform."* The README marks this as
*scheduled*: the client is to adopt [Python Multiplatform](https://github.com/thisisthepy/python-multiplatform)
so that the Python model code can run inside the app instead of on a separate server.

### G3 — Works offline

*"Full functionality without internet connection."* This follows from G2: once the model runs on
the device, chatting must not depend on a network.

### G4 — Real-time, streaming conversation

*"WebSocket-based real-time messaging."* Tokens are shown as they are generated, including the
model's reasoning and its tool calls.

### G5 — A model-serving API in Python

Until G2 lands, and for machines that are stronger than the phone in your pocket, a Python server
loads open-weight models (Qwen 3, Llama 3.1) and serves them to the client. The goal is local,
private LLM serving — an Ollama replacement — on torchnative, as a single serving system.
Continuous batching and paged attention are planned. The multiple backends in the code today
(llama.cpp GGUF, transformers + bitsandbytes, GPTQ) are being removed (transition); vLLM will not be
used. The README says the API is *planned to become OpenAI-like*.

> Decision relayed by the maintainer, 2026-10-02.

### G6 — Tool-using assistant

The README's API reference shows a conversation in which the assistant calls a `get_weather` tool
and answers from its result. Tool calls are part of the chat protocol, not an add-on.

### G7 — Cross-platform continuity

*"Seamless experience across all devices."* (README: *Cross-Platform Sync*.)

> Inferred — confirm with the maintainer: what is synchronised (chat history? settings? model
> choice?) and through what (a server, a file, peer-to-peer) is not stated anywhere.

### G8 — A native desktop app without a JVM

The maintainer's native-build note: *"Compiles the desktop target to a standalone native
executable — no JVM, no JDK on the target machine, no installer."* It also states the wider aim
that the stack-level GraalVM metadata *"become a version-pinned bundle applications consume
instead of regenerating"* — that bundle belongs to `compose-graal-hello`, not to Gemstone.

### G9 — Clean Architecture in the client

The README: *"Gemstone AI follows Clean Architecture principles with clear separation of concerns"*,
with `domain/`, `adapter/` and `framework/` layers.

## 3. Principles

- **Open source, MIT-licensed.** (`LICENSE.md`.)
- **Open-weight models first.** Every model the README lists is an open-weight model run locally;
  remote providers are not mentioned as a goal.
- **Shared code over per-platform code.** Platform source sets contain only entry points and the
  HTTP engine choice.

> Inferred — confirm with the maintainer: the third principle is read from the code layout
> (`commonMain` holds the UI, view models and protocol; `androidMain`, `iosMain`, `desktopMain`,
> `wasmJsMain` and `cioMain` hold only entry points and Ktor engines).

## 4. What Gemstone is not

> Inferred — confirm with the maintainer. None of these is stated in the README; each is what the
> stated goals leave out.

- **Not a model-training or fine-tuning tool.** It loads and quantises published weights; it does
  not train.
- **Not a hosted service.** There is no account system, multi-tenant server or billing. The API
  server is for the user's own machine or network.
- **Not the Python runtime.** Embedding CPython in Kotlin is Python Multiplatform's job. Gemstone
  consumes it; it does not reimplement it.
- **Not a general LLM SDK.** The Python serving system exists to serve the chat app, not as a library for
  other applications.

## 5. Questions the intent does not yet answer

1. Do third-party web services (SerpApi web search, Open-Meteo weather, currency and holiday APIs)
   fit *"privacy-first"* and *"full functionality without internet"*? The tools that use them are
   implemented today; see `SPEC.md` § "Outside intent".
2. Are remote model providers (OpenAI, Anthropic, Hugging Face Inference) in scope? The README's
   project tree lists `OpenAIClient.kt`, `AnthropicClient.kt` and `HuggingFaceClient.kt`, but the
   feature list never mentions remote models and no such code exists.
3. What does *sync* mean (G7)?
4. What exactly is the torchnative serving engine? Current proposal, pending confirmation:
   transformers 5.x continuous batching with a paged KV cache as the engine, kernels in torchnative.
   (The choice of torchnative over llama.cpp is decided; this engine detail is not.)
