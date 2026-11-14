"""Block-by-block markdown streaming."""

from io import StringIO

from rich.console import Console

from thirtycli.render import AssistantStream, split_complete


def console() -> Console:
    return Console(file=StringIO(), force_terminal=True, width=80, color_system="standard")


def test_split_paragraph():
    chunk, rest = split_complete("hello\n\nworld")
    assert chunk == "hello\n\n"
    assert rest == "world"


def test_split_waits_for_closed_fence():
    text = "```py\nprint(1)\n"
    assert split_complete(text) == ("", text)


def test_split_after_closed_fence():
    text = "```py\nprint(1)\n```\nafter"
    chunk, rest = split_complete(text)
    assert chunk == "```py\nprint(1)\n```\n"
    assert rest == "after"


def test_split_forces_long_block():
    text = "x" * 4000
    chunk, rest = split_complete(text)
    assert chunk == text
    assert rest == ""


def test_split_stitches_long_fence():
    text = "```\n" + ("a\n" * 4000)
    chunk, rest = split_complete(text)
    assert chunk.endswith("```\n")
    assert rest.startswith("```")


def test_stream_commits_blocks():
    con = console()
    stream = AssistantStream(con, live_ok=True)
    stream.feed("first paragraph\n\n")
    stream.feed("second ")
    stream.feed("paragraph\n\n")
    stream.close()
    out = con.file.getvalue()
    assert "first paragraph" in out
    assert "second" in out


def test_stream_handles_code():
    con = console()
    stream = AssistantStream(con, live_ok=True)
    stream.feed("before\n\n```python\nprint('hi')\n```\n\nafter\n\n")
    stream.close()
    out = con.file.getvalue()
    assert "before" in out
    assert "print" in out
    assert "after" in out


def test_stream_plain_mode_streams_raw():
    con = Console(file=StringIO(), width=80)
    stream = AssistantStream(con, live_ok=False)
    stream.feed("a")
    stream.feed("b")
    stream.close()
    assert "ab" in con.file.getvalue()
