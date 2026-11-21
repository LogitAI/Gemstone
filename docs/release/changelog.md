# Changelog

Changes that a user of the server or the client can notice, newest first. Each entry links the
issue that records the decision.

## 1.0.0 (unreleased)

Gemstone's first release; `pyproject.toml` has said 1.0.0 since before any release was cut. The
entries below are changes against earlier `develop` builds (team leader, 2026-11-21: rename to 1.0.0).

### In 1.0

- **One serving engine** for every model, built on transformers, on upstream PyTorch or on
  [torchnative](https://github.com/thisisthepy/torchnative) (0.1.0b11 or newer) on free-threaded
  Python 3.15t with the GIL off: streaming chat, continuous batching, a paged KV cache, q8_0 weights on
  torchnative, several resident models with keep-alive, and tool calling.
- **OpenAI- and Ollama-compatible APIs** next to the app's own WebSocket chat, with an optional API key
  (`GEMSTONE_API_KEY`).
- **The chat client** for desktop (JVM, and a GraalVM native executable on Windows x64), Android and the
  web (served by the API at `/`); iOS is partial (the Xcode project builds for the simulator).
- **Install**: `uv sync --locked`, with `--python 3.13 --extra torch` or `--python 3.15t --extra torchnative`.
  On 3.15t, `psutil`, `safetensors`, `tokenizers` and `hf-xet` are built from source (C compiler and Rust,
  [#178](https://github.com/LogitAI/Gemstone/issues/178)).
- **Checked** by the two test workflows on the release commit: upstream PyTorch with every client job, and
  torchnative on 3.15t from the lock file, including the start command.

### After 1.0

Cross-device sync ([#167](https://github.com/LogitAI/Gemstone/issues/167)) and a global search overlay
([#207](https://github.com/LogitAI/Gemstone/issues/207)), by the owner's decision of 2026-11-21.

### Changed

- **Seeded sampling on the exclusive path gives different text, once.** The engine's exclusive
  path now samples with the same per-request sampler as continuous batching instead of
  `torch.manual_seed` and `generate`'s own sampling ([#182](https://github.com/LogitAI/Gemstone/issues/182)).
  A seed now gives the same tokens whether or not the request is batched, and a seeded reply no
  longer changes when another model samples at the same time. The cost: a seeded request that
  takes the exclusive path (it sets `typical_p` or `repeat_penalty`, passes extra `generate`
  arguments, runs with batching off, or asks for more tokens than the KV cache holds) returns
  different text than the same seed returned on `develop` before #182. Seeded batched requests are unchanged.

### Fixed

- Two simultaneous deletes of one session returned 500 instead of 404, and an expiry sweep could
  fail while another request created a session ([#180](https://github.com/LogitAI/Gemstone/issues/180)).
