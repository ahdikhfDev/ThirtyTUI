"""Features added for the opencode-style loop: mentions, undo, compact, fetch, grep."""

import json
from email.message import Message
from io import StringIO

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from rich.console import Console

from tests.test_chat_loop import ScriptedModel
from thirtycli import ui
from thirtycli.chat import ChatState, compact, expand_mentions, run_shell_direct, run_turn
from thirtycli.config import Config
from thirtycli.render import AssistantStream
from thirtycli.sessions import Session
from thirtycli.theme import THEME
from thirtycli.tools import ToolError, build_tools, tool_map


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def console(width: int = 100) -> Console:
    return Console(file=StringIO(), width=width, theme=THEME, highlight=False)


def test_expand_mentions(tmp_path):
    (tmp_path / "a.py").write_text("print('hi')\n", encoding="utf-8")
    text = expand_mentions("explain @a.py please", tmp_path)
    assert '<file path="a.py">' in text
    assert "print('hi')" in text
    assert expand_mentions("no mentions here", tmp_path) == "no mentions here"
    assert expand_mentions("mail @missing.py", tmp_path) == "mail @missing.py"


def test_expand_mentions_rejects_escape(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    assert "@../secret.txt" in expand_mentions("see @../secret.txt", tmp_path)


def test_stream_falls_back_to_plain_text():
    con = console()
    stream = AssistantStream(con, live_ok=False)
    stream.feed("hello ")
    stream.feed("**world**")
    stream.close()
    out = con.file.getvalue()
    assert "hello **world**" in out


def test_live_markdown_path_runs():
    con = Console(file=StringIO(), force_terminal=True, width=80)
    stream = AssistantStream(con, live_ok=True)
    stream.feed("## hi\n\n```py\nx = 1\n```\n")
    stream.close()
    assert "hi" in con.file.getvalue()


def test_search_text(tmp_path):
    (tmp_path / "app.py").write_text("def handler():\n    return 1\n", encoding="utf-8")
    tools = tool_map(build_tools(tmp_path))
    hits = tools["search_text"].invoke({"pattern": "def handler"})
    assert "app.py:1" in hits

    none = tools["search_text"].invoke({"pattern": "zzz-not-there"})
    assert "No matches" in none


def test_web_fetch_strips_markup(monkeypatch):
    body = b"<html><head><script>evil()</script></head><body><h1>Hello</h1><p>there</p></body></html>"
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"

    class Fake:
        def __init__(self):
            self.headers = headers

        def read(self, _n):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Fake())
    tools = tool_map(build_tools("."))
    text = tools["web_fetch"].invoke({"url": "https://example.com"})
    assert "Hello" in text and "there" in text
    assert "evil()" not in text


def test_web_fetch_rejects_other_schemes():
    tools = tool_map(build_tools("."))
    with pytest.raises(ToolError):
        tools["web_fetch"].invoke({"url": "file:///etc/passwd"})


def test_tool_summary_collapses_output():
    con = console()
    text = "\n".join(f"line {i}" for i in range(12))
    ui.tool_result_summary("read_file", text, 0.3, con)
    out = con.file.getvalue()
    assert "12 lines" in out
    assert "ctrl+o" in out
    assert "line 11" not in out


def test_tool_summary_shows_errors_inline():
    con = console()
    ui.tool_result_summary("web_fetch", "HTTP 403 for https://x.dev/", 0.1, con)
    out = con.file.getvalue()
    assert "HTTP 403" in out


def test_expand_tool_prints_everything():
    con = console()
    text = "\n".join(f"line {i}" for i in range(12))
    ui.expand_tool({"name": "read_file", "result": text, "seconds": 0.3}, con)
    out = con.file.getvalue()
    assert "line 11" in out
    assert "read_file" in out


def test_expand_slash_command(tmp_path):
    from thirtycli.chat import handle_command

    state = ChatState(cfg=Config(tools=False), session=Session(), model=ScriptedModel(), tools=[])
    state.tool_log.append({"name": "read_file", "result": "secret-line", "seconds": 0.1})
    con = console()
    assert handle_command("/expand", state, con, tmp_path) is True
    assert "secret-line" in con.file.getvalue()

    con2 = console()
    assert handle_command("/expand", ChatState(cfg=Config(), session=Session(), model=ScriptedModel()), con2, tmp_path)
    assert "nothing to expand" in con2.file.getvalue()


