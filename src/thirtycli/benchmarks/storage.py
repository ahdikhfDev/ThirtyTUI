"""Persistent benchmark storage: ~/.thirtycli/benchmarks/runs/<run>.json."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import home
from ..secrets import redact
from .models import RunResult, TaskResult, as_jsonable


def bench_dir() -> Path:
    path = home() / "benchmarks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    path = bench_dir() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = bench_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir() -> Path:
    path = bench_dir() / "work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _redact(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def save_run(run: RunResult) -> Path:
    payload = _redact(as_jsonable(run.to_dict()))
    path = runs_dir() / f"{run.run_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_run(run_id: str) -> RunResult:
    path = runs_dir() / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"No benchmark run '{run_id}' in {runs_dir()}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunResult.from_dict(data)


def list_runs(suite: str = "", limit: int = 50) -> list[dict]:
    rows = []
    for path in sorted(runs_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if suite and data.get("suite") != suite:
            continue
        summary = data.get("summary") or {}
        rows.append(
            {
                "run_id": data.get("run_id") or path.stem,
                "suite": data.get("suite", ""),
                "cli": data.get("cli", ""),
                "model": data.get("model", ""),
                "provider": data.get("provider", ""),
                "created": data.get("created", 0),
                "success_rate": summary.get("success_rate", 0),
                "verified_rate": summary.get("verified_rate", 0),
                "ran": summary.get("ran", 0),
                "skipped": summary.get("skipped", 0),
                "cost": summary.get("cost"),
                "mean_duration": summary.get("mean_duration", 0),
            }
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def history(suite: str = "", limit: int = 50) -> list[dict]:
    return list_runs(suite=suite, limit=limit)


def task_failures(run: RunResult) -> list[TaskResult]:
    return [task for task in run.tasks if not task.skipped and not task.success]
