"""Multi-agent scaling harness: one fixed 8-task DAG, several worker counts.

The DAG is handed to the orchestrator by a fixed planner, so every point measures
scheduling and tool execution instead of planning luck. Each point runs inside a
fresh throwaway directory: the real repository is never read or written.

The task graph is six independent modules, one join that imports them all and one
final marker file. Nothing in the graph overlaps a file path, so a conflict count
above zero is a genuine finding rather than a benchmark artefact.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..orchestrator import Orchestrator, Task, TaskStatus
from ..pricing import estimate, price_for
from ..secrets import redact

MOD_COUNT = 6
# The six modules return 1..6, which sum to 21; TOTAL is the fixed join target.
MODULE_SUM = sum(range(1, MOD_COUNT + 1))
TOTAL_VALUE = 42
GOAL = "Write six independent modules, join them into summary.py, then write DONE.md."

FINISH_EVENTS = {"task.complete", "task.fail", "task.cancel"}
VERIFIED = {"verified", "verified_with_warnings"}
# Event timestamps are rounded to milliseconds, so compare with a small slack.
CLOCK_TOLERANCE = 0.002


# --------------------------------------------------------------------- reporting


@dataclass
class ScalingPoint:
    """One worker count, measured once."""

    workers: int
    wall: float
    tasks_completed: int
    tasks_total: int
    failed: int = 0
    blocked: int = 0
    conflicts: int = 0
    verification_failures: int = 0
    tokens: dict = field(default_factory=dict)
    cost: float | None = None
    speedup: float = 0.0
    utilization: float = 0.0
    dependency_violations: int = 0
    retries: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True only when every task finished and none failed or was blocked."""
        return (
            self.tasks_completed >= self.tasks_total
            and self.failed == 0
            and self.blocked == 0
        )

    def to_dict(self) -> dict:
        return {
            "workers": self.workers,
            "wall": self.wall,
            "tasks_completed": self.tasks_completed,
            "tasks_total": self.tasks_total,
            "complete": self.complete,
            "failed": self.failed,
            "blocked": self.blocked,
            "conflicts": self.conflicts,
            "verification_failures": self.verification_failures,
            "tokens": dict(self.tokens),
            "cost": self.cost,
            "speedup": self.speedup,
            "utilization": self.utilization,
            "dependency_violations": self.dependency_violations,
            "retries": self.retries,
            "errors": list(self.errors),
        }


