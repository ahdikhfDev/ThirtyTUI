"""Data model for reproducible benchmark runs."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def new_run_id(prefix: str = "bench") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


@dataclass
class TaskSpec:
    """One benchmark item. Callables stay in memory and are never persisted."""

    id: str
    suite: str
    prompt: str = ""
    files: dict[str, str] = field(default_factory=dict)
    check_command: str = ""
    expected_files: tuple[str, ...] = ()
    timeout: int = 120
    metadata: dict = field(default_factory=dict)
    local_runner: Callable[[Path, RunConfig, int], TaskOutcome] | None = field(
        default=None, repr=False, compare=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "suite": self.suite,
            "prompt": self.prompt,
            "files": dict(self.files),
            "check_command": self.check_command,
            "expected_files": list(self.expected_files),
            "timeout": self.timeout,
            "metadata": dict(self.metadata),
        }


@dataclass
class TaskOutcome:
    """What an adapter or local task produced, before the runner's checks."""

    success: bool
    output: str = ""
    error: str = ""
    duration: float = 0.0
    tokens: dict = field(default_factory=dict)
    cost: float | None = None
    verified: bool = False
    files: tuple[str, ...] = ()
    metrics: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class TaskResult:
    run_id: str
    suite: str
    task: str
    trial: int
    cli: str
    model: str
    provider: str
    success: bool
    verified: bool
    duration: float
    exit_code: int | None = None
    output: str = ""
    error: str = ""
    tokens: dict = field(default_factory=dict)
    cost: float | None = None
    checks: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""
    dataset: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "task": self.task,
            "trial": self.trial,
            "cli": self.cli,
            "model": self.model,
            "provider": self.provider,
            "success": self.success,
            "verified": self.verified,
            "duration": self.duration,
            "exit_code": self.exit_code,
            "output": self.output,
            "error": self.error,
            "tokens": dict(self.tokens),
            "cost": self.cost,
            "checks": dict(self.checks),
            "metrics": dict(self.metrics),
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "dataset": self.dataset,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskResult:
        return cls(
            run_id=str(data.get("run_id") or ""),
            suite=str(data.get("suite") or ""),
            task=str(data.get("task") or ""),
            trial=int(data.get("trial") or 1),
            cli=str(data.get("cli") or ""),
            model=str(data.get("model") or ""),
            provider=str(data.get("provider") or ""),
            success=bool(data.get("success")),
            verified=bool(data.get("verified")),
            duration=float(data.get("duration") or 0.0),
            exit_code=data.get("exit_code"),
            output=str(data.get("output") or ""),
            error=str(data.get("error") or ""),
            tokens=dict(data.get("tokens") or {}),
            cost=data.get("cost"),
            checks=dict(data.get("checks") or {}),
            metrics=dict(data.get("metrics") or {}),
            skipped=bool(data.get("skipped")),
            skip_reason=str(data.get("skip_reason") or ""),
            dataset=str(data.get("dataset") or ""),
            timestamp=float(data.get("timestamp") or time.time()),
        )


@dataclass
class RunConfig:
    suite: str
    cli: str = "thirtycli"
    model: str = ""
    provider: str = ""
    timeout: int = 120
    concurrency: int = 2
    trials: int = 1
    seed: int = 0
    temperature: float = 0.0
    max_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "suite": self.suite,
            "cli": self.cli,
            "model": self.model,
            "provider": self.provider,
            "timeout": self.timeout,
            "concurrency": self.concurrency,
            "trials": self.trials,
            "seed": self.seed,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


@dataclass
class RunResult:
    run_id: str
    suite: str
    cli: str
    model: str
    provider: str
    config: dict
    tasks: list[TaskResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    finished: float | None = None
    dataset: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, (self.finished or time.time()) - self.created)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "suite": self.suite,
            "cli": self.cli,
            "model": self.model,
            "provider": self.provider,
            "config": dict(self.config),
            "tasks": [task.to_dict() for task in self.tasks],
            "summary": dict(self.summary),
            "created": self.created,
            "finished": self.finished,
            "dataset": self.dataset,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, data: dict) -> RunResult:
        return cls(
            run_id=str(data.get("run_id") or ""),
            suite=str(data.get("suite") or ""),
            cli=str(data.get("cli") or ""),
            model=str(data.get("model") or ""),
            provider=str(data.get("provider") or ""),
            config=dict(data.get("config") or {}),
            tasks=[TaskResult.from_dict(item) for item in data.get("tasks") or []],
            summary=dict(data.get("summary") or {}),
            created=float(data.get("created") or time.time()),
            finished=data.get("finished"),
            dataset=str(data.get("dataset") or ""),
            notes=[str(item) for item in data.get("notes") or []],
        )


def summarize(tasks: list[TaskResult]) -> dict:
    """Counts and means; skipped trials are excluded from success rates."""
    ran = [task for task in tasks if not task.skipped]
    skipped = [task for task in tasks if task.skipped]
    completed = [task for task in ran if task.success]
    verified = [task for task in ran if task.verified]
    tokens = {"input": 0, "output": 0, "total": 0, "cached": 0}
    cost = 0.0
    has_cost = False
    for task in ran:
        for key in tokens:
            tokens[key] += int((task.tokens or {}).get(key, 0) or 0)
        if task.cost is not None:
            has_cost = True
            cost += float(task.cost)
    return {
        "tasks": len(tasks),
        "ran": len(ran),
        "skipped": len(skipped),
        "completed": len(completed),
        "verified": len(verified),
        "success_rate": round(len(completed) / len(ran), 4) if ran else 0.0,
        "verified_rate": round(len(verified) / len(ran), 4) if ran else 0.0,
        "mean_duration": round(sum(task.duration for task in ran) / len(ran), 3) if ran else 0.0,
        "total_duration": round(sum(task.duration for task in ran), 3),
        "tokens": tokens,
        "cost": round(cost, 6) if has_cost else None,
        "errors": sum(1 for task in ran if task.error),
        "skip_reasons": sorted({task.skip_reason for task in skipped if task.skip_reason}),
    }


def as_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [as_jsonable(item) for item in value]
    return str(value)
