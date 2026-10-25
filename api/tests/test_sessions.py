"""
Session API error handling (SPEC S1.3; issue #97). A fake registry and store stand in for the
model, so nothing is loaded or downloaded.
"""
import re

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server
from api.src.main.registry import Registry
from api.tests.test_ollama_api import QWEN, SMOL, FakeEngine, FakeStore
from api.tests.test_residency import ws_chat


@pytest.fixture
def setup(monkeypatch):
    loads = []

    def loader(hf_id):
        loads.append(hf_id)
        return FakeEngine(hf_id, ["Hello", " there", "!"])

    store = FakeStore(SMOL)  # the catalogue model is not downloaded yet
    reg = Registry(loader=loader, store=store)
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as client:
        yield dict(client=client, loads=loads, store=store, registry=reg)
    reg.unload()


def assert_error(response, status):
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str) and response.json()["detail"]


def test_unknown_model_is_404_at_creation(setup):
    assert_error(setup["client"].post("/api/models/someone/not-pulled/sessions/"), 404)
    assert_error(setup["client"].post("/api/models/not-a-model/sessions/"), 404)
    assert setup["loads"] == [] and setup["store"].downloads == []


def test_model_in_the_store_but_not_the_catalogue_is_accepted(setup):
    r = setup["client"].post(f"/api/models/{SMOL}/sessions/")
    assert r.status_code == 200 and r.json()["model_id"] == SMOL
    assert setup["loads"] == []


@pytest.mark.parametrize("path", ["/api/sessions/", "/api/models/qwen3/sessions/", "/api/models/default/sessions/"])
def test_catalogue_model_not_downloaded_is_not_unknown(setup, path):
    r = setup["client"].post(path)
    assert r.status_code == 200
    assert set(r.json()) == {"model_id", "session_id", "message"}
    assert setup["loads"] == [] and setup["store"].downloads == []  # creating loads and fetches nothing


def test_session_ids_name_the_model_and_are_unique_within_one_second(setup):
    ids = [setup["client"].post("/api/models/qwen3/sessions/").json()["session_id"] for _ in range(20)]
    assert all(re.fullmatch(r"qwen3_\d{14}_[0-9a-f]{8}", session_id) for session_id in ids), ids
    assert len(set(ids)) == len(ids)  # 20 creations fall within a second or two: the time alone repeats


@pytest.mark.parametrize("method", ["delete", "post"])
def test_deleting_an_unknown_session_is_404(setup, method):
    assert_error(getattr(setup["client"], method)("/api/sessions/nope_20260101000000_deadbeef"), 404)


def test_deleting_twice_is_404_the_second_time(setup):
    c = setup["client"]
    sid = c.post("/api/sessions/").json()["session_id"]
    assert c.delete(f"/api/sessions/{sid}").json() == {"message": "Session deleted successfully"}
    assert_error(c.delete(f"/api/sessions/{sid}"), 404)


def test_create_chat_delete_flow_still_works(setup):
    c = setup["client"]
    sid = c.post("/api/models/qwen3/sessions/").json()["session_id"]
    assert ws_chat(c, sid) == "Hello there!"
    assert setup["loads"] == [QWEN]
    assert c.delete(f"/api/sessions/{sid}").status_code == 200


@pytest.mark.parametrize("method", ["delete", "post"])
def test_session_of_a_hugging_face_id_can_be_deleted(setup, method):
    # The session id embeds the model id, so it holds a "/" (issue #117)
    created = setup["client"].post(f"/api/models/{SMOL}/sessions/").json()
    assert "/" in created["session_id"]
    r = getattr(setup["client"], method)(f"/api/sessions/{created['session_id']}")
    assert r.status_code == 200
    assert_error(getattr(setup["client"], method)(f"/api/sessions/{created['session_id']}"), 404)


# Concurrency (#180, docs/serving/free-threading.md FT1). The races are forced with events, so they
# fail without the session lock on the GIL build too, not only on free-threaded 3.15t.

import threading

from api.src.main import settings
from api.src.main.settings import Session


@pytest.fixture
def sessions(monkeypatch):
    table = {}
    monkeypatch.setattr(Session, "_Session__sessions", table)
    monkeypatch.setenv("GEMSTONE_SESSION_TTL", "3600")
    return table


