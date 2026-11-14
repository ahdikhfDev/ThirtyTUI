"""Real benchmark suites for ThirtyCLI.

Coding tasks need a model and are skipped honestly when none is configured.
The agents/performance/reliability/security suites execute the real scheduler,
permission policy, change tracker and sandbox helpers in disposable workspaces;
they do not call a model and do not invent results.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path

from ..changes import WorkspaceTracker
from ..config import Config
from ..models import ModelError, build_model
from ..orchestrator import Orchestrator, TaskStatus
from ..permissions import PermissionLevel, PermissionPolicy, check_command
from ..sandbox import ProcessSandbox, capability_report, restricted_env
from ..secrets import redact
from ..sessions import Session
from ..tools import build_tools, tool_map
from .datasets import DatasetUnavailable, datasets, internal_tasks, load_dataset
from .models import RunConfig, TaskOutcome, TaskSpec

SUITE_NAMES = ("coding", "agents", "performance", "cost", "reliability", "security")


def _outcome(success: bool, **kwargs) -> TaskOutcome:
    return TaskOutcome(success=success, **kwargs)


# --------------------------------------------------------------------- coding


def coding_tasks() -> list[TaskSpec]:
    tasks = list(internal_tasks())
    for info in datasets():
        if info.name in {"internal", "swebench", "terminalbench", "polyglot"}:
            continue
        if not info.available:
            continue
        try:
            tasks.extend(load_dataset(info.name))
        except DatasetUnavailable:
            continue
    for task in tasks:
        task.suite = "coding"
    return tasks


def cost_tasks() -> list[TaskSpec]:
    tasks = coding_tasks()[:1]
    for task in tasks:
        task.suite = "cost"
    return tasks


# --------------------------------------------------------------------- agents


def _agent_parallel(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {"id": "t1", "title": "left", "agent": "explorer", "depends_on": []},
            {"id": "t2", "title": "right", "agent": "analyst", "depends_on": []},
        ]

    def runner(_task, _board) -> str:
        time.sleep(0.08)
        return "ok"

    def run(workers: int) -> tuple[float, bool]:
        started = time.perf_counter()
        orch = Orchestrator(
            model=None,
            root=workspace,
            workers=workers,
            planner=planner,
            runner=runner,
            persist=False,
            verify=False,
            memory=False,
        )
        orch.run("parallel benchmark")
        elapsed = time.perf_counter() - started
        return elapsed, all(task.status == TaskStatus.COMPLETED for task in orch.board.tasks)

    sequential, ok_sequential = run(1)
    parallel, ok_parallel = run(2)
    speedup = sequential / parallel if parallel else 0.0
    success = bool(ok_sequential and ok_parallel and speedup >= 1.15)
    return _outcome(
        success,
        metrics={
            "sequential_s": round(sequential, 3),
            "parallel_s": round(parallel, 3),
            "speedup": round(speedup, 2),
        },
        error="" if success else "parallel run was not faster than the sequential baseline",
    )


def _agent_dependencies(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    order: list[str] = []

    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {"id": "t1", "title": "first", "agent": "explorer", "depends_on": []},
            {"id": "t2", "title": "second", "agent": "coder", "depends_on": ["t1"]},
        ]

    def runner(task, board) -> str:
        if task.id == "t2" and board.get("t1").status != TaskStatus.COMPLETED:
            raise RuntimeError("dependency ran before its prerequisite")
        order.append(task.id)
        return task.id

    orch = Orchestrator(
        model=None,
        root=workspace,
        workers=2,
        planner=planner,
        runner=runner,
        persist=False,
        verify=False,
        memory=False,
    )
    orch.run("dependency benchmark")
    success = order == ["t1", "t2"]
    return _outcome(success, metrics={"order": order})


def _agent_conflict(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    (workspace / "shared.txt").write_text("original\n", encoding="utf-8")
    tracker = WorkspaceTracker(workspace)
    first = tracker.check_write("worker-a", "shared.txt")
    second = tracker.check_write("worker-b", "shared.txt", lambda _a, _b: False)
    success = first is None and second is not None
    return _outcome(
        success,
        metrics={"first_write": first, "second_write": second},
        error="" if success else "conflicting write was not denied",
    )


def _agent_verification(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    def planner(_goal: str, _limit: int) -> list[dict]:
        return [{"id": "t1", "title": "fix auth", "description": "fix auth", "agent": "coder", "depends_on": []}]

    orch = Orchestrator(
        model=None,
        root=workspace,
        workers=1,
        planner=planner,
        runner=lambda _task, _board: "claimed success",
        persist=False,
        verify=True,
        memory=False,
    )
    orch.run("verification benchmark")
    task = orch.board.tasks[0]
    success = task.status == TaskStatus.VERIFICATION_FAILED
    return _outcome(
        success,
        metrics={"status": task.status.value, "verification": task.verification.get("status")},
        error="" if success else "a claim without file changes was accepted",
    )


def _agent_retry(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    calls = {"count": 0}

    def runner(_task, _board) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient failure")
        return "recovered"

    def planner(_goal: str, _limit: int) -> list[dict]:
        return [{"id": "t1", "title": "retry", "agent": "coder", "depends_on": [], "max_retries": 1}]

    orch = Orchestrator(
        model=None,
        root=workspace,
        workers=1,
        planner=planner,
        runner=runner,
        persist=False,
        verify=False,
        memory=False,
    )
    orch.run("retry benchmark")
    task = orch.board.tasks[0]
    success = task.status == TaskStatus.COMPLETED and task.attempts == 2
    return _outcome(success, metrics={"attempts": task.attempts, "calls": calls["count"]})


def _agent_cancel(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    cancel = threading.Event()

    def runner(_task, _board) -> str:
        cancel.set()
        return "should not count"

    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {"id": "t1", "title": "one", "agent": "explorer", "depends_on": []},
            {"id": "t2", "title": "two", "agent": "analyst", "depends_on": []},
        ]

    orch = Orchestrator(
        model=None,
        root=workspace,
        workers=2,
        planner=planner,
        runner=runner,
        cancel=cancel,
        persist=False,
        verify=False,
        memory=False,
    )
    orch.run("cancel benchmark")
    success = all(task.status != TaskStatus.COMPLETED for task in orch.board.tasks)
    return _outcome(success, metrics={"statuses": [task.status.value for task in orch.board.tasks]})


def agent_tasks() -> list[TaskSpec]:
    return [
        TaskSpec("parallel-speedup", "agents", "two independent tasks", local_runner=_agent_parallel, timeout=60),
        TaskSpec("dependency-order", "agents", "dependent task waits", local_runner=_agent_dependencies, timeout=60),
        TaskSpec("conflict-deny", "agents", "unrelated workers share a file", local_runner=_agent_conflict, timeout=30),
        TaskSpec("verification-gate", "agents", "claim without changes", local_runner=_agent_verification, timeout=30),
        TaskSpec("retry-recovery", "agents", "transient failure retries", local_runner=_agent_retry, timeout=30),
        TaskSpec("cancellation", "agents", "cancel active run", local_runner=_agent_cancel, timeout=30),
    ]


# ---------------------------------------------------------------- performance


def _perf_startup(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-m", "thirtycli", "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        samples.append((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            return _outcome(False, error=proc.stderr[:400])
    average = sum(samples) / len(samples)
    return _outcome(True, metrics={"startup_ms": round(average, 1), "samples": [round(s, 1) for s in samples]})


def _perf_import(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c", "import thirtycli"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        samples.append((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            return _outcome(False, error=proc.stderr[:400])
    return _outcome(
        True,
        metrics={"import_ms": round(sum(samples) / len(samples), 1), "samples": [round(s, 1) for s in samples]},
    )


def _perf_tool(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    tools = tool_map(build_tools(workspace, allow_shell=False))
    tool = tools["now"]
    started = time.perf_counter()
    for _ in range(50):
        tool.invoke({})
    elapsed = (time.perf_counter() - started) * 1000
    return _outcome(True, metrics={"tool_ms": round(elapsed, 2), "calls": 50, "per_call_ms": round(elapsed / 50, 3)})


def _perf_memory(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {"id": "t1", "title": "one", "agent": "explorer", "depends_on": []},
            {"id": "t2", "title": "two", "agent": "analyst", "depends_on": []},
        ]

    tracemalloc.start()
    started = time.process_time()
    orch = Orchestrator(
        model=None,
        root=workspace,
        workers=2,
        planner=planner,
        runner=lambda _task, _board: "ok",
        persist=False,
        verify=False,
        memory=False,
    )
    orch.run("memory benchmark")
    cpu = time.process_time() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ok = all(task.status == TaskStatus.COMPLETED for task in orch.board.tasks)
    return _outcome(
        ok,
        metrics={"peak_mb": round(peak / 1_048_576, 2), "cpu_s": round(cpu, 3)},
    )


def performance_tasks() -> list[TaskSpec]:
    return [
        TaskSpec("startup-latency", "performance", "CLI startup", local_runner=_perf_startup, timeout=60),
        TaskSpec("import-latency", "performance", "import thirtycli", local_runner=_perf_import, timeout=60),
        TaskSpec("tool-latency", "performance", "50 tool calls", local_runner=_perf_tool, timeout=30),
        TaskSpec("memory-cpu", "performance", "scheduler memory and CPU", local_runner=_perf_memory, timeout=30),
        TaskSpec(
            "first-token",
            "performance",
            "first model token",
            local_runner=lambda workspace, config, timeout: _outcome(
                False,
                skipped=True,
                skip_reason="first-token latency requires a provider/model; run with a configured model",
            ),
            timeout=30,
        ),
    ]


# ---------------------------------------------------------------- reliability


def _rel_session(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    session = Session(model="benchmark")
    session.add("user", "remember this")
    session.add("assistant", "remembered")
    session.save()
    again = Session.load(session.id)
    ok = [item["content"] for item in again.messages] == ["remember this", "remembered"]
    return _outcome(ok, metrics={"session": session.id, "messages": len(again.messages)})


def _rel_provider_error(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    try:
        build_model(Config(provider="not-a-real-provider", model="none"))
    except ModelError as exc:
        return _outcome(True, metrics={"error": redact(str(exc))[:120]})
    return _outcome(False, error="invalid provider did not raise ModelError")


def _rel_resume(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    task_dir = workspace / "tasks"
    runs: list[str] = []

    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {"id": "t1", "title": "first", "agent": "explorer", "depends_on": [], "max_retries": 0},
            {"id": "t2", "title": "second", "agent": "coder", "depends_on": [], "max_retries": 0},
        ]

    def first_runner(task, _board) -> str:
        if task.id == "t2":
            raise RuntimeError("interrupted")
        return "done"

    first = Orchestrator(
        model=None,
        root=workspace,
        workers=1,
        planner=planner,
        runner=first_runner,
        persist=True,
        task_dir=task_dir,
        verify=False,
        memory=False,
    )
    first.run("resume benchmark")
    run_id = first.board.run_id

    def second_runner(task, _board) -> str:
        runs.append(task.id)
        return "done"

    again = Orchestrator(
        model=None,
        root=workspace,
        workers=1,
        runner=second_runner,
        persist=True,
        task_dir=task_dir,
        verify=False,
        memory=False,
    )
    again.run("", resume=run_id)
    statuses = {task.id: task.status for task in again.board.tasks}
    ok = statuses["t1"] == TaskStatus.COMPLETED and statuses["t2"] == TaskStatus.COMPLETED and runs == ["t2"]
    return _outcome(ok, metrics={"rerun": runs, "statuses": {key: value.value for key, value in statuses.items()}})


def reliability_tasks() -> list[TaskSpec]:
    return [
        TaskSpec("session-recovery", "reliability", "save and reload a session", local_runner=_rel_session, timeout=30),
        TaskSpec("provider-error", "reliability", "invalid provider is handled", local_runner=_rel_provider_error, timeout=30),
        TaskSpec("resume-skip", "reliability", "resume does not repeat completed tasks", local_runner=_rel_resume, timeout=60),
    ]


# ------------------------------------------------------------------- security


def _sec_path(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    policy = PermissionPolicy(workspace, level=PermissionLevel.AUTONOMOUS, sandbox_available=True)
    decision = policy.check("write_file", {"path": "../escape.txt"})
    return _outcome(not decision.allowed, metrics={"reason": decision.reason})


def _sec_command(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    decision = check_command("rm -rf /")
    return _outcome(not decision.allowed, metrics={"reason": decision.reason})


def _sec_read_only(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    policy = PermissionPolicy(workspace, level=PermissionLevel.READ_ONLY, sandbox_available=True)
    decision = policy.check("write_file", {"path": "inside.txt"})
    return _outcome(not decision.allowed, metrics={"reason": decision.reason})


def _sec_approval(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    policy = PermissionPolicy(workspace, level=PermissionLevel.CONTROLLED, approver=None)
    decision = policy.authorize("write_file", {"path": "inside.txt"})
    return _outcome(not decision.allowed, metrics={"reason": decision.reason})


def _sec_secret(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    text = redact("key sk-abcdef1234567890 leaked")
    ok = "sk-abcdef1234567890" not in text
    return _outcome(ok, metrics={"redacted": text})


def _sec_env(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    name = "THIRTYCLI_BENCH_FAKE_KEY"
    previous = os.environ.get(name)
    os.environ[name] = "must-not-leak"
    try:
        env = restricted_env()
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous
    ok = name not in env
    return _outcome(ok, metrics={"removed": ok})


def _sec_sandbox(workspace: Path, config: RunConfig, timeout: int) -> TaskOutcome:
    report = capability_report()
    sandbox = ProcessSandbox(workspace, timeout=timeout)
    described = sandbox.describe()
    fields_ok = all(key in described.get("sandbox", {}) for key in ("available", "kind", "reason"))
    # Honest result: absence of OS isolation is reported, not treated as a pass.
    return _outcome(
        fields_ok,
        metrics={
            "os_isolation": report.available,
            "kind": report.kind,
            "reason": report.reason,
        },
        error="" if fields_ok else "sandbox report is missing fields",
    )


def security_tasks() -> list[TaskSpec]:
    return [
        TaskSpec("path-traversal", "security", "write outside workspace", local_runner=_sec_path, timeout=30),
        TaskSpec("dangerous-command", "security", "rm -rf / blocked", local_runner=_sec_command, timeout=30),
        TaskSpec("read-only-write", "security", "read-only worker write", local_runner=_sec_read_only, timeout=30),
        TaskSpec("approval-bypass", "security", "controlled without approver", local_runner=_sec_approval, timeout=30),
        TaskSpec("secret-redaction", "security", "API key redaction", local_runner=_sec_secret, timeout=30),
        TaskSpec("env-scrub", "security", "secret env removed", local_runner=_sec_env, timeout=30),
        TaskSpec("sandbox-honesty", "security", "sandbox capability report", local_runner=_sec_sandbox, timeout=30),
    ]


# --------------------------------------------------------------------- registry


def tasks_for(suite: str) -> list[TaskSpec]:
    if suite == "coding":
        return coding_tasks()
    if suite == "agents":
        return agent_tasks()
    if suite == "performance":
        return performance_tasks()
    if suite == "cost":
        return cost_tasks()
    if suite == "reliability":
        return reliability_tasks()
    if suite == "security":
        return security_tasks()
    raise KeyError(f"unknown benchmark suite: {suite}")
