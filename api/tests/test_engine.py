"""
The serving engine (SPEC S1.11): one transformers-based engine, streaming, seeded sampling,
one generation at a time per model (#36), and cancellation (#35).
"""
import threading
import time

from api.tests.conftest import CHAT_TEMPLATE


MESSAGES = [{"role": "user", "content": "The capital of France is"}]
GREEDY = dict(temperature=0, max_new_tokens=16)


def generate(engine, **kwargs):
    return "".join(engine(MESSAGES, **{**GREEDY, **kwargs}))


def test_streams_text_in_several_chunks(engine):
    chunks = list(engine(MESSAGES, **GREEDY))

    assert len(chunks) > 1
    assert "".join(chunks).strip()


def test_greedy_output_equals_transformers_generate(engine):
    tokenizer = engine.tokenizer
    prompt = tokenizer.apply_chat_template(
        MESSAGES, chat_template=CHAT_TEMPLATE, add_generation_prompt=True, tokenize=False
    )
    inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
    output = engine.model.generate(**inputs, max_new_tokens=16, do_sample=False)
    expected = tokenizer.decode(output[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)

    assert generate(engine) == expected


def test_same_seed_gives_same_sample(engine):
    first = generate(engine, temperature=1.0, seed=7)
    second = generate(engine, temperature=1.0, seed=7)

    assert first == second


def test_concurrent_requests_run_one_at_a_time_and_match_sequential(engine, monkeypatch):
    expected = generate(engine)

    active = 0
    peak = 0
    counter = threading.Lock()
    original = engine.model.generate

    def tracked_generate(*args, **kwargs):
        nonlocal active, peak
        with counter:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.2)  # widen the window in which an overlap would show
            return original(*args, **kwargs)
        finally:
            with counter:
                active -= 1

    monkeypatch.setattr(engine.model, "generate", tracked_generate)

    results = [None, None]
    errors = []

    def run(i):
        try:
            results[i] = generate(engine)
        except Exception as e:  # surfaced below
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not errors
    assert results == [expected, expected]
    assert peak == 1


def test_cancel_stops_generation_early(engine):
    cancel = threading.Event()
    chunks = []
    for chunk in engine(MESSAGES, temperature=0, max_new_tokens=200, min_new_tokens=200, cancel=cancel):
        chunks.append(chunk)
        cancel.set()

    generated = engine.tokenizer("".join(chunks), add_special_tokens=False)["input_ids"]
    assert len(generated) < 100


def test_abandoned_stream_releases_the_model(engine):
    stream = engine(MESSAGES, temperature=0, max_new_tokens=200, min_new_tokens=200)
    next(stream)
    stream.close()

    finished = threading.Event()
    threading.Thread(target=lambda: (generate(engine), finished.set()), daemon=True).start()

    assert finished.wait(timeout=60)
