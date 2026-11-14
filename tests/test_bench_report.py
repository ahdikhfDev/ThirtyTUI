"""Benchmark reporting: summaries, Markdown, JSON, comparison and leaderboard."""

import json

from thirtycli.benchmarks.models import RunResult, TaskResult, summarize
from thirtycli.benchmarks.report import (
    comparison_rows,
    failure_rows,
    leaderboard_rows,
    render_comparison,
    render_history,
    render_leaderboard,
    render_report,
    render_run,
    summary_rows,
)

CREATED = 1_700_000_000.0  # 2023-11-14 22:13:20 UTC
CREATED_TEXT = "2023-11-14 22:13:20 UTC"
SECRET = "sk-abcdef1234567890"


def task(run_id, suite, name, success, verified, duration, **kwargs):
    return TaskResult(
        run_id=run_id,
        suite=suite,
        task=name,
        trial=kwargs.pop("trial", 1),
        cli="thirtycli",
        model="m1",
        provider="p1",
        success=success,
        verified=verified,
        duration=duration,
        **kwargs,
    )


def sample_run(run_id="bench-a", suite="coding", model="m1", provider="p1", created=CREATED):
    """Two passes (one verified), one failure, one skipped."""
    tasks = [
        task(
            run_id,
            suite,
            "alpha",
            True,
            True,
            2.0,
            exit_code=0,
            tokens={"input": 100, "output": 20, "total": 120},
            cost=0.5,
            checks={"tests": True},
        ),
        task(
            run_id,
            suite,
            "beta",
            False,
            False,
            6.0,
            exit_code=1,
            tokens={"input": 10, "output": 5, "total": 15},
            cost=0.25,
            checks={"tests": False, "lint": True},
            error=f"boom with key {SECRET}",
        ),
        task(run_id, suite, "gamma", True, False, 4.0, exit_code=0, tokens={}, cost=None),
        task(
            run_id,
            suite,
            "delta",
            False,
            False,
            1.0,
            skipped=True,
            skip_reason="missing api key",
        ),
    ]
    return RunResult(
        run_id=run_id,
        suite=suite,
        cli="thirtycli",
        model=model,
        provider=provider,
        config={
            "suite": suite,
            "cli": "thirtycli",
            "model": model,
            "provider": provider,
            "timeout": 120,
            "concurrency": 2,
            "trials": 1,
            "seed": 7,
        },
        tasks=tasks,
        summary=summarize(tasks),
        created=created,
        finished=created + 13.0,
        dataset="ds-a",
        notes=["hand run"],
    )


def simple_run(run_id, suite, results, model="m1", created=CREATED):
    tasks = [
        task(run_id, suite, name, success, verified, duration, skipped=skipped)
        for name, success, verified, duration, skipped in results
    ]
    return RunResult(
        run_id=run_id,
        suite=suite,
        cli="thirtycli",
        model=model,
        provider="p1",
        config={"suite": suite},
        tasks=tasks,
        summary=summarize(tasks),
        created=created,
        dataset="ds-a",
    )


def section(text, heading):
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


# --------------------------------------------------------------------------- summary rows


def test_summary_rows_carry_every_field_and_keep_skipped():
    rows = summary_rows(sample_run())

    assert [row["task"] for row in rows] == ["alpha", "beta", "gamma", "delta"]
    assert set(rows[0]) == {
        "task",
        "suite",
        "success",
        "verified",
        "duration",
        "tokens_total",
        "cost",
        "skipped",
        "error",
    }

    alpha, beta, gamma, delta = rows
    assert alpha["success"] is True and alpha["verified"] is True
    assert alpha["tokens_total"] == 120 and alpha["cost"] == 0.5
    assert alpha["skipped"] is False

    assert beta["success"] is False
    assert SECRET not in beta["error"]
    assert "sk-****7890" in beta["error"]

    assert gamma["cost"] is None and gamma["tokens_total"] == 0

    assert delta["skipped"] is True
    assert delta["success"] is False and delta["verified"] is False


def test_render_run_table_shows_header_and_counts():
    text = render_run(sample_run())

    assert "Run: bench-a" in text
    assert "Suite: coding" in text
    assert "CLI: thirtycli" in text
    assert "Model: m1" in text
    assert "Provider: p1" in text
    assert "Dataset: ds-a" in text
    assert "Tasks: 4  Ran: 3  Skipped: 1" in text
    assert "Success: 2/3 (66.7%)" in text
    assert "Verified: 1/3 (33.3%)" in text
    assert "Mean duration: 4.00s" in text
    assert "Tokens: 135" in text
    assert "Cost: $0.7500" in text
    # skipped trials are listed but never counted as a pass
    assert "delta" in text and "skipped" in text
    assert SECRET not in text


