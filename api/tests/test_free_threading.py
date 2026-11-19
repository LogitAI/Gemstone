"""
Free-threading concurrency stress tests for issue #177 (FT1, FT2, FT3) and the GIL guard.
"""
import sys
import sysconfig
import threading
import time
import itertools
import queue
import pytest

from api.src.main.settings import Session
from api.src.main import settings
from api.src.main.engine import _next_request_id, _REQUEST_IDS_LOCK
from api.src.main.leases import once
from api.src.main import ollama_api


def test_gil_stays_disabled_on_free_threaded_build():
    gil_disabled = sysconfig.get_config_var("Py_GIL_DISABLED")
    if gil_disabled != 1:
        pytest.skip("GIL build: free-threaded GIL guard applies only to 3.15t")
    import api.src.main.server
    import api.src.main.engine
    assert not sys._is_gil_enabled()


def test_ft1_session_concurrent_create_sweep_and_close(monkeypatch):
    sessions = {}
    monkeypatch.setattr(Session, "_Session__sessions", sessions)
    monkeypatch.setenv("GEMSTONE_SESSION_TTL", "0.01")

    clock_time = [100.0]
    monkeypatch.setattr(settings, "clock", lambda: clock_time[0])

    # Pre-populate so sweep() iterates over a non-trivial dict while other threads mutate it.
    for i in range(150):
        Session(model_id="init-model", session_id=f"init_{i}")

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    errors = []
    barrier = threading.Barrier(12)

    def worker(worker_id):
        try:
            barrier.wait(timeout=5)
            for i in range(300):
                if i % 4 == 0:
                    clock_time[0] += 0.05
                    Session.sweep()
                elif i % 4 == 1:
                    s = Session(f"model-{worker_id}")
                    if i % 8 == 1:
                        try:
                            Session.close(s.session_id)
                        except ValueError:
                            pass
                elif i % 4 == 2:
                    Session(model_id="shared-model", session_id=f"shared_{worker_id}_{i}")
                else:
                    try:
                        Session.close(f"shared_{(worker_id + 1) % 12}_{i - 1}")
                    except ValueError:
                        pass
        except BaseException as e:
            errors.append(e)

    try:
        threads = [threading.Thread(target=worker, args=(w,)) for w in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    finally:
        sys.setswitchinterval(old_interval)
    assert not any(t.is_alive() for t in threads), "session stress threads deadlocked"
    assert errors == [], f"session stress encountered errors: {errors}"
    Session._Session__sessions.clear()


def test_ft2_request_ids_unique_under_concurrent_batch_enqueue():
    ids_collected = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def worker():
        try:
            barrier.wait(timeout=5)
            for _ in range(500):
                rid = _next_request_id()
                with lock:
                    ids_collected.append(rid)
        except BaseException as e:
            with lock:
                ids_collected.append(e)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    
    assert not any(isinstance(x, Exception) for x in ids_collected)
    assert len(ids_collected) == 16 * 500
    assert len(set(ids_collected)) == len(ids_collected)


def test_ft3_ollama_stream_concurrent_next_and_close_releases_lease_once():
    class MockLease:
        def __init__(self):
            self.release_count = 0
            self.load_duration = 0.0
            self._lock = threading.Lock()

        def release(self):
            with self._lock:
                self.release_count += 1

    errors = []

    for _ in range(100):
        lease = MockLease()
        cancel = threading.Event()
        release_fn = once(lease.release)

        class SlowIterator:
            def __init__(self):
                self._idx = 0
                self._closed = False
                self._lock = threading.Lock()

            def __iter__(self):
                return self

            def __next__(self):
                time.sleep(0.001)
                with self._lock:
                    if self._closed or self._idx > 10:
                        raise StopIteration
                    self._idx += 1
                    return f"token-{self._idx}"

            def close(self):
                with self._lock:
                    if self._closed:
                        raise ValueError("already closed")
                    self._closed = True

        iterator = SlowIterator()
        closer = ollama_api._closer(iterator, cancel=cancel, release=release_fn)

        def step_next():
            try:
                for _ in iterator:
                    if cancel.is_set():
                        break
            except Exception:
                pass

        def step_close():
            time.sleep(0.002)
            try:
                closer()
            except Exception:
                pass

        t1 = threading.Thread(target=step_next)
        t2 = threading.Thread(target=step_close)

        t1.start()
        t2.start()
        t1.join(2)
        t2.join(2)

        # ensure closer is called at least once
        closer()

        if lease.release_count != 1:
            errors.append(f"Lease released {lease.release_count} times instead of 1")

    assert errors == [], f"FT3 lease release failures: {errors}"


def test_ft4_locked_tokenizer_isolates_concurrent_truncation_and_max_length():
    from api.src.main.engine import _LockedTokenizer

    class FakeTokenizerFast:
        def __init__(self):
            self._truncation_max_length = None

        def __call__(self, text, truncation=False, max_length=None, **kwargs):
            if truncation and max_length is not None:
                self._truncation_max_length = max_length
                time.sleep(0.0001)  # yield thread to allow race if unlocked
                actual_len = self._truncation_max_length
                # slice tokens based on actual_len
                tokens = list(range(20))
                return {"input_ids": tokens[:actual_len]}
            return {"input_ids": list(range(20))}

    raw = FakeTokenizerFast()
    lock = threading.Lock()
    locked = _LockedTokenizer(raw, lock)

    errors = []
    barrier = threading.Barrier(12)
    lengths = [4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26]

    def worker(target_len):
        try:
            barrier.wait(timeout=5)
            for _ in range(100):
                res = locked("test input", truncation=True, max_length=target_len)
                ids = res["input_ids"]
                if len(ids) != target_len:
                    errors.append(f"Expected length {target_len}, got {len(ids)}")
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(l,)) for l in lengths]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert not any(t.is_alive() for t in threads), "tokenizer concurrency test threads deadlocked"
    assert errors == [], f"FT4 tokenizer isolation errors: {errors}"

