"""Agent-loop features: tool history, plan mode, parallel calls, fallback, rules."""

import json
from io import StringIO

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from rich.console import Console

from tests.test_chat_loop import ScriptedModel
from thirtycli.chat import ChatState, build_messages, project_rules, run_turn
from thirtycli.config import Config
from thirtycli.pricing import estimate, price_for
from thirtycli.sessions import Session
from thirtycli.theme import THEME
from thirtycli.tools import ToolError, build_tools, tool_map


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def console() -> Console:
    return Console(file=StringIO(), width=100, theme=THEME, highlight=False)


class FailingModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "failing"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("provider exploded")

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover


def test_tool_history_is_persisted(tmp_path):
    model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "now", "args": {}, "id": "c1"}]),
            AIMessage(content="final"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(tmp_path), root=tmp_path
    )
    run_turn(state, "time?", console())

    saved = Session.load(state.session.id)
    roles = [message["role"] for message in saved.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert saved.messages[1]["tool_calls"][0]["name"] == "now"
    assert saved.messages[2]["tool_call_id"] == "c1"

    rebuilt = build_messages(Config(), saved)
    assert isinstance(rebuilt[2], AIMessage) and rebuilt[2].tool_calls
    assert isinstance(rebuilt[3], ToolMessage)


def test_plan_mode_blocks_writes(tmp_path):
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "x.txt", "content": "nope"}, "id": "c1"}
                ],
            ),
            AIMessage(content="here is my plan"),
        ]
    )
    state = ChatState(
        cfg=Config(),
        session=Session(),
        model=model,
        tools=build_tools(tmp_path),
        root=tmp_path,
        plan_mode=True,
    )
    con = console()
    answer = run_turn(state, "write a file", con)
    assert answer == "here is my plan"
    assert not (tmp_path / "x.txt").exists()
    assert "Plan mode" in con.file.getvalue()


def test_parallel_safe_tools(tmp_path):
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"path": "a.txt"}, "id": "c1"},
                    {"name": "read_file", "args": {"path": "b.txt"}, "id": "c2"},
                ],
            ),
            AIMessage(content="read both"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(tmp_path), root=tmp_path
    )
    run_turn(state, "read both", console())
    tool_messages = [m for m in state.session.messages if m["role"] == "tool"]
    assert len(tool_messages) == 2
    assert "alpha" in tool_messages[0]["content"]
    assert "beta" in tool_messages[1]["content"]


def test_fallback_model(monkeypatch):
    monkeypatch.setattr(
        "thirtycli.chat.build_model",
        lambda cfg: ScriptedModel(replies=[AIMessage(content="fallback ok")]),
    )
    state = ChatState(
        cfg=Config(provider="openai", model="primary", fallback_model="backup"),
        session=Session(),
        model=FailingModel(),
        tools=[],
    )
    assert run_turn(state, "hi", console()) == "fallback ok"
    assert state.used_fallback is True


def test_project_rules_are_injected(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Always answer in Indonesian.", encoding="utf-8")
    rules = project_rules(tmp_path)
    assert "Always answer in Indonesian." in rules

    messages = build_messages(Config(), Session(), tmp_path)
    assert "Always answer in Indonesian." in messages[0].content


def test_project_rules_absent(tmp_path):
    assert project_rules(tmp_path) == ""
    assert "AGENTS.md" not in build_messages(Config(), Session(), tmp_path)[0].content


def test_plan_mode_note_in_system_prompt(tmp_path):
    messages = build_messages(Config(), Session(), tmp_path, plan_mode=True)
    assert "PLAN MODE" in messages[0].content


def test_pricing():
    assert price_for("deepseek-chat") == (0.27, 1.10)
    assert price_for("some-unknown-model") is None
    cost = estimate("deepseek-chat", {"input": 1_000_000, "output": 0, "total": 1_000_000})
    assert cost == pytest.approx(0.27)
    assert estimate("unknown", {"total": 5}) is None
    assert estimate("deepseek-chat", {}) is None


def test_web_search_parses_results(monkeypatch):
    page = (
        '<a rel="nofollow" class="result__a" href="https://example.com/a">First <b>hit</b></a>'
        '<a class="result__snippet" href="#">Snippet for the first hit</a>'
        '<a rel="nofollow" class="result__a" href="https://example.com/b">Second hit</a>'
        '<a class="result__snippet" href="#">Second snippet</a>'
    )
    monkeypatch.setattr("thirtycli.tools._get", lambda url: page)
    tools = tool_map(build_tools("."))
    out = tools["web_search"].invoke({"query": "thirtycli"})
    assert "First hit" in out
    assert "https://example.com/a" in out
    assert "Snippet for the first hit" in out


def test_git_tools_outside_repo(tmp_path):
    tools = tool_map(build_tools(tmp_path))
    with pytest.raises(ToolError):
        tools["git_status"].invoke({})


def test_outside_read_requires_approval(tmp_path, monkeypatch):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("classified", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"path": str(outside)}, "id": "c1"}
                ],
            ),
            AIMessage(content="denied, ok"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(workspace), root=workspace
    )
    con = console()
    monkeypatch.setattr(con, "input", lambda *_a, **_k: "n")
    assert run_turn(state, "read it", con) == "denied, ok"
    assert "classified" not in con.file.getvalue()
    assert "denied" in con.file.getvalue()


def test_write_outside_workspace_after_approval(tmp_path, monkeypatch):
    outside = tmp_path / "desktopish" / "halo.html"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": str(outside), "content": "<h1>hi</h1>"},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage(content="written"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(workspace), root=workspace
    )
    con = console()
    monkeypatch.setattr(con, "input", lambda *_a, **_k: "y")
    assert run_turn(state, "tulis ke desktop", con) == "written"
    assert outside.read_text(encoding="utf-8") == "<h1>hi</h1>"
    assert "halo.html" in con.file.getvalue()


def test_session_usage_survives_reload(tmp_path):
    session = Session()
    session.add("user", "hi")
    session.add_usage(12, 8)
    session.save()
    again = Session.load(session.id)
    assert again.usage == {"input": 12, "output": 8, "total": 20}
    assert json.dumps(again.usage)
