import pytest

from thirtycli.tools import ToolError, build_tools, tool_map


@pytest.fixture()
def ws(tmp_path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return tmp_path


def test_read_write_list(ws):
    tools = tool_map(build_tools(ws))
    listing = tools["list_dir"].invoke({"path": "."})
    assert "notes.txt" in listing
    assert "src/" in listing

    body = tools["read_file"].invoke({"path": "notes.txt"})
    assert "alpha" in body and "beta" in body
    assert body.count("\n") >= 1

    tools["write_file"].invoke({"path": "out/new.txt", "content": "x"})
    assert (ws / "out" / "new.txt").read_text(encoding="utf-8") == "x"


def test_outside_paths_are_allowed(ws):
    outside = ws.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tools = tool_map(build_tools(ws))
    assert "secret" in tools["read_file"].invoke({"path": "../outside.txt"})
    assert "secret" in tools["read_file"].invoke({"path": str(outside)})


def test_resolve_in_root_still_guards_mentions(ws):
    from thirtycli.tools import resolve_in_root

    with pytest.raises(ToolError):
        resolve_in_root(ws, "../outside.txt")


def test_edit_file(ws):
    tools = tool_map(build_tools(ws))
    tools["write_file"].invoke({"path": "code.py", "content": "a = 1\nb = 2\n"})
    out = tools["edit_file"].invoke({"path": "code.py", "old": "b = 2", "new": "b = 3"})
    assert "replaced 1" in out
    assert (ws / "code.py").read_text(encoding="utf-8") == "a = 1\nb = 3\n"


def test_edit_file_requires_unique_snippet(ws):
    tools = tool_map(build_tools(ws))
    tools["write_file"].invoke({"path": "dup.txt", "content": "x\nx\n"})
    with pytest.raises(ToolError):
        tools["edit_file"].invoke({"path": "dup.txt", "old": "x", "new": "y"})
    out = tools["edit_file"].invoke({"path": "dup.txt", "old": "x", "new": "y", "count": 2})
    assert "2" in out
    assert (ws / "dup.txt").read_text(encoding="utf-8") == "y\ny\n"


def test_edit_file_missing_snippet(ws):
    tools = tool_map(build_tools(ws))
    with pytest.raises(ToolError):
        tools["edit_file"].invoke({"path": "notes.txt", "old": "nope", "new": "x"})


def test_shell(ws):
    tools = tool_map(build_tools(ws))
    out = tools["run_shell"].invoke({"command": "echo thirty"})
    assert "thirty" in out
    assert "exit=0" in out


def test_shell_can_be_disabled(ws):
    names = {t.name for t in build_tools(ws, allow_shell=False)}
    assert "run_shell" not in names
    assert "read_file" in names