def test_render_run_json_is_parseable():
    payload = json.loads(render_run(sample_run(), fmt="json"))

    assert payload["run_id"] == "bench-a"
    assert payload["suite"] == "coding"
    assert payload["dataset"] == "ds-a"
    assert [item["task"] for item in payload["tasks"]] == ["alpha", "beta", "gamma", "delta"]
    assert payload["tasks"][3]["skipped"] is True


def test_unknown_format_is_rejected():
    calls = [(render_run, sample_run()), (render_comparison, [sample_run()])]
    for call, argument in calls:
        try:
            call(argument, fmt="yaml")
        except ValueError as error:
            assert "yaml" in str(error)
        else:  # pragma: no cover - guard
            raise AssertionError("expected ValueError")


# --------------------------------------------------------------------------- markdown report


def test_render_report_has_all_sections_and_metadata():
    report = render_report(sample_run())

    for heading in (
        "# Benchmark report",
        "## Metadata",
        "## Configuration",
        "## Summary",
        "## Task results",
        "## Failure analysis",
        "## Notes",
    ):
        assert heading in report

    assert "bench-a" in report
    assert CREATED_TEXT in report
    assert "| CLI | thirtycli |" in report
    assert "| Model | m1 |" in report
    assert "| Provider | p1 |" in report
    assert "| Dataset | ds-a |" in report
    assert "| timeout | 120 |" in report
    assert "| concurrency | 2 |" in report
    assert "| trials | 1 |" in report
    assert "| seed | 7 |" in report
    assert "| Success | 2/3 (66.7%) |" in report
    assert "| Skipped | 1 |" in report
    assert "| Error |" in report or "| Errors | 1 |" in report


def test_render_report_marks_missing_cost_and_tokens_as_not_available():
    run = RunResult(
        run_id="bench-bare",
        suite="coding",
        cli="thirtycli",
        model="m1",
        provider="p1",
        config={},
        tasks=[task("bench-bare", "coding", "solo", True, False, 1.5)],
        created=CREATED,
    )
    report = render_report(run)

    assert "| Cost | n/a |" in report
    assert "| Tokens (in/out/total) | n/a / n/a / n/a |" in report
    assert "| (none recorded) | n/a |" in report
    assert "| solo | coding | pass | no | 1.50s | n/a | n/a | - |" in report
    assert "No failed tasks." in report
    assert "No notes recorded." in report


def test_failure_analysis_lists_only_failed_non_skipped_tasks():
    report = render_report(sample_run())
    failures = section(report, "## Failure analysis")

    assert "beta" in failures
    assert "delta" not in failures
    assert "alpha" not in failures
    assert "failed checks: tests" in failures
    assert "exit code 1" in failures
    assert SECRET not in report
    assert "sk-****7890" in report


def test_report_mentions_skips_in_notes():
    report = render_report(sample_run())

    assert "task(s) skipped (missing api key)" in report


# --------------------------------------------------------------------------- comparison


def test_comparison_rows_preserve_input_order_and_fields():
    runs = [sample_run(run_id="bench-b"), sample_run(run_id="bench-a")]
    rows = comparison_rows(runs)

    assert [row["run_id"] for row in rows] == ["bench-b", "bench-a"]
    assert set(rows[0]) == {
        "run_id",
        "suite",
        "cli",
        "model",
        "provider",
        "ran",
        "skipped",
        "success_rate",
        "verified_rate",
        "mean_duration",
        "cost",
        "tokens_total",
    }
    assert rows[0]["ran"] == 3
    assert rows[0]["skipped"] == 1
    assert rows[0]["success_rate"] == round(2 / 3, 4)
    assert rows[0]["verified_rate"] == round(1 / 3, 4)
    assert rows[0]["mean_duration"] == 4.0
    assert rows[0]["cost"] == 0.75
    assert rows[0]["tokens_total"] == 135
    assert not any("score" in key.replace("success_rate", "").replace("verified_rate", "") for key in rows[0])


def test_render_comparison_table_and_json():
    runs = [sample_run(run_id="bench-a"), sample_run(run_id="bench-b", model="m2")]
    text = render_comparison(runs)

    assert "bench-a" in text and "bench-b" in text
    assert "66.7%" in text
    assert "only meaningful when dataset, configuration, model and provider match" in text
    assert "runs differ in model (m1, m2)" in text

    payload = json.loads(render_comparison(runs, fmt="json"))
    assert isinstance(payload, list)
    assert [row["run_id"] for row in payload] == ["bench-a", "bench-b"]


def test_render_comparison_without_differences_has_no_warning():
    text = render_comparison([sample_run(run_id="bench-a"), sample_run(run_id="bench-b")])

    assert "Warning:" not in text
    assert "Note:" in text


