import json
from io import StringIO
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field
from rich.console import Console

from thirtycli.chat import ChatState, handle_command, run_turn
from thirtycli.config import Config
from thirtycli.sessions import Session
from thirtycli.theme import THEME
from thirtycli.tools import build_tools


class ScriptedModel(BaseChatModel):
    """Replays canned AIMessages; streams tool calls then text."""

    replies: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.replies.pop(0)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.replies.pop(0)
        if message.tool_calls:
            for index, call in enumerate(message.tool_calls):
                chunk = AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call["args"]),
                            "id": call["id"],
                            "index": index,
                        }
                    ],
                )
                yield ChatGenerationChunk(message=chunk)
                run_manager and run_manager.on_llm_new_token("")
            return
        words = str(message.content).split(" ")
        for position, word in enumerate(words):
            text = word if position == len(words) - 1 else word + " "
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
            run_manager and run_manager.on_llm_new_token(text)


class CancellingModel(BaseChatModel):
    """Marks the shared cancel event as soon as streaming starts."""

    cancel: Any = None

    @property
    def _llm_type(self) -> str:
        return "cancelling"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=""))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.cancel.set()
        yield ChatGenerationChunk(message=AIMessageChunk(content="partial "))


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def console() -> Console:
    return Console(file=StringIO(), width=100, theme=THEME, highlight=False)


def test_turn_saves_user_and_answer():
    session = Session(model="fake")
    state = ChatState(cfg=Config(), session=session, model=ScriptedModel(replies=[AIMessage(content="hello there")]))
    answer = run_turn(state, "hi", console())
    assert answer == "hello there"
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"] == "hi"
    assert Session.load(session.id).messages[1]["content"] == "hello there"


def test_turn_runs_tool_then_answers(tmp_path):
    tools = build_tools(tmp_path)
    session = Session(model="fake")
    model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "now", "args": {}, "id": "call_1"}]),
            AIMessage(content="done"),
        ]
    )
    state = ChatState(cfg=Config(tools=True), session=session, model=model, tools=tools)
    con = console()
    answer = run_turn(state, "what time is it", con)
    out = con.file.getvalue()

    assert answer == "done"
    assert "now(" in out
    assert session.messages[-1]["content"] == "done"


def test_turn_survives_unknown_tool(tmp_path):
    session = Session(model="fake")
    model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "nope", "args": {}, "id": "call_9"}]),
            AIMessage(content="recovered"),
        ]
    )
    state = ChatState(cfg=Config(), session=session, model=model, tools=[], )
    assert run_turn(state, "go", console()) == "recovered"


def test_slash_commands(tmp_path):
    session = Session(model="fake")
    session.add("user", "x")
    state = ChatState(cfg=Config(tools=False), session=session, model=ScriptedModel(), tools=[])

    assert handle_command("/exit", state, console(), tmp_path) is False
    assert handle_command("/help", state, console(), tmp_path) is True
    assert handle_command("/tools on", state, console(), tmp_path) is True
    assert state.cfg.tools is True and state.tools  # refreshed
    assert handle_command("/tools off", state, console(), tmp_path) is True
    assert state.cfg.tools is False
    old_id = state.session.id
    assert handle_command("/clear", state, console(), tmp_path) is True
    assert state.session.id != old_id
    assert state.session.messages == []
    assert Session.load(old_id).messages[0]["content"] == "x"


def test_unknown_command_is_harmless(tmp_path):
    state = ChatState(cfg=Config(), session=Session(), model=ScriptedModel(), tools=[])
    assert handle_command("/wat", state, console(), tmp_path) is True


def test_cancel_stops_generation():
    state = ChatState(cfg=Config(), session=Session(model="fake"), model=ScriptedModel())
    state.model = CancellingModel(cancel=state.cancel)
    con = console()

    assert run_turn(state, "hi", con) == ""
    assert "interrupted" in con.file.getvalue()
    assert [message["role"] for message in state.session.messages] == ["user"]
    assert not state.cancel.is_set()


def test_cancel_does_not_poison_the_next_turn():
    state = ChatState(cfg=Config(), session=Session(model="fake"), model=ScriptedModel())
    state.model = CancellingModel(cancel=state.cancel)
    run_turn(state, "hi", console())

    state.model = ScriptedModel(replies=[AIMessage(content="fine")])
    assert run_turn(state, "again", console()) == "fine"
