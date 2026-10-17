# Free-threading checklist (CPython 3.15t)

Concurrency review of the Python API for issue #177. Free-threaded CPython 3.15t (no GIL) is the
standard interpreter for the torchnative path since 2026-10-17 (#188); the upstream PyTorch
comparison stays on 3.13. Native wheels are tracked in #178. This file lists every place in `api/src/main/` where state is
shared between threads or where code leans on the GIL, with a verdict for each. Line numbers are
from `develop` at b55520e (2026-10-17). Third-party code was read in transformers 5.18.0.

Verdicts: **safe** (locks or the design already cover it), **lock** (needs a lock), **test**
(probably safe, needs a stress test on 3.15t), **upstream** (third-party, check upstream). **fixed**
names the pull request that resolved it.

## Threads in play

- The asyncio event loop (uvicorn), and anyio's worker pool for `run_in_threadpool` and sync routes.
- Per engine: the transformers continuous-batching loop thread, Gemstone's dispatcher thread
  (`engine.py:507`), and one `model.generate` worker per exclusive request (`engine.py:314`).
- Registry keep-alive `threading.Timer` threads (`registry.py:902`).
- Per tool-using reply: a `ThreadPoolExecutor` (`utils/__init__.py:76`) plus one daemon thread per
  tool call (`utils/__init__.py:217`).

## Hazards that need work

| ID | Where | Shared state | What the GIL hides today | Verdict | Fix or test |
|---|---|---|---|---|---|
| FT1 | `settings.py:49-95` | `Session.__sessions` (class dict). Written by `create_session` and `delete_session` (sync routes, worker threads, `server.py:98,108`) and by the WebSocket handler on the loop (`server.py:122`); every constructor runs `sweep`. | `sweep` iterates `__sessions.items()` (`:84`) while another thread inserts (`:73`): `RuntimeError: dictionary changed size during iteration`. `close` does `in` then `pop` (`:91-93`) and `sweep` pops (`:86`): two threads dropping one id raise `KeyError`. `__new__` does `in` then `[]` (`:55-56`): `KeyError` if a sweep runs between. The GIL only narrows these windows. | fixed (#181) | One class-level `threading.Lock` around the lookup in `__new__`, the registration in `__init__`, `sweep` and `close`; `pop(sid, None)`. |
| FT2 | `engine.py:535,630` | `_REQUEST_IDS = itertools.count(1)`, advanced by every batched request thread inside `gate.shared` but outside any lock. | Under the GIL `count.__next__` is one atomic C call. Whether it is atomic in 3.15t is not guaranteed by the docs; a duplicate id would overwrite another request's queue (`:537`) and share its sampler state (`:732`). | fixed (#181) | Take the id under `self._queues_lock` (already held two lines later), or use a module lock. Cheap; removes the dependency. |
| FT3 | `ollama_api.py:338-353` | The `lines()` generator, closed by `_closer` from whichever thread runs cleanup (the `_stream` finally, `LeasedStreamingResponse`, or its `weakref.finalize` on GC). | The design relies on `items.close()` raising `ValueError` ("generator already executing") when another thread is inside `next`. With the GIL that check is serialized; on 3.15t it must be confirmed that concurrent `close`/`next` always raises instead of racing on the frame state. | test | Stress test below; if it flakes, use the pattern of `openai_api.py:353-356` (set `cancel`, then close under a lock that `next` also holds). |
| FT4 | `engine.py:224,232,564,284`; `openai_api.py:337-342`; `ollama_api.py:244,259,800` | One `PreTrainedTokenizerFast` per engine, called from every request thread (`apply_chat_template`, encode, `decode`), the exclusive worker (`TextIteratorStreamer`) and, at `ollama_api.py:800`, the event loop. | The Rust `tokenizers` object is behind PyO3 borrow checks; concurrent calls that touch truncation or padding state already raise "Already borrowed" in some versions. Without the GIL, overlapping calls become routine. | upstream | Check that the `tokenizers` release has a cp315t wheel and declares free-threading support. If not, a per-engine tokenizer lock (or one tokenizer per thread). Also move `ollama_api.py:800` off the loop (not FT-specific). |
| FT5 | `engine.py:523,540,558,575` | transformers `ContinuousBatchingManager`: `add_request`, `cancel_request`, `is_running` from request threads, `get_result` from the dispatcher, the generation loop on its own thread. | In 5.18.0 the cross-thread paths are queues and locks (`input_queue`, `cancel_queue`, `scheduler._cancellation_lock`, `OutputRouter._lock`), so it looks FT-ready by design, but transformers does not test without the GIL. | upstream | Check transformers' free-threading status; run the batching stress test below. |
| FT6 | `engine.py:309,184,503`; `registry.py:940-947` | torch / torchnative: forward passes from several threads at once (one CB loop per engine, exclusive workers, `embed`), up to `max_loaded` engines resident; `gc.collect` and `empty_cache` from unload threads. | Upstream torch ships free-threaded wheels; torchnative's behaviour without the GIL is unmeasured. | upstream | Ask torchnative for its FT status and `Py_mod_gil` declaration; run the batching and two-engine tests below. |
| FT7 | `engine.py:306-307` | `torch.manual_seed(seed)` sets the global RNG. | Not hidden by the GIL (pre-existing): engine A's seeded exclusive generation reseeds the RNG that engine B's concurrent exclusive sampling draws from, so a seeded reply may not reproduce. FT makes the overlap more frequent. | fixed (#183) | A registry-wide lock around seeded exclusive generations, or a per-call `torch.Generator` once torchnative has one (see `engine.py:590-591`). |
| FT8 | `utils/weather.py:12`, `calendar.py:12`, `currency.py:12`, `web_search.py:21` | One module-level `requests.Session` per tool module, used by concurrent tool threads. | requests does not document `Session` as thread-safe; already true with the GIL. | upstream | Check requests/urllib3 FT status; otherwise one session per call or per thread (`threading.local`). |

## Checked and safe as is

| ID | Where | Why it is safe |
|---|---|---|
| FT9 | `security.py:72-92` (`_warned`) | Written only by `warn_if_open`, called once at start-up on the main thread (`api/__main__.py:20`, `server.py:181`) before uvicorn starts. Worst case a duplicate log line. |
| FT10 | `leases.py:24-34` (`once`), `:43-64` (`acquire`) | `done` and `state` are read and written under a lock; anyio waits for the worker on cancellation, and both outcomes (granted after abandon, abandoned after grant) release exactly once. |
| FT11 | `leases.py:78` (`weakref.finalize`) | Without the GIL the finalizer can run on any thread; `once` makes it idempotent with the body's own cleanup. |
| FT12 | `registry.py:736-948` (residency) | `_residents`, `_loading` and every `Resident` field are changed under `self._cond`. `_expire` (`:907-913`) re-checks identity, `active` and `expires_at` under the lock, so a timer that fires after `_cancel_timer` (`:920` clears `expires_at`) or after a re-arm is a no-op. `_close` runs outside the lock on detached residents only, each detached once. |
| FT13 | `registry.py:118-123` (`_plain_classes`) | Check-then-act, but its callers (`:563`, `:779`) run under the one-load-at-a-time `_loading` flag; at worst a duplicate class. |
| FT14 | `registry.py:452-454` (`HFStore._stats`) | Concurrent `list`/`get` (`ollama_api.py:557,584`) may compute a header twice; single dict operations are internally locked in the FT build. |
| FT15 | `registry.py:511-530` (`AliasStore`) | Every read and write under `self._lock`; the file is replaced atomically. |
| FT16 | `engine.py:354-407` (`_Gate`), `:499-518` (`_start`, `_stop`) | All counters under one `Condition`; `on_first`/`on_last` run under it, so the dispatcher handle and the attention switch are serialized. |
| FT17 | `engine.py:479-480,528-531,536-537,576-577` (`_queues`) | Map access under `_queues_lock`; a `put` to a queue already popped is harmless. Per-request `queue.Queue` is thread-safe. |
| FT18 | `engine.py:607-627` (`_SamplerState`) | Locked. Draws are taken on the CB loop thread and forgotten from request threads. |
| FT19 | `engine.py:748-757` (`_LazySamplers`) | Two threads may both define the classes; each manager gets one consistent list. Benign. |
| FT20 | `engine.py:148-152` (`Engine.close`) | The tuple swap is not atomic, but the registry closes an engine once, after detaching it with no lease (`registry.py:935-937`). |
| FT21 | `engine.py:276-329` (exclusive worker) | `failure` is read after `join`; `stop` and `cancel` are `Event`s; the streamer is queue-based. |
| FT22 | `openai_api.py:276-356` (`Generation`) | `step` and `close` hold `self.lock`; `close` sets `cancel` first so a step in flight ends soon. |
| FT23 | `utils/__init__.py:74-239` (`FunctionCallResult`) | `job_list`, the message queue and the counter are under `__queue_mutex`, which the `Condition` shares. `register_tools` runs before any `stage` (`models/base.py:53-54`); the unlocked `len(job_list)` at `models/base.py:100` runs on the thread that also calls `finalize`. |
| FT24 | `utils/__init__.py:225`, `utils/cache.py:13-14` | `tool_call_caches` is set by pool threads and cleared by `Session.sweep`/`close`: single dict operations. A clear between `in` and `[]` raises `KeyError`, which `run` turns into a tool error string (`:214-215`). |
| FT25 | Loop and worker handoffs | Gemstone never calls the event loop from a worker; every handoff is `run_in_threadpool`, which anyio does not abandon on cancellation (so `server.py:160,173` never overlap `next` and `close`). transformers' `call_soon_threadsafe` path is used only for registered async handlers, which Gemstone does not register. Cancel flags are `threading.Event` (`server.py:153`, `openai_api.py:288`, `ollama_api.py:395`). |

## C extensions that could re-enable the GIL

Importing an extension module that does not declare `Py_mod_gil` (`Py_MOD_GIL_NOT_USED`) turns the
GIL back on for the whole process, with a `RuntimeWarning`. To check (cannot be verified on 3.13):
tokenizers, hf-xet, psutil, pyyaml, safetensors, torchnative, and the other compiled packages in the
current environment: torch, numpy, regex, pydantic-core, markupsafe, charset-normalizer, websockets
(plus httptools and uvloop if `uvicorn[standard]` is installed).

Measured on torchnative 0.1.0b6 (CI run 39023653125, #177): `import torch` prints "The global
interpreter lock (GIL) has been enabled to load module 'torch._C'", so the 3.15t run holds the GIL.
torchnative's extension does not declare `Py_mod_gil`; the fix belongs to torchnative.

At runtime: `sys._is_gil_enabled()` tells whether the GIL is on after the imports;
`PYTHON_GIL=0` or `python -X gil=0` forces it off even for undeclared modules (then the code above
must be safe on its own).

## How to verify

`pyproject.toml` allows 3.15 since #188. Run each under `PYTHON_GIL=0`, one at a time.

1. Smoke: import `api.src.main.server` and the engine stack, then assert `not sys._is_gil_enabled()`
   without `PYTHON_GIL=0` set: any `RuntimeWarning` names the module to fix. Add the same assert to a
   conftest guard for the 3.15t CI job.
2. FT1: 16 threads, 10 000 iterations each, mixing `Session(model_id=...)`, `Session(session_id=...)`
   and `Session.close` with `GEMSTONE_SESSION_TTL` tiny and a fake `clock`; expect no exception.
3. FT2: 32 threads drawing from one `itertools.count`, assert all ids are distinct; at engine level,
   spy on `_queues` during 64 concurrent batched requests and assert no id repeats.
4. FT3: one thread steps `lines()` over a slow fake engine while another calls the `_closer`
   cleanup; 10 000 rounds; assert the lease is released exactly once and no other exception escapes.
5. FT4: 16 threads calling `apply_chat_template`, `tokenizer(...)` and `decode` on one SmolLM2
   tokenizer; compare with single-threaded results; no `RuntimeError`.
6. FT5, FT6: `test_batching.py` with 32 concurrent greedy streams and random cancels; outputs must
   equal the sequential ones (float32). Then two resident engines generating at once.
7. FT7: two engines running seeded exclusive requests (`repeat_penalty != 1`) in parallel; each
   reply must equal its solo run.
8. FT8: concurrent tool calls against a local fake HTTP server; every result must match its request.
