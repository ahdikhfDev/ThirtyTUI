"""Benchmark reporting: plain tables, Markdown reports, JSON and comparisons.

Every number here is a raw count, a rate for a single metric, or a mean. There
is no composite score anywhere in this module: comparisons keep the caller's
order, and the leaderboard is a deterministic lexicographic ordering of
individual metrics.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..secrets import redact
from .models import RunResult, summarize

NA = "n/a"
_TASK_HEADERS = ("Task", "Suite", "Status", "Verified", "Duration", "Tokens", "Cost", "Error")
_ORDERING = "success rate, then verified rate, then lower mean duration"


# --------------------------------------------------------------------------- helpers


def _stats(run: RunResult) -> dict:
    """Recompute from tasks when possible, otherwise trust a stored summary."""
    if run.tasks:
        return summarize(run.tasks)
    stats = summarize([])
    for key, value in (run.summary or {}).items():
        if key in stats:
            stats[key] = value
    stats["tokens"] = dict(stats.get("tokens") or {})
    return stats


def _tokens_total(tokens) -> int:
    data = tokens or {}
    total = int(data.get("total") or 0)
    if total:
        return total
    return int(data.get("input") or 0) + int(data.get("output") or 0)


def _pct(rate) -> str:
    if rate is None:
        return NA
    return f"{float(rate) * 100:.1f}%"


def _dur(seconds) -> str:
    if seconds is None:
        return NA
    return f"{float(seconds):.2f}s"


def _cost(value) -> str:
    if value is None:
        return NA
    return f"${float(value):.4f}"


def _count(value) -> str:
    if value is None:
        return NA
    return f"{int(value):,}"


def _tokens(value) -> str:
    return _count(value) if value else NA


def _utc(timestamp) -> str:
    if not timestamp:
        return NA
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _clean(text, limit: int | None = None) -> str:
    text = " ".join(redact(str(text or "")).split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text


def _cell(text, limit: int | None = None) -> str:
    return _clean(text, limit=limit).replace("|", "\\|")


def _table(headers, rows, right=()) -> str:
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(values) -> str:
        parts = [
            value.rjust(widths[index]) if index in right else value.ljust(widths[index])
            for index, value in enumerate(values)
        ]
        return "  ".join(parts).rstrip()

    body = [line(list(headers)), "  ".join("-" * width for width in widths)]
    body.extend(line(row) for row in cells)
    return "\n".join(body)


def _status(row: dict) -> str:
    if row["skipped"]:
        return "skipped"
    return "pass" if row["success"] else "fail"


def _verified(row: dict) -> str:
    if row["skipped"]:
        return "-"
    return "yes" if row["verified"] else "no"


def _check_failures(checks) -> list[str]:
    return [str(name) for name, ok in (checks or {}).items() if not ok]


# --------------------------------------------------------------------------- rows


def summary_rows(run: RunResult) -> list[dict]:
    """One row per task result, in run order. Skipped rows never count as success."""
    rows = []
    for task in run.tasks:
        skipped = bool(task.skipped)
        rows.append(
            {
                "task": task.task,
                "suite": task.suite or run.suite,
                "success": bool(task.success) and not skipped,
                "verified": bool(task.verified) and not skipped,
                "duration": round(float(task.duration or 0.0), 3),
                "tokens_total": _tokens_total(task.tokens),
                "cost": task.cost,
                "skipped": skipped,
                "error": _clean(task.error),
            }
        )
    return rows


def comparison_rows(runs: list[RunResult]) -> list[dict]:
    """Per-run metrics in the caller's order. No weighting, no composite score."""
    rows = []
    for run in runs:
        stats = _stats(run)
        rows.append(
            {
                "run_id": run.run_id,
                "suite": run.suite,
                "cli": run.cli,
                "model": run.model,
                "provider": run.provider,
                "ran": stats.get("ran", 0),
                "skipped": stats.get("skipped", 0),
                "success_rate": stats.get("success_rate", 0.0),
                "verified_rate": stats.get("verified_rate", 0.0),
                "mean_duration": stats.get("mean_duration", 0.0),
                "cost": stats.get("cost"),
                "tokens_total": _tokens_total(stats.get("tokens")),
            }
        )
    return rows


