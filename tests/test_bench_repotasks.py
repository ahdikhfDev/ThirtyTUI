"""The repository task pack is real: every snapshot fails, every fix passes."""

import os
import subprocess
import sys

import pytest

from thirtycli.benchmarks.datasets import get_dataset, load_dataset
from thirtycli.benchmarks.repotasks import SOLUTIONS, repo_tasks

TASKS = repo_tasks()
IDS = [task.id for task in TASKS]
BY_ID = {task.id: task for task in TASKS}

# Importing pytest plugins is the slow part of a cold start on Windows. The task
# checks are plain pytest runs, so autoloading is skipped to keep this file fast.
ENV = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


def write_files(workspace, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def run_check(task, workspace):
    return subprocess.run(
        task.check_command,
        shell=True,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
        env=ENV,
    )


# -- shape of the pack --------------------------------------------------------


def test_pack_has_ten_tasks_with_unique_ids():
    assert len(TASKS) == 10
    assert len(set(IDS)) == 10
    assert set(IDS) == set(SOLUTIONS)
    for task_id in IDS:
        assert task_id == task_id.lower()
        assert " " not in task_id and "_" not in task_id


def test_every_task_is_a_real_repo_task(tmp_path):
    for task in TASKS:
        assert task.suite == "coding"
        assert task.timeout == 180
        assert task.metadata["dataset"] == "repo"
        assert task.metadata["language"] == "python"
        assert task.metadata["kind"]
        assert task.prompt.strip()
        assert task.expected_files
        assert sys.executable in task.check_command
        assert "pytest" in task.check_command or "check_tests.py" in task.check_command
        assert task.files
        # The test-writing task ships no tests on purpose: the model must add them.
        if task.id != "repo-slug-tests":
            assert any(name.startswith("test_") for name in task.files)
        for name in task.expected_files:
            assert name in task.files or name in SOLUTIONS[task.id], name


def test_solutions_never_leak_into_metadata():
    for task in TASKS:
        assert set(task.metadata) == {"dataset", "kind", "language"}
        for content in SOLUTIONS[task.id].values():
            assert content not in repr(task.metadata)


def test_tasks_are_rebuilt_per_call():
    first = repo_tasks()
    first[0].files["money.py"] = "mutated"
    second = repo_tasks()
    assert second[0].files["money.py"] != "mutated"


# -- every snapshot fails, every solution passes ------------------------------


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_snapshot_fails_the_check(task, tmp_path):
    write_files(tmp_path, task.files)
    result = run_check(task, tmp_path)
    assert result.returncode != 0, f"{task.id} already passes without a fix"


@pytest.mark.parametrize("task", TASKS, ids=IDS)
def test_solution_passes_the_check(task, tmp_path):
    write_files(tmp_path, task.files)
    write_files(tmp_path, SOLUTIONS[task.id])
    result = run_check(task, tmp_path)
    assert result.returncode == 0, f"{task.id}\n{result.stdout}\n{result.stderr}"


# -- the test-writing task ----------------------------------------------------


def test_test_writing_task_fails_without_tests(tmp_path):
    task = BY_ID["repo-slug-tests"]
    write_files(tmp_path, task.files)
    result = run_check(task, tmp_path)
    assert result.returncode != 0
    assert "CHECK FAILED" in result.stdout
    assert "test_slug.py is missing" in result.stdout


def test_test_writing_task_accepts_a_failing_suite(tmp_path):
    task = BY_ID["repo-slug-tests"]
    write_files(tmp_path, task.files)
    (tmp_path / "test_slug.py").write_text(
        "from slug import slugify\n\n\ndef test_uppercase_is_kept():\n"
        '    assert slugify("Hello") == "Hello"\n',
        encoding="utf-8",
    )
    result = run_check(task, tmp_path)
    assert result.returncode != 0
    assert "fail against the reference slug.py" in result.stdout


def test_test_writing_task_solution_adds_the_test_file(tmp_path):
    task = BY_ID["repo-slug-tests"]
    assert task.check_command.endswith("check_tests.py")
    assert "test_slug.py" not in task.files

    write_files(tmp_path, task.files)
    write_files(tmp_path, SOLUTIONS[task.id])

    assert (tmp_path / "test_slug.py").is_file()
    assert "def test_" in (tmp_path / "test_slug.py").read_text(encoding="utf-8")
    # The passing run of the same workspace is covered by test_solution_passes_the_check.


# -- dataset wiring -----------------------------------------------------------


def test_repo_dataset_is_available():
    info = get_dataset("repo")
    assert info.available is True
    assert info.source == "builtin"
    assert info.license == "MIT"
    assert info.reason
    assert info.task_count == 10


def test_load_dataset_returns_the_pack():
    tasks = load_dataset("repo")
    assert len(tasks) == 10
    assert [task.id for task in tasks] == IDS
    assert all(task.metadata["dataset"] == "repo" for task in tasks)
