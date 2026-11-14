"""`thirtycli bench` commands: real runs, persisted history, honest skips."""

import json

import pytest
from typer.testing import CliRunner

from thirtycli.cli import app
from thirtycli.models import DETECT_ORDER, env_var_for


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("THIRTYCLI_BENCH_KEEP", raising=False)
    for provider in (*DETECT_ORDER, "ollama", "lmstudio"):
        monkeypatch.delenv(env_var_for(provider), raising=False)
    return tmp_path


def invoke(*args):
    return CliRunner().invoke(app, list(args))


def run_suite(name: str) -> dict:
    result = invoke("bench", "run", name, "--json")
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_bench_list_json_describes_suites_datasets_and_adapters():
    result = invoke("bench", "list", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    assert payload["suites"] == [
        "coding",
        "agents",
        "performance",
        "cost",
        "reliability",
        "security",
    ]
    internal = next(item for item in payload["datasets"] if item["name"] == "internal")
    assert internal["available"] is True
    assert internal["tasks"] >= 1
    assert "thirtycli" in [item["name"] for item in payload["adapters"]]


def test_bench_run_security_reports_real_policy_results():
    payload = run_suite("security")

    assert payload["warnings"] and "no model" in payload["warnings"][0]
    run = payload["runs"][0]
    assert run["suite"] == "security"
    assert len(run["tasks"]) == 7
    assert run["summary"]["ran"] == 7
    assert run["summary"]["success_rate"] == 1.0
    assert run["summary"]["verified"] == 7


def test_bench_run_agents_exercises_the_scheduler():
    payload = run_suite("agents")
    run = payload["runs"][0]

    assert len(run["tasks"]) == 6
    assert run["summary"]["ran"] == 6
    assert run["summary"]["verified"] == 6
    assert all(not task["skipped"] for task in run["tasks"])


def test_bench_run_coding_without_a_model_skips_honestly():
    payload = run_suite("coding")
    run = payload["runs"][0]

    assert run["summary"]["ran"] == 0
    assert run["summary"]["skipped"] == len(run["tasks"]) >= 1
    assert all(task["skipped"] for task in run["tasks"])
    assert all("model" in task["skip_reason"].lower() for task in run["tasks"])


def test_bench_run_rejects_unknown_suite():
    result = invoke("bench", "run", "nope")

    assert result.exit_code == 1
    assert "unknown benchmark suite" in result.output


def test_bench_run_mechanics_suites_require_the_builtin_adapter():
    result = invoke("bench", "run", "agents", "--adapter", "opencode")

    assert result.exit_code == 1
    assert "only support the thirtycli adapter" in result.output


def test_bench_history_report_compare_and_leaderboard(home):
    ids = []
    for _ in range(2):
        ids.append(run_suite("security")["runs"][0]["run_id"])

    listed = invoke("bench", "history", "--json")
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    assert set(ids) <= {row["run_id"] for row in rows}

    filtered = invoke("bench", "history", "--suite", "agents", "--json")
    assert filtered.exit_code == 0
    assert json.loads(filtered.output) == []

    report_json = invoke("bench", "report", ids[0], "--format", "json")
    assert report_json.exit_code == 0, report_json.output
    assert json.loads(report_json.output)["run_id"] == ids[0]

    report_markdown = invoke("bench", "report", ids[0])
    assert report_markdown.exit_code == 0
    assert ids[0] in report_markdown.output

    report_table = invoke("bench", "report", ids[0], "--format", "table")
    assert report_table.exit_code == 0
    assert "suite" in report_table.output.lower()

    compare = invoke("bench", "compare", ids[0], ids[1])
    assert compare.exit_code == 0, compare.output
    assert "Run" in compare.output
    assert "composite" not in compare.output.lower()

    board = invoke("bench", "leaderboard", "--json")
    assert board.exit_code == 0, board.output
    ranked = json.loads(board.output)
    assert [row["rank"] for row in ranked] == list(range(1, len(ranked) + 1))
    assert {row["run_id"] for row in ranked} >= set(ids)


def test_bench_compare_needs_two_runs(home):
    run_suite("security")

    result = invoke("bench", "compare")
    assert result.exit_code == 1
    assert "at least two runs" in result.output


def test_bench_report_of_a_missing_run_fails_cleanly():
    result = invoke("bench", "report", "bench-19700101-000000-000000")

    assert result.exit_code == 1
    assert "No benchmark run" in result.output
    assert "Traceback" not in result.output