def leaderboard_rows(runs: list[RunResult], suite: str = "") -> list[dict]:
    """Runs ranked by (-success_rate, -verified_rate, mean_duration, run_id)."""
    rows = [row for row in comparison_rows(runs) if not suite or row["suite"] == suite]
    rows.sort(
        key=lambda row: (
            -row["success_rate"],
            -row["verified_rate"],
            row["mean_duration"],
            row["run_id"],
        )
    )
    return [{"rank": rank, **row} for rank, row in enumerate(rows, start=1)]


def failure_rows(run: RunResult) -> list[dict]:
    """Failed tasks only; skipped trials are not failures."""
    rows = []
    for task in run.tasks:
        if task.skipped or task.success:
            continue
        rows.append(
            {
                "task": task.task,
                "error": _clean(task.error),
                "duration": round(float(task.duration or 0.0), 3),
                "exit_code": task.exit_code,
                "checks": dict(task.checks or {}),
            }
        )
    return rows


# --------------------------------------------------------------------------- renderers


def _task_rows(run: RunResult) -> list[list[str]]:
    rows = []
    for row in summary_rows(run):
        rows.append(
            [
                row["task"] or NA,
                row["suite"] or NA,
                _status(row),
                _verified(row),
                _dur(row["duration"]),
                _tokens(row["tokens_total"]),
                _cost(row["cost"]),
                _clean(row["error"], limit=120),
            ]
        )
    return rows


def render_run(run: RunResult, fmt: str = "table") -> str:
    if fmt == "json":
        return json.dumps(run.to_dict(), indent=2)
    if fmt != "table":
        raise ValueError(f"Unknown format '{fmt}' (use 'table' or 'json')")

    stats = _stats(run)
    tokens = _tokens_total(stats.get("tokens"))
    lines = [
        f"Run: {run.run_id or NA}",
        f"Suite: {run.suite or NA}",
        f"CLI: {run.cli or NA}",
        f"Model: {run.model or NA}",
        f"Provider: {run.provider or NA}",
        f"Dataset: {run.dataset or NA}",
        "",
        (
            f"Tasks: {stats.get('tasks', 0)}  Ran: {stats.get('ran', 0)}  "
            f"Skipped: {stats.get('skipped', 0)}  Errors: {stats.get('errors', 0)}"
        ),
        (
            f"Success: {stats.get('completed', 0)}/{stats.get('ran', 0)} "
            f"({_pct(stats.get('success_rate'))})  "
            f"Verified: {stats.get('verified', 0)}/{stats.get('ran', 0)} "
            f"({_pct(stats.get('verified_rate'))})"
        ),
        (
            f"Mean duration: {_dur(stats.get('mean_duration'))}  "
            f"Total duration: {_dur(stats.get('total_duration'))}"
        ),
        f"Tokens: {_tokens(tokens)}  Cost: {_cost(stats.get('cost'))}",
        "",
    ]

    rows = _task_rows(run)
    if rows:
        lines.append(_table(_TASK_HEADERS, rows, right={4, 5, 6}))
    else:
        lines.append("No task results recorded.")
    return "\n".join(lines)


def _markdown_value(value) -> str:
    if value is None:
        return NA
    if isinstance(value, bool):
        return "yes" if value else "no"
    return _clean(value) or NA


