"""CLI integration for the multi-agent run command (no network)."""

import json

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from tests.test_chat_loop import ScriptedModel
from thirtycli.cli import app
from thirtycli.memory import ProjectMemory
from thirtycli.orchestrator import Task, TaskBoard, list_boards, tasks_dir
from thirtycli.sessions import Session


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_run_command_plans_and_merges(tmp_path, monkeypatch):
    plan = json.dumps(
        [
            {"id": "t1", "title": "Scan the project", "agent": "explorer", "depends_on": []},
            {"id": "t2", "title": "Write the summary", "agent": "analyst", "depends_on": []},
        ]
    )
    model = ScriptedModel(
        replies=[
            AIMessage(content=plan),  # planner
            AIMessage(content="scan result"),  # worker t1
            AIMessage(content="analysis result"),  # worker t2
            AIMessage(content="merged answer"),  # merge
        ]
    )
    monkeypatch.setattr("thirtycli.cli.build_model", lambda cfg: model)

    result = CliRunner().invoke(app, ["run", "inspect the project", "--workers", "1"])

    assert result.exit_code == 0, result.output
    assert "merged answer" in result.output
    assert "Scan the project" in result.output
    assert "run " in result.output
    rows = list_boards()
    assert len(rows) == 1
    assert rows[0]["tasks"] == 2
    assert rows[0]["completed"] == 2


def test_agents_and_skills_commands(home):
    result = CliRunner().invoke(app, ["agents"])
    assert result.exit_code == 0, result.output
    assert "planner" in result.output and "security" in result.output

    result = CliRunner().invoke(app, ["skills"])
    assert result.exit_code == 0, result.output
    assert "Python Development" in result.output


def test_tasks_command_without_runs(home):
    result = CliRunner().invoke(app, ["tasks"])
    assert result.exit_code == 0, result.output
    assert "no agent runs yet" in result.output


def test_session_rename(home):
    session = Session()
    session.add("user", "original")
    session.save()

    result = CliRunner().invoke(app, ["sessions", "rename", session.id, "New", "name"])

    assert result.exit_code == 0, result.output
    assert Session.load(session.id).title == "New name"


def test_tasks_show_and_trace(home):
    board = TaskBoard(goal="demo goal")
    board.add(Task("t1", "first task", agent="coder"))
    board.save()
    events = [
        {"event": "plan", "ts": 1.0, "run_id": board.run_id, "goal": "demo goal", "tasks": ["t1 · coder · first task"]},
        {"event": "task.start", "ts": 2.0, "run_id": board.run_id, "task": "t1", "agent": "coder"},
    ]
    (tasks_dir() / f"{board.run_id}.events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    result = CliRunner().invoke(app, ["tasks", "show", board.run_id])
    assert result.exit_code == 0, result.output
    assert "demo goal" in result.output and "first task" in result.output

    result = CliRunner().invoke(app, ["tasks", "show", board.run_id, "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == board.run_id

    result = CliRunner().invoke(app, ["trace", "show", board.run_id])
    assert result.exit_code == 0, result.output
    assert "plan" in result.output and "task.start" in result.output


def test_memory_commands(home):
    result = CliRunner().invoke(
        app, ["memory", "add", "Use", "Postgres", "for", "storage", "--kind", "decision", "--tags", "db"]
    )
    assert result.exit_code == 0, result.output
    entry = ProjectMemory(home).all()[0]
    assert "Postgres" in entry.text

    result = CliRunner().invoke(app, ["memory", "list"])
    assert "Postgres" in result.output

    result = CliRunner().invoke(app, ["memory", "search", "postgres"])
    assert entry.id in result.output

    result = CliRunner().invoke(app, ["memory", "edit", entry.id, "Use", "SQLite"])
    assert result.exit_code == 0, result.output
    assert ProjectMemory(home).all()[0].text == "Use SQLite"

    result = CliRunner().invoke(app, ["memory", "rm", entry.id])
    assert result.exit_code == 0, result.output
    assert ProjectMemory(home).all() == []


def test_run_coding_worker_end_to_end(tmp_path, monkeypatch):
    plan = json.dumps(
        [
            {
                "id": "t1",
                "title": "add hello.txt",
                "description": "Create hello.txt containing hi",
                "agent": "coder",
                "depends_on": [],
            }
        ]
    )
    model = ScriptedModel(
        replies=[
            AIMessage(content=plan),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "hello.txt", "content": "hi\n"},
                        "id": "call_write",
                    }
                ],
            ),
            AIMessage(content="wrote hello.txt"),
        ]
    )
    monkeypatch.setattr("thirtycli.cli.build_model", lambda cfg: model)

    result = CliRunner().invoke(
        app,
        ["run", "write hello", "--coding", "--yes", "--permission", "controlled", "--workers", "1"],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi\n"
    assert "wrote hello.txt" in result.output
    rows = list_boards()
    assert rows[0]["completed"] == 1
