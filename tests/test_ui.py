from io import StringIO

from rich.console import Console

from thirtycli import ui
from thirtycli.config import Config
from thirtycli.sessions import Session
from thirtycli.theme import MARK_ROWS, THEME, prompt_text


def _console(width=80):
    return Console(file=StringIO(), width=width, theme=THEME, highlight=False)


def test_mark_is_square():
    assert len({len(row) for row in MARK_ROWS}) == 1
    assert ">_" in MARK_ROWS[3]
    assert "\u25cf" in MARK_ROWS[2]


def test_prompt_is_themed():
    text = prompt_text()
    assert text.plain == ">_ "
    assert text.style == "accent"


def test_banner_renders():
    con = _console(100)
    session = Session(id="abc12345")
    ui.banner(Config(), session, con)
    out = con.file.getvalue()
    assert "ThirtyCLI" in out
    assert "openai" in out
    assert "abc12345" in out


def test_banner_narrow_falls_back():
    con = _console(40)
    ui.banner(Config(), Session(), con)
    out = con.file.getvalue()
    assert "ThirtyCLI" in out


def test_help_panel_lists_commands():
    con = _console(100)
    ui.help_panel(con)
    out = con.file.getvalue()
    for cmd in ("/help", "/model", "/tools", "/exit"):
        assert cmd in out


def test_tool_and_status_lines():
    con = _console(100)
    ui.tool_call("read_file", {"path": "a.py"}, con)
    ui.tool_result_summary("read_file", "one\ntwo", 0.0, con)
    ui.info("hi", con)
    ui.ok("done", con)
    ui.error("boom", con)
    out = con.file.getvalue()
    assert "read_file" in out
    assert "boom" in out