def test_render_comparison_with_no_runs():
    assert render_comparison([]) == "No runs to compare."


# --------------------------------------------------------------------------- leaderboard


def test_leaderboard_orders_by_rate_then_duration_then_run_id():
    runs = [
        simple_run("b-run", "coding", [("t1", True, True, 5.0, False), ("t2", True, True, 5.0, False)]),
        simple_run("a-run", "coding", [("t1", True, True, 3.0, False), ("t2", True, False, 3.0, False)]),
        simple_run("c-run", "coding", [("t1", True, True, 2.0, False), ("t2", True, False, 2.0, False)]),
        simple_run("z-run", "other", [("t1", False, False, 1.0, False)]),
    ]

    rows = leaderboard_rows(runs)
    assert [row["run_id"] for row in rows] == ["b-run", "c-run", "a-run", "z-run"]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]

    # highest verified rate wins the tie on success rate
    assert rows[0]["verified_rate"] == 1.0
    assert rows[1]["verified_rate"] == 0.5 and rows[1]["mean_duration"] == 2.0
    assert rows[2]["mean_duration"] == 3.0


def test_leaderboard_is_deterministic_on_full_ties():
    runs = [
        simple_run("b-tie", "coding", [("t1", True, True, 4.0, False)]),
        simple_run("a-tie", "coding", [("t1", True, True, 4.0, False)]),
    ]

    assert [row["run_id"] for row in leaderboard_rows(runs)] == ["a-tie", "b-tie"]
    assert [row["run_id"] for row in leaderboard_rows(list(reversed(runs)))] == ["a-tie", "b-tie"]


def test_leaderboard_filters_by_suite_and_has_no_composite_score():
    runs = [
        simple_run("coding-run", "coding", [("t1", True, True, 2.0, False)]),
        simple_run("other-run", "other", [("t1", False, False, 1.0, False)]),
    ]

    rows = leaderboard_rows(runs, suite="coding")
    assert [row["run_id"] for row in rows] == ["coding-run"]
    assert rows[0]["rank"] == 1
    assert set(rows[0]) == {
        "rank",
        "run_id",
        "suite",
        "cli",
        "model",
        "provider",
        "ran",
        "skipped",
        "success_rate",
        "verified_rate",
        "mean_duration",
        "cost",
        "tokens_total",
    }


def test_render_leaderboard_spells_out_the_ordering():
    runs = [
        simple_run("coding-run", "coding", [("t1", True, True, 2.0, False)]),
        simple_run("other-run", "other", [("t1", True, False, 3.0, False)]),
    ]
    text = render_leaderboard(runs, suite="coding")

    assert "Suite: coding" in text
    assert "coding-run" in text
    assert "other-run" not in text
    assert "Ordering: success rate, then verified rate, then lower mean duration. No composite score." in text
    assert render_leaderboard([], suite="coding").endswith(
        "Ordering: success rate, then verified rate, then lower mean duration. No composite score."
    )


# --------------------------------------------------------------------------- failures / history


def test_failure_rows_exclude_skips_and_keep_checks():
    rows = failure_rows(sample_run())

    assert [row["task"] for row in rows] == ["beta"]
    assert set(rows[0]) == {"task", "error", "duration", "exit_code", "checks"}
    assert rows[0]["duration"] == 6.0
    assert rows[0]["exit_code"] == 1
    assert rows[0]["checks"] == {"tests": False, "lint": True}
    assert SECRET not in rows[0]["error"]


def test_render_history_formats_utc_and_missing_values():
    rows = [
        {
            "run_id": "bench-b",
            "suite": "coding",
            "cli": "thirtycli",
            "model": "m1",
            "provider": "p1",
            "created": CREATED,
            "success_rate": 0.5,
            "verified_rate": 0.25,
            "ran": 4,
            "skipped": 1,
            "cost": 0.75,
            "mean_duration": 3.0,
        },
        {
            "run_id": "bench-a",
            "suite": "coding",
            "cli": "thirtycli",
            "model": "m2",
            "created": None,
            "success_rate": None,
            "verified_rate": None,
            "ran": 0,
            "cost": None,
        },
    ]
    text = render_history(rows)
    lines = text.splitlines()

    assert lines[0].split() == [
        "Run",
        "Suite",
        "CLI",
        "Model",
        "Success",
        "Verified",
        "Ran",
        "Cost",
        "Created",
    ]
    assert "bench-b" in text
    assert "50.0%" in text and "25.0%" in text
    assert CREATED_TEXT in text
    assert "$0.7500" in text
    assert "n/a" in text
    assert render_history([]) == "No benchmark runs recorded."
