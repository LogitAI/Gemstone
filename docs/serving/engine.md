# Serving engine: decisions and design record

What the maintainer decided about Gemstone's model serving on 2026-10-02, what is still a
proposal, and the findings both rest on. `docs/INTENT.md` G5 and
`docs/SPEC.md` S1.8, S1.11–S1.16 are the contract. This file records why.

---

## 1. Decided

| # | Decision | Consequence |
|---|---|---|
| D1 | **One serving system.** The multiple-backend structure (`CoreRuntime` registry, `BackendType`, GGUF / BIN / GPTQ runtimes) is removed. | `api/src/main/backend/` is replaced by one engine. `llama-cpp-python`, `bitsandbytes` and the per-OS wheel URLs leave `pyproject.toml`. Done (`SPEC.md` S1.8). |
| D2 | **The engine depends on torchnative.** | Python moves from `>=3.12,<3.13` to `>=3.13`, the version torchnative requires. Since 2026-10-17 the standard interpreter on torchnative is free-threaded 3.15t (#177). Models load through `transformers` (`from_pretrained`) on the real `torch` API. |
| D3 | **Gemstone is an Ollama replacement.** | Local, private LLM serving for the user's own machine is the goal, not only a backend for the chat app. See §4 for what that implies. |
| D4 | **Continuous batching and paged attention will be introduced.** | Concurrent requests are served in one batch, and the KV cache is paged. Implemented on upstream PyTorch (#63, #64); `partial` in `SPEC.md` S1.12, S1.13. |
| D5 | **The native desktop build (GraalVM native-image) is managed by `compose-multiplatform-extended`.** | Gemstone keeps its own native-image path only until that Gradle plugin does the same job. See §6. |

## 2. Proposal, pending confirmation

**Engine = transformers 5.x continuous batching + paged KV cache. Kernels live in torchnative.
Gemstone owns the serving layer above it.**

Status: implemented on upstream PyTorch (#85, #91), not yet verified on torchnative
(`SPEC.md` S1.11). The maintainer has not confirmed it as a decision.

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

transformers 5.15.1, the version torchnative measured against on 2026-10-02, already ships
(Gemstone now requires `transformers>=5.18`, `pyproject.toml`):

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

These follow from D3. Their exact scope is still to be confirmed. Status in SPEC: model
management, residency and the Ollama API are `partial` (S1.14, S1.15, #66, #75); 4-bit weights
are `planned` (S1.16, #67).

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
  [`docs/build/native-desktop.md`](../build/native-desktop.md), `tools/drive-desktop.ps1`.
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

First result (2026-10-17, torchnative 0.1.0b6, CI run 39023653125): generation fails, because the
torchnative `torch._C` shim lacks `triu_` (transformers' continuous batching), `nansum` and
`Generator.get_state`. The missing operators are reported on #177. All three missing operators
(along with `tril_`, `nanmean` and `Generator.set_state`; `thisisthepy/torchnative#115`) were fixed in
`thisisthepy/torchnative#117` (`58bd28a7`) and shipped in `torchnative 0.1.0b7` and `0.1.0b8`
(`sys._is_gil_enabled() == False`, exclusive generation works). The remaining continuous-batching
`index_copy_` slice-view bottleneck is fixed in `thisisthepy/torchnative#254` (PR #338, awaiting `0.1.0b9`).

### 7.1 Throughput baseline table (#74)

Measured on CI run `44592107867` (commit `416a649`, runner `ubuntu26 20261111.82.6`, `Intel(R) Xeon(R) 6975P-C`, model `HuggingFaceTB/SmolLM2-135M`, float32, CPU, `--tokens 64`, `--attn sdpa_paged`, artifact `batching-throughput-baseline-44592107867`). Comparison between torchnative 0.1.0b9 (CPython 3.15.2t, GIL off) and upstream PyTorch 2.15.0+cpu (CPython 3.13.18):

| Concurrency | torchnative (1 request / batched / exclusive) | upstream torch (1 request / batched / exclusive) |
|---|---|---|
| c=1 | 5.3 / 5.2 / 7.4 tok/s | 34.3 / 34.1 / 52.9 tok/s |
| c=2 | 5.2 / 8.6 / 7.6 tok/s | 34.6 / 50.3 / 53.4 tok/s |
| c=4 | 5.2 / 15.0 / 7.5 tok/s | 34.8 / 87.1 / 54.1 tok/s |

### 7.2 Breakdown of the gap (#74)

The table in §7.1 says how far behind torchnative is, not where the time goes. The plan below came
from the Gemstone teammate and was approved by the thisisthepy leader on 2026-11-20 (no owner
decision is involved; it is a measurement, not a direction). `benchmarks/batching_breakdown.py`
measures three layers in one process, on the same model and prompts (SmolLM2-135M, float32, CPU,
`--tokens 64`, `--attn sdpa_paged`), and writes one JSON:

| Layer | What runs | What the difference to the layer above means |
|---|---|---|
| `forward` | The raw model forward with a `DynamicCache`: the prefill of the prompts and one decode step (one token per row), timed separately, at batch 1 and 4. No manager, no Gemstone. | The substrate's kernel time. Decode-step milliseconds is the key number. |
| `manager` | transformers' `ContinuousBatchingManager`, built with the engine's `ContinuousBatchingConfig`, driven step by step in the calling thread (`prepare_next_batch`, `_generation_step`, `update_batch`, each timed) with no thread, no queue, no tokenizer and none of Gemstone's logits processors. Batch 1 and 4. | The paged KV cache write, the paged attention gather and the scheduler. |
| `engine` | The full `Engine`, 4 concurrent requests on 4 threads, as served. The manager's three calls are timed from inside the loop as well, so the time outside them is visible. Inside `_generation_step` the model forward, Gemstone's logits-processor list (and each filter of it) and the manager's sampling are timed too, and one step's logits processing is profiled by operator (`logits_ops`); every `tokenizer.decode` of the request threads is clocked (`decode_call`). Run twice: `engine` with greedy requests, where the neutral filters are skipped, and `engine_sampled` with the API's default sampling and a fixed seed, where every filter runs. | Gemstone's own overhead: the per-request samplers (inside the step: `step_logits`), per-token decoding (off the loop thread), the queues and the gate (`outside_loop_ms`). |

Every series discards a warm-up run and reports its repetitions, median and minimum (`summarize`).
For one decode step at each batch size, the script also records the ten most expensive operators
(`time_operators`): with `torch.profiler` where the substrate has one, and always with a
`TorchDispatchMode` that clocks every operator the dispatcher sees, with the time of nested
operators subtracted. torchnative 0.1.0b9 has no profiler (`torch.profiler.profile()` raises
`NotImplementedError: _supported_activities`; `torchnative/rust/torch_c/src/bootstrap.py` states it),
but its dispatcher does call a `TorchDispatchMode` (`torchnative/docs/design/DISPATCH3.md`), so the
table exists on both substrates and is comparable only with itself (the mode's own overhead is in
the numbers; `torch.profiler` on upstream is the reference for what the mode costs).

`test-torchnative.yml` with `measure=true` runs the script twice per substrate, alternating
torchnative and upstream PyTorch (`--round 1` torchnative, upstream, `--round 2` torchnative,
upstream) on the same runner, and uploads `batching-breakdown.json` in the same artifact as
`batching-throughput.json`.

#### 7.2.1 Run 44792524320 and what it attributed (#74)

Run `44792524320` (commit `91407d9`, runner `ubuntu26 20261111.196.1`, `AMD EPYC 7763`, round 1 of
the A/B alternation; torchnative 0.1.0b9 on CPython 3.15.2t with the GIL off, upstream torch
2.15.0+cpu on 3.13.18). Medians, ms per decode step at batch 4 / concurrency 4:

| Layer | torchnative | upstream |
|---|---|---|
| `forward`, decode step | 122.0 | 75.0 |
| `manager`, `_generation_step` | 134.6 | 81.8 |
| `manager`, prepare + forward + update | 135.2 | 82.2 |
| `engine`, `_generation_step` (inside the loop) | 229.7 | 96.9 |
| `engine`, `prepare_next_batch` | 0.85 | 0.88 |
| `engine`, `update_batch` | 0.09 | 0.07 |
| `engine`, end to end | 235.6 | 103.5 |

Where the engine's extra time is: not around the manager's calls. Prepare and update together are
under 1 ms, and the whole run spent 217 ms (torchnative) and 244 ms (upstream) outside the three
calls, about 3.5 ms per step, which covers the loop start and stop, the queues, the gate and the
per-token decoding. The extra 95 ms (torchnative) and 15 ms (upstream) per step is inside
`_generation_step` itself: 229.7 against the manager layer's 134.6 on the same model, cache and
batch size.

What runs inside `_generation_step` in the engine and not in the manager layer is Gemstone's five
logits processors (`model_runner.py`, `_forward_process_and_sample`: forward, then
`logit_processor(...)`, then the argmax). The manager layer is built without them
(`build_manager`, `do_processing` False). Before this change every one of the five ran on every
step whatever the request asked for, a greedy request included: temperature (one division over
`[B, V]`), top_k (a descending sort of the whole vocabulary row), top_p (a second sort, a softmax,
a cumulative sum, a scatter), min_p (a softmax and a max) and the draw (a softmax, a cumulative
sum, a scatter and a `where`), about 45 operators over `[4, 49152]` per step. On torchnative
0.1.0b9 two of them are far more expensive than upstream, read from its source
(`torchnative/rust/torch_c/src/aten.rs`): `aten.cumsum.default` on the CPU is written as one
narrow-and-add pair per element along the scanned dimension followed by a `cat` of all the parts,
so one cumulative sum over a 49152-token row is about 49 thousand kernel launches, and each row of
`aten.sort.default` is a transcribed `std::sort` run row by row. Two cumulative sums and two sorts
ran per step, for greedy requests too.

The attribution of the 95 ms to the processors is inferred from this code reading and from the
layers' difference: the engine layer of run 44792524320 did not time the processors separately.
`measure_engine` now clocks the model forward, the processor list and each filter, and the
sampling inside the step, and profiles one step's logits processing by operator, so the next
`measure=true` run states it directly (`step_model`, `step_logits`, `step_logits_by_processor`,
`logits_ops`), for a greedy batch (`engine`, where the filters are now skipped) and for a batch with
the API's default sampling (`engine_sampled`, where every filter runs).

What was changed in the engine (`api/src/main/engine.py`, SPEC S1.12), without changing a token:

- A filter whose argument is neutral for every row of the step (temperature 1, `top_k` 0, `top_p`
  1, `min_p` 0, no seed) is skipped. The batch's filters learn this in `prepare_tensor_args`,
  which the loop thread runs before each step's forward; the exclusive sampler reads its own
  arguments. A greedy batch, the benchmark's case and every `temperature=0` request, runs none of
  the five. Expected effect on torchnative at c=4: the `engine` step falls from about 230 ms to
  about the manager's 135 ms plus the sampling, so throughput from 17 to about 28 tokens/s on the
  EPYC runner; on upstream from 97 to about 82 ms per step. To be confirmed by the next run.
- `top_k`'s threshold comes from `torch.topk` of the batch's largest k instead of a sort of the
  whole row (the k-th largest value is the same). This is for sampled requests, where nothing is
  skipped: with the API's default `top_k=40`, a partial selection of 40 values replaces one of
  the two full-row sorts per step. Expected effect: a few ms per step on torchnative (its topk is
  `nth_element` plus a partial sort on CPU), under 1 ms upstream. The second sort (top_p) and the
  two cumulative sums remain on the sampled path; their exact cost on torchnative is what the
  next run's `logits_ops` table shows.

Not changed, and why:

- The two cumulative sums of the sampled path (top_p and the draw) have no exact replacement
  inside Gemstone: a prefix sum computed in another order (a parallel scan, a sum over the
  surviving tokens only) rounds differently and can move a seeded draw at a boundary. The fix
  belongs in torchnative, as a one-pass `cumsum` kernel (agent proposal, not confirmed: an issue
  there would be the next step, see the #74 thread).
- The per-token `tokenizer.decode` of the whole sequence (`_Batcher.stream`): linear in the reply
  so far, so quadratic over a reply, but it runs on the request thread beside the forward, and at
  64 tokens it is a fraction of a millisecond per token. It matters only for replies of thousands
  of tokens; an incremental decode changes where chunks split, so it is a separate change.
- The request threads' `queue.get(timeout=0.05)` and the dispatcher's `get_result(timeout=0.1)`
  wake on each token; the timeouts only bound cancel detection and the loop's stop. No per-step
  cost was found there.
