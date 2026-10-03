"""
Tool-calling robustness (SPEC S1.6, issue #113): a malformed tool call, a runaway calculator
expression and a slow tool must not break, hang or spin the chat. A fake engine stands in for the
model and every tool is local, so no weights and no network are used.
"""
import json
import time

import pytest

from api.src.main.models.base import BaseModel
from api.src.main.models.config import ChatHistory
from api.src.main.utils import FunctionCalling, FunctionCallResult, FunctionSchema
from api.src.main.utils.calculator import calculate


def staged(calling, implementations=None, timeout=None):
    result = FunctionCallResult()
    result.register_tools(
        [FunctionSchema(name=n, description="", parameters={}) for n in (implementations or {})],
        implementations or {},
    )
    result.stage(calling)
    history = []
    deadline = time.time() + 5
    while (final := result.finalize(history)) is False:
        assert time.time() < deadline, "tool never finished"
        time.sleep(0.01)
    return result, history, final


def tool_text(history):
    return " ".join(str(m["content"]) for m in history if m["role"] == "tool")


@pytest.mark.parametrize("calling", [
    "not json",
    "{broken",
    "[1, 2, 3]",
    '"just a string"',
    json.dumps(dict(arguments=dict(x=1))),
    json.dumps(dict(name="echo", arguments=[1, 2])),
])
def test_malformed_tool_call_yields_an_error_result(calling):
    _, history, final = staged(calling, dict(echo=lambda **kw: "ok"))
    assert "error" in tool_text(history).lower()
    assert "<tool_call>" in final  # the client still gets its frames


class ScriptedEngine:
    def __init__(self, first):
        self.first = first

    def __call__(self, messages, **kwargs):
        if messages[-1]["role"] == "tool":
            yield "Done: " + messages[-1]["content"]
        else:
            yield from self.first


def make_model(first, **tools):
    class FakeModel(BaseModel):
        model_id = "fake"
        supported_tools = FunctionCalling(
            schemas=[FunctionSchema(name=n, description="", parameters={}) for n in tools],
            implementations=tools,
        )

    return FakeModel(engine=ScriptedEngine(first))


def test_chat_survives_a_broken_tool_call():
    model = make_model(["<tool_call>", "{this is not json", "</tool_call>"])
    out = "".join(model.chat(ChatHistory(), "hi"))
    assert "Done:" in out
    assert "error" in out.lower()


def test_calculator_gives_up_on_a_huge_exponent_quickly():
    start = time.time()
    result = calculate("9**9**9**9")
    assert time.time() - start < 1
    assert result.startswith("Error")


@pytest.mark.parametrize("expression,expected", [
    ("2 + 3 * 4", "14"), ("(2 + 3) * 4", "20"), ("2**3", "8"), ("2**-1", "0.5"), ("10/3", "3.333333333"),
    ("-5 + 2", "-3"), ("abs(-5)", "5"), ("sqrt(16)", "4"), ("sin(pi/2)", "1"), ("log(e)", "1"),
    ("pow(2, 10)", "1024"), ("exp(0)", "1"), ("round(2.567, 1)", "2.6"), ("7//2", "3"), ("1/0", "Error: Division by zero"),
])
def test_calculator_keeps_supported_syntax(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize("expression", [
    "pow(9, 9**9)", "9**9**9", "2**100000000", "9**9999*9**9999*9**9999", "__import__('os')", "abs.__class__",
    "sqrt", "foo(1)", "(",
])
def test_calculator_rejects_the_rest(expression):
    start = time.time()
    assert calculate(expression).startswith("Error")
    assert time.time() - start < 1


def test_slow_tool_hits_the_deadline(monkeypatch):
    monkeypatch.setenv("GEMSTONE_TOOL_TIMEOUT", "0.2")
    release = __import__("threading").Event()
    try:
        start = time.time()
        _, history, _ = staged(json.dumps(dict(name="slow", arguments={})),
                               dict(slow=lambda: release.wait(10) and "late"))
        assert time.time() - start < 3
        assert "timed out" in tool_text(history)
    finally:
        release.set()


def test_chat_finishes_when_a_tool_times_out(monkeypatch):
    monkeypatch.setenv("GEMSTONE_TOOL_TIMEOUT", "0.2")
    release = __import__("threading").Event()
    try:
        model = make_model(["<tool_call>", json.dumps(dict(name="slow", arguments={})), "</tool_call>"],
                           slow=lambda: release.wait(10) and "late")
        out = "".join(model.chat(ChatHistory(), "hi"))
        assert "timed out" in out
    finally:
        release.set()


def test_waiting_for_a_slow_tool_does_not_spin(capsys):
    def slow():
        time.sleep(0.5)
        return "done"

    model = make_model(["<tool_call>", json.dumps(dict(name="slow", arguments={})), "</tool_call>"], slow=slow)
    cpu = time.thread_time()
    wall = time.time()
    out = "".join(model.chat(ChatHistory(), "hi"))
    cpu = time.thread_time() - cpu
    assert time.time() - wall >= 0.5
    assert "Done: done" in out
    assert cpu < 0.1, f"busy wait: {cpu:.2f}s CPU in a 0.5s wait"