@dataclass
class ScalingReport:
    model: str = ""
    provider: str = ""
    points: list[ScalingPoint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None

    @property
    def duration(self) -> float:
        return max(0.0, (self.finished or time.time()) - self.started)


# --------------------------------------------------------------------- task graph


def scaling_tasks() -> list[dict]:
    """The fixed planner payload: ids are stable across points and runs."""
    tasks: list[dict] = []
    for n in range(1, MOD_COUNT + 1):
        tasks.append(
            {
                "id": f"mod{n}",
                "title": f"Write mod_{n}.py",
                "description": (
                    f"Create the file mod_{n}.py with write_file. Content, exactly:\n"
                    f"def value() -> int:\n    return {n}\n"
                    "Write nothing else and do not touch other files."
                ),
                "agent": "coder",
                "depends_on": [],
                "max_retries": 1,
            }
        )
    tasks.append(
        {
            "id": "join",
            "title": "Write summary.py",
            "description": (
                "Create summary.py with write_file. It imports mod_1..mod_6 "
                "(from mod_<n> import value as v<n>), adds their value() results into "
                f"MODULE_SUM ({MODULE_SUM}) and sets TOTAL = {TOTAL_VALUE} "
                "(the join target, not the raw sum). Path: summary.py"
            ),
            "agent": "coder",
            "depends_on": [f"mod{n}" for n in range(1, MOD_COUNT + 1)],
            "max_retries": 1,
        }
    )
    tasks.append(
        {
            "id": "done",
            "title": "Write DONE.md",
            "description": (
                f"Create DONE.md with write_file containing the single line TOTAL={TOTAL_VALUE}. "
                "Path: DONE.md"
            ),
            "agent": "coder",
            "depends_on": ["join"],
            "max_retries": 1,
        }
    )
    return tasks


# ------------------------------------------------------------------- measurement


def _dependency_violations(tasks: list[Task], events: list[dict]) -> int:
    """Count tasks that started before one of their dependencies finished.

    Starts and finishes come from the orchestrator's own event stream (first
    ``task.start``, last finish event per task). When an event has no usable
    timestamp the board's ``started``/``finished`` fields stand in, which is only
    as precise as those floats.
    """
    starts: dict[str, float] = {}
    finishes: dict[str, float] = {}
    for payload in events:
        task_id = payload.get("task")
        stamp = payload.get("ts")
        if not task_id or not isinstance(stamp, (int, float)):
            continue
        kind = payload.get("event")
        if kind == "task.start":
            starts.setdefault(str(task_id), float(stamp))
        elif kind in FINISH_EVENTS:
            finishes[str(task_id)] = float(stamp)

    known = {task.id: task for task in tasks}
    violations = 0
    for task in tasks:
        began = starts.get(task.id)
        if began is None:
            began = task.started
        if began is None:
            continue
        for dep_id in task.depends_on:
            dep = known.get(dep_id)
            ended = finishes.get(dep_id)
            if ended is None and dep is not None:
                ended = dep.finished
            if ended is not None and ended > began + CLOCK_TOLERANCE:
                violations += 1
    return violations


def _count_verification_failures(tasks: list[Task]) -> int:
    """Tasks whose verification ran and did not come back verified.

    Tasks that never reached verification (cancelled, blocked) carry no status and
    are counted as incomplete instead of as verification failures.
    """
    failures = 0
    for task in tasks:
        status = (task.verification or {}).get("status")
        if status and status not in VERIFIED:
            failures += 1
    return failures


def _run_point(
    model,
    root: Path,
    workers: int,
    timeout: float | None,
    keep: bool,
    model_name: str,
    on_event,
    notes: list[str],
) -> ScalingPoint:
    workdir = Path(tempfile.mkdtemp(prefix=f"scaling-w{workers}-", dir=str(root)))
    try:
        events: list[dict] = []

        def record(payload: dict) -> None:
            events.append(payload)
            if on_event is not None:
                try:
                    on_event(payload)
                except Exception:
                    pass  # a broken observer must not fail the measurement

        cancel = threading.Event()
        orch = Orchestrator(
            model=model,
            root=workdir,
            workers=workers,
            max_tasks=len(scaling_tasks()),
            max_retries=1,
            planner=lambda _goal, _limit: scaling_tasks(),
            on_event=record,
            cancel=cancel,
            persist=False,
            coding=True,
            permission="autonomous",
            approver=lambda _question: True,
            route=False,
            memory=False,
            verify=True,
            verify_checks=False,
        )

        timer: threading.Timer | None = None
        if timeout and timeout > 0:
            timer = threading.Timer(float(timeout), cancel.set)
            timer.daemon = True
            timer.start()

        errors: list[str] = []
        began = time.perf_counter()
        try:
            orch.run(GOAL)
        except Exception as exc:  # one bad point must not lose the whole report
            errors.append(redact(f"orchestrator raised: {exc}"))
        wall = time.perf_counter() - began
        if timer is not None:
            timer.cancel()

        board = orch.board
        tasks = list(board.tasks) if board is not None else []
        total = len(tasks) or len(scaling_tasks())
        completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
        failed = sum(
            1
            for task in tasks
            if task.status in {TaskStatus.FAILED, TaskStatus.VERIFICATION_FAILED}
        )
        blocked = sum(1 for task in tasks if task.status == TaskStatus.BLOCKED)
        task_seconds = sum(task.duration for task in tasks)
        utilization = 0.0
        if wall > 0 and workers > 0:
            utilization = min(1.0, task_seconds / (wall * workers))

        tokens: dict = {}
        if board is not None:
            tokens = {str(key): int(value) for key, value in (board.usage or {}).items()}
        cost = estimate(model_name, tokens) if model_name else None

        if cancel.is_set() and completed < total:
            errors.append(
                redact(
                    f"timeout: cancelled after {float(timeout or 0):g}s with "
                    f"{completed}/{total} tasks completed"
                )
            )
        for task in tasks:
            if task.error:
                errors.append(redact(f"{task.id}: {task.error}"))

        return ScalingPoint(
            workers=workers,
            wall=wall,
            tasks_completed=completed,
            tasks_total=total,
            failed=failed,
            blocked=blocked,
            conflicts=len(board.conflicts) if board is not None else 0,
            verification_failures=_count_verification_failures(tasks),
            tokens=tokens,
            cost=cost,
            utilization=round(utilization, 3),
            dependency_violations=_dependency_violations(tasks, events),
            retries=sum(1 for task in tasks if task.attempts > 1),
            errors=list(dict.fromkeys(errors)),
        )
    finally:
        if keep:
            notes.append(f"workspace kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def run_scaling(
    model,
    root,
    workers=(1, 2, 4, 6),
    timeout=900,
    keep=False,
    model_name="",
    provider="",
    on_event=None,
) -> ScalingReport:
    """Run the fixed DAG once per worker count and report every point honestly."""
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    report = ScalingReport(model=model_name, provider=provider, started=time.time())
    notes = report.notes
    counts = [max(1, int(count)) for count in workers] or [1]

    points: list[ScalingPoint] = []
    for count in counts:
        try:
            point = _run_point(model, base, count, timeout, keep, model_name, on_event, notes)
        except Exception as exc:  # defensive: keep the earlier points
            message = redact(f"workers={count}: point failed: {exc}")
            notes.append(message)
            point = ScalingPoint(
                workers=count,
                wall=0.0,
                tasks_completed=0,
                tasks_total=len(scaling_tasks()),
                errors=[message],
            )
        points.append(point)

    baseline = next((point for point in points if point.workers == 1), points[0])
    if len(points) > 1 and baseline.workers != 1:
        notes.append(
            f"no 1-worker point in this run; speedup is relative to workers={baseline.workers}"
        )
    for point in points:
        if baseline.wall > 0 and point.wall > 0:
            point.speedup = round(baseline.wall / point.wall, 3)
        if any(error.startswith("timeout:") for error in point.errors):
            notes.append(
                f"workers={point.workers}: cancelled by the timeout after {float(timeout or 0):g}s; "
                "the point is incomplete"
            )

    if not model_name:
        notes.append("cost unavailable: no model name supplied")
    elif all(point.cost is None for point in points):
        if price_for(model_name) is None:
            notes.append(f"cost unavailable: no pricing for model {model_name}")
        else:
            notes.append("cost unavailable: no token usage was reported by the model")
    if any(point.tokens for point in points):
        notes.append("tokens cover sub-agent turns; the final merge call is not counted")

    report.points = points
    report.finished = time.time()
    return report


# ------------------------------------------------------------------------ output


def render_scaling(report: ScalingReport) -> str:
    """ASCII table: workers, wall, done, speedup, utilization, tokens, cost."""
    lines = [f"Scaling: model={report.model or '-'} provider={report.provider or '-'}", ""]
    lines.append(
        f"{'workers':>7}  {'wall(s)':>8}  {'done':>7}  {'speedup':>7}  {'util':>5}  "
        f"{'conflicts':>9}  {'verify_fail':>11}  {'tokens':>8}  {'cost':>10}"
    )
    for point in report.points:
        done = f"{point.tasks_completed}/{point.tasks_total}"
        cost = "-" if point.cost is None else f"{point.cost:.6f}"
        tokens = int(point.tokens.get("total", 0) or 0)
        lines.append(
            f"{point.workers:>7}  {point.wall:>8.2f}  {done:>7}  {point.speedup:>7.2f}  "
            f"{point.utilization:>5.2f}  {point.conflicts:>9}  "
            f"{point.verification_failures:>11}  {tokens:>8}  {cost:>10}"
        )

    incomplete = [point for point in report.points if not point.complete]
    if incomplete:
        lines.append("")
        for point in incomplete:
            lines.append(
                f"incomplete: workers={point.workers} ({point.tasks_completed}/"
                f"{point.tasks_total} tasks, {point.failed} failed, {point.blocked} blocked)"
            )
    if report.notes:
        lines.append("")
        for note in report.notes:
            lines.append(f"note: {note}")

    lines.append("")
    lines.append(
        "Measurements only compare scheduling for the same model and provider; "
        "repeat a point before trusting a difference."
    )
    return "\n".join(lines) + "\n"


def render_scaling_json(report: ScalingReport) -> dict:
    """Machine-readable export, same fields as the table plus the raw numbers."""
    return {
        "model": report.model,
        "provider": report.provider,
        "started": report.started,
        "finished": report.finished,
        "notes": list(report.notes),
        "points": [point.to_dict() for point in report.points],
    }
