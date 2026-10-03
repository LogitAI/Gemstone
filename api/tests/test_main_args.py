"""
Server bind address, reload flag and the open-bind warning (SPEC S1.1; issue #116). No model.
"""
import logging

import pytest

from api.src.main import security
from api.src.main.security import resolve_server_config as resolve


def test_defaults_are_loopback_and_no_reload():
    c = resolve(["run", "server"], {})
    assert (c.host, c.port, c.reload) == ("127.0.0.1", 23100, False)


def test_env_host_with_port():
    c = resolve(["run", "server"], {"GEMSTONE_HOST": "0.0.0.0:11434"})
    assert (c.host, c.port) == ("0.0.0.0", 11434)


def test_env_host_without_port_keeps_default_port():
    c = resolve(["run", "server"], {"GEMSTONE_HOST": "0.0.0.0"})
    assert (c.host, c.port) == ("0.0.0.0", 23100)


def test_env_ipv6_forms():
    assert (resolve(["run", "server"], {"GEMSTONE_HOST": "[::1]:5"}).host,
            resolve(["run", "server"], {"GEMSTONE_HOST": "[::1]:5"}).port) == ("::1", 5)
    assert resolve(["run", "server"], {"GEMSTONE_HOST": "[::]"}).host == "::"
    assert resolve(["run", "server"], {"GEMSTONE_HOST": "::1"}).host == "::1"


def test_env_scheme_is_ignored_like_ollama_host():
    c = resolve(["run", "server"], {"GEMSTONE_HOST": "http://10.0.0.2:8080"})
    assert (c.host, c.port) == ("10.0.0.2", 8080)


def test_arguments_win_over_env():
    env = {"GEMSTONE_HOST": "0.0.0.0:11434"}
    c = resolve(["run", "server", "192.168.0.5", "9000"], env)
    assert (c.host, c.port) == ("192.168.0.5", 9000)


def test_host_argument_alone_keeps_env_port():
    c = resolve(["run", "server", "localhost"], {"GEMSTONE_HOST": "0.0.0.0:11434"})
    assert (c.host, c.port) == ("localhost", 11434)


def test_reload_only_with_flag_or_dev_env():
    assert resolve(["run", "server", "--reload"], {}).reload is True
    assert resolve(["run", "server"], {"GEMSTONE_DEV": "1"}).reload is True
    assert resolve(["run", "server"], {"GEMSTONE_DEV": "0"}).reload is False
    assert resolve(["run", "server"], {"GEMSTONE_DEV": ""}).reload is False


def test_flag_does_not_shift_positional_arguments():
    c = resolve(["run", "server", "--reload", "1.2.3.4", "77"], {})
    assert (c.host, c.port, c.reload) == ("1.2.3.4", 77, True)
    c = resolve(["run", "server", "1.2.3.4", "77", "--reload"], {})
    assert (c.host, c.port, c.reload) == ("1.2.3.4", 77, True)


def test_main_passes_the_resolved_values_to_uvicorn(monkeypatch):
    import api.__main__ as entry
    seen = {}
    monkeypatch.setattr(entry, "run_uvicorn", lambda target, **kw: seen.update(target=target, **kw))
    monkeypatch.delenv("GEMSTONE_API_KEY", raising=False)
    monkeypatch.setenv("GEMSTONE_HOST", "127.0.0.1:5555")
    entry.main(["run", "server"])
    assert (seen["host"], seen["port"], seen["reload"]) == ("127.0.0.1", 5555, False)


@pytest.mark.parametrize("host,loopback", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("127.5.5.5", True),
    ("0.0.0.0", False), ("::", False), ("192.168.0.2", False), ("example.com", False),
])
def test_is_loopback(host, loopback):
    assert security.is_loopback_host(host) is loopback


def test_open_bind_without_key_warns_once(caplog):
    security._reset_warning_for_tests()
    with caplog.at_level(logging.WARNING, logger="gemstone.security"):
        security.warn_if_open("0.0.0.0", {})
        security.warn_if_open("0.0.0.0", {})
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert "GEMSTONE_API_KEY" in caplog.text


def test_no_warning_for_loopback_or_with_key(caplog):
    security._reset_warning_for_tests()
    with caplog.at_level(logging.WARNING, logger="gemstone.security"):
        security.warn_if_open("127.0.0.1", {})
        security.warn_if_open("0.0.0.0", {"GEMSTONE_API_KEY": "k"})
    assert not caplog.records