def run_threads(*targets):
    errors = []

    def wrap(target):
        def run():
            try:
                target()
            except BaseException as e:  # reported by the test, not lost in the thread
                errors.append(e)
        return run

    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads), "a session call deadlocked"
    return errors


def test_sweep_does_not_fail_while_another_thread_creates_a_session(sessions):
    sweeping, created = threading.Event(), threading.Event()

    class Slow:
        """ An entry whose idle time is read slowly: the first read lets the other thread in. """
        tool_call_caches = {}

        @property
        def last_used(self):
            if not sweeping.is_set():
                sweeping.set()
                created.wait(1)  # with the lock the creator waits for this sweep instead
            return settings.clock()

    sessions["slow"] = Slow()

    def create():
        sweeping.wait(5)
        Session("model")
        created.set()

    errors = run_threads(Session.sweep, create)
    assert errors == []
    assert len(sessions) == 2


def test_two_concurrent_closes_of_one_session_close_it_once(sessions):
    session = Session("model")
    both_in = threading.Barrier(2)

    class Caches(dict):
        def clear(self):
            try:
                both_in.wait(1)  # without the lock both closers get here before either pops
            except threading.BrokenBarrierError:
                pass
            super().clear()

    session.tool_call_caches = Caches()
    errors = run_threads(lambda: Session.close(session.session_id), lambda: Session.close(session.session_id))
    assert sessions == {}
    assert [type(e) for e in errors] == [ValueError]  # the 404 of an unknown session, not a KeyError


# Per-session KV cache lifecycle (#211, SPEC S1.3, S1.4, S1.14): freed on session close/expiry,
# model unload/eviction, and when total session KV memory exceeds GEMSTONE_MAX_SESSION_KV_MEMORY.

class _FakeLayer:
    def __init__(self, nbytes: int):
        import torch
        self.keys = torch.zeros(nbytes // 2, dtype=torch.uint8)
        self.values = torch.zeros(nbytes - nbytes // 2, dtype=torch.uint8)


class _FakeDynamicCache:
    def __init__(self, nbytes: int, seq_len: int = 8):
        self.layers = [_FakeLayer(nbytes)]
        self._seq_len = seq_len

    def get_seq_length(self):
        return self._seq_len

    def crop(self, tokens_to_remove: int):
        assert tokens_to_remove <= 0
        self._seq_len += tokens_to_remove


def test_session_kv_cache_freed_on_close_expiry_and_model_unload(sessions, monkeypatch):
    s1 = Session("qwen3")
    s1.kv_cache.store("Qwen/Qwen3-0.6B", [1, 2, 3, 4], _FakeDynamicCache(1024, seq_len=4))
    assert s1.kv_cache.memory_bytes == 1024

    Session.close(s1.session_id)
    assert s1.kv_cache.past_key_values is None and s1.kv_cache.memory_bytes == 0

    s2 = Session("qwen3")
    s2.kv_cache.store("Qwen/Qwen3-0.6B", [1, 2, 3, 4], _FakeDynamicCache(1024, seq_len=4))
    Session.clear_model_caches("Qwen/Qwen3-0.6B")
    assert s2.kv_cache.past_key_values is None and s2.kv_cache.memory_bytes == 0


def test_session_kv_cache_evicts_oldest_session_over_memory_budget(sessions, monkeypatch):
    monkeypatch.setenv("GEMSTONE_MAX_SESSION_KV_MEMORY", "2000")
    now = [100.0]
    monkeypatch.setattr(settings, "clock", lambda: now[0])

    s1 = Session("qwen3")
    s1.kv_cache.store("Qwen/Qwen3-0.6B", [1, 2, 3], _FakeDynamicCache(1200, seq_len=3))
    now[0] += 10.0
    s2 = Session("qwen3")
    s2.kv_cache.store("Qwen/Qwen3-0.6B", [4, 5, 6], _FakeDynamicCache(1200, seq_len=3))
    Session.enforce_kv_budget()

    assert s1.kv_cache.past_key_values is None  # older session's KV was evicted
    assert s2.kv_cache.past_key_values is not None and s2.kv_cache.memory_bytes == 1200
