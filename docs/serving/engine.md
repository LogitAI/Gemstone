# Serving engine: decisions and design record

What the maintainer decided about Gemstone's model serving on 2026-10-02, what is still a
proposal, and the findings both rest on. `docs/INTENT.md` G5 and
`docs/SPEC.md` S1.8, S1.11–S1.16 are the contract. This file records why.

---

## 1. Decided

| # | Decision | Consequence |
|---|---|---|
| D1 | **One serving system.** The multiple-backend structure (`CoreRuntime` registry, `BackendType`, GGUF / BIN / GPTQ runtimes) is removed. | `api/src/main/backend/` is replaced by one engine. `llama-cpp-python`, `bitsandbytes` and the per-OS wheel URLs leave `pyproject.toml`. |
| D2 | **The engine depends on torchnative.** | Python moves from `>=3.12,<3.13` to `>=3.13`, the version torchnative requires. Models load through `transformers` (`from_pretrained`) on the real `torch` API. |
| D3 | **Gemstone is an Ollama replacement.** | Local, private LLM serving for the user's own machine is the goal, not only a backend for the chat app. See §4 for what that implies. |
| D4 | **Continuous batching and paged attention will be introduced.** | Concurrent requests are served in one batch, and the KV cache is paged. |
| D5 | **The native desktop build (GraalVM native-image) is managed by `compose-multiplatform-extended`.** | Gemstone keeps its own native-image path only until that Gradle plugin does the same job. See §6. |

## 2. Proposal, pending confirmation

**Engine = transformers 5.x continuous batching + paged KV cache. Kernels live in torchnative.
Gemstone owns the serving layer above it.**

| Layer | Owner | Contents |
|---|---|---|
| Serving (Ollama parity) | Gemstone | model pull / list / show / rm / ps, keep-alive, several resident models, Ollama and OpenAI APIs, tool calling, sessions |
| Scheduling and KV cache | transformers | the continuous-batching scheduler and paged cache it already ships; gaps are contributed upstream |
| Kernels | torchnative | paged attention, quantised matmul, per-device code |

The engine code should use only the public `torch` / `transformers` API. A GPU server can then run
the same code on upstream PyTorch (with `flash_paged` and CUDA graphs), while devices run it on
torchnative. That keeps D1 true: one codebase, two substrates.

## 3. Why

### 3.1 vLLM is not a dependency

This is not a separate decision by the maintainer. It follows from D1 and D2 (2026-10-02): vLLM
assumes the CUDA PyTorch runtime and cannot run on torchnative. If D1 or D2 changes, revisit it.

- vLLM's kernels (`vllm._C`, paged attention) link against the libtorch C++ ABI. torchnative
  replaces `torch._C` and has no libtorch ABI to link against.
- vLLM relies on `torch.compile` and CUDA graphs. torchnative recommends refusing `torch.compile`
  permanently, because it cannot coexist with abi3 (`torchnative/docs/graph/COMPILE.md`).
- vLLM targets Linux datacenter GPUs. Ollama users are mostly on Macs (Metal), Windows machines
  without CUDA, and CPUs. vLLM does not run on Android or iOS.
- Depending on vLLM for servers and torchnative for devices would bring back the two-stack design
  D1 removes.

If a multi-user GPU deployment is ever needed, vLLM can run as a separate process behind its own
OpenAI-compatible endpoint, which the client can point at without vLLM becoming a dependency.

### 3.2 Not written from scratch

transformers 5.15.1, the version torchnative measures against, already ships:

- `transformers/generation/continuous_batching/`: `scheduler.py`, `cache_manager.py` (paged KV
  blocks), `offloading_manager.py`, `model_runner.py`, `continuous_api.py` (`ContinuousMixin`, a
  parent of `GenerationMixin`).
- Paged attention in `transformers/integrations/`: `eager_paged.py` and `sdpa_paged.py` are plain
  torch ops (gather plus SDPA), so they are the candidates for torchnative. `flash_paged.py` is
  CUDA only.
