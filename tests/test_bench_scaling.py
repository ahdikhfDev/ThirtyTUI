"""Scaling harness: fixed DAG, honest metrics, no network and no real model.

The fake model derives its tool call from the prompt text rather than replaying a
script, because workers run concurrently and a reply list would race.
"""

import re
import shutil
import threading
import time
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from thirtycli.benchmarks.scaling import (
    render_scaling,
    render_scaling_json,
    run_scaling,
    scaling_tasks,
)

USAGE = {"input_tokens": 12, "output_tokens": 4, "total_tokens": 16}

MOD_SOURCE = "def value() -> int:\n    return {n}\n"

SUMMARY_SOURCE = "\n".join(
    [
        "from mod_1 import value as v1",
        "from mod_2 import value as v2",
        "from mod_3 import value as v3",
        "from mod_4 import value as v4",
        "from mod_5 import value as v5",
        "from mod_6 import value as v6",
        "",
        "MODULE_SUM = v1() + v2() + v3() + v4() + v5() + v6()",
        "TOTAL = 42",
        "",
    ]
)

DONE_SOURCE = "TOTAL=42\n"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("THIRTYCLI_HOME", str(path))
    return path


class DagModel:
    """Writes whichever file the task names; safe under concurrent workers."""

    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = float(delay)
        self.fail = fail
        self.calls = 0
        self._lock = threading.Lock()

    def bind_tools(self, tools, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        with self._lock:
            self.calls += 1
        if self.fail:
            raise RuntimeError("provider is down; key sk-abcdef1234567890 rejected")
        if self.delay:
            time.sleep(self.delay)
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="done", usage_metadata=USAGE)
        target = _write_target(messages)
        if target is None:
            return AIMessage(content="done", usage_metadata=USAGE)
        path, content = target
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"path": path, "content": content},
                    "id": f"call-{uuid.uuid4().hex[:8]}",
                }
            ],
            usage_metadata=USAGE,
        )


def _write_target(messages) -> tuple[str, str] | None:
    text = "\n".join(
        str(message.content) for message in messages if isinstance(message, HumanMessage)
    )
    if "DONE.md" in text:
        return "DONE.md", DONE_SOURCE
    if "summary.py" in text:
        return "summary.py", SUMMARY_SOURCE
    match = re.search(r"mod_(\d)\.py", text)
    if match:
        number = int(match.group(1))
        return f"mod_{number}.py", MOD_SOURCE.format(n=number)
    return None


def test_two_workers_complete_the_fixed_dag(tmp_path):
    root = tmp_path / "work"
    report = run_scaling(DagModel(), root, workers=(2,), timeout=60, keep=True)
    point = report.points[0]

    assert point.tasks_total == 8
    assert point.tasks_completed == 8
    assert point.failed == 0 and point.blocked == 0
    assert point.dependency_violations == 0
    assert point.conflicts == 0
    assert point.verification_failures == 0
    assert point.speedup == 1.0

    workspaces = list(root.glob("scaling-w2-*"))
    assert len(workspaces) == 1
    workspace = workspaces[0]
    for number in range(1, 7):
        source = (workspace / f"mod_{number}.py").read_text(encoding="utf-8")
        assert f"return {number}" in source
    summary = (workspace / "summary.py").read_text(encoding="utf-8")
    assert "TOTAL = 42" in summary
    assert all(f"from mod_{number} import" in summary for number in range(1, 7))
    assert (workspace / "DONE.md").read_text(encoding="utf-8").strip() == "TOTAL=42"

    shutil.rmtree(workspace)


def test_two_worker_counts_render_and_type_check(tmp_path):
    report = run_scaling(
        DagModel(),
        tmp_path / "runs",
        workers=(1, 2),
        timeout=60,
        model_name="gpt-4o-mini",
        provider="openai",
    )

    assert [point.workers for point in report.points] == [1, 2]
    baseline = report.points[0]
    assert baseline.speedup == 1.0
    for point in report.points:
        # speedup is always wall(1 worker) / wall(this point); clock noise decides
        # which count wins on a fake model, so only the definition is asserted.
        assert point.speedup == pytest.approx(round(baseline.wall / point.wall, 3))
    for point in report.points:
        assert point.tasks_completed == point.tasks_total == 8
        assert isinstance(point.tokens, dict)
        assert all(isinstance(value, int) for value in point.tokens.values())
        assert point.tokens.get("total", 0) > 0
        assert isinstance(point.cost, float)
        assert 0.0 <= point.utilization <= 1.0
        assert point.dependency_violations == 0

    text = render_scaling(report)
    assert "workers" in text
    assert "speedup" in text
    assert "util" in text
    assert text.isascii()
    assert "only compare scheduling for the same model and provider" in text

    payload = render_scaling_json(report)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["provider"] == "openai"
    assert len(payload["points"]) == 2
    assert payload["points"][0]["workers"] == 1
    assert payload["points"][0]["speedup"] == 1.0
    assert payload["finished"] >= payload["started"]

    unpriced = run_scaling(DagModel(), tmp_path / "free", workers=(1,), timeout=60)
    assert unpriced.points[0].cost is None
    assert any("cost unavailable" in note for note in unpriced.notes)


def test_failing_model_records_errors_without_raising(tmp_path):
    report = run_scaling(DagModel(fail=True), tmp_path / "boom", workers=(2,), timeout=60)
    point = report.points[0]

    assert point.errors
    assert point.tasks_completed < point.tasks_total
    assert point.failed > 0
    joined = " ".join(point.errors)
    assert "sk-abcdef1234567890" not in joined
    assert "sk-****7890" in joined
    assert "incomplete" in render_scaling(report)


def test_timeout_marks_the_point_incomplete(tmp_path):
    began = time.perf_counter()
    report = run_scaling(DagModel(delay=0.5), tmp_path / "slow", workers=(1,), timeout=0.05)
    elapsed = time.perf_counter() - began

    assert elapsed < 15
    point = report.points[0]
    assert point.tasks_completed < point.tasks_total
    assert any("timeout" in error for error in point.errors)
    assert any("timeout" in note for note in report.notes)
    assert "incomplete" in render_scaling(report)


def test_scaling_tasks_is_a_fixed_dag():
    tasks = {spec["id"]: spec for spec in scaling_tasks()}

    assert list(tasks) == [f"mod{n}" for n in range(1, 7)] + ["join", "done"]
    assert all(tasks[f"mod{n}"]["depends_on"] == [] for n in range(1, 7))
    assert tasks["join"]["depends_on"] == [f"mod{n}" for n in range(1, 7)]
    assert tasks["done"]["depends_on"] == ["join"]
    assert all(spec["agent"] == "coder" for spec in tasks.values())
