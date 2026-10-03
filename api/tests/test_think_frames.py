"""
Reasoning and tool tags reach the app as frames of their own (SPEC S1.4, #112): the app matches
`<think>` and `</think>` exactly, and the one-at-a-time path's streamer glues them to whitespace
(`'<think>\n'`, `'</think>\n\n'`).
"""
from api.src.main.models.base import BaseModel
from api.src.main.models.config import ChatHistory
from api.src.main.utils import FunctionCalling


class ScriptedEngine:
    def __init__(self, chunks):
        self.chunks = chunks

    def __call__(self, messages, **kwargs):
        yield from self.chunks


class Plain(BaseModel):
    model_id = "test"
    supported_tools = FunctionCalling.DISABLED


def frames(chunks):
    model = Plain(engine=ScriptedEngine(chunks))
    return [f for f in model.chat(ChatHistory(), "Hi", tools=[]) if f]


def test_think_tags_glued_to_whitespace_become_their_own_frames():
    out = frames(["<think>\n", "The user ", "greets me.", "\n</think>\n\n", "Hello", "!"])

    assert "<think>" in out
    assert "</think>" in out
    assert "".join(out) == "<think>\nThe user greets me.\n</think>\n\nHello!"


def test_text_and_tag_in_one_chunk_are_split():
    out = frames(["ok<think>", "x", "</think>done"])

    assert out == ["ok", "<think>", "x", "</think>", "done"]


def test_text_without_tags_is_untouched():
    assert frames(["Hello", " world"]) == ["Hello", " world"]
