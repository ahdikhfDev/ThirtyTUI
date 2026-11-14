"""Benchmark datasets: internal suite, optional HumanEval/MBPP, official dirs."""

import json
import subprocess
import sys

import pytest

from thirtycli.benchmarks.datasets import (
    DatasetUnavailable,
    datasets,
    get_dataset,
    load_dataset,
)
from thirtycli.benchmarks.storage import data_dir

KNOWN = {"internal", "humaneval", "mbpp", "swebench", "terminalbench", "polyglot"}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    for name in ("SWEBENCH", "TERMINALBENCH", "POLYGLOT"):
        monkeypatch.delenv(f"THIRTYCLI_BENCH_{name}_DIR", raising=False)
    return tmp_path


def run_check(task, workspace):
    return subprocess.run(
        task.check_command,
        shell=True,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=task.timeout,
    )


def write_jsonl(name: str, records: list[dict]) -> None:
    path = data_dir() / name
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


# -- the internal suite -------------------------------------------------------


def test_datasets_describes_every_known_dataset():
    infos = {info.name: info for info in datasets()}
    assert set(infos) == KNOWN
    assert infos["internal"].available is True
    assert infos["internal"].task_count == 3
    assert infos["internal"].reason
    assert infos["humaneval"].available is False
    assert infos["mbpp"].available is False


def test_internal_tasks_are_real_repo_tasks():
    tasks = load_dataset("internal")
    assert [task.id for task in tasks] == ["calc-add", "slugify", "parse-csv"]
    for task in tasks:
        assert task.suite == "coding"
        assert task.metadata["kind"] == "internal"
        assert task.expected_files
        assert sys.executable in task.check_command
        assert "pytest" in task.check_command
        for name in task.expected_files:
            assert name in task.files
        assert any(name.startswith("test_") for name in task.files)
        assert task.prompt


