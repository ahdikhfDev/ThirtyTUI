"""Suggestion menus: `/` command palette and `@` file picks."""

from thirtycli.repl import file_suggestions, make_completer, make_reader, slash_suggestions
from thirtycli.ui import COMMANDS


def test_slash_shows_everything_for_bare_slash():
    results = slash_suggestions("/", COMMANDS)
    assert len(results) == len(COMMANDS)
    names = [name for name, _desc in results]
    assert "/compact" in names
    assert "/tools" in names


def test_slash_filters_by_prefix():
    results = slash_suggestions("/m", COMMANDS)
    assert [name for name, _ in results] == ["/model"]


def test_slash_stops_once_arguments_begin():
    assert slash_suggestions("/model deepseek", COMMANDS) == []


def test_slash_ignores_non_commands():
    assert slash_suggestions("hello", COMMANDS) == []
    assert slash_suggestions("@file.py", COMMANDS) == []


def test_descriptions_are_attached():
    results = dict(slash_suggestions("/", COMMANDS))
    assert results["/help"] == "show this list"
    assert "summarise" in results["/compact"].lower()
    assert "switch model" in results["/model"]


def test_completer_attaches_descriptions():
    from prompt_toolkit.document import Document

    completer = make_completer(["src/app.py", "app.py"])
    items = list(completer.get_completions(Document("/"), None))
    assert {item.text for item in items} == {name for name, _ in COMMANDS}
    assert all(item.display_meta_text for item in items)

    model = [item for item in items if item.text == "/model"][0]
    assert "switch model" in model.display_meta_text


def test_completer_handles_files():
    from prompt_toolkit.document import Document

    completer = make_completer(["src/app.py", "app.py"])
    items = list(completer.get_completions(Document("explain @app"), None))
    assert [item.text for item in items] == ["app.py", "src/app.py"]
    assert items[0].display_meta_text == "file"


def test_file_suggestions_rank_by_name():
    files = ["src/app/main.py", "docs/app.md", "app.py", "tests/test_app.py"]
    hits = file_suggestions("app", files)
    assert hits[0] == "app.py"
    assert set(hits) >= {"app.py", "docs/app.md"}


def test_file_suggestions_empty_fragment_lists_files():
    files = ["a.py", "b.py"]
    assert file_suggestions("", files) == ["a.py", "b.py"]


def test_file_suggestions_limit():
    files = [f"file{i}.py" for i in range(100)]
    assert len(file_suggestions("", files)) <= 40


def test_prompt_session_builds_with_palette():
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    reader = make_reader("deepseek · tools on", force=True, input=DummyInput(), output=DummyOutput())
    assert callable(reader)
