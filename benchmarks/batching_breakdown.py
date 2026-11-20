"""
Where one decode step's time goes on the batched path: forward, manager, engine (#74, SPEC S1.12).

    uv run --extra torch python benchmarks/batching_breakdown.py [--tokens 64] [--batches 1,4] [--json out.json]

The throughput baseline (`batching_throughput.py`, `docs/serving/engine.md` 7.1) put torchnative about
5.8x behind upstream PyTorch at 4 concurrent requests. This script splits a decode step into three
layers, measured in one process on the same model (`GEMSTONE_TEST_MODEL`, default SmolLM2-135M,
float32, read from the local Hugging Face cache only) and the same prompts:

- `forward`: the raw model forward with a `DynamicCache`, no manager and no Gemstone. The prefill of
  the prompts and ONE decode step (one new token per row) are timed separately, at every batch size.
  Decode-step milliseconds is the key number: the substrate's kernel time.
- `manager`: transformers' `ContinuousBatchingManager`, built with the engine's configuration but
  driven step by step in this thread (`prepare_next_batch`, `_generation_step`, `update_batch`, each
  timed), with no thread, no queue, no tokenizer and none of Gemstone's logits processors. The
  difference to `forward` is the paged KV cache write, the paged attention gather and the scheduler.
- `engine`: the full Gemstone `Engine`, N concurrent requests on N threads, as served. The manager's
  three calls are timed from inside its loop too, so the time outside them (per-token decoding,
  queues and the gate) is visible as `outside_loop_ms`. Inside `_generation_step` the model forward,
  Gemstone's logits processors (the list, and each filter) and the manager's sampling are timed as
  well, because the processors run inside that call and the manager layer has none (#74): their
  cost is the difference between the two layers' forward time. One decode step's logits processing
  is also run under the operator profilers (`logits_ops`), and every `tokenizer.decode` of the
  request threads is clocked (`decode_call`, lock wait included). The layer runs twice: `engine`
  with greedy requests (`temperature=0`, as the throughput script), where a filter at its neutral
  value is skipped (SPEC S1.12), and `engine_sampled` with the API's default sampling (`SAMPLED`:
  the defaults of `models/base.py` and a fixed seed), where every filter runs.

Every series discards a warm-up run and reports repetitions, median and minimum. For one decode step
at each batch size the ten most expensive operators are recorded twice: with `torch.profiler` where
the substrate has one (torchnative 0.1.0b9 has none and says so), and with a `TorchDispatchMode`
that clocks every operator the dispatcher sees, nested operators subtracted (`time_operators`).

`test-torchnative.yml` with `measure=true` runs this on torchnative and upstream PyTorch alternately
on the same runner (`--round`) and uploads the JSON next to `batching-throughput.json`. Measure alone
on an idle machine (check `uptime` first): the numbers are meaningless under load.
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from api.tests.conftest import CHAT_TEMPLATE, TEST_DEVICE, TEST_MODEL  # noqa: E402
from benchmarks.batching_throughput import PROMPTS, run as run_engine  # noqa: E402

TOP = 10  # operators kept per profile table
# The sampling of the `engine_sampled` layer: the API's defaults (`api/src/main/models/base.py`)
# with a fixed seed, so every filter runs and the replies are reproducible.
SAMPLED = dict(temperature=0.2, top_p=0.95, top_k=40, min_p=0.05, seed=1)


# ------------------------------------------------------------------------------------------------ #
# Statistics and operator tables (no model needed; api/tests/test_benchmarks.py covers them)
# ------------------------------------------------------------------------------------------------ #

def summarize(samples_ms, warmup=0):
    """ A timed series after its first `warmup` samples are dropped: repetitions, median, min, mean. """
    kept = [float(s) for s in list(samples_ms)[warmup:]]
    if not kept:
        return {"repetitions": 0, "median_ms": None, "min_ms": None, "mean_ms": None, "samples_ms": []}
    return {
        "repetitions": len(kept),
        "median_ms": statistics.median(kept),
        "min_ms": min(kept),
        "mean_ms": statistics.fmean(kept),
        "samples_ms": kept,
    }


def time_operators(fn):
    """
    Runs `fn` under a `TorchDispatchMode` that times every operator the dispatcher sees. Self time is
    the operator's time minus the operators it dispatched itself. Returns the top ten by self time, or
    `available: False` with the reason when the substrate cannot run a dispatch mode.
    """
    import torch

    try:
        from torch.utils._python_dispatch import TorchDispatchMode
    except Exception as e:  # the substrate has no dispatch modes
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "ops": []}

    totals, selfs, counts = {}, {}, {}
    stack = []  # time of the nested operators of each operator in flight, innermost last

    class Timer(TorchDispatchMode):
        def __torch_dispatch__(self, func, types, args=(), kwargs=None):
            name = str(func)
            start = time.perf_counter()
            stack.append(0.0)
            try:
                return func(*args, **(kwargs or {}))
            finally:
                elapsed = time.perf_counter() - start
                nested = stack.pop()
                if stack:
                    stack[-1] += elapsed
                totals[name] = totals.get(name, 0.0) + elapsed * 1000
                selfs[name] = selfs.get(name, 0.0) + (elapsed - nested) * 1000
                counts[name] = counts.get(name, 0) + 1

    try:
        with torch.no_grad(), Timer():
            fn()
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "ops": []}
    rows = sorted(selfs, key=lambda n: selfs[n], reverse=True)
    return {
        "available": True,
        "operators_seen": len(selfs),
        "total_self_ms": sum(selfs.values()),
        "ops": [{"op": n, "self_ms": selfs[n], "total_ms": totals[n], "calls": counts[n]} for n in rows[:TOP]],
    }


def profile_operators(fn):
    """ Runs `fn` under `torch.profiler` (CPU) and returns the top ten operators by self CPU time, or
    `available: False` with the reason: torchnative has no profiler and raises on it. """
    import torch

    try:
        from torch.profiler import ProfilerActivity, profile

        with torch.no_grad(), profile(activities=[ProfilerActivity.CPU]) as prof:
            fn()
        events = sorted(prof.key_averages(), key=lambda e: e.self_cpu_time_total, reverse=True)
    except Exception as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "ops": []}
    return {
        "available": True,
        "operators_seen": len(events),
        "ops": [
            {"op": e.key, "self_ms": e.self_cpu_time_total / 1000, "total_ms": e.cpu_time_total / 1000, "calls": e.count}
            for e in events[:TOP]
        ],
    }


def operator_tables(fn):
    """ Both operator tables of one call of `fn` (`fn` runs twice). """
    return {"torch_profiler": profile_operators(fn), "dispatch_timer": time_operators(fn)}


# ------------------------------------------------------------------------------------------------ #
# The three layers
# ------------------------------------------------------------------------------------------------ #

def encode_prompts(tokenizer, n):
    """ The first `n` benchmark prompts rendered with the test chat template, as the engine renders them. """
    prompts = (PROMPTS * ((n // len(PROMPTS)) + 1))[:n]
    ids = []
    for prompt in prompts:
        text = tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}], chat_template=CHAT_TEMPLATE, add_generation_prompt=True,
            tokenize=False,
        )
        ids.append(list(tokenizer(text, add_special_tokens=False)["input_ids"]))
    return ids


def _sync(device):
    """ Waits for an accelerator before reading the clock; a no-op on the CPU. """
    if device.type != "cpu":
        import torch

        module = getattr(torch, device.type, None)
        if module is not None and hasattr(module, "synchronize"):
            module.synchronize()


def measure_forward(model, prompt_ids, tokens, warmup_tokens, profile_at):
    """
    Layer 1. The prompts are cut to the shortest one's length (their tails, which end with the
    generation prompt), so the batch needs no padding and no mask, and every row sees the same context
    length. Prefill is one forward of the batch; a decode step is one forward of one token per row on
    the `DynamicCache`. Only the forward is inside the clock; the greedy argmax is outside. The decode
    step `profile_at` is run under the profilers instead of the clock.
    """
    import torch
    from transformers import DynamicCache

    length = min(len(p) for p in prompt_ids)
    batch = torch.tensor([p[-length:] for p in prompt_ids], dtype=torch.long, device=model.device)
    keep = {}
    if hasattr(model, "_supports_logits_to_keep") and model._supports_logits_to_keep():
        keep["logits_to_keep"] = 1  # the LM head runs on the last position only, as the manager does

    def run(steps, profiled_step=None):
        tables = None
        with torch.no_grad():
            cache = DynamicCache()
            _sync(model.device)
            start = time.perf_counter()
            out = model(input_ids=batch, past_key_values=cache, use_cache=True, **keep)
            _sync(model.device)
            prefill_ms = (time.perf_counter() - start) * 1000
            next_ids = out.logits[:, -1, :].argmax(dim=-1, keepdim=True)
            step_ms = []
            for step in range(steps):
                if step == profiled_step:
                    holder = {}

                    def one_step(ids=next_ids):
                        holder["out"] = model(input_ids=ids, past_key_values=cache, use_cache=True, **keep)

                    tables = operator_tables(one_step)
                    out = holder["out"]
                else:
                    start = time.perf_counter()
                    out = model(input_ids=next_ids, past_key_values=cache, use_cache=True, **keep)
                    _sync(model.device)
                    step_ms.append((time.perf_counter() - start) * 1000)
                next_ids = out.logits[:, -1, :].argmax(dim=-1, keepdim=True)
        return prefill_ms, step_ms, tables

    run(warmup_tokens)  # warm-up, discarded
    prefill_ms, step_ms, tables = run(tokens, profiled_step=profile_at if profile_at < tokens else None)
    return {
        "batch": len(prompt_ids),
        "prompt_tokens": length,
        "prefill": summarize([prefill_ms]),
        "decode_step": summarize(step_ms),
        "profiled_at_step": profile_at if profile_at < tokens else None,
        "ops": tables,
    }


def build_manager(model, tokenizer, attn, kv_cache_tokens, page_size, max_batch_tokens, max_batch_requests):
    """
    transformers' manager with the configuration `api/src/main/engine.py` (`_Batcher`) gives it, and
    the same attention switch, but without Gemstone's logits processors. Returns the manager, the
    attention implementation the batched path runs on and the model's original one.
    """
    from transformers import ContinuousBatchingConfig, GenerationConfig

    from api.src.main.engine import PAGED_ATTENTION

    paged = PAGED_ATTENTION[attn]
    original = model.config._attn_implementation
    eos = model.generation_config.eos_token_id
    if eos is None:
        eos = tokenizer.eos_token_id
    generation_config = GenerationConfig(do_sample=False, eos_token_id=eos, max_new_tokens=256)
    cb_config = ContinuousBatchingConfig(
        page_size=page_size,
        num_blocks=max(1, -(-kv_cache_tokens // page_size)),
        max_batch_tokens=max_batch_tokens,
        max_requests_per_batch=max_batch_requests,
        max_blocks_per_request=0,
        max_memory_percent=0.9,
        allow_block_sharing=False,
        use_cuda_graph=False,
        use_async_batching=False,
        default_compile_level=0,
    )
    model.set_attn_implementation(paged)
    try:
        switched = model.config._attn_implementation
        if switched != paged and switched == paged.split("|", 1)[1]:
            paged = switched  # transformers 5.19+ runs continuous batching on the plain implementation
        if switched != paged:
            raise RuntimeError(f"{type(model).__name__} cannot switch to {paged} attention (got {switched}).")
        manager = model.init_continuous_batching(
            generation_config=generation_config, continuous_batching_config=cb_config
        )
        manager.warmup()
    finally:
        if model.config._attn_implementation != original:
            model.set_attn_implementation(original)
    return manager, paged, original


def drive_manager(manager, model, prompt_ids, tokens, profile_at=None):
    """
    One generation of `tokens` tokens per prompt, driven in this thread exactly as the manager's own
    loop body does it (`_generation_loop_body`), with the clock around each of its three calls. EOS is
    off, so every request runs the full length and the batch never shrinks. Returns the per-step
    timings, the finished outputs and the operator tables of step `profile_at` (a decode step).
    """
    import torch

    processor = manager._create_batch_processor()  # resets the existing one and its paged cache
    manager.batch_processor = processor
    manager.background_thread_status.clear()
    for i, ids in enumerate(prompt_ids):
        accepted = manager.add_request(
            input_ids=list(ids), request_id=f"breakdown-{i}", max_new_tokens=tokens, streaming=False, eos_token_id=-1,
        )
        if accepted is None:
            raise RuntimeError("the manager did not accept a request")

    steps, tables = [], None
    with torch.no_grad():
        while True:
            start = time.perf_counter()
            ready = processor.prepare_next_batch()
            prepared = time.perf_counter()
            if not ready:
                if processor.has_pending_requests():
                    raise RuntimeError("the scheduler scheduled nothing while requests are pending")
                break
            prefill = any(
                state.status.name.startswith("PREFILLING") for state in processor.scheduler.active_requests.values()
            )
            if len(steps) == profile_at and not prefill:
                # The step runs once per profiler on the same prepared batch: the forward writes the same
                # KV slots and the same tokens again, and one `update_batch` consumes them.
                tables = operator_tables(lambda: processor._generation_step(model))
                processor.update_batch()
                steps.append({"kind": "profiled", "prepare_ms": 0.0, "forward_ms": 0.0, "update_ms": 0.0})
                continue
            processor._generation_step(model)
            _sync(model.device)
            stepped = time.perf_counter()
            processor.update_batch()
            updated = time.perf_counter()
            steps.append({
                "kind": "prefill" if prefill else "decode",
                "prepare_ms": (prepared - start) * 1000,
                "forward_ms": (stepped - prepared) * 1000,
                "update_ms": (updated - stepped) * 1000,
            })
    outputs = []
    while True:
        result = manager.get_result(timeout=0)
        if result is None:
            break
        outputs.append(result)
    return steps, outputs, tables


def measure_manager(manager, model, paged, original, prompt_ids, tokens, warmup_tokens, profile_at):
    """ Layer 2: `drive_manager` once as warm-up (discarded) and once for the record, on the paged attention. """
    if model.config._attn_implementation != paged:
        model.set_attn_implementation(paged)
    try:
        drive_manager(manager, model, prompt_ids, warmup_tokens)
        steps, outputs, tables = drive_manager(manager, model, prompt_ids, tokens, profile_at=profile_at)
    finally:
        if model.config._attn_implementation != original:
            model.set_attn_implementation(original)
    failed = [o.request_id for o in outputs if o.error is not None]
    if failed:
        raise RuntimeError(f"requests failed in the manager: {failed}: {[o.error for o in outputs if o.error][:1]}")
    decode = [s for s in steps if s["kind"] == "decode"]
    prefill = [s for s in steps if s["kind"] == "prefill"]

    def total(step):
        return step["prepare_ms"] + step["forward_ms"] + step["update_ms"]

    return {
        "batch": len(prompt_ids),
        "prompt_tokens": [len(p) for p in prompt_ids],
        "generated_tokens": sum(len(o.generated_tokens) for o in outputs),
        "steps": len(steps),
        "prefill_step": summarize([total(s) for s in prefill]),
        "decode_step": summarize([total(s) for s in decode]),
        "decode_prepare": summarize([s["prepare_ms"] for s in decode]),
        "decode_forward": summarize([s["forward_ms"] for s in decode]),
        "decode_update": summarize([s["update_ms"] for s in decode]),
        "profiled_at_step": profile_at if tables is not None else None,
        "ops": tables,
    }


def engine_phases(timings, by_processor, profiled_step):
    """
    The engine layer's per-phase summaries (no model needed; `api/tests/test_benchmarks.py`). The
    per-step series `forward`, `model` and `sample` clocked the step run under the operator profilers
    too, so that step is dropped from them; `logits` and the per-filter series did not record it.
    `prepare` (which also counts the calls that found nothing), `update` and `decode` are left as
    they are.
    """
    def without_profiled(series):
        if profiled_step is None or profiled_step >= len(series):
            return list(series)
        return [s for i, s in enumerate(series) if i != profiled_step]

    return {
        "step_forward": summarize(without_profiled(timings["forward"])),
        "step_model": summarize(without_profiled(timings["model"])),
        "step_logits": summarize(timings["logits"]),
        "step_logits_by_processor": {name: summarize(series) for name, series in by_processor.items()},
        "step_sample": summarize(without_profiled(timings["sample"])),
        "step_prepare": summarize(timings["prepare"]),
        "step_update": summarize(timings["update"]),
        "decode_call": summarize(timings["decode"]),
        "decode_total_ms": sum(timings["decode"]),
        "profiled_at_step": profiled_step,
    }


def measure_engine(concurrency, tokens, attn, dtype, device, warmup_tokens, profile_at=None, sampling=None):
    """
    Layer 3: the full `Engine` serving `concurrency` requests at once, as `batching_throughput.py`'s
    `batched` case (`sampling`: its sampling arguments, None for greedy), with the manager's three
    loop calls timed from inside its thread. `prepare` also
    counts the calls that found nothing to do (the loop polls between the last token and the stop),
    and `outside_loop_ms` includes the loop's idle wait of up to 0.1 s at the end.

    Inside `_generation_step` three more phases are clocked (#74): `model`, the model forward;
    `logits`, Gemstone's logits-processor list, and each filter of it by class name; `sample`, the
    manager's argmax and output copy. What is left of the step is the logits slicing and `.float()`.
    The logits processing of step `profile_at` runs under the operator profilers instead of the clock
    (`logits_ops`), and that step is dropped from the per-step series (`engine_phases`). Every
    `tokenizer.decode` of the request threads is clocked as `decode_call` (lock wait included); it
    runs off the loop thread, so its total is CPU time beside the forward, not time in the step.
    """
    from transformers.generation.continuous_batching.cb_logits_processors import (
        ContinuousBatchingLogitsProcessorList,
    )
    from transformers.generation.continuous_batching.continuous_api import ContinuousBatchProcessor
    from transformers.generation.continuous_batching.model_runner import ModelRunner

    from api.src.main.engine import Engine, _SAMPLERS

    timings = {"prepare": [], "forward": [], "model": [], "logits": [], "sample": [], "update": [], "decode": []}
    by_processor = {}
    state = {"step": 0, "tables": None, "profiling": False}
    originals = {
        "prepare": ContinuousBatchProcessor.prepare_next_batch,
        "forward": ContinuousBatchProcessor._generation_step,
        "update": ContinuousBatchProcessor.update_batch,
        "sample": ModelRunner._sample,
        "logits": ContinuousBatchingLogitsProcessorList.__call__,
    }
    filters = {cls.__name__: cls.__call__ for cls in _SAMPLERS}

    def timed(name, original, series):
        def call(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                if not state["profiling"]:
                    series.append((time.perf_counter() - start) * 1000)

        return call

    def logits_call(self, *args, **kwargs):
        step = state["step"]  # None during the warm-up, which is never profiled
        if step is not None:
            state["step"] = step + 1
        if step is None or step != profile_at:
            return timed("logits", originals["logits"], timings["logits"])(self, *args, **kwargs)
        holder = {}

        def processed():
            holder["scores"] = originals["logits"](self, *args, **kwargs)

        state["profiling"] = True
        try:
            state["tables"] = operator_tables(processed)
        finally:
            state["profiling"] = False
        if "scores" not in holder:  # neither profiler could run the call: run it unprofiled
            holder["scores"] = originals["logits"](self, *args, **kwargs)
        return holder["scores"]

    ContinuousBatchProcessor.prepare_next_batch = timed("prepare", originals["prepare"], timings["prepare"])
    ContinuousBatchProcessor._generation_step = timed("forward", originals["forward"], timings["forward"])
    ContinuousBatchProcessor.update_batch = timed("update", originals["update"], timings["update"])
    ModelRunner._sample = timed("sample", originals["sample"], timings["sample"])
    ContinuousBatchingLogitsProcessorList.__call__ = logits_call
    for cls in _SAMPLERS:
        series = by_processor[cls.__name__] = []
        cls.__call__ = timed(cls.__name__, filters[cls.__name__], series)
    engine = None
    try:
        engine = Engine(
            TEST_MODEL, chat_template=CHAT_TEMPLATE, local_files_only=True, batching=True,
            attn_implementation=attn, dtype=dtype, device=device,
        )
        if not engine.batching:
            raise RuntimeError(f"batching unavailable: {engine.batching_unavailable}")
        # The model forward and the tokenizer's decode are instance attributes, so only this engine
        # is clocked: `nn.Module.__call__` reads `self.forward`, `_Batcher.stream` reads `tokenizer.decode`.
        engine.model.forward = timed("model", engine.model.forward, timings["model"])
        engine.tokenizer.decode = timed("decode", engine.tokenizer.decode, timings["decode"])
        prompts = (PROMPTS * ((concurrency // len(PROMPTS)) + 1))[:concurrency]
        state["step"] = None  # the warm-up is never profiled
        run_engine(engine, prompts[:1], warmup_tokens, False, sampling)  # warm-up, discarded
        for series in list(timings.values()) + list(by_processor.values()):
            series.clear()
        state["step"] = 0
        generated, elapsed = run_engine(engine, prompts, tokens, True, sampling)
        engine.close()
    finally:
        ContinuousBatchProcessor.prepare_next_batch = originals["prepare"]
        ContinuousBatchProcessor._generation_step = originals["forward"]
        ContinuousBatchProcessor.update_batch = originals["update"]
        ModelRunner._sample = originals["sample"]
        ContinuousBatchingLogitsProcessorList.__call__ = originals["logits"]
        for cls in _SAMPLERS:
            cls.__call__ = filters[cls.__name__]
        if engine is not None:
            engine.model.__dict__.pop("forward", None)
            engine.tokenizer.__dict__.pop("decode", None)
    # `prepare_next_batch` runs once more than the steps (the call that finds nothing left): the
    # forward series is the step count.
    steps = len(timings["forward"])
    in_loop_ms = sum(timings["prepare"]) + sum(timings["forward"]) + sum(timings["update"])
    profiled = profile_at if state["tables"] is not None else None
    return {
        "concurrency": concurrency,
        "sampling": dict(temperature=0) if sampling is None else dict(sampling),
        "generated_tokens": generated,
        "elapsed_seconds": elapsed,
        "tokens_per_second": generated / elapsed if elapsed > 0 else 0.0,
        "steps": steps,
        "ms_per_step_end_to_end": elapsed * 1000 / steps if steps else None,
        "in_loop_ms": in_loop_ms,
        "outside_loop_ms": elapsed * 1000 - in_loop_ms,
        **engine_phases(timings, by_processor, profiled),
        "logits_ops": state["tables"],
    }


# ------------------------------------------------------------------------------------------------ #

def substrate():
    import importlib.metadata as meta

    for name in ("torchnative", "torch"):
        try:
            return f"{name} {meta.version(name)}"
        except Exception:
            continue
    return "unknown"


def show(label, summary):
    if summary["repetitions"]:
        print(f"{label:>34}: median {summary['median_ms']:9.2f} ms  min {summary['min_ms']:9.2f} ms  "
              f"(n={summary['repetitions']})")
    else:
        print(f"{label:>34}: no samples")


def show_ops(label, tables):
    if not tables:
        return
    for kind in ("torch_profiler", "dispatch_timer"):
        table = tables[kind]
        if not table["available"]:
            print(f"{label} {kind}: unavailable ({table['reason']})")
            continue
        print(f"{label} {kind}: top {len(table['ops'])} of {table['operators_seen']} operators by self time")
        for row in table["ops"]:
            print(f"    {row['self_ms']:9.3f} ms  {row['calls']:5d} x  {row['op']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tokens", type=int, default=64, help="tokens generated per request in every layer")
    parser.add_argument("--batches", default="1,4", help="batch sizes of the forward and manager layers")
    parser.add_argument("--concurrency", type=int, default=4, help="concurrent requests of the engine layer")
    parser.add_argument("--attn", default="sdpa_paged", choices=["sdpa_paged", "eager_paged"])
    parser.add_argument("--warmup-tokens", type=int, default=8, help="length of the discarded warm-up run")
    parser.add_argument("--profile-at", type=int, default=8, help="the decode step run under the operator profilers")
    parser.add_argument("--round", type=int, default=1, help="A/B round, recorded in the JSON (the workflow alternates substrates)")
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()
    batches = [int(b) for b in args.batches.split(",") if b.strip()]

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    import transformers

    device = TEST_DEVICE
    print(f"model={TEST_MODEL} tokens={args.tokens} batches={batches} concurrency={args.concurrency} "
          f"attn={args.attn} device={device} round={args.round} substrate={substrate()}")
    load_average = None
    try:
        load_average = os.getloadavg()
        print(f"load average: {load_average}")
    except OSError:
        pass

    tokenizer = AutoTokenizer.from_pretrained(TEST_MODEL, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(TEST_MODEL, local_files_only=True, dtype=torch.float32).to(device).eval()
    prompt_ids = encode_prompts(tokenizer, max(batches))

    # Layer 1: the raw forward
    forward = {"attn_implementation": model.config._attn_implementation, "by_batch": {}}
    for batch in batches:
        result = measure_forward(model, prompt_ids[:batch], args.tokens, args.warmup_tokens, args.profile_at)
        forward["by_batch"][str(batch)] = result
        print(f"forward batch {batch} (prompt {result['prompt_tokens']} tokens, {forward['attn_implementation']})")
        show("prefill", result["prefill"])
        show("decode step", result["decode_step"])
        show_ops(f"  forward batch {batch} decode step", result["ops"])

    # Layer 2: the manager, driven in this thread
    manager, paged, original = build_manager(
        model, tokenizer, args.attn, kv_cache_tokens=8192, page_size=64, max_batch_tokens=256, max_batch_requests=16,
    )
    managed = {"attn_implementation": paged, "by_batch": {}}
    for batch in batches:
        result = measure_manager(
            manager, model, paged, original, prompt_ids[:batch], args.tokens, args.warmup_tokens, args.profile_at,
        )
        managed["by_batch"][str(batch)] = result
        print(f"manager batch {batch} ({paged}; {result['generated_tokens']} tokens in {result['steps']} steps)")
        show("prefill step", result["prefill_step"])
        show("decode step (prepare+forward+update)", result["decode_step"])
        show("decode prepare_next_batch", result["decode_prepare"])
        show("decode _generation_step", result["decode_forward"])
        show("decode update_batch", result["decode_update"])
        show_ops(f"  manager batch {batch} decode step", result["ops"])
    manager.destroy()
    manager.batch_processor = None
    model.destroy_cached_continuous_batching_manager()
    del manager, model

    # Layer 3: the engine, greedy (as the throughput script) and with the API's default sampling
    layers = {}
    for label, sampling in (("engine", None), ("engine_sampled", SAMPLED)):
        engine = layers[label] = measure_engine(
            args.concurrency, args.tokens, args.attn, torch.float32, device, args.warmup_tokens, args.profile_at,
            sampling,
        )
        print(f"{label} c={engine['concurrency']} {engine['sampling']}: {engine['generated_tokens']} tokens in "
              f"{engine['elapsed_seconds']:.2f} s = {engine['tokens_per_second']:.1f} tokens/s, {engine['steps']} steps, "
              f"{engine['ms_per_step_end_to_end']:.2f} ms/step end to end, "
              f"{engine['in_loop_ms']:.0f} ms in the loop, {engine['outside_loop_ms']:.0f} ms outside it")
        show("step _generation_step", engine["step_forward"])
        show("  of which model forward", engine["step_model"])
        show("  of which logits processors", engine["step_logits"])
        for name, summary in engine["step_logits_by_processor"].items():
            show(f"    {name}", summary)
        show("  of which sampling", engine["step_sample"])
        show("step prepare_next_batch", engine["step_prepare"])
        show("step update_batch", engine["step_update"])
        show("tokenizer.decode per call", engine["decode_call"])
        print(f"{'tokenizer.decode total':>34}: {engine['decode_total_ms']:9.2f} ms off the loop thread")
        show_ops(f"  {label} step {engine['profiled_at_step']} logits processors", engine["logits_ops"])

    if args.json:
        payload = {
            "model": TEST_MODEL,
            "tokens": args.tokens,
            "batches": batches,
            "concurrency": args.concurrency,
            "attn": args.attn,
            "device": device,
            "dtype": "float32",
            "round": args.round,
            "python": sys.version,
            "substrate": substrate(),
            "transformers": transformers.__version__,
            "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
            "load_average": load_average,
            "forward": forward,
            "manager": managed,
            "engine": layers["engine"],
            "engine_sampled": layers["engine_sampled"],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)


if __name__ == "__main__":
    main()
