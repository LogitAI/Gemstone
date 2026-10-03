"""
Operations (issue #120): logging instead of prints, session idle expiry, shutdown, the documented
quick start and web_search's imports. Fake engines and a fake clock stand in for the model.
"""
import importlib
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.src.main import registry as registry_module
from api.src.main import server, settings
from api.src.main.registry import Registry
from api.tests.test_ollama_api import SMOL, FakeEngine, FakeStore
from api.tests.test_residency import new_session, ws_chat

ROOT = Path(__file__).resolve().parents[2]
START = "uv run --extra torch python -m api run server"
SECRET_PROMPT = "tell me the secret-word-xyzzy"


@pytest.fixture
def client(monkeypatch):
    reg = Registry(loader=lambda hf_id: FakeEngine(hf_id, ["Hello", " there", "!"]), store=FakeStore(SMOL))
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as c:
        yield c
    reg.unload()


# -- logging ---------------------------------------------------------------------------------------

def test_no_prompt_or_answer_on_stdout_or_info(client, capsys, caplog):
    caplog.set_level(logging.INFO, logger="gemstone")
    sid = new_session(client)
    assert ws_chat(client, sid, SECRET_PROMPT) == "Hello there!"
    out = capsys.readouterr()
    text = out.out + out.err + "\n".join(r.getMessage() for r in caplog.records)
    assert "secret-word-xyzzy" not in text
    assert "Hello there" not in text
    assert "PROMPT:" not in text
    assert any(r.name.startswith("gemstone") for r in caplog.records)


def test_prompt_and_answer_at_debug(client, caplog, capsys):
    caplog.set_level(logging.DEBUG, logger="gemstone")
    sid = new_session(client)
    ws_chat(client, sid, SECRET_PROMPT)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("gemstone"))
    assert "secret-word-xyzzy" in text
    assert "Hello" in text
    assert "secret-word-xyzzy" not in capsys.readouterr().out


def test_importing_qwen3_prints_nothing(capsys):
    from api.src.main.models.qwen3 import model
    importlib.reload(model)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value,level", [("DEBUG", logging.DEBUG), ("warning", logging.WARNING),
                                         (None, logging.INFO), ("bogus", logging.INFO)])
def test_log_level_from_env(monkeypatch, value, level):
    if value is None:
        monkeypatch.delenv("GEMSTONE_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("GEMSTONE_LOG_LEVEL", value)
    old = logging.getLogger("gemstone").level
    try:
        settings.configure_logging()
        assert logging.getLogger("gemstone").level == level
    finally:
        logging.getLogger("gemstone").setLevel(old)


# -- session idle expiry ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(settings, "clock", c)
    monkeypatch.delenv("GEMSTONE_SESSION_TTL", raising=False)
    yield c
    for sid in list(settings.Session._Session__sessions):
        settings.Session.close(sid)


def test_session_expires_after_idle_ttl(clock, monkeypatch):
    monkeypatch.setenv("GEMSTONE_SESSION_TTL", "60")
    s = settings.Session(model_id="m")
    s.tool_call_caches["a"] = "result"
    clock.now += 59
    assert settings.Session(session_id=s.session_id) is s  # a lookup counts as use
    clock.now += 59
    assert settings.Session(session_id=s.session_id) is s
    clock.now += 61
    with pytest.raises(ValueError):
        settings.Session(session_id=s.session_id)
    assert s.tool_call_caches == {}
    with pytest.raises(ValueError):
        settings.Session.close(s.session_id)


def test_session_sweep_drops_idle_sessions_on_create(clock, monkeypatch):
    monkeypatch.setenv("GEMSTONE_SESSION_TTL", "10")
    old = settings.Session(model_id="m")
    clock.now += 11
    new = settings.Session(model_id="m")
    assert old.session_id not in settings.Session._Session__sessions
    assert new.session_id in settings.Session._Session__sessions


def test_session_ttl_zero_never_expires(clock, monkeypatch):
    monkeypatch.setenv("GEMSTONE_SESSION_TTL", "0")
    s = settings.Session(model_id="m")
    clock.now += 10 ** 9
    assert settings.Session(session_id=s.session_id) is s


def test_session_default_ttl_is_a_day(clock):
    s = settings.Session(model_id="m")
    clock.now += 23 * 3600
    assert settings.Session(session_id=s.session_id) is s
    clock.now += 25 * 3600
    with pytest.raises(ValueError):
        settings.Session(session_id=s.session_id)


# -- shutdown --------------------------------------------------------------------------------------

def test_shutdown_closes_loaded_engines(monkeypatch):
    engines = []

    def loader(hf_id):
        engines.append(FakeEngine(hf_id, ["x"]))
        return engines[-1]

    reg = Registry(loader=loader, store=FakeStore(SMOL))
    monkeypatch.setattr(registry_module, "registry", reg)
    with TestClient(server.app) as c:
        ws_chat(c, new_session(c), "hi")
        assert engines and not engines[0].closed_engine
    assert engines[0].closed_engine
    assert reg.loaded() == []


# -- web_search and dependencies -------------------------------------------------------------------

def test_web_search_loads_dotenv_once():
    src = (ROOT / "api/src/main/utils/web_search.py").read_text()
    assert src.count("load_dotenv()") == 1


def test_requests_is_declared():
    assert re.search(r'^\s*"requests[>=<~ "]', (ROOT / "pyproject.toml").read_text(), re.M)


# -- the documented quick start --------------------------------------------------------------------

@pytest.mark.parametrize("path", ["README.md", "docs/locale/README_ko.md", "docs/guide/getting-started.html"])
def test_quick_start_works_without_an_activated_venv(path):
    text = (ROOT / path).read_text()
    assert START in text
    for line in text.splitlines():
        if "python -m api run server" in line and "GEMSTONE" not in line:
            assert "uv run" in line, f"{path}: bare python in: {line}"


def test_ci_runs_the_documented_command():
    wf = (ROOT / ".github/workflows/test.yml").read_text()
    assert START in wf and "/api/version" in wf
