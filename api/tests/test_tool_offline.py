"""
Tools without a connection (SPEC S3.2, #190): a tool that needs the internet and cannot connect gives
the model an "unavailable offline" result instead of the connection error, any other failure stays the
tool's own error, and local tools are untouched. Every request is refused in-process by replacing
`requests.Session.request` (which `requests.get` uses too), so nothing reaches the network.
"""
import json
import time

import pytest
import requests

from api.src.main.utils import FunctionCalling, FunctionCallResult, FunctionSchema

# What urllib3 and requests say when there is no network: DNS fails, or nothing answers.
NO_NETWORK = [
    requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='api.open-meteo.com', port=443): Max retries exceeded with url: /v1/forecast "
        "(Caused by NameResolutionError(\"Failed to resolve 'api.open-meteo.com' "
        "([Errno 8] nodename nor servname provided, or not known)\"))"
    ),
    requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='www.bing.com', port=443): Max retries exceeded with url: /search "
        "(Caused by NewConnectionError('Failed to establish a new connection: [Errno 51] Network is unreachable'))"
    ),
]

NETWORK_CALLS = {
    "get_weather": dict(location="Seoul"),
    "get_weather_forecast": dict(location="Seoul", days=3),
    "get_calendar_events": dict(date="2026-12-25", country="KR"),
    "get_upcoming_holidays": dict(country="KR", days=30),
    "get_exchange_rate": dict(from_currency="USD", to_currency="KRW", amount=10),
    "search_web": dict(query="gemstone"),
    "search_website": dict(query="gemstone", site_url="example.com"),
    "fetch_webpage": dict(url="https://example.com/"),
}


def run_tool(name, arguments):
    implementations = FunctionCalling.DEFAULT.implementations
    result = FunctionCallResult()
    result.register_tools([FunctionSchema(name=name, description="", parameters={})], implementations)
    result.stage(json.dumps(dict(name=name, arguments=arguments)))
    history = []
    deadline = time.time() + 10
    while result.finalize(history) is False:
        assert time.time() < deadline, "tool never finished"
        time.sleep(0.01)
    return " ".join(str(m["content"]) for m in history if m["role"] == "tool")


@pytest.fixture
def refuse(monkeypatch):
    def install(error):
        def request(self, *args, **kwargs):
            raise error
        monkeypatch.setattr(requests.Session, "request", request)
    return install


@pytest.mark.parametrize("error", NO_NETWORK, ids=["dns", "unreachable"])
@pytest.mark.parametrize("name", list(NETWORK_CALLS))
def test_a_network_tool_without_a_connection_is_unavailable_offline(refuse, name, error):
    refuse(error)

    text = run_tool(name, NETWORK_CALLS[name])

    assert text.startswith("Unavailable offline:"), text
    assert name in text
    assert "Max retries" not in text, "the raw connection error is not passed on"


def test_another_failure_stays_the_tools_own_error(refuse):
    refuse(requests.exceptions.HTTPError("503 Server Error: Service Unavailable for url: https://example.com/"))

    text = run_tool("fetch_webpage", dict(url="https://example.com/"))

    assert "Unavailable offline" not in text
    assert "503" in text


def test_a_tools_own_validation_error_is_not_offline(refuse):
    refuse(NO_NETWORK[0])  # even with no network, a request that was never sent is not "offline"

    text = run_tool("get_exchange_rate", dict(from_currency="USD", to_currency="KRW", amount=0))

    assert "Unavailable offline" not in text
    assert "greater than 0" in text


def test_a_local_tool_is_untouched(refuse):
    refuse(NO_NETWORK[0])

    assert run_tool("calculate", dict(expression="2 + 3")).strip().endswith("5")
