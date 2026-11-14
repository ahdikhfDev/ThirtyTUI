"""Benchmark runner: disposable workspaces, real checks, trials, persistence.

Everything runs offline: local tasks exercise the same code path the real
adapter uses (workspace setup, file checks, check command, cleanup) without
calling a provider.
"""

import sys
import threading
import time

import pytest

from thirtycli.benchmarks.adapters import AdapterInfo
from thirtycli.benchmarks.models import RunConfig, TaskOutcome, TaskSpec
from thirtycli.benchmarks.runner import BenchmarkRunner, parse_suite_names, run_suites
from thirtycli.benchmarks.storage import load_run, runs_dir, work_dir


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    monkeypatch.delenv("THIRTYCLI_BENCH_KEEP", raising=False)
    return tmp_path


class FakeAdapter:
    """A stand-in for the real adapter: no model, no network, no CLI."""

    name = "fake"

    def __init__(self, available: bool = True, reason: str = "", outcome=None) -> None:
        self.available = available
        self.reason = reason
        self.outcome = outcome
        self.calls = 0

    def info(self) -> AdapterInfo:
        return AdapterInfo(name=self.name, available=self.available, reason=self.reason)

    def run(self, task: TaskSpec, workspace, timeout: int) -> TaskOutcome:
        self.calls += 1
        if self.outcome is not None:
            return self.outcome
        (workspace / "answer.txt").write_text("done\n", encoding="utf-8")
        return TaskOutcome(success=True, output="wrote answer.txt", verified=True)


def spec(task_id: str = "t1", **overrides) -> TaskSpec:
    data = {"id": task_id, "suite": "agents"}
    data.update(overrides)
    return TaskSpec(**data)


def config(**overrides) -> RunConfig:
    data = {"suite": "agents", "cli": "fake", "model": "", "provider": ""}
    data.update(overrides)
    return RunConfig(**data)


def run_one(task: TaskSpec, monkeypatch, adapter=None, persist=True, **cfg) -> tuple:
    monkeypatch.setattr("thirtycli.benchmarks.runner.tasks_for", lambda _suite: [task])
    runner = BenchmarkRunner(config(**cfg), adapter or FakeAdapter(), persist=persist)
    return runner.run(), runner


def test_success_runs_checks_and_persists(tmp_path, monkeypatch):
    def solve(workspace, _config, _timeout):
        (workspace / "out.txt").write_text("hello\n", encoding="utf-8")
        return TaskOutcome(success=True, output="created out.txt", metrics={"note": "local"})

    task = spec(
        expected_files=("out.txt",),
        check_command=f'"{sys.executable}" -c "print(1)"',
        local_runner=solve,
    )
    run, _ = run_one(task, monkeypatch)

    assert run.tasks[0].success is True
    assert run.tasks[0].verified is True
    assert run.tasks[0].checks["files"]["ok"] is True
    assert run.tasks[0].checks["command"]["ok"] is True
    assert run.tasks[0].metrics == {"note": "local"}
    assert run.summary["success_rate"] == 1.0

    saved = load_run(run.run_id)
    assert saved.suite == "agents"
    assert saved.tasks[0].task == "t1"
    assert saved.tasks[0].checks["files"]["expected"] == ["out.txt"]


def test_missing_expected_file_fails_the_task(monkeypatch):
    def solve(_workspace, _config, _timeout):
        return TaskOutcome(success=True, output="trust me")

    run, _ = run_one(spec(expected_files=("needed.txt",), local_runner=solve), monkeypatch)

    task = run.tasks[0]
    assert task.success is False
    assert task.verified is False
    assert "expected files missing" in task.error
    assert task.checks["files"]["missing"] == ["needed.txt"]


def test_failing_check_command_fails_the_task(monkeypatch):
    def solve(workspace, _config, _timeout):
        (workspace / "out.txt").write_text("ok", encoding="utf-8")
        return TaskOutcome(success=True)

    task = spec(
        expected_files=("out.txt",),
        check_command=f'"{sys.executable}" -c "raise SystemExit(3)"',
        local_runner=solve,
    )
    run, _ = run_one(task, monkeypatch)

    assert run.tasks[0].success is False
    assert run.tasks[0].error == "check command failed"
    assert run.tasks[0].exit_code == 3


def test_runner_captures_a_raised_exception(monkeypatch):
    def explode(_workspace, _config, _timeout):
        raise RuntimeError("worker blew up")

    run, _ = run_one(spec(local_runner=explode), monkeypatch)

    assert len(run.tasks) == 1
    assert run.tasks[0].success is False
    assert "worker blew up" in run.tasks[0].error
    assert run.summary["errors"] == 1


