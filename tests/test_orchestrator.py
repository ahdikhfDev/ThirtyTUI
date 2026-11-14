"""Orchestrator: dependency scheduling, retries, cancellation, persistence."""

import json
import threading

import pytest

from thirtycli.orchestrator import (
    Orchestrator,
    Task,
    TaskBoard,
    TaskStatus,
    list_boards,
    parse_plan,
    plan_graph,
    tasks_from_specs,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def specs(*items):
    return [
        {
            "id": task_id,
            "title": title,
            "description": description or title,
            "agent": agent,
            "depends_on": list(deps),
            "max_retries": retries,
        }
        for task_id, title, agent, deps, description, retries in items
    ]


def board_after(orch):
    return {task.id: task for task in orch.board.tasks}


def test_parse_plan_validates_agents_and_dependencies():
    reply = """
    ```json
    [
      {"id": "t1", "title": "Read the project", "agent": "explorer", "depends_on": []},
      {"id": "t2", "title": "Fix auth", "agent": "backend", "depends_on": ["t1", "ghost"]},
      {"id": "t3", "title": "Mystery", "agent": "does-not-exist", "depends_on": ["t2"]},
      {"id": "t4", "title": "Too many"}
    ]
    ```
    """
    parsed = parse_plan(reply, limit=3)
    assert [item["id"] for item in parsed] == ["t1", "t2", "t3"]
    assert parsed[1]["depends_on"] == ["t1"]
    assert parsed[2]["agent"]
    assert parsed[2]["depends_on"] == ["t2"]


def test_parse_plan_rejects_garbage():
    assert parse_plan("not json at all") == []
    assert parse_plan("[]") == []
    assert plan_graph(None, "anything") == []


def test_tasks_from_specs_dedupes_and_clamps(tmp_path):
    tasks = tasks_from_specs(
        [
            {"id": "t1", "title": "one", "depends_on": ["t1", "nope"], "max_retries": 99},
            {"id": "t1", "title": "duplicate", "depends_on": []},
        ],
        "goal",
    )
    ids = [task.id for task in tasks]
    assert len(ids) == len(set(ids))
    assert tasks[0].depends_on == []
    assert tasks[0].max_retries == 3


def test_tasks_from_specs_falls_back_to_single_task():
    tasks = tasks_from_specs([], "Fix the login redirect loop")
    assert len(tasks) == 1
    assert tasks[0].id == "t1"
    assert tasks[0].agent  # selected from the catalog


def test_board_roundtrip_and_list(tmp_path):
    board = TaskBoard(goal="demo")
    board.add(Task("t1", "one", agent="coder"))
    board.add(Task("t2", "two", agent="tester", depends_on=["t1"]))
    board.tasks[0].status = TaskStatus.COMPLETED
    path = board.save(tmp_path)
    assert path.exists()

    again = TaskBoard.load(board.run_id, tmp_path)
    assert again.goal == "demo"
    assert [task.id for task in again.tasks] == ["t1", "t2"]
    assert again.tasks[0].status == TaskStatus.COMPLETED
    rows = list_boards(tmp_path)
    assert rows[0]["run_id"] == board.run_id
    assert rows[0]["completed"] == 1


def test_independent_tasks_run_in_parallel(tmp_path):
    barrier = threading.Barrier(2, timeout=3)
    seen_threads = set()
    lock = threading.Lock()

    def runner(task, _board):
        with lock:
            seen_threads.add(threading.get_ident())
        barrier.wait()
        return f"done {task.id}"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "explorer", [], "first", 0),
        ("t2", "second", "analyst", [], "second", 0),
    )
    orch = Orchestrator(model=None, root=tmp_path, workers=2, planner=planner, runner=runner, persist=False)
    orch.run("parallel goal")
    assert {task.status for task in orch.board.tasks} == {TaskStatus.COMPLETED}
    assert len(seen_threads) == 2


def test_dependency_waits_for_prerequisite(tmp_path):
    order = []

    def runner(task, board):
        if task.id == "t2":
            assert board.get("t1").status == TaskStatus.COMPLETED
        order.append(task.id)
        return f"done {task.id}"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "explorer", [], "first", 0),
        ("t2", "second", "coder", ["t1"], "second", 0),
    )
    orch = Orchestrator(model=None, root=tmp_path, workers=2, planner=planner, runner=runner, persist=False)
    orch.run("dependent goal")
    assert order == ["t1", "t2"]


