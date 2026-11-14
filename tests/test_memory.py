"""Project memory: add/dedupe, persistence, search, context, isolation, resilience."""

import json
import time

import pytest

from thirtycli.memory import MemoryEntry, ProjectMemory, memory_path


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    target = tmp_path / "home"
    monkeypatch.setenv("THIRTYCLI_HOME", str(target))
    return target


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


def test_add_and_dedupe(project):
    memory = ProjectMemory(project)
    first = memory.add("Use SQLite for local state", kind="decision", tags=("db",))
    again = memory.add("  use   sqlite for LOCAL state ")

    assert isinstance(first, MemoryEntry)
    assert again.id == first.id
    entries = memory.all()
    assert len(entries) == 1
    assert entries[0].kind == "decision"
    assert entries[0].tags == ("db",)
    assert entries[0].project == str(project.resolve())


def test_persists_across_instances(project):
    ProjectMemory(project).add("pnpm, not npm", kind="constraint", tags=("tooling",))

    reopened = ProjectMemory(project)
    assert [entry.text for entry in reopened.all()] == ["pnpm, not npm"]
    assert reopened.all()[0].tags == ("tooling",)


def test_store_is_json_under_thirtycli_home(project, home):
    memory = ProjectMemory(project)
    entry = memory.add("CI runs on GitHub Actions", kind="outcome", tags=("ci", "ci", ""))

    assert memory.path == memory_path(project)
    assert memory.path.parent == home / "memory"
    data = json.loads(memory.path.read_text(encoding="utf-8"))
    assert data["project"] == str(project.resolve())
    assert data["entries"][0]["id"] == entry.id
    assert data["entries"][0]["tags"] == ["ci"]


def test_search_prefers_tag_hits(project):
    memory = ProjectMemory(project)
    memory.add("the word auth appears in passing here")
    time.sleep(0.01)
    tagged = memory.add("sessions expire after an hour", tags=("auth",))

    hits = memory.search("auth")
    assert [entry.id for entry in hits][0] == tagged.id
    assert len(hits) == 2


def test_search_matches_text_case_insensitively(project):
    memory = ProjectMemory(project)
    jwt = memory.add("Auth uses JWT access tokens", kind="architecture", tags=("auth",))
    memory.add("Database migrations run through alembic", kind="note", tags=("db",))

    hits = memory.search("JWT")
    assert [entry.id for entry in hits] == [jwt.id]


def test_empty_query_returns_newest(project):
    memory = ProjectMemory(project)
    memory.add("older note")
    time.sleep(0.01)
    memory.add("newer note")

    assert [entry.text for entry in memory.search("")] == ["newer note", "older note"]
    assert len(memory.search("", limit=1)) == 1


def test_context_is_a_markdown_block(project):
    memory = ProjectMemory(project)
    memory.add("Auth uses JWT access tokens", kind="decision", tags=("auth",))
    memory.add("Never commit secrets", kind="constraint", tags=("auth",))

    text = memory.context("auth", limit=3, max_chars=500)
    lines = text.splitlines()
    assert lines[0] == "## Project memory"
    assert set(lines[1:]) == {
        "- [decision] Auth uses JWT access tokens",
        "- [constraint] Never commit secrets",
    }
    assert memory.context("nothing-matches-this") == ""


def test_context_respects_max_chars(project):
    memory = ProjectMemory(project)
    memory.add("A" * 200, tags=("topic",))
    memory.add("B" * 200, tags=("topic",))

    text = memory.context("topic", limit=5, max_chars=60)
    assert text.startswith("## Project memory")
    assert len(text) <= 60
    assert text.endswith("…")


def test_context_with_empty_query_uses_newest(project):
    memory = ProjectMemory(project)
    memory.add("older note")
    time.sleep(0.01)
    memory.add("newer note")

    text = memory.context("", limit=1)
    assert text == "## Project memory\n- [note] newer note"


def test_update_and_remove(project):
    memory = ProjectMemory(project)
    entry = memory.add("old text", kind="note")

    changed = memory.update(entry.id, text="new text", tags=("x", "x", ""))
    assert changed is not None
    assert changed.text == "new text"
    assert changed.tags == ("x",)
    assert changed.updated >= entry.updated
    assert memory.update("missing-id") is None

    assert memory.remove(entry.id) is True
    assert memory.remove(entry.id) is False
    assert memory.all() == []


def test_update_text_only_keeps_tags(project):
    memory = ProjectMemory(project)
    entry = memory.add("first draft", tags=("keep",))

    changed = memory.update(entry.id, text="second draft")
    assert changed is not None
    assert changed.text == "second draft"
    assert changed.tags == ("keep",)


def test_projects_are_isolated(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    one = ProjectMemory(first)
    two = ProjectMemory(second)
    assert one.path != two.path

    one.add("first only")
    assert two.all() == []

    two.add("second only")
    assert [entry.text for entry in one.all()] == ["first only"]
    assert [entry.text for entry in two.all()] == ["second only"]


def test_corrupt_store_starts_empty(project):
    memory = ProjectMemory(project)
    memory.add("one")
    memory.path.write_text("{not json at all", encoding="utf-8")

    assert memory.all() == []
    assert memory.search("one") == []

    memory.add("two")
    assert [entry.text for entry in memory.all()] == ["two"]


def test_rejects_empty_text_and_unknown_kind(project):
    memory = ProjectMemory(project)
    with pytest.raises(ValueError):
        memory.add("   ")
    with pytest.raises(ValueError):
        memory.add("gossip", kind="gossip")
    assert memory.all() == []
