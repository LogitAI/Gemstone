"""
Continuous batching (SPEC S1.12) on a paged KV cache (S1.13).

Concurrent requests to one engine run in one batch instead of waiting for each other, and batching
does not change results: greedy output equals sequential generation, and a seeded sample is the
same whether or not the request shared its batch. Cancelling or closing one stream removes only
that request.
"""
import threading
import time

PROMPTS = [
    [{"role": "user", "content": "The capital of France is"}],
    [{"role": "user", "content": "Once upon a time, there was"}],
    [{"role": "user", "content": "The best way to learn Python is"}],
    [{"role": "user", "content": "My favourite fruit is"}],
]
GREEDY = dict(temperature=0, max_new_tokens=16)
SAMPLED = dict(temperature=1.0, top_p=0.9, top_k=50, min_p=0.0, max_new_tokens=16)


def generate(engine, messages, **kwargs):
    return "".join(engine(messages, **{**GREEDY, **kwargs}))


def run_concurrently(engine, calls, timeout=300):
    """
    Run `engine(messages, **kwargs)` for every `(messages, kwargs)` in `calls`, each on its own
    thread, all released at once. Returns, per call, the chunks and the time each one arrived.
    """
    barrier = threading.Barrier(len(calls))
    chunks = [[] for _ in calls]
    times = [[] for _ in calls]
    errors = []

    def run(i, messages, kwargs):
        try:
            barrier.wait()
            for chunk in engine(messages, **{**GREEDY, **kwargs}):
                chunks[i].append(chunk)
                times[i].append(time.monotonic())
        except Exception as e:  # surfaced below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i, m, kw), daemon=True) for i, (m, kw) in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    assert not errors, errors
    assert not any(t.is_alive() for t in threads), "a concurrent request did not finish"
    return chunks, times


def test_engine_batches_by_default(engine):
    assert engine.batching, engine.batching_unavailable


def test_two_concurrent_greedy_requests_equal_sequential(engine):
    expected = [generate(engine, messages) for messages in PROMPTS[:2]]

    chunks, _ = run_concurrently(engine, [(messages, {}) for messages in PROMPTS[:2]])

    assert ["".join(c) for c in chunks] == expected


def test_four_concurrent_greedy_requests_equal_sequential(engine):
    expected = [generate(engine, messages) for messages in PROMPTS]

    chunks, _ = run_concurrently(engine, [(messages, {}) for messages in PROMPTS])

    assert ["".join(c) for c in chunks] == expected


def test_concurrent_requests_overlap_in_time(engine):
    # Serialised requests would finish one before the other produces anything. Batched requests
    # have all produced output before either of them finishes.
    calls = [(messages, dict(max_new_tokens=24)) for messages in PROMPTS[:2]]

    _, times = run_concurrently(engine, calls)

    assert all(times), "every request must produce output"
    first_chunks = max(t[0] for t in times)
    first_finish = min(t[-1] for t in times)
    assert first_chunks < first_finish


def test_same_seed_sample_is_identical_batched_or_alone(engine):
    alone = generate(engine, PROMPTS[0], seed=7, **SAMPLED)
    other_alone = generate(engine, PROMPTS[1], seed=11, **SAMPLED)

    chunks, _ = run_concurrently(engine, [
        (PROMPTS[0], dict(seed=7, **SAMPLED)),
        (PROMPTS[1], dict(seed=11, **SAMPLED)),
        (PROMPTS[2], {}),  # a greedy neighbour in the same batch
    ])

    assert "".join(chunks[0]) == alone
    assert "".join(chunks[1]) == other_alone
    assert "".join(chunks[2]) == generate(engine, PROMPTS[2])


def test_cancel_and_close_remove_only_their_own_request(engine):
    long = dict(max_new_tokens=64)
    full = list(engine(PROMPTS[0], **{**GREEDY, **long}))
    assert len(full) >= 8, "precondition: the uncancelled reply streams many chunks"
    expected = generate(engine, PROMPTS[1], max_new_tokens=32)

    cancel = threading.Event()
    cancelled, closed, kept = [], [], []
    errors = []
    barrier = threading.Barrier(3)

    def cancelled_request():
        try:
            barrier.wait()
            for chunk in engine(PROMPTS[0], cancel=cancel, **{**GREEDY, **long}):
                cancelled.append(chunk)
                if len(cancelled) == 2:
                    cancel.set()
        except Exception as e:
            errors.append(e)

    def closed_request():
        try:
            barrier.wait()
            stream = engine(PROMPTS[0], **{**GREEDY, **long})
            for chunk in stream:
                closed.append(chunk)
                if len(closed) == 2:
                    break
            stream.close()
        except Exception as e:
            errors.append(e)

    def kept_request():
        try:
            barrier.wait()
            kept.extend(engine(PROMPTS[1], **{**GREEDY, "max_new_tokens": 32}))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=f, daemon=True) for f in (cancelled_request, closed_request, kept_request)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)

    assert not errors, errors
    assert not any(t.is_alive() for t in threads)
    assert len(cancelled) < len(full)
    assert len(closed) == 2
    assert "".join(kept) == expected
    # The engine still serves requests afterwards.
    assert generate(engine, PROMPTS[1], max_new_tokens=32) == expected