def test_failure_blocks_dependents_but_independent_tasks_continue(tmp_path):
    def runner(task, _board):
        if task.id == "t1":
            raise RuntimeError("boom")
        return "ok"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "broken", "coder", [], "broken", 0),
        ("t2", "independent", "analyst", [], "independent", 0),
        ("t3", "dependent", "reviewer", ["t1"], "dependent", 0),
    )
    orch = Orchestrator(model=None, root=tmp_path, workers=3, planner=planner, runner=runner, persist=False)
    final = orch.run("failure goal")
    tasks = board_after(orch)
    assert tasks["t1"].status == TaskStatus.FAILED
    assert tasks["t2"].status == TaskStatus.COMPLETED
    assert tasks["t3"].status == TaskStatus.BLOCKED
    assert orch.board.overall() == TaskStatus.FAILED
    assert "ok" in final


def test_retry_then_success(tmp_path):
    attempts = {"count": 0}

    def runner(task, _board):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("flaky")
        return "recovered"

    planner = lambda goal, limit: specs(("t1", "flaky", "coder", [], "flaky", 1))  # noqa: E731
    orch = Orchestrator(model=None, root=tmp_path, planner=planner, runner=runner, persist=False)
    orch.run("retry goal")
    assert attempts["count"] == 2
    assert orch.board.tasks[0].status == TaskStatus.COMPLETED
    assert orch.board.tasks[0].attempts == 2


def test_cancel_stops_tasks_without_false_completion(tmp_path):
    cancel = threading.Event()

    def runner(task, _board):
        if task.id == "t1":
            cancel.set()
        return "should not count"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "coder", [], "first", 0),
        ("t2", "second", "analyst", [], "second", 0),
    )
    orch = Orchestrator(
        model=None,
        root=tmp_path,
        workers=2,
        planner=planner,
        runner=runner,
        cancel=cancel,
        persist=False,
    )
    orch.run("cancel goal")
    assert all(task.status != TaskStatus.COMPLETED for task in orch.board.tasks)
    assert {task.status for task in orch.board.tasks} <= {TaskStatus.CANCELLED, TaskStatus.BLOCKED}


def test_resume_skips_completed_tasks(tmp_path):
    runs = []

    def first_runner(task, _board):
        runs.append(task.id)
        if task.id == "t2":
            raise RuntimeError("needs resume")
        return "done 1"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "coder", [], "first", 0),
        ("t2", "second", "tester", [], "second", 0),
    )
    task_dir = tmp_path / "tasks"
    orch = Orchestrator(
        model=None,
        root=tmp_path,
        workers=1,
        planner=planner,
        runner=first_runner,
        persist=True,
        task_dir=task_dir,
    )
    orch.run("resume goal")
    run_id = orch.board.run_id
    assert board_after(orch)["t1"].status == TaskStatus.COMPLETED
    assert board_after(orch)["t2"].status == TaskStatus.FAILED

    resumed = []

    def second_runner(task, _board):
        resumed.append(task.id)
        return "done 2"

    again = Orchestrator(
        model=None,
        root=tmp_path,
        workers=1,
        runner=second_runner,
        persist=True,
        task_dir=task_dir,
    )
    again.run("", resume=run_id)
    assert board_after(again)["t1"].status == TaskStatus.COMPLETED
    assert board_after(again)["t2"].status == TaskStatus.COMPLETED
    assert "t1" not in resumed
    assert resumed == ["t2"]


def test_merge_falls_back_without_a_model(tmp_path):
    def runner(task, _board):
        return f"result {task.id}"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "coder", [], "first", 0),
        ("t2", "second", "tester", [], "second", 0),
    )
    orch = Orchestrator(model=None, root=tmp_path, planner=planner, runner=runner, persist=False)
    final = orch.run("merge goal")
    assert "first" in final and "result t1" in final
    assert "second" in final and "result t2" in final


def test_events_are_structured_and_redacted(tmp_path):
    events = []

    def runner(task, _board):
        if task.id == "t2":
            raise RuntimeError("bad key sk-abcdef1234567890")
        return "ok"

    planner = lambda goal, limit: specs(  # noqa: E731
        ("t1", "first", "coder", [], "first", 0),
        ("t2", "second", "tester", [], "second", 0),
    )
    orch = Orchestrator(
        model=None,
        root=tmp_path,
        workers=1,
        planner=planner,
        runner=runner,
        on_event=events.append,
        persist=False,
    )
    orch.run("event goal")
    names = [event["event"] for event in events]
    assert "plan" in names and "task.start" in names
    assert "task.complete" in names and "task.fail" in names
    joined = json.dumps(events)
    assert "sk-abcdef1234567890" not in joined
    assert "sk-****7890" in joined
    assert all("run_id" in event for event in events)
