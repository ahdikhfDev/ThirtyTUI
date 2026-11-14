"""Dependency-aware multi-agent orchestration.

The orchestrator turns a goal into a task graph, runs independent tasks in
parallel, retries failures, blocks dependents of failed tasks, persists the
board so an interrupted run can resume, and merges the results.

It is deliberately independent from the interactive chat loop: callers inject a
model/runner and an optional event callback. Nothing here is faked — a task only
becomes ``completed`` when its runner returns.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from .agent_catalog import agent_system_prompt, get_agent, select_agents
from .agents import AgentCancelled, run_subagent
from .audit import AuditLog
from .changes import WorkspaceTracker
from .config import home
from .memory import ProjectMemory
from .models import Config, build_model, route_model
from .permissions import (
    QUALITY_TOOLS,
    READ_ONLY_TOOLS,
    SHELL_TOOLS,
    WRITE_TOOLS,
    PermissionLevel,
    PermissionPolicy,
)
from .sandbox import ProcessSandbox, capability_report
from .secrets import redact
from .skills import skill_context
from .tools import build_readonly_tools, build_tools, tool_map
from .verification import VerificationResult, VerificationStatus, verify_task


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


UNFINISHED = {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.WAITING}
BAD_DEPENDENCY = {
    TaskStatus.FAILED,
    TaskStatus.VERIFICATION_FAILED,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
}


@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    agent: str = "coder"
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    output: str = ""
    error: str = ""
    attempts: int = 0
    max_retries: int = 1
    started: float | None = None
    finished: float | None = None
    verification: dict = field(default_factory=dict)
    permission: str = ""

    @property
    def duration(self) -> float:
        if self.started is None:
            return 0.0
        return max(0.0, (self.finished or time.time()) - self.started)

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "agent": self.agent,
            "depends_on": list(self.depends_on),
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "attempts": self.attempts,
            "max_retries": self.max_retries,
            "started": self.started,
            "finished": self.finished,
            "verification": self.verification,
            "permission": self.permission,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        try:
            status = TaskStatus(str(data.get("status", "pending")))
        except ValueError:
            status = TaskStatus.PENDING
        return cls(
            id=str(data.get("id") or f"t{uuid.uuid4().hex[:4]}"),
            title=str(data.get("title") or "task"),
            description=str(data.get("description") or ""),
            agent=str(data.get("agent") or "coder"),
            depends_on=[str(item) for item in data.get("depends_on") or []],
            status=status,
            output=str(data.get("output") or ""),
            error=str(data.get("error") or ""),
            attempts=int(data.get("attempts") or 0),
            max_retries=max(0, int(data.get("max_retries") or 0)),
            started=data.get("started"),
            finished=data.get("finished"),
            verification=dict(data.get("verification") or {}),
            permission=str(data.get("permission") or ""),
        )


def tasks_dir() -> Path:
    path = home() / "tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class TaskBoard:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    goal: str = ""
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    tasks: list[Task] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    final: str = ""
    conflicts: list[dict] = field(default_factory=list)
    session_id: str = ""

    def add(self, task: Task) -> Task:
        existing = {item.id for item in self.tasks}
        base = task.id or f"t{len(self.tasks) + 1}"
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        task.id = candidate
        self.tasks.append(task)
        self.updated = time.time()
        return task

    def get(self, task_id: str) -> Task | None:
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def ready(self) -> list[Task]:
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            dependencies = [self.get(dep) for dep in task.depends_on]
            if all(dep is not None and dep.status == TaskStatus.COMPLETED for dep in dependencies):
                ready.append(task)
        return ready

    def ancestors(self, task_id: str) -> set[str]:
        found: set[str] = set()
        stack = [task_id]
        while stack:
            current = self.get(stack.pop())
            if current is None:
                continue
            for dep in current.depends_on:
                if dep not in found:
                    found.add(dep)
                    stack.append(dep)
        return found

    def related(self, first: str, second: str) -> bool:
        if first == second:
            return True
        return first in self.ancestors(second) or second in self.ancestors(first)

    def refresh_blocked(self) -> None:
        """Block pending tasks whose dependency failed, was cancelled or is blocked."""
        changed = True
        while changed:
            changed = False
            for task in self.tasks:
                if task.status != TaskStatus.PENDING:
                    continue
                for dep_id in task.depends_on:
                    dep = self.get(dep_id)
                    if dep is None or dep.status in BAD_DEPENDENCY:
                        task.status = TaskStatus.BLOCKED
                        task.error = f"blocked by {dep_id}"
                        task.finished = time.time()
                        changed = True
                        break

    @property
    def completed_count(self) -> int:
        return sum(1 for task in self.tasks if task.status == TaskStatus.COMPLETED)

    def overall(self) -> TaskStatus:
        if not self.tasks:
            return TaskStatus.PENDING
        statuses = [task.status for task in self.tasks]
        if any(status in {TaskStatus.FAILED, TaskStatus.VERIFICATION_FAILED, TaskStatus.BLOCKED} for status in statuses):
            return TaskStatus.FAILED
        if all(status == TaskStatus.COMPLETED for status in statuses):
            return TaskStatus.COMPLETED
        if any(status == TaskStatus.CANCELLED for status in statuses):
            return TaskStatus.CANCELLED
        if any(status == TaskStatus.RUNNING for status in statuses):
            return TaskStatus.RUNNING
        return TaskStatus.PENDING

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "status": self.overall().value,
            "tasks": len(self.tasks),
            "completed": self.completed_count,
            "failed": sum(
                1
                for task in self.tasks
                if task.status in {TaskStatus.FAILED, TaskStatus.VERIFICATION_FAILED}
            ),
            "blocked": sum(1 for task in self.tasks if task.status == TaskStatus.BLOCKED),
            "cancelled": sum(1 for task in self.tasks if task.status == TaskStatus.CANCELLED),
            "conflicts": len(self.conflicts),
            "usage": dict(self.usage),
        }

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "created": self.created,
            "updated": self.updated,
            "status": self.overall().value,
            "tasks": [task.to_dict() for task in self.tasks],
            "usage": self.usage,
            "final": self.final,
            "conflicts": self.conflicts,
            "session_id": self.session_id,
        }

    def save(self, directory: Path | None = None) -> Path:
        self.updated = time.time()
        target = (Path(directory) if directory is not None else tasks_dir())
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{self.run_id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, run_id: str, directory: Path | None = None) -> TaskBoard:
        target = (Path(directory) if directory is not None else tasks_dir())
        path = target / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No task run '{run_id}' in {target}")
        data = json.loads(path.read_text(encoding="utf-8"))
        board = cls(
            run_id=str(data.get("run_id") or run_id),
            goal=str(data.get("goal") or ""),
            created=float(data.get("created") or time.time()),
            updated=float(data.get("updated") or time.time()),
            usage=dict(data.get("usage") or {}),
            final=str(data.get("final") or ""),
            conflicts=list(data.get("conflicts") or []),
            session_id=str(data.get("session_id") or ""),
        )
        for raw in data.get("tasks") or []:
            board.tasks.append(Task.from_dict(raw))
        return board


def list_boards(directory: Path | None = None) -> list[dict]:
    target = (Path(directory) if directory is not None else tasks_dir())
    if not target.is_dir():
        return []
    rows = []
    for path in sorted(target.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tasks = data.get("tasks") or []
        rows.append(
            {
                "run_id": data.get("run_id") or path.stem,
                "goal": data.get("goal") or "",
                "status": data.get("status") or "pending",
                "tasks": len(tasks),
                "completed": sum(1 for task in tasks if task.get("status") == "completed"),
                "session_id": data.get("session_id") or "",
                "updated": data.get("updated") or 0,
            }
        )
    return rows


PLAN_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are the planner for ThirtyCLI, a terminal coding agent. Reply with JSON only.",
        ),
        (
            "human",
            "Goal:\n{goal}\n\n"
            "Split the goal into at most {limit} concrete tasks. Return a JSON array of objects with:\n"
            '  "id": short unique id such as "t1"\n'
            '  "title": one line\n'
            '  "description": self-contained instructions for one agent\n'
            '  "agent": one of {agents}\n'
            '  "depends_on": array of ids that must finish first (use [] when independent)\n'
            '  "max_retries": 0-3\n'
            "Only reference ids that appear earlier in the array. Prefer independent tasks; "
            "use dependencies only when a later task truly needs an earlier result. "
            "Do not invent facts about the project. Keep it to {limit} tasks or fewer.",
        ),
    ]
)

MERGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You merge results from several coding agents into one answer. "
            "Be concise, keep file paths, commands and concrete facts, and say clearly "
            "which tasks failed or were skipped. Do not invent anything that is not in the results.",
        ),
        ("human", "Goal:\n{goal}\n\nTask results:\n{results}"),
    ]
)

AGENT_IDS = (
    "planner, architect, coder, backend, frontend, fullstack, database, api, devops, "
    "debugger, reviewer, security, tester, performance, refactor, docs, explorer, "
    "dependencies, analyst"
)


def parse_plan(text: str, limit: int = 6) -> list[dict]:
    """Validate a planner reply into task specs. Never raises on bad JSON."""
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    specs: list[dict] = []
    seen: list[str] = []
    for index, raw in enumerate(data[: max(1, limit)]):
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("id") or f"t{index + 1}").strip() or f"t{index + 1}"
        if task_id in seen:
            task_id = f"{task_id}-{index + 1}"
        title = str(raw.get("title") or raw.get("description") or "").strip()
        if not title:
            continue
        agent = str(raw.get("agent") or "").strip().lower()
        if get_agent(agent) is None:
            picked = select_agents(f"{title}\n{raw.get('description') or ''}", 1)
            agent = picked[0].id if picked else "coder"
        deps = [
            str(dep)
            for dep in (raw.get("depends_on") or [])
            if str(dep) in seen
        ]
        try:
            retries = max(0, min(3, int(raw.get("max_retries") or 0)))
        except (TypeError, ValueError):
            retries = 0
        specs.append(
            {
                "id": task_id,
                "title": title,
                "description": str(raw.get("description") or title).strip(),
                "agent": agent,
                "depends_on": deps,
                "max_retries": retries,
            }
        )
        seen.append(task_id)
    return specs


def plan_graph(model, goal: str, limit: int = 6) -> list[dict]:
    """Ask the model for a task graph; fall back to one safe task."""
    if model is None:
        return []
    try:
        reply = (PLAN_PROMPT | model | StrOutputParser()).invoke(
            {"goal": goal, "limit": max(1, limit), "agents": AGENT_IDS}
        )
    except Exception:
        return []
    return parse_plan(reply, limit)


def tasks_from_specs(
    specs: list[dict] | list[Task],
    goal: str,
    default_retries: int = 1,
) -> list[Task]:
    """Normalise planner output into Tasks, deduplicating ids and bad deps."""
    board = TaskBoard(goal=goal)
    for index, raw in enumerate(specs):
        if isinstance(raw, Task):
            task = raw
            task.max_retries = max(0, min(3, task.max_retries))
        else:
            title = str(raw.get("title") or raw.get("description") or goal).strip() or goal
            agent = str(raw.get("agent") or "coder").strip().lower()
            if get_agent(agent) is None:
                picked = select_agents(f"{title}\n{raw.get('description') or ''}", 1)
                agent = picked[0].id if picked else "coder"
            task = Task(
                id=str(raw.get("id") or f"t{index + 1}"),
                title=title,
                description=str(raw.get("description") or title).strip(),
                agent=agent,
                depends_on=[str(dep) for dep in (raw.get("depends_on") or [])],
                max_retries=max(0, min(3, int(raw.get("max_retries", default_retries) or 0))),
            )
        board.add(task)
    valid = {task.id for task in board.tasks}
    for task in board.tasks:
        task.depends_on = [dep for dep in dict.fromkeys(task.depends_on) if dep in valid and dep != task.id]
    if not board.tasks:
        agent = select_agents(goal, 1)
        board.add(Task(id="t1", title=goal, description=goal, agent=agent[0].id if agent else "coder"))
    return board.tasks


class Orchestrator:
    """Plan → dependency-aware parallel workers → merge, with persistence."""

    def __init__(
        self,
        model=None,
        root: Path | str = ".",
        workers: int = 3,
        max_steps: int = 4,
        max_retries: int = 1,
        max_tasks: int = 6,
        planner: Callable[[str, int], list[dict] | list[Task]] | None = None,
        runner: Callable[[Task, TaskBoard], str] | None = None,
        on_event: Callable[[dict], None] | None = None,
        cancel: threading.Event | None = None,
        persist: bool = True,
        task_dir: Path | None = None,
        cfg: Config | None = None,
        coding: bool = False,
        permission: str = "controlled",
        approver: Callable[[str], bool] | None = None,
        allow_network: bool = True,
        unsandboxed: bool = False,
        verify: bool = True,
        verify_checks: bool = False,
        verifier: Callable[..., VerificationResult] | None = None,
        route: bool = True,
        route_prefer: str = "balanced",
        allow_switch: bool = False,
        audit: AuditLog | None = None,
        tracker: WorkspaceTracker | None = None,
        memory: bool = True,
    ) -> None:
        self.model = model
        self.cfg = cfg
        self.root = Path(root).resolve()
        self.workers = max(1, int(workers))
        self.max_steps = max(1, int(max_steps))
        self.max_retries = max(0, min(3, int(max_retries)))
        self.max_tasks = max(1, min(12, int(max_tasks)))
        self.planner = planner or self._plan
        self.runner = runner or self._default_runner
        self.on_event = on_event
        self.cancel = cancel or threading.Event()
        self.persist = persist
        self.task_dir = task_dir
        self.board: TaskBoard | None = None
        self.trace: list[dict] = []
        self._usage_lock = threading.Lock()

        self.coding = bool(coding)
        try:
            self.permission_level = PermissionLevel(str(permission))
        except ValueError:
            self.permission_level = PermissionLevel.CONTROLLED
        self.approver = approver
        self.allow_network = bool(allow_network)
        self.unsandboxed = bool(unsandboxed)
        self.verify_enabled = bool(verify)
        self.verify_checks = bool(verify_checks)
        self.verifier = verifier or verify_task
        self.sandbox = ProcessSandbox(self.root, allow_network=self.allow_network)
        self.sandbox_report = capability_report()
        self.audit = audit or AuditLog()
        self.tracker = tracker if tracker is not None else (
            WorkspaceTracker(self.root) if self.coding else None
        )
        self.memory = ProjectMemory(self.root) if memory else None
        self._tools = (
            build_tools(self.root, allow_shell=True, sandbox=self.sandbox)
            if self.coding
            else build_readonly_tools(self.root)
        )
        self._tools_by_name = tool_map(self._tools)
        self._shell_lock = threading.RLock()
        self._conflict_reports: list[dict] = []
        self.route = bool(route and cfg is not None)
        self.route_prefer = route_prefer
        self.allow_switch = bool(allow_switch)
        self._model_cache: dict[tuple[str, str], object] = {}

    # -- planning ------------------------------------------------------
    def _plan(self, goal: str, limit: int) -> list[dict] | list[Task]:
        return plan_graph(self.model, goal, limit)

    # -- execution -----------------------------------------------------
    def run(self, goal: str, resume: str | None = None) -> str:
        if resume:
            board = TaskBoard.load(resume, self.task_dir)
            self._prepare_resume(board)
        else:
            specs = self.planner(goal, self.max_tasks)
            board = TaskBoard(goal=goal, tasks=tasks_from_specs(specs, goal, self.max_retries))
        self.board = board
        if not board.goal:
            board.goal = goal
        self._event(
            "plan",
            goal=board.goal,
            tasks=[f"{task.id} · {task.agent} · {task.title}" for task in board.tasks],
            resumed=bool(resume),
        )
        if self.persist:
            board.save(self.task_dir)

        self._execute(board)
        if self.tracker is not None:
            board.conflicts = self.tracker.conflicts() + self._conflict_reports
            if board.conflicts:
                self._event("run.conflicts", conflicts=board.conflicts)
        final = self._merge(board)
        board.final = final
        board.updated = time.time()
        if self.persist:
            board.save(self.task_dir)
        self._event("run.finish", status=board.overall().value, completed=board.completed_count)
        return final

    def _prepare_resume(self, board: TaskBoard) -> None:
        for task in board.tasks:
            if task.status == TaskStatus.COMPLETED:
                continue
            task.status = TaskStatus.PENDING
            task.error = ""
            task.attempts = 0
            task.started = None
            task.finished = None

    def _execute(self, board: TaskBoard) -> None:
        if not any(task.status != TaskStatus.COMPLETED for task in board.tasks):
            return
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures: dict = {}
            try:
                while True:
                    if self.cancel.is_set():
                        self._cancel_unfinished(board)
                        break
                    board.refresh_blocked()
                    for task in board.ready():
                        if len(futures) >= self.workers:
                            break
                        task.status = TaskStatus.RUNNING
                        task.started = time.time()
                        futures[pool.submit(self._run_task, board, task)] = task
                    if not futures:
                        break
                    done, _pending = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
                    for future in done:
                        task = futures.pop(future)
                        try:
                            future.result()
                        except Exception as exc:  # defensive: never lose a failure
                            task.status = TaskStatus.FAILED
                            task.error = str(exc)
                            task.finished = time.time()
                            self._event("task.fail", task, error=str(exc))
                        if self.persist:
                            board.save(self.task_dir)
            except KeyboardInterrupt:
                self.cancel.set()
                self._cancel_unfinished(board)
                if self.persist:
                    board.save(self.task_dir)
                raise
            finally:
                board.refresh_blocked()
                if self.persist:
                    board.save(self.task_dir)

    def _run_task(self, board: TaskBoard, task: Task) -> None:
        task.started = task.started or time.time()
        while True:
            if self.cancel.is_set():
                self._finish(task, TaskStatus.CANCELLED, "cancelled by the user")
                return
            task.attempts += 1
            task.status = TaskStatus.RUNNING
            began = time.time()
            self._event("task.start", task, attempt=task.attempts)
            try:
                output = self.runner(task, board)
            except AgentCancelled:
                self._finish(task, TaskStatus.CANCELLED, "cancelled by the user")
                return
            except Exception as exc:
                if self.cancel.is_set():
                    self._finish(task, TaskStatus.CANCELLED, "cancelled by the user")
                    return
                task.error = redact(str(exc))
                if task.attempts <= task.max_retries:
                    task.status = TaskStatus.WAITING
                    self._event("task.retry", task, attempt=task.attempts, error=task.error)
                    continue
                self._finish(task, TaskStatus.FAILED, task.error, began)
                return
            if self.cancel.is_set():
                self._finish(task, TaskStatus.CANCELLED, "cancelled by the user")
                return
            task.output = redact(str(output or "")).strip()
            task.error = ""
            if self.verify_enabled:
                verification = self._verify(task)
                if verification is not None and verification.status is VerificationStatus.FAILED:
                    self._finish(
                        task,
                        TaskStatus.VERIFICATION_FAILED,
                        f"verification failed: {verification.reason}",
                        began,
                    )
                    return
            self._finish(task, TaskStatus.COMPLETED, "", began)
            return

    def _finish(self, task: Task, status: TaskStatus, error: str, began: float | None = None) -> None:
        task.status = status
        task.error = redact(error)
        task.finished = time.time()
        event = {
            TaskStatus.COMPLETED: "task.complete",
            TaskStatus.FAILED: "task.fail",
            TaskStatus.VERIFICATION_FAILED: "task.fail",
            TaskStatus.CANCELLED: "task.cancel",
        }.get(status, "task.update")
        self._event(event, task, error=task.error, seconds=time.time() - (began or time.time()))

    def _cancel_unfinished(self, board: TaskBoard) -> None:
        board.refresh_blocked()
        for task in board.tasks:
            if task.status in {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.WAITING, TaskStatus.RUNNING}:
                task.status = TaskStatus.CANCELLED
                task.error = "cancelled by the user"
                task.finished = time.time()

    def _task_type(self, task: Task) -> str:
        text = f"{task.title} {task.description}".lower()
        if any(word in text for word in ("architecture", "design", "schema", "migrate")):
            return "architecture"
        if any(word in text for word in ("test", "pytest", "coverage")):
            return "testing"
        if any(word in text for word in ("review", "audit", "security", "vulnerab")):
            return "review"
        if any(word in text for word in ("docs", "readme", "documentation")):
            return "docs"
        if any(word in text for word in ("debug", "bug", "crash", "error", "fix")):
            return "debugging"
        return "coding"

    def _model_for(self, task: Task):
        if not self.route or self.cfg is None or self.model is None:
            return self.model, {}
        decision = route_model(
            self.cfg,
            task_type=self._task_type(task),
            requires_tools=bool(self._tools),
            prefer=self.route_prefer,
            allow_switch=self.allow_switch,
        )
        payload = {
            "provider": decision.provider,
            "model": decision.model,
            "reason": decision.reason,
            "fallback": decision.fallback,
            "score": decision.score,
            "task_type": self._task_type(task),
        }
        self._event("route.decision", task, **payload)
        if decision.provider == self.cfg.provider and decision.model == self.cfg.model:
            return self.model, payload
        key = (decision.provider, decision.model)
        cached = self._model_cache.get(key)
        if cached is not None:
            return cached, payload
        try:
            routed = Config(**{**self.cfg.__dict__, "provider": decision.provider, "model": decision.model})
            built = build_model(routed)
        except Exception as exc:
            self._event("route.error", task, error=str(exc))
            return self.model, payload
        self._model_cache[key] = built
        return built, payload

    def _policy(self, permission: str) -> PermissionPolicy:
        level = self.permission_level
        if str(permission).strip().lower() == "read-only":
            level = PermissionLevel.READ_ONLY
        return PermissionPolicy(
            self.root,
            level=level,
            approver=self.approver,
            allow_network=self.allow_network,
            unsandboxed=self.unsandboxed,
            sandbox_available=self.sandbox_report.available,
        )

    def _related(self, first: str, second: str) -> bool:
        return self.board.related(first, second) if self.board is not None else True

    def _audit_tool(
        self,
        task: Task,
        tool: str,
        args: dict,
        check,
        decision,
        result: str = "",
        error: str = "",
    ) -> None:
        approval = "not-needed"
        if check.approval_required:
            approval = "approved" if decision.allowed else "denied"
        self.audit.record(
            "tool",
            task_id=task.id,
            agent_id=task.agent,
            tool=tool,
            action=str(args.get("path") or args.get("command") or "")[:300],
            decision="allowed" if decision.allowed else "denied",
            approval=approval,
            result=result,
            paths=(args.get("path"),) if args.get("path") else (),
            error=error or ("" if decision.allowed else decision.reason),
        )

    def _executor_for(self, task: Task, permission: str, tools: list | None = None):
        available = tool_map(tools if tools is not None else self._tools)
        policy = self._policy(permission)

        def execute(name: str, args: dict) -> str:
            check = policy.check(name, args)
            decision = policy.authorize(name, args)
            if not decision.allowed:
                self._audit_tool(task, name, args, check, decision, result="denied")
                return f"Tool denied: {decision.reason}"
            tool = available.get(name)
            if tool is None:
                self._audit_tool(task, name, args, check, decision, error="unknown tool")
                return f"Unknown tool: {name}"
            path = str(args.get("path") or "")
            mutating = name in WRITE_TOOLS or name in SHELL_TOOLS or name in QUALITY_TOOLS
            if self.tracker is not None and mutating and path and name != "git_commit":
                conflict = self.tracker.check_write(task.id, path, self._related)
                if conflict:
                    self._conflict_reports.append(
                        {"path": path, "tasks": [task.id], "reason": conflict}
                    )
                    self._event("task.conflict", task, path=path, error=conflict)
                    self._audit_tool(task, name, args, check, decision, error=conflict)
                    return f"Tool denied: {conflict}"
            if mutating and (name in SHELL_TOOLS or name in QUALITY_TOOLS or name == "git_commit"):
                lock = self._shell_lock
            elif mutating and self.tracker is not None and path:
                lock = self.tracker.lock(path)
            else:
                lock = nullcontext()
            try:
                with lock:
                    output = str(tool.invoke(args))
                    if self.tracker is not None and mutating:
                        if name in SHELL_TOOLS or name in QUALITY_TOOLS or name == "git_commit":
                            self.tracker.capture(task.id)
                        elif path:
                            action = "delete" if name == "delete_file" else "write"
                            self.tracker.note(task.id, path, action)
            except Exception as exc:
                self._audit_tool(task, name, args, check, decision, error=str(exc))
                return f"Tool error: {exc}"
            self._audit_tool(task, name, args, check, decision, result="ok")
            return output

        return execute

    def _checks_runner(self):
        tool = self._tools_by_name.get("run_tests")
        if tool is None:
            return None

        def run_checks() -> tuple[bool, str]:
            text = str(tool.invoke({}))
            return text.strip().startswith("exit=0"), text[:4000]

        return run_checks

    def _verify(self, task: Task) -> VerificationResult | None:
        profile = get_agent(task.agent)
        permission = profile.permission if profile is not None else self.permission_level.value
        task.permission = permission
        changes = self.tracker.changes(task.id) if self.tracker is not None else []
        diff_text = self.tracker.diff(task.id) if self.tracker is not None else ""
        checks = self._checks_runner() if self.verify_checks else None
        try:
            result = self.verifier(task, changes, diff_text, permission, checks)
        except Exception as exc:
            result = VerificationResult(
                VerificationStatus.INCONCLUSIVE,
                f"verifier crashed: {exc}",
                ("verifier error",),
                tuple(str(change.path) for change in changes),
            )
        task.verification = {
            "status": result.status.value,
            "reason": result.reason,
            "checks": list(result.checks),
            "files": list(result.files),
        }
        self._event(
            "task.verify",
            task,
            verification=result.status.value,
            reason=result.reason,
            files=list(result.files),
        )
        return result

    def _default_runner(self, task: Task, board: TaskBoard) -> str:
        if self.model is None:
            raise RuntimeError("orchestrator has no model; pass model= or runner=")
        profile = get_agent(task.agent) or get_agent("coder")
        if profile is None:
            raise RuntimeError(f"unknown agent '{task.agent}'")
        permission = profile.permission if profile is not None else self.permission_level.value
        task.permission = permission
        if permission == "read-only" or self.permission_level is PermissionLevel.READ_ONLY:
            tools = [tool for tool in self._tools if tool.name in READ_ONLY_TOOLS]
        else:
            tools = self._tools
        if profile.allowed_tools is not None:
            allowed = set(profile.allowed_tools)
            tools = [tool for tool in tools if tool.name in allowed]
        skill_block = skill_context(f"{task.title}\n{task.description}", 2)
        memory_block = (
            self.memory.context(f"{task.title} {task.description}", 3)
            if self.memory is not None
            else ""
        )
        system = agent_system_prompt(profile, "\n\n".join(part for part in (skill_block, memory_block) if part))
        model, _route = self._model_for(task)
        result = run_subagent(
            model,
            task.description or task.title,
            role=profile.id,
            tools=tools,
            max_steps=self.max_steps,
            system_prompt=system,
            cancel=self.cancel,
            execute_tool=self._executor_for(task, permission, tools),
        )
        with self._usage_lock:
            for key in ("input", "output", "total"):
                board.usage[key] = board.usage.get(key, 0) + int(result.usage.get(key, 0))
        return result.text

    # -- merge and events ----------------------------------------------
    def _merge(self, board: TaskBoard) -> str:
        completed = [task for task in board.tasks if task.status == TaskStatus.COMPLETED]
        if not completed:
            return ""
        if len(completed) == 1:
            return completed[0].output
        fallback = "\n\n".join(
            f"### {task.id} · {task.title} ({task.agent})\n{task.output}" for task in completed
        )
        if self.model is None:
            return fallback
        failures = [
            f"{task.id} ({task.agent}): {task.error or task.status.value}"
            for task in board.tasks
            if task.status
            in {
                TaskStatus.FAILED,
                TaskStatus.VERIFICATION_FAILED,
                TaskStatus.BLOCKED,
                TaskStatus.CANCELLED,
            }
        ]
        results = fallback
        if board.conflicts:
            results += "\n\nFile conflicts:\n" + "\n".join(
                f"{row['path']}: {', '.join(row['tasks'])}" for row in board.conflicts
            )
        if failures:
            results += "\n\nNot completed:\n" + "\n".join(failures)
        try:
            merged = (MERGE_PROMPT | self.model | StrOutputParser()).invoke(
                {"goal": board.goal, "results": results[:24000]}
            )
            return redact(str(merged).strip())
        except Exception as exc:
            self._event("merge.error", error=str(exc))
            return fallback

    def _event(self, event: str, task: Task | None = None, **fields) -> None:
        payload = {
            "ts": round(time.time(), 3),
            "event": event,
            "run_id": self.board.run_id if self.board else "",
        }
        if task is not None:
            payload.update(
                task=task.id,
                agent=task.agent,
                status=task.status.value,
                title=task.title,
            )
        for key, value in fields.items():
            payload[key] = redact(value) if isinstance(value, str) else value
        self.trace.append(payload)
        if self.on_event is not None:
            try:
                self.on_event(payload)
            except Exception:
                pass
        if self.persist and self.board is not None:
            target = Path(self.task_dir) if self.task_dir is not None else tasks_dir()
            try:
                target.mkdir(parents=True, exist_ok=True)
                with (target / f"{self.board.run_id}.events.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            except OSError:
                pass