- `transformers serve`: `/v1/chat/completions`, `/v1/completions` and `/v1/models`, with a
  `continuous_batching` option.

### 3.3 Kernels cannot live in Gemstone

On torchnative, `torch.library` registrations succeed and do nothing: 1549 of them during one
`import torch` (`torchnative/docs/design/REGISTRATIONS.md`). A custom paged-attention kernel
registered from Gemstone would silently not exist. Fast kernels have to be operators inside
torchnative's Rust core, which needs scheduling with the torchnative maintainers.

## 4. What "Ollama replacement" implies

These follow from D3 and are recorded in SPEC as `planned`. Their exact scope is still to be
confirmed:

- **Model management**: pull from Hugging Face, list, show, remove, and list loaded models (`ps`).
- **Residency**: keep-alive per model, several models loaded at once, eviction under memory
  pressure.
- **APIs**: an Ollama-compatible API (so existing Ollama clients work) next to the
  OpenAI-compatible one the README already plans.
- **4-bit weights**: most Ollama models are run at roughly Q4_K_M quality. Without a usable 4-bit
  path, Gemstone does not replace Ollama.

## 5. Risks and dependencies on torchnative

Measured or stated by torchnative itself, as of 2026-10-02:

| Risk | Detail | Source |
|---|---|---|
| Continuous batching unverified | transformers' continuous-batching modules import on torchnative, but nothing has run there. The code is written around CUDA streams and CUDA graphs. | `torchnative/docs/models/FROM_CONFIG.md`, `IMPORT_WALLS.md` |
| 4-bit quality | `TorchnativeConfig("q8_0")` loads through transformers' `HfQuantizer` slot. Q4_0 shows 29.5% logit RMS error on SmolLM2 and "degrades generation". | torchnative `README.md` |
| GGUF import | No GGUF reader yet, so existing Ollama/llama.cpp model files cannot be loaded. | torchnative `README.md` |
| Attention speed | Long-sequence prefill is about 2x slower than upstream at 1024 tokens (stale measurement). Paged attention built from plain torch ops may be slower still. | torchnative `README.md`, `docs/kernels/FLASH.md` |
| GPU backends | Metal and Vulkan run a transformer forward pass with gates. CUDA is wired but has never been compiled or run. | torchnative `README.md` |
| Model coverage | Llama and Qwen2 agree with upstream. Qwen3 is not in the verified list, and nothing larger than ~135M parameters has been measured. | torchnative `README.md` |
| Maturity | torchnative is pre-alpha. | torchnative `README.md` |

## 6. Native desktop build

`compose-multiplatform-extended` is a fork of the Compose Gradle plugin. Its aim is to produce one
native-image executable instead of a JVM-bundled package. It has no code yet, so nothing can move
there today.

- **Kept in Gemstone for now:** the `nativeCompile` / `nativeDist` tasks in `app/build.gradle.kts`,
  `NativeRuntime.kt`, `app/native-metadata/`, `META-INF/native-image/`,
  [`docs/build/native-desktop.md`](../build/native-desktop.md), `script/drive-desktop.ps1`.
- **Moves later:** once the plugin does the same job, the Gradle tasks become plugin configuration.
  Reachability metadata for the Compose/Skiko stack goes to the plugin, and only Gemstone-specific
  metadata stays here.
- **Not GraalVM-specific:** the JNA dependency (Jewel needs it on any JVM) stays in Gemstone.
- **Not committed:** the `GEMSTONE_INPUT_PROBE` debug branch in `Main.desktop.kt`.

## 7. Next step

A spike, not a commitment. Run `generate_batch` with `sdpa_paged` and `eager_paged` on torchnative
with a small model (SmolLM2-135M or Qwen3-0.6B). Check correctness against upstream and measure
throughput under concurrent requests. Report the missing operators and the 4-bit requirement to
torchnative. Confirm §2 or revise it from the result.
