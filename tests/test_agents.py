"""Sub-agents: tool loop, delegation interception, parallelism."""

import time

import pytest
from langchain_core.messages import AIMessage

from tests.test_chat_loop import ScriptedModel, console
from thirtycli.agents import SubAgentResult, run_subagent
from thirtycli.chat import ChatState, run_turn
from thirtycli.config import Config
from thirtycli.sessions import Session
from thirtycli.tools import build_readonly_tools, build_tools, tool_map


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def test_readonly_tools_exclude_writes_and_shell(tmp_path):
    names = {tool.name for tool in build_readonly_tools(tmp_path)}
    assert "read_file" in names
    assert "search_text" in names
    assert not {"write_file", "edit_file", "run_shell", "delegate"} & names


def test_subagent_runs_a_tool_then_answers(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": {"path": "a.txt"}, "id": "c1"}]),
            AIMessage(content="found hello"),
        ]
    )
    result = run_subagent(model, "read a.txt", tools=build_readonly_tools(tmp_path))
    assert result.text == "found hello"
    assert result.steps == 2


def test_subagent_stops_at_step_limit(tmp_path):
    calls = [
        AIMessage(content="", tool_calls=[{"name": "now", "args": {}, "id": f"c{i}"}]) for i in range(2)
    ]
    model = ScriptedModel(replies=[*calls, AIMessage(content="final")])
    result = run_subagent(model, "loop", tools=build_readonly_tools(tmp_path), max_steps=2)
    assert result.text == "final"
    assert result.steps == 3


def test_delegate_tool_is_intercepted(tmp_path, monkeypatch):
    seen: dict = {}

    def fake(model, task, role="worker", tools=None, max_steps=4):
        seen["task"] = task
        seen["role"] = role
        return SubAgentResult("sub says hi", {"input": 7, "output": 3, "total": 10}, 2)

    monkeypatch.setattr("thirtycli.chat.run_subagent", fake)
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[{"name": "delegate", "args": {"task": "look at a", "role": "scout"}, "id": "c1"}],
            ),
            AIMessage(content="done"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(tmp_path), root=tmp_path
    )
    con = console()
    assert run_turn(state, "go", con) == "done"
    assert seen == {"task": "look at a", "role": "scout"}
    assert state.session.usage["total"] == 10
    tool_messages = [m for m in state.session.messages if m["role"] == "tool"]
    assert "[scout] sub says hi" in tool_messages[0]["content"]


def test_delegates_run_in_parallel(tmp_path, monkeypatch):
    def slow(model, task, role="worker", tools=None, max_steps=4):
        time.sleep(0.2)
        return SubAgentResult(f"{task} done", {}, 1)

    monkeypatch.setattr("thirtycli.chat.run_subagent", slow)
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delegate", "args": {"task": "one"}, "id": "c1"},
                    {"name": "delegate", "args": {"task": "two"}, "id": "c2"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(tmp_path), root=tmp_path
    )
    began = time.monotonic()
    assert run_turn(state, "go", console()) == "done"
    elapsed = time.monotonic() - began
    assert elapsed < 0.35  # two 0.2s sub-agents overlapped
    contents = [m["content"] for m in state.session.messages if m["role"] == "tool"]
    assert any("one done" in c for c in contents)
    assert any("two done" in c for c in contents)


def test_delegate_is_exposed_to_the_model(tmp_path):
    names = tool_map(build_tools(tmp_path))
    assert "delegate" in names
