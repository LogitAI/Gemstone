# Changelog

Changes that a user of the server or the client can notice, newest first. Each entry links the
issue that records the decision.

## 1.0.0 (unreleased)

Gemstone's first release; `pyproject.toml` has said 1.0.0 since before any release was cut. The
entries below are changes against earlier `develop` builds (team leader, 2026-11-21: rename to 1.0.0).

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

### Added

- **`GEMSTONE_DEVICE` and a `cuda` extra.** `uv sync --python 3.13 --extra cuda` installs PyTorch's
  CUDA 13.2 wheels on Linux and Windows, and `GEMSTONE_DEVICE=cuda` (or `cuda:<n>`, `mps`) puts every
  model on that device; `GET /api/ps` reports `size_vram` for it ([#164](https://github.com/LogitAI/Gemstone/issues/164)).
- **`GEMSTONE_QUANTIZATION`.** On torchnative, every model loads in the named format, `q4_k` for 4-bit
  weights, within a measured quality bound of the float model ([#67](https://github.com/LogitAI/Gemstone/issues/67)).
