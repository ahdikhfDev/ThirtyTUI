"""Dashboard state consumes real orchestrator events."""

from thirtycli.dashboard import DashboardState


def plan_event():
    return {
        "ts": 1.0,
        "event": "plan",
        "run_id": "run1",
        "goal": "add auth",
        "tasks": ["t1 · backend · build the API", "t2 · tester · test the API"],
    }


def test_plan_and_task_lifecycle():
    state = DashboardState()
    state.apply(plan_event())
    assert state.run_id == "run1"
    assert state.status == "running"
    assert set(state.tasks) == {"t1", "t2"}
    assert state.tasks["t1"]["agent"] == "backend"

    state.apply({"ts": 2.0, "event": "task.start", "task": "t1", "agent": "backend", "attempt": 1})
    assert state.tasks["t1"]["status"] == "running"
    assert state.counts()["running"] == 1

    state.apply({"ts": 3.0, "event": "task.verify", "task": "t1", "verification": "verified"})
    assert state.tasks["t1"]["verification"] == "verified"

    state.apply({"ts": 4.0, "event": "task.complete", "task": "t1"})
    assert state.tasks["t1"]["status"] == "completed"
    assert state.counts()["completed"] == 1


def test_completed_only_after_complete_event():
    state = DashboardState()
    state.apply(plan_event())
    state.apply({"ts": 2.0, "event": "task.start", "task": "t1"})
    assert state.tasks["t1"]["status"] != "completed"
    state.apply({"ts": 3.0, "event": "task.complete", "task": "t1"})
    assert state.tasks["t1"]["status"] == "completed"


def test_failure_retry_cancel_and_conflict():
    state = DashboardState()
    state.apply(plan_event())
    state.apply({"ts": 2.0, "event": "task.retry", "task": "t1", "error": "flaky"})
    assert state.tasks["t1"]["status"] == "waiting"
    assert state.tasks["t1"]["error"] == "flaky"
    state.apply({"ts": 3.0, "event": "task.fail", "task": "t1", "error": "boom"})
    assert state.tasks["t1"]["status"] == "failed"
    state.apply({"ts": 4.0, "event": "task.cancel", "task": "t2"})
    assert state.tasks["t2"]["status"] == "cancelled"
    state.apply({"ts": 5.0, "event": "task.conflict", "task": "t1", "error": "shared file"})
    assert state.tasks["t1"]["error"] == "shared file"
    state.apply({"ts": 6.0, "event": "run.conflicts", "conflicts": [{"path": "a.py"}]})
    assert any(item["kind"] == "conflict" for item in state.activity)


def test_route_and_finish():
    state = DashboardState()
    state.apply(plan_event())
    state.apply(
        {
            "ts": 2.0,
            "event": "route.decision",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "reason": "cheap",
            "fallback": False,
        }
    )
    assert state.routing["provider"] == "deepseek"
    state.apply({"ts": 3.0, "event": "run.finish", "status": "completed", "completed": 2})
    assert state.status == "completed"
    assert state.finished == 3.0


def test_lines_contain_required_sections_and_width():
    state = DashboardState()
    state.apply(plan_event())
    state.apply({"ts": 2.0, "event": "task.start", "task": "t1"})
    lines = state.lines(width=70)
    text = "\n".join(lines)
    for section in ("EXECUTION", "AGENTS", "TASKS", "LIVE ACTIVITY", "RESOURCES"):
        assert section in text
    assert all(len(line) <= 70 for line in lines)
    assert "[~]" in text


def test_activity_is_capped_and_unknown_events_are_ignored():
    state = DashboardState()
    for index in range(100):
        state.apply({"event": "mystery", "task": "t1", "ts": index})
    assert len(state.activity) == 60
    before = len(state.tasks)
    state.apply({"event": "task.start"})  # no task id
    assert len(state.tasks) == before
