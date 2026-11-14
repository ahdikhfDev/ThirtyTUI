"""Adapters that run one benchmark task through thirtycli or an external CLI.

There is no faking here: the built-in adapter reports what the orchestrator
actually did (board status, recorded files, verification, usage), and an
external CLI without a verified invocation reports itself as skipped instead of
pretending to have run. A benchmark that silently "passes" is worse than no
benchmark at all, so ``verified`` is only set from the orchestrator's own
verification evidence.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .. import __version__
from ..orchestrator import Orchestrator, TaskStatus
from ..pricing import estimate
from ..secrets import redact
from .models import TaskOutcome, TaskSpec

VERSION_TIMEOUT = 10
VERIFIED_STATUSES = {"verified", "verified_with_warnings"}
TEMPLATE_ENV = "THIRTYCLI_BENCH_{name}_CMD"


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    available: bool
    version: str = ""
    reason: str = ""


class BenchmarkAdapter(Protocol):
    name: str

    def info(self) -> AdapterInfo: ...

    def run(self, task: TaskSpec, workspace: Path, timeout: int) -> TaskOutcome: ...


def _one_line(text: str, limit: int = 200) -> str:
    return " ".join(str(text or "").split())[:limit]


def _clip(text: str, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:limit]


class ThirtyCLIAdapter:
    """Run a task through the real orchestrator with a caller-supplied model."""

    name = "thirtycli"

    def __init__(self, model=None, cfg=None) -> None:
        self.model = model
        self.cfg = cfg
        self.model_name = (
            str(getattr(model, "model_name", "") or "") or str(getattr(cfg, "model", "") or "")
        )

    def info(self) -> AdapterInfo:
        if self.model is None:
            return AdapterInfo(
                name=self.name,
                available=False,
                reason="no model configured; the built-in adapter needs a model instance",
            )
        label = f"model {self.model_name}" if self.model_name else "model bound"
        return AdapterInfo(name=self.name, available=True, version=__version__, reason=label)

    def run(self, task: TaskSpec, workspace: Path, timeout: int) -> TaskOutcome:
        started = time.time()
        workspace = Path(workspace)
        prompt = str(task.prompt or task.id)
        description = _description(task, prompt)

        def planner(goal: str, limit: int) -> list[dict]:
            return [
                {
                    "id": "t1",
                    "title": _clip(prompt) or task.id,
                    "description": description,
                    "agent": "coder",
                    "depends_on": [],
                    "max_retries": 0,
                }
            ]

        cancel = threading.Event()
        timer = threading.Timer(max(1, int(timeout)), cancel.set)
        timer.daemon = True
        timer.start()
        orch = None
        try:
            orch = Orchestrator(
                model=self.model,
                root=workspace,
                workers=1,
                coding=True,
                permission="autonomous",
                approver=lambda question: True,
                persist=False,
                memory=False,
                route=False,
                verify=True,
                max_retries=0,
                planner=planner,
                cancel=cancel,
            )
            orch.run(description)
        except Exception as exc:  # a crash is a failed task, never a passing one
            return TaskOutcome(
                success=False,
                error=redact(str(exc)),
                duration=round(time.time() - started, 3),
            )
        finally:
            timer.cancel()

        board = orch.board if orch is not None else None
        if board is None:
            return TaskOutcome(
                success=False,
                error="orchestrator produced no board",
                duration=round(time.time() - started, 3),
            )

        status = board.overall()
        success = status is TaskStatus.COMPLETED
        tasks = list(board.tasks)
        verification = {
            item.id: str((item.verification or {}).get("status", "")) for item in tasks
        }
        verified = bool(tasks) and all(
            verification.get(item.id) in VERIFIED_STATUSES for item in tasks
        )
        files = tuple(
            dict.fromkeys(
                str(path)
                for item in tasks
                for path in (item.verification or {}).get("files") or ()
            )
        )
        usage = {str(key): int(value or 0) for key, value in (board.usage or {}).items()}
        timed_out = cancel.is_set() and not success
        if timed_out:
            error = f"timed out after {timeout}s"
        else:
            problems = [f"{item.id}: {item.error}" for item in tasks if item.error]
            error = "" if success else ("; ".join(problems) or f"orchestrator finished with status {status.value}")

        return TaskOutcome(
            success=success,
            output=str(board.final or ""),
            error=error,
            duration=round(time.time() - started, 3),
            tokens=usage,
            cost=estimate(self.model_name, usage),
            verified=verified,
            files=files,
            metrics={
                "task_status": status.value,
                "verification": verification,
                "attempts": {item.id: item.attempts for item in tasks},
                "timed_out": timed_out,
            },
        )


class ExternalCLIAdapter:
    """Run a task through another coding CLI, only when invoked honestly."""

    def __init__(
        self,
        name: str,
        binaries: tuple[str, ...],
        version_flag: str = "--version",
        command_template: str | None = None,
    ) -> None:
        self.name = name
        self.binaries = tuple(binaries)
        self.version_flag = version_flag
        self.command_template = command_template

    def _find(self) -> tuple[str, str]:
        for binary in self.binaries:
            path = shutil.which(binary)
            if path:
                return binary, path
        return "", ""

    def _env_key(self) -> str:
        return TEMPLATE_ENV.format(name=self.name.upper().replace("-", "_"))

    def _template(self) -> str | None:
        override = str(os.environ.get(self._env_key(), "") or "").strip()
        if override:
            return override
        template = str(self.command_template or "").strip()
        return template or None

    def info(self) -> AdapterInfo:
        binary, path = self._find()
        if not path:
            expected = ", ".join(self.binaries) or self.name
            return AdapterInfo(
                name=self.name,
                available=False,
                reason=(
                    f"{expected} not found on PATH; install the CLI or set "
                    f"{self._env_key()} to a verified invocation"
                ),
            )
        version = ""
        try:
            proc = subprocess.run(
                [path, *shlex.split(self.version_flag)],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
            )
            text = (proc.stdout or "") + (proc.stderr or "")
            version = _one_line(redact(text))
        except (OSError, subprocess.SubprocessError) as exc:
            version = f"version check failed: {_one_line(redact(str(exc)))}"
        return AdapterInfo(
            name=self.name,
            available=True,
            version=version,
            reason=f"found {binary}",
        )

    def run(self, task: TaskSpec, workspace: Path, timeout: int) -> TaskOutcome:
        template = self._template()
        if template is None:
            return TaskOutcome(
                success=False,
                skipped=True,
                skip_reason=f"{self.name}: no verified invocation configured",
            )
        prompt = str(task.prompt or task.id)
        command = template.replace("{prompt}", shlex.quote(prompt))
        started = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                timeout=max(1, int(timeout)),
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return TaskOutcome(
                success=False,
                error=f"{self.name}: timed out after {timeout}s",
                duration=round(time.time() - started, 3),
                metrics={"command": redact(command)},
            )
        except OSError as exc:
            return TaskOutcome(
                success=False,
                error=redact(str(exc)),
                duration=round(time.time() - started, 3),
                metrics={"command": redact(command)},
            )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        output = redact("\n".join(part for part in (out, err) if part))
        exit_code = int(proc.returncode)
        return TaskOutcome(
            success=exit_code == 0,
            output=output,
            error="" if exit_code == 0 else f"{self.name} exited with code {exit_code}",
            duration=round(time.time() - started, 3),
            verified=False,
            metrics={"exit_code": exit_code, "command": redact(command)},
        )


class AiderAdapter(ExternalCLIAdapter):
    def __init__(self) -> None:
        super().__init__("aider", ("aider",))


class OpenCodeAdapter(ExternalCLIAdapter):
    def __init__(self) -> None:
        super().__init__("opencode", ("opencode",))


class ClaudeCodeAdapter(ExternalCLIAdapter):
    def __init__(self) -> None:
        super().__init__("claude", ("claude",))


ADAPTERS: dict[str, type] = {
    "thirtycli": ThirtyCLIAdapter,
    "aider": AiderAdapter,
    "opencode": OpenCodeAdapter,
    "claude": ClaudeCodeAdapter,
}

ALIASES = {"claude-code": "claude", "claude_code": "claude"}


def get_adapter(name: str, model=None, cfg=None) -> BenchmarkAdapter:
    key = str(name or "").strip().lower()
    key = ALIASES.get(key, key)
    factory = ADAPTERS.get(key)
    if factory is None:
        known = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"unknown benchmark adapter '{name}' (known: {known})")
    if factory is ThirtyCLIAdapter:
        return ThirtyCLIAdapter(model=model, cfg=cfg)
    return factory()


def _description(task: TaskSpec, prompt: str) -> str:
    details = [f"Benchmark suite: {task.suite}", f"Task id: {task.id}"]
    if task.check_command:
        details.append(f"Verification command: {task.check_command}")
    if task.expected_files:
        details.append("Expected files: " + ", ".join(task.expected_files))
    if task.metadata:
        pairs = ", ".join(f"{key}={value}" for key, value in list(task.metadata.items())[:8])
        details.append(f"Metadata: {_one_line(pairs, 300)}")
    return prompt + "\n\n" + "\n".join(details)