def test_internal_calc_task_fails_before_and_passes_after_the_fix(tmp_path):
    task = next(item for item in load_dataset("internal") if item.id == "calc-add")
    workspace = tmp_path / "calc"
    workspace.mkdir()
    for name, content in task.files.items():
        (workspace / name).write_text(content, encoding="utf-8")

    failing = run_check(task, workspace)
    assert failing.returncode != 0
    assert "test_calc.py" in failing.stdout

    (workspace / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    passing = run_check(task, workspace)
    assert passing.returncode == 0, passing.stdout + passing.stderr


def test_internal_tasks_are_rebuilt_per_call():
    first = load_dataset("internal")
    first[0].files["calc.py"] = "mutated"
    second = load_dataset("internal")
    assert second[0].files["calc.py"] != "mutated"


# -- HumanEval ----------------------------------------------------------------


def test_humaneval_is_unavailable_without_the_file():
    info = get_dataset("humaneval")
    assert info.available is False
    assert "not installed" in info.reason
    assert "humaneval" in info.reason
    with pytest.raises(DatasetUnavailable):
        load_dataset("humaneval")


def test_humaneval_jsonl_is_parsed_into_tasks():
    write_jsonl(
        "humaneval.jsonl",
        [
            {
                "task_id": "HumanEval/0",
                "prompt": "def add(a, b):\n    ...",
                "test": "def check(candidate):\n    assert candidate(1, 2) == 3",
                "entry_point": "add",
            }
        ],
    )
    info = get_dataset("humaneval")
    assert info.available is True
    assert info.task_count == 1

    tasks = load_dataset("humaneval")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == "HumanEval/0"
    assert task.suite == "humaneval"
    assert task.files["solution.py"] == ""
    harness = task.files["test_solution.py"]
    assert "from solution import add" in harness
    assert "assert candidate(1, 2) == 3" in harness
    assert "check(add)" in harness
    assert "print('OK')" in harness
    assert task.check_command == f"{sys.executable} test_solution.py"
    assert task.expected_files == ("solution.py",)
    assert task.timeout == 120
    assert task.metadata["entry_point"] == "add"
    assert "solution.py" in task.prompt


def test_humaneval_json_array_is_parsed_too():
    path = data_dir() / "humaneval.json"
    path.write_text(
        json.dumps(
            [
                {
                    "task_id": "HumanEval/7",
                    "prompt": "def double(x):\n    ...",
                    "test": "def check(candidate):\n    assert candidate(2) == 4",
                    "entry_point": "double",
                }
            ]
        ),
        encoding="utf-8",
    )
    tasks = load_dataset("humaneval")
    assert [task.id for task in tasks] == ["HumanEval/7"]


def test_humaneval_garbage_is_unavailable():
    (data_dir() / "humaneval.jsonl").write_text("not json\n{broken\n", encoding="utf-8")
    info = get_dataset("humaneval")
    assert info.available is False
    assert "no records" in info.reason
    with pytest.raises(DatasetUnavailable):
        load_dataset("humaneval")


# -- MBPP ---------------------------------------------------------------------


def test_mbpp_is_unavailable_without_the_file():
    info = get_dataset("mbpp")
    assert info.available is False
    assert "mbpp" in info.reason
    with pytest.raises(DatasetUnavailable):
        load_dataset("mbpp")


def test_mbpp_jsonl_is_parsed_into_tasks():
    write_jsonl(
        "mbpp.jsonl",
        [
            {
                "task_id": 1,
                "text": "Write a function that adds two numbers.",
                "test_list": ["assert add(1, 2) == 3", "assert add(0, 0) == 0"],
                "code": "def add(a, b):\n    return a + b",
            }
        ],
    )
    info = get_dataset("mbpp")
    assert info.available is True
    assert info.task_count == 1

    tasks = load_dataset("mbpp")
    task = tasks[0]
    assert task.id == "1"
    assert task.suite == "mbpp"
    assert task.files["solution.py"] == ""
    harness = task.files["test_solution.py"]
    assert "from solution import *" in harness
    assert "assert add(1, 2) == 3" in harness
    assert "assert add(0, 0) == 0" in harness
    assert "print('OK')" in harness
    assert task.check_command == f"{sys.executable} test_solution.py"
    assert "adds two numbers" in task.prompt


def test_mbpp_record_without_tests_is_not_usable():
    write_jsonl("mbpp.jsonl", [{"task_id": "x", "text": "do something", "test_list": []}])
    assert get_dataset("mbpp").available is False
    with pytest.raises(DatasetUnavailable):
        load_dataset("mbpp")


# -- official directory datasets ---------------------------------------------


def test_official_datasets_are_unavailable_without_installation():
    for name in ("swebench", "terminalbench", "polyglot"):
        info = get_dataset(name)
        assert info.available is False
        assert "official dataset not installed" in info.reason
        assert f"THIRTYCLI_BENCH_{name.upper()}_DIR" in info.reason
        with pytest.raises(DatasetUnavailable):
            load_dataset(name)


def test_official_dataset_loads_validated_records(tmp_path, monkeypatch):
    directory = tmp_path / "swebench-data"
    directory.mkdir()
    manifest = {
        "version": "2.1-custom",
        "tasks": [
            {
                "id": "repo-123",
                "prompt": "Fix the parser regression in parser.py.",
                "check_command": f"{sys.executable} -m pytest -q",
                "files": {"parser.py": ""},
                "expected_files": ["parser.py"],
                "timeout": 90,
                "metadata": {"repo": "demo"},
            },
            {"id": "incomplete", "prompt": "no check command"},
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("THIRTYCLI_BENCH_SWEBENCH_DIR", str(directory))

    info = get_dataset("swebench")
    assert info.available is True
    assert info.task_count == 1
    assert info.version == "2.1-custom"

    tasks = load_dataset("swebench")
    assert len(tasks) == 1
    task = tasks[0]
    assert task.id == "repo-123"
    assert task.suite == "swebench"
    assert task.files == {"parser.py": ""}
    assert task.expected_files == ("parser.py",)
    assert task.timeout == 90
    assert task.check_command.endswith("-m pytest -q")
    assert task.metadata["repo"] == "demo"
    assert task.metadata["kind"] == "swebench"


def test_official_dataset_reads_tasks_jsonl(tmp_path, monkeypatch):
    directory = tmp_path / "terminalbench"
    directory.mkdir()
    (directory / "tasks.jsonl").write_text(
        json.dumps(
            {
                "id": "tb-1",
                "prompt": "Make the failing command succeed.",
                "check_command": "true",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THIRTYCLI_BENCH_TERMINALBENCH_DIR", str(directory))
    tasks = load_dataset("terminalbench")
    assert [task.id for task in tasks] == ["tb-1"]
    assert tasks[0].metadata["kind"] == "terminalbench"


def test_official_dataset_directory_without_manifest_is_unavailable(tmp_path, monkeypatch):
    directory = tmp_path / "polyglot"
    directory.mkdir()
    monkeypatch.setenv("THIRTYCLI_BENCH_POLYGLOT_DIR", str(directory))
    info = get_dataset("polyglot")
    assert info.available is False
    assert "manifest.json or tasks.jsonl" in info.reason
    with pytest.raises(DatasetUnavailable):
        load_dataset("polyglot")


def test_official_dataset_rejects_records_without_check_command(tmp_path, monkeypatch):
    directory = tmp_path / "polyglot"
    directory.mkdir()
    (directory / "manifest.json").write_text(
        json.dumps([{"id": "x", "prompt": "do it"}]), encoding="utf-8"
    )
    monkeypatch.setenv("THIRTYCLI_BENCH_POLYGLOT_DIR", str(directory))
    info = get_dataset("polyglot")
    assert info.available is False
    assert "prompt and check_command" in info.reason
    with pytest.raises(DatasetUnavailable):
        load_dataset("polyglot")


# -- lookup errors ------------------------------------------------------------


def test_unknown_dataset_is_rejected():
    with pytest.raises(KeyError):
        get_dataset("nope")
    with pytest.raises(DatasetUnavailable):
        load_dataset("nope")
