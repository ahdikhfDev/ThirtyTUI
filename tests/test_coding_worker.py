"""Real coding workers: write tools, permission gates, verification, conflicts."""

import pytest
from langchain_core.messages import AIMessage

from tests.test_chat_loop import ScriptedModel
from thirtycli.audit import AuditLog
from thirtycli.config import Config
from thirtycli.models import RouteDecision
from thirtycli.orchestrator import Orchestrator, Task, TaskBoard, TaskStatus


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def one_task(title="add hello.txt", agent="coder", description="Create hello.txt containing hi"):
    def planner(goal, limit):
        return [
            {
                "id": "t1",
                "title": title,
                "description": description,
                "agent": agent,
                "depends_on": [],
                "max_retries": 0,
            }
        ]

    return planner


def write_call(path="hello.txt", content="hi\n"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"path": path, "content": content},
                "id": "call_write",
            }
        ],
    )


def test_coding_worker_writes_file_and_verification_passes(tmp_path):
    audit = AuditLog(tmp_path / "audit" / "audit.jsonl")
    model = ScriptedModel(replies=[write_call(), AIMessage(content="created hello.txt")])
    orch = Orchestrator(
        model=model,
        root=tmp_path,
        workers=1,
        planner=one_task(),
        coding=True,
        permission="controlled",
        approver=lambda _question: True,
        persist=False,
        memory=False,
        route=False,
        audit=audit,
    )

    final = orch.run("write hello")

    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi\n"
    task = orch.board.tasks[0]
    assert task.status == TaskStatus.COMPLETED
    assert task.verification["status"] == "verified"
    assert "hello.txt" in task.verification["files"]
    assert "created hello.txt" in final
    events = audit.tail()
    tool_event = next(event for event in events if event.get("event") == "tool")
    assert tool_event["decision"] == "allowed"
    assert tool_event["approval"] == "approved"


def test_denied_write_is_not_applied_and_verification_fails(tmp_path):
    audit = AuditLog(tmp_path / "audit" / "audit.jsonl")
    model = ScriptedModel(replies=[write_call(), AIMessage(content="could not write")])
    orch = Orchestrator(
        model=model,
        root=tmp_path,
        workers=1,
        planner=one_task(),
        coding=True,
        permission="controlled",
        approver=lambda _question: False,
        persist=False,
        memory=False,
        route=False,
        audit=audit,
    )

    orch.run("write hello")

    assert not (tmp_path / "hello.txt").exists()
    task = orch.board.tasks[0]
    assert task.status == TaskStatus.VERIFICATION_FAILED
    assert "verification failed" in task.error
    denied = next(event for event in audit.tail() if event.get("event") == "tool")
    assert denied["decision"] == "denied"
    assert denied["approval"] == "denied"


def test_read_only_agent_cannot_write(tmp_path):
    model = ScriptedModel(replies=[write_call(), AIMessage(content="no write")])
    orch = Orchestrator(
        model=model,
        root=tmp_path,
        workers=1,
        planner=one_task(title="inspect the project", agent="explorer"),
        coding=True,
        permission="autonomous",
        persist=False,
        memory=False,
        route=False,
    )

    orch.run("inspect")

    assert not (tmp_path / "hello.txt").exists()
    assert orch.board.tasks[0].status == TaskStatus.COMPLETED
    assert orch.board.tasks[0].verification["status"] == "verified"


def test_unrelated_workers_cannot_overwrite_the_same_file(tmp_path):
    def planner(goal, limit):
        return [
            {
                "id": "t1",
                "title": "write first file",
                "description": "Create shared.txt with one",
                "agent": "coder",
                "depends_on": [],
                "max_retries": 0,
            },
            {
                "id": "t2",
                "title": "write second file",
                "description": "Create shared.txt with two",
                "agent": "coder",
                "depends_on": [],
                "max_retries": 0,
            },
        ]

    replies = [
        write_call("shared.txt", "one\n"),
        AIMessage(content="first done"),
        write_call("shared.txt", "two\n"),
        AIMessage(content="second done"),
    ]
    orch = Orchestrator(
        model=ScriptedModel(replies=replies),
        root=tmp_path,
        workers=1,
        planner=planner,
        coding=True,
        permission="controlled",
        approver=lambda _question: True,
        persist=False,
        memory=False,
        route=False,
    )

    orch.run("two writers")

    assert (tmp_path / "shared.txt").read_text(encoding="utf-8") == "one\n"
    tasks = {task.id: task for task in orch.board.tasks}
    assert tasks["t1"].status == TaskStatus.COMPLETED
    assert tasks["t2"].status == TaskStatus.VERIFICATION_FAILED
    assert orch.board.conflicts
    assert any(row["path"] == "shared.txt" for row in orch.board.conflicts)


def test_route_decision_is_recorded_and_used(tmp_path, monkeypatch):
    routed = ScriptedModel(replies=[])
    decision = RouteDecision("deepseek", "deepseek-chat", "cheaper for a small edit", 2.0, False)
    monkeypatch.setattr("thirtycli.orchestrator.route_model", lambda *args, **kwargs: decision)
    monkeypatch.setattr("thirtycli.orchestrator.build_model", lambda cfg: routed)

    orch = Orchestrator(
        model=ScriptedModel(replies=[]),
        cfg=Config(provider="openai", model="gpt-4o"),
        root=tmp_path,
        persist=False,
        memory=False,
        route=True,
    )
    orch.board = TaskBoard(goal="route")
    model, payload = orch._model_for(Task("t1", "tiny fix", agent="coder"))

    assert model is routed
    assert payload["provider"] == "deepseek"
    assert payload["reason"] == "cheaper for a small edit"
    assert any(event["event"] == "route.decision" for event in orch.trace)