def test_tool_log_is_filled(tmp_path):
    model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "now", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
    )
    state = ChatState(
        cfg=Config(), session=Session(), model=model, tools=build_tools(tmp_path), root=tmp_path
    )
    run_turn(state, "time?", console())
    assert state.tool_log and state.tool_log[-1]["name"] == "now"


def test_yolo_slash_toggles(tmp_path):
    from thirtycli.chat import handle_command

    session = Session()
    state = ChatState(cfg=Config(tools=False), session=session, model=ScriptedModel(), tools=[])
    assert handle_command("/yolo on", state, console(), tmp_path) is True
    assert state.auto_yes is True
    assert handle_command("/yolo off", state, console(), tmp_path) is True
    assert state.auto_yes is False


def test_undo_and_clear(tmp_path):
    from thirtycli.chat import handle_command

    session = Session()
    session.add("user", "one")
    session.add("assistant", "two")
    state = ChatState(cfg=Config(tools=False), session=session, model=ScriptedModel(), tools=[])
    assert handle_command("/undo", state, console(), tmp_path) is True
    assert session.messages == []
    assert handle_command("/clear", state, console(), tmp_path) is True


def test_compact_replaces_history():
    session = Session()
    session.add("user", "build a page")
    session.add("assistant", "done")
    state = ChatState(
        cfg=Config(tools=False),
        session=session,
        model=ScriptedModel(replies=[AIMessage(content="- goal: build a page")]),
        tools=[],
    )
    con = console()
    compact(state, con)
    assert len(session.messages) == 1
    assert "Summary of the conversation" in session.messages[0]["content"]
    assert "build a page" in session.messages[0]["content"]


def test_shell_escape_feeds_context(tmp_path):
    session = Session()
    state = ChatState(cfg=Config(tools=False), session=session, model=ScriptedModel(), tools=[], root=tmp_path)
    con = console()
    run_shell_direct("echo thirty", state, con)
    assert "thirty" in con.file.getvalue()
    assert "I ran this shell command" in session.messages[-1]["content"]


def test_write_requires_approval(tmp_path, monkeypatch):
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "new.txt", "content": "hello"},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="stopped"),
        ]
    )
    session = Session()
    state = ChatState(
        cfg=Config(),
        session=session,
        model=model,
        tools=build_tools(tmp_path),
        root=tmp_path,
    )
    con = console()
    monkeypatch.setattr(con, "input", lambda *_a, **_k: "n")
    answer = run_turn(state, "write it", con)
    assert answer == "stopped"
    assert not (tmp_path / "new.txt").exists()
    assert "denied" in con.file.getvalue()


def test_write_approved(tmp_path, monkeypatch):
    model = ScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": "new.txt", "content": "hello"}, "id": "c1"}
                ],
            ),
            AIMessage(content="written"),
        ]
    )
    state = ChatState(
        cfg=Config(),
        session=Session(),
        model=model,
        tools=build_tools(tmp_path),
        root=tmp_path,
    )
    con = console()
    monkeypatch.setattr(con, "input", lambda *_a, **_k: "y")
    assert run_turn(state, "write it", con) == "written"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "hello"


def test_usage_accumulates():
    session = Session()
    session.add_usage(10, 5)
    session.add_usage(1, 2)
    assert session.usage == {"input": 11, "output": 7, "total": 18}
    again = Session.load(session.id) if session.save() else None
    assert again.usage["total"] == 18


def test_footer_and_diff_render():
    con = console()
    ui.footer(1.23, {"total": 42}, "deepseek-chat", con)
    ui.diff_preview("a.py", "one\ntwo\n", "one\nthree\n", con)
    out = con.file.getvalue()
    assert "42 tok" in out
    assert "a.py" in out
    assert "+three" in out


def test_usage_normalisation():
    from thirtycli.chat import _normalise_usage

    assert _normalise_usage({"input_tokens": 3, "output_tokens": 4}) == {
        "input": 3,
        "output": 4,
        "total": 7,
    }
    assert _normalise_usage(None) == {}


def test_message_chunk_usage_shape():
    chunk = AIMessageChunk(content="x", usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3})
    assert chunk.usage_metadata["total_tokens"] == 3
    assert json.dumps({"ok": True})