def render_report(run: RunResult) -> str:
    """Full Markdown report: metadata, summary, tasks, failures, notes."""
    stats = _stats(run)
    token_data = stats.get("tokens") or {}
    total_tokens = _tokens_total(token_data)

    wall = NA
    if run.finished:
        wall = _dur(max(0.0, float(run.finished) - float(run.created or 0)))

    lines = [f"# Benchmark report — {run.run_id or 'unknown run'}", "", "## Metadata", ""]
    lines += ["| Field | Value |", "| --- | --- |"]
    lines.append(f"| Run ID | {f'`{run.run_id}`' if run.run_id else NA} |")
    lines.append(f"| Timestamp (UTC) | {_utc(run.created)} |")
    lines.append(f"| Finished (UTC) | {_utc(run.finished) if run.finished else NA} |")
    lines.append(f"| CLI | {_cell(run.cli) or NA} |")
    lines.append(f"| Model | {_cell(run.model) or NA} |")
    lines.append(f"| Provider | {_cell(run.provider) or NA} |")
    lines.append(f"| Dataset | {_cell(run.dataset) or NA} |")
    lines.append(f"| Suite | {_cell(run.suite) or NA} |")
    lines.append(f"| Wall duration | {wall} |")

    lines += ["", "## Configuration", "", "| Setting | Value |", "| --- | --- |"]
    config = run.config or {}
    preferred = ["timeout", "concurrency", "trials", "seed", "temperature", "max_tokens"]
    keys = [key for key in preferred if key in config]
    keys += sorted(key for key in config if key not in preferred)
    if keys:
        for key in keys:
            lines.append(f"| {_cell(key)} | {_markdown_value(config[key])} |")
    else:
        lines.append(f"| (none recorded) | {NA} |")

    lines += ["", "## Summary", "", "| Metric | Value |", "| --- | --- |"]
    lines.append(f"| Tasks | {stats.get('tasks', 0)} |")
    lines.append(f"| Ran | {stats.get('ran', 0)} |")
    lines.append(f"| Skipped | {stats.get('skipped', 0)} |")
    lines.append(
        f"| Success | {stats.get('completed', 0)}/{stats.get('ran', 0)} ({_pct(stats.get('success_rate'))}) |"
    )
    lines.append(
        f"| Verified | {stats.get('verified', 0)}/{stats.get('ran', 0)} ({_pct(stats.get('verified_rate'))}) |"
    )
    lines.append(f"| Mean duration | {_dur(stats.get('mean_duration'))} |")
    lines.append(f"| Total duration | {_dur(stats.get('total_duration'))} |")
    lines.append(
        "| Tokens (in/out/total) | "
        f"{_tokens(token_data.get('input'))} / {_tokens(token_data.get('output'))} / {_tokens(total_tokens)} |"
    )
    lines.append(f"| Cost | {_cost(stats.get('cost'))} |")
    lines.append(f"| Errors | {stats.get('errors', 0)} |")

    lines += ["", "## Task results", ""]
    task_rows = summary_rows(run)
    if task_rows:
        lines += [
            "| Task | Suite | Status | Verified | Duration | Tokens | Cost | Error |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for row in task_rows:
            lines.append(
                "| {task} | {suite} | {status} | {verified} | {duration} | {tokens} | {cost} | {error} |".format(
                    task=_cell(row["task"]) or NA,
                    suite=_cell(row["suite"]) or NA,
                    status=_status(row),
                    verified=_verified(row),
                    duration=_dur(row["duration"]),
                    tokens=_tokens(row["tokens_total"]),
                    cost=_cost(row["cost"]),
                    error=_cell(row["error"], limit=160) or "-",
                )
            )
    else:
        lines.append("No task results recorded.")

    lines += ["", "## Failure analysis", ""]
    failures = failure_rows(run)
    if failures:
        for item in failures:
            detail = _cell(item["error"], limit=200) or "(no error message)"
            lines.append(f"- **{_cell(item['task']) or NA}** — {detail}")
            extra = []
            if item["exit_code"] is not None:
                extra.append(f"exit code {item['exit_code']}")
            failed_checks = _check_failures(item["checks"])
            if failed_checks:
                extra.append("failed checks: " + ", ".join(failed_checks))
            if extra:
                lines.append(f"  - {', '.join(extra)}")
    else:
        lines.append("No failed tasks.")

    lines += ["", "## Notes", ""]
    note_lines = [f"- {_clean(note, limit=400)}" for note in run.notes if note]
    if stats.get("skipped"):
        reasons = [str(reason) for reason in (stats.get("skip_reasons") or [])]
        suffix = f" ({', '.join(reasons)})" if reasons else ""
        note_lines.append(f"- {stats['skipped']} task(s) skipped{suffix}")
    lines.extend(note_lines or ["- No notes recorded."])

    return "\n".join(lines)


def render_comparison(runs: list[RunResult], fmt: str = "table") -> str:
    rows = comparison_rows(runs)
    if fmt == "json":
        return json.dumps(rows, indent=2)
    if fmt != "table":
        raise ValueError(f"Unknown format '{fmt}' (use 'table' or 'json')")
    if not rows:
        return "No runs to compare."

    table_rows = [
        [
            row["run_id"] or NA,
            row["suite"] or NA,
            row["cli"] or NA,
            row["model"] or NA,
            row["provider"] or NA,
            _count(row["ran"]),
            _count(row["skipped"]),
            _pct(row["success_rate"]),
            _pct(row["verified_rate"]),
            _dur(row["mean_duration"]),
            _tokens(row["tokens_total"]),
            _cost(row["cost"]),
        ]
        for row in rows
    ]
    table = _table(
        [
            "Run",
            "Suite",
            "CLI",
            "Model",
            "Provider",
            "Ran",
            "Skipped",
            "Success",
            "Verified",
            "Mean",
            "Tokens",
            "Cost",
        ],
        table_rows,
        right={5, 6, 7, 8, 9, 10, 11},
    )

    notes = ["Note: comparisons are only meaningful when dataset, configuration, model and provider match."]
    differ = []
    for field in ("suite", "model", "provider", "dataset"):
        values = sorted({str(getattr(run, field) or "") for run in runs})
        if len(values) > 1:
            labels = ", ".join(value or NA for value in values)
            differ.append(f"{field} ({labels})")
    if differ:
        notes.append("Warning: runs differ in " + "; ".join(differ) + ".")
    return table + "\n\n" + "\n".join(notes)


def render_leaderboard(runs: list[RunResult], suite: str = "") -> str:
    rows = leaderboard_rows(runs, suite=suite)
    lines = [f"Suite: {suite}"] if suite else []
    if rows:
        table_rows = [
            [
                str(row["rank"]),
                row["run_id"] or NA,
                row["suite"] or NA,
                row["cli"] or NA,
                row["model"] or NA,
                _count(row["ran"]),
                _count(row["skipped"]),
                _pct(row["success_rate"]),
                _pct(row["verified_rate"]),
                _dur(row["mean_duration"]),
                _tokens(row["tokens_total"]),
                _cost(row["cost"]),
            ]
            for row in rows
        ]
        lines.append(
            _table(
                [
                    "#",
                    "Run",
                    "Suite",
                    "CLI",
                    "Model",
                    "Ran",
                    "Skipped",
                    "Success",
                    "Verified",
                    "Mean",
                    "Tokens",
                    "Cost",
                ],
                table_rows,
                right={0, 5, 6, 7, 8, 9, 10, 11},
            )
        )
    else:
        lines.append("No runs to rank.")

    ordering = f"Ordering: {_ORDERING}. No composite score."
    return "\n".join(lines + ["", ordering])


def render_history(rows: list[dict]) -> str:
    if not rows:
        return "No benchmark runs recorded."
    table_rows = [
        [
            str(row.get("run_id") or NA),
            str(row.get("suite") or NA),
            str(row.get("cli") or NA),
            str(row.get("model") or NA),
            _pct(row.get("success_rate")),
            _pct(row.get("verified_rate")),
            _count(row.get("ran")),
            _cost(row.get("cost")),
            _utc(row.get("created")),
        ]
        for row in rows
    ]
    return _table(
        ["Run", "Suite", "CLI", "Model", "Success", "Verified", "Ran", "Cost", "Created"],
        table_rows,
        right={4, 5, 6, 7},
    )