def test_skipped_tasks_are_excluded_from_rates(monkeypatch):
    def no_model(_workspace, _config, _timeout):
        return TaskOutcome(success=False, skipped=True, skip_reason="no key for fake")

    run, _ = run_one(spec(local_runner=no_model), monkeypatch)

    assert run.tasks[0].skipped is True
    assert run.tasks[0].skip_reason == "no key for fake"
    assert run.summary["ran"] == 0
    assert run.summary["skipped"] == 1
    assert run.summary["success_rate"] == 0.0
    assert run.summary["skip_reasons"] == ["no key for fake"]


def test_unavailable_adapter_reports_a_skip(monkeypatch):
    adapter = FakeAdapter(available=False, reason="no model configured")
    run, _ = run_one(spec(), monkeypatch, adapter=adapter, persist=False)

    assert run.tasks[0].skipped is True
    assert "no model configured" in run.tasks[0].skip_reason
    assert adapter.calls == 0


def test_trials_produce_one_result_each(monkeypatch):
    run, _ = run_one(spec(local_runner=lambda *_args: TaskOutcome(success=True)), monkeypatch, trials=3)

    assert sorted(task.trial for task in run.tasks) == [1, 2, 3]
    assert run.summary["tasks"] == 3


def test_persist_false_writes_nothing(monkeypatch):
    run, _ = run_one(
        spec(local_runner=lambda *_args: TaskOutcome(success=True)), monkeypatch, persist=False
    )

    assert run.summary["success_rate"] == 1.0
    assert not list(runs_dir().glob("*.json"))


def test_workspace_is_removed_after_each_task(monkeypatch):
    seen = []

    def keep_path(workspace, _config, _timeout):
        seen.append(workspace)
        (workspace / "scratch.txt").write_text("temporary", encoding="utf-8")
        return TaskOutcome(success=True)

    run_one(spec(local_runner=keep_path), monkeypatch, persist=False)

    assert seen and not seen[0].exists()
    assert not [path for path in work_dir().iterdir() if path.is_dir()]


def test_workspace_is_kept_when_requested(monkeypatch):
    seen = []

    def keep_path(workspace, _config, _timeout):
        seen.append(workspace)
        return TaskOutcome(success=True)

    monkeypatch.setenv("THIRTYCLI_BENCH_KEEP", "1")
    run_one(spec(local_runner=keep_path), monkeypatch, persist=False)

    try:
        assert seen[0].is_dir()
    finally:
        import shutil

        shutil.rmtree(seen[0], ignore_errors=True)


def test_tasks_run_concurrently(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()

    def slow(_workspace, _config, _timeout):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.2)
        with lock:
            active -= 1
        return TaskOutcome(success=True)

    first, second = spec("a", local_runner=slow), spec("b", local_runner=slow)
    monkeypatch.setattr("thirtycli.benchmarks.runner.tasks_for", lambda _suite: [first, second])
    started = time.perf_counter()
    run = BenchmarkRunner(config(concurrency=2), FakeAdapter(), persist=False).run()
    elapsed = time.perf_counter() - started

    assert peak == 2
    assert len(run.tasks) == 2
    assert elapsed < 0.6


def test_parse_suite_names():
    assert parse_suite_names("all") == [
        "coding",
        "agents",
        "performance",
        "cost",
        "reliability",
        "security",
    ]
    assert parse_suite_names("agents, security") == ["agents", "security"]
    with pytest.raises(KeyError):
        parse_suite_names("agents,nope")


def test_run_suites_uses_the_suite_task_list(monkeypatch):
    monkeypatch.setattr(
        "thirtycli.benchmarks.runner.tasks_for",
        lambda suite: [spec(f"{suite}-1", local_runner=lambda *_args: TaskOutcome(success=True))],
    )
    runs = run_suites(["agents", "security"], FakeAdapter(), config(), persist=False)

    assert [run.suite for run in runs] == ["agents", "security"]
    assert [task.task for run in runs for task in run.tasks] == ["agents-1", "security-1"]


def test_security_suite_passes_real_policy_checks(monkeypatch):
    run = run_suites(["security"], FakeAdapter(), config(), persist=False)[0]

    assert len(run.tasks) == 7
    assert all(task.success for task in run.tasks), [task.error for task in run.tasks]
    assert run.summary["success_rate"] == 1.0
    assert "reason" in run.tasks[0].metrics


def test_reliability_suite_executes_for_real(monkeypatch):
    run = run_suites(["reliability"], FakeAdapter(), config(), persist=False)[0]

    assert len(run.tasks) == 3
    assert all(task.success for task in run.tasks), [task.error for task in run.tasks]


def test_agent_suite_exercises_the_scheduler(monkeypatch):
    run = run_suites(["agents"], FakeAdapter(), config(concurrency=3), persist=False)[0]

    assert len(run.tasks) == 6
    assert all(task.success and task.verified for task in run.tasks), [
        (task.task, task.error) for task in run.tasks if not task.success
    ]
    assert run.summary["verified_rate"] == 1.0
