"""Benchmark runner: disposable workspaces, checks, trials, persistence."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from ..pricing import estimate
from ..secrets import redact
from .adapters import AdapterInfo, BenchmarkAdapter
from .datasets import datasets
from .models import RunConfig, RunResult, TaskResult, TaskSpec, new_run_id, summarize
from .storage import save_run, work_dir
from .suites import SUITE_NAMES, tasks_for

MAX_OUTPUT = 4_000


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"\n... truncated ({len(text) - limit} chars)"


def _write_files(workspace: Path, files: dict[str, str]) -> None:
    for raw, content in files.items():
        target = (workspace / raw).resolve()
        if workspace != target and workspace not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _check_files(workspace: Path, expected: tuple[str, ...]) -> dict:
    missing = [name for name in expected if not (workspace / name).exists()]
    return {"expected": list(expected), "missing": missing, "ok": not missing}


def _check_command(workspace: Path, command: str, timeout: int) -> dict:
    if not command.strip():
        return {"command": "", "ok": True, "exit_code": None, "output": ""}
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout)),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"command": command, "ok": False, "exit_code": None, "output": "timed out"}
    except OSError as exc:
        return {"command": command, "ok": False, "exit_code": None, "output": str(exc)}
    output = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return {
        "command": command,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": _clip(redact(output)),
    }


class BenchmarkRunner:
    def __init__(self, config: RunConfig, adapter: BenchmarkAdapter, persist: bool = True) -> None:
        self.config = config
        self.adapter = adapter
        self.persist = persist
        self.run_id = new_run_id()
        self.info: AdapterInfo = adapter.info()

    def run(self) -> RunResult:
        started = time.time()
        tasks = tasks_for(self.config.suite)
        run = RunResult(
            run_id=self.run_id,
            suite=self.config.suite,
            cli=self.adapter.name,
            model=self.config.model,
            provider=self.config.provider,
            config=self.config.to_dict(),
            created=started,
            dataset="internal" if self.config.suite in {"agents", "performance", "reliability", "security"} else "",
        )
        if not self.info.available and self.config.suite in {"coding", "cost"}:
            run.notes.append(self.info.reason or "adapter unavailable")
        if self.config.suite == "coding":
            for info in datasets():
                if info.name != "internal" and not info.available:
                    run.notes.append(f"{info.name}: {info.reason}")

        jobs = [(task, trial) for task in tasks for trial in range(1, max(1, self.config.trials) + 1)]
        if not jobs:
            run.tasks = []
            run.summary = summarize([])
            run.finished = time.time()
            if self.persist:
                save_run(run)
            return run

        with ThreadPoolExecutor(max_workers=max(1, self.config.concurrency)) as pool:
            futures = {
                pool.submit(self._run_one, task, trial): (task, trial) for task, trial in jobs
            }
            for future in as_completed(futures):
                task, trial = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # never lose a benchmark failure
                    result = TaskResult(
                        run_id=self.run_id,
                        suite=self.config.suite,
                        task=task.id,
                        trial=trial,
                        cli=self.adapter.name,
                        model=self.config.model,
                        provider=self.config.provider,
                        success=False,
                        verified=False,
                        duration=0.0,
                        error=redact(str(exc)),
                    )
                run.tasks.append(result)

        run.tasks.sort(key=lambda item: (item.task, item.trial))
        run.summary = summarize(run.tasks)
        run.finished = time.time()
        if self.persist:
            save_run(run)
        return run

    def _run_one(self, task: TaskSpec, trial: int) -> TaskResult:
        workspace = Path(
            tempfile.mkdtemp(
                prefix=f"{self.config.suite}-{task.id}-{trial}-",
                dir=str(work_dir()),
            )
        )
        started = time.perf_counter()
        checks: dict = {}
        try:
            _write_files(workspace, task.files)
            if task.local_runner is not None:
                outcome = task.local_runner(workspace, self.config, task.timeout)
            elif not self.info.available:
                outcome = type(
                    "Skipped",
                    (),
                    {
                        "success": False,
                        "output": "",
                        "error": "",
                        "duration": 0.0,
                        "tokens": {},
                        "cost": None,
                        "verified": False,
                        "files": (),
                        "metrics": {},
                        "skipped": True,
                        "skip_reason": self.info.reason or "adapter unavailable",
                    },
                )()
            else:
                outcome = self.adapter.run(task, workspace, max(1, task.timeout or self.config.timeout))
            duration = time.perf_counter() - started

            if outcome.skipped:
                return TaskResult(
                    run_id=self.run_id,
                    suite=self.config.suite,
                    task=task.id,
                    trial=trial,
                    cli=self.adapter.name,
                    model=self.config.model,
                    provider=self.config.provider,
                    success=False,
                    verified=False,
                    duration=duration,
                    output=_clip(redact(outcome.output)),
                    error=redact(outcome.error),
                    tokens=dict(outcome.tokens or {}),
                    cost=outcome.cost,
                    checks=checks,
                    metrics=dict(outcome.metrics or {}),
                    skipped=True,
                    skip_reason=redact(outcome.skip_reason or "skipped"),
                    dataset=str(task.metadata.get("dataset") or ""),
                )

            files_check = _check_files(workspace, task.expected_files)
            checks["files"] = files_check
            command_check = _check_command(
                workspace,
                task.check_command,
                max(1, task.timeout or self.config.timeout),
            )
            checks["command"] = command_check
            success = bool(outcome.success and files_check["ok"] and command_check["ok"])
            error = outcome.error
            if outcome.success and not files_check["ok"]:
                error = error or "expected files missing: " + ", ".join(files_check["missing"])
            if outcome.success and not command_check["ok"]:
                error = error or "check command failed"
            verified = bool(outcome.verified or (task.local_runner is not None and success))
            cost = outcome.cost
            tokens = dict(outcome.tokens or {})
            if cost is None and self.config.model and tokens.get("total"):
                cost = estimate(self.config.model, tokens)
            return TaskResult(
                run_id=self.run_id,
                suite=self.config.suite,
                task=task.id,
                trial=trial,
                cli=self.adapter.name,
                model=self.config.model,
                provider=self.config.provider,
                success=success,
                verified=verified,
                duration=duration,
                exit_code=command_check.get("exit_code"),
                output=_clip(redact(outcome.output)),
                error=redact(error),
                tokens=tokens,
                cost=cost,
                checks=checks,
                metrics=dict(outcome.metrics or {}),
                dataset=str(task.metadata.get("dataset") or ""),
            )
        finally:
            if not os.environ.get("THIRTYCLI_BENCH_KEEP"):
                shutil.rmtree(workspace, ignore_errors=True)


def run_suites(
    names: list[str],
    adapter: BenchmarkAdapter,
    config: RunConfig,
    persist: bool = True,
) -> list[RunResult]:
    runs = []
    for name in names:
        if name not in SUITE_NAMES:
            raise KeyError(f"unknown benchmark suite: {name}")
        suite_config = replace(config, suite=name)
        runs.append(BenchmarkRunner(suite_config, adapter, persist=persist).run())
    return runs


def parse_suite_names(raw: str) -> list[str]:
    if raw == "all":
        return list(SUITE_NAMES)
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in SUITE_NAMES]
    if unknown:
        raise KeyError(f"unknown benchmark suite(s): {', '.join(unknown)}")
    return names
