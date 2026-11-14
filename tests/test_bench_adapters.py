"""Benchmark adapters: the built-in orchestrator adapter and external CLIs."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from tests.test_chat_loop import ScriptedModel
from thirtycli.benchmarks.adapters import (
    ADAPTERS,
    ExternalCLIAdapter,
    ThirtyCLIAdapter,
    get_adapter,
)
from thirtycli.benchmarks.models import TaskSpec


class NamedScriptedModel(ScriptedModel):
    """ScriptedModel with a price-less model name, like a real chat model."""

    model_name: str = "scripted-1"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    for key in ("AIDER", "OPENCODE", "CLAUDE"):
        monkeypatch.delenv(f"THIRTYCLI_BENCH_{key}_CMD", raising=False)
    return tmp_path


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_adds():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    return root


def task(**overrides) -> TaskSpec:
    data = {
        "id": "calc-add",
        "suite": "coding",
        "prompt": "Fix add() in calc.py so the tests pass.",
        "check_command": f"{sys.executable} -m pytest -q",
        "expected_files": ("calc.py",),
    }
    data.update(overrides)
    return TaskSpec(**data)


def python_command(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


# -- built-in adapter ---------------------------------------------------------


def test_thirtycli_info_is_unavailable_without_a_model():
    info = ThirtyCLIAdapter().info()
    assert info.name == "thirtycli"
    assert info.available is False
    assert "model" in info.reason


def test_thirtycli_info_uses_model_name_and_version():
    adapter = ThirtyCLIAdapter(model=NamedScriptedModel())
    info = adapter.info()
    assert info.available is True
    assert info.version
    assert "scripted-1" in info.reason


def test_thirtycli_run_reports_success_and_verification(workspace):
    model = NamedScriptedModel(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage(content="fixed add() to return the sum"),
        ]
    )
    adapter = ThirtyCLIAdapter(model=model)
    outcome = adapter.run(task(), workspace, timeout=60)

    assert outcome.success is True
    assert outcome.verified is True
    assert outcome.error == ""
    assert "sum" in outcome.output
    assert "calc.py" in outcome.files
    assert outcome.metrics["task_status"] == "completed"
    assert outcome.metrics["verification"]["t1"] == "verified"
    assert outcome.metrics["timed_out"] is False
    # The model has no price entry, so the cost stays unknown instead of zero.
    assert outcome.cost is None
    assert (workspace / "calc.py").read_text(encoding="utf-8").splitlines()[1].strip() == "return a + b"


def test_thirtycli_run_reports_failure_without_a_model(workspace):
    outcome = ThirtyCLIAdapter().run(task(), workspace, timeout=30)
    assert outcome.success is False
    assert outcome.verified is False
    assert "no model" in outcome.error


def test_thirtycli_run_reports_failure_when_the_model_writes_nothing(workspace):
    model = NamedScriptedModel(replies=[AIMessage(content="nothing to do")])
    outcome = ThirtyCLIAdapter(model=model).run(task(), workspace, timeout=30)
    assert outcome.success is False
    assert outcome.verified is False
    assert "verification" in outcome.error
    assert outcome.metrics["verification"]["t1"] == "failed"


# -- external adapters --------------------------------------------------------


def test_external_info_reason_when_binary_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr("thirtycli.benchmarks.adapters.shutil.which", lambda name: None)
    adapter = ExternalCLIAdapter("aider", ("aider",))
    info = adapter.info()
    assert info.available is False
    assert "aider" in info.reason
    assert "THIRTYCLI_BENCH_AIDER_CMD" in info.reason


def test_external_info_reports_version_from_the_binary(monkeypatch):
    monkeypatch.setattr(
        "thirtycli.benchmarks.adapters.shutil.which", lambda name: "/fake/bin/aider"
    )
    seen: dict = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["timeout"] = kwargs.get("timeout")
        return SimpleNamespace(stdout="aider 0.99.0\n", stderr="", returncode=0)

    monkeypatch.setattr("thirtycli.benchmarks.adapters.subprocess.run", fake_run)
    info = ExternalCLIAdapter("aider", ("aider",)).info()
    assert info.available is True
    assert info.version == "aider 0.99.0"
    assert "found aider" in info.reason
    assert seen["args"] == ["/fake/bin/aider", "--version"]
    assert seen["timeout"] == 10


def test_external_run_skips_without_a_verified_invocation(tmp_path):
    adapter = get_adapter("aider")
    outcome = adapter.run(task(), tmp_path, timeout=30)
    assert outcome.skipped is True
    assert outcome.success is False
    assert "no verified invocation configured" in outcome.skip_reason
    assert outcome.verified is False


def test_external_run_uses_the_env_template(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "THIRTYCLI_BENCH_AIDER_CMD", python_command("print('aider-ran')")
    )
    outcome = get_adapter("aider").run(task(), tmp_path, timeout=30)
    assert outcome.success is True
    assert "aider-ran" in outcome.output
    assert outcome.verified is False
    assert outcome.metrics["exit_code"] == 0


def test_external_run_reports_a_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "THIRTYCLI_BENCH_OPENCODE_CMD", python_command("raise SystemExit(3)")
    )
    outcome = get_adapter("opencode").run(task(), tmp_path, timeout=30)
    assert outcome.success is False
    assert outcome.verified is False
    assert "3" in outcome.error
    assert outcome.metrics["exit_code"] == 3


def test_get_adapter_returns_the_built_in_adapter():
    adapter = get_adapter("thirtycli", model=NamedScriptedModel())
    assert isinstance(adapter, ThirtyCLIAdapter)
    assert adapter.info().available is True


def test_get_adapter_accepts_the_claude_alias():
    assert isinstance(get_adapter("claude-code"), ExternalCLIAdapter)
    assert get_adapter("claude-code").name == "claude"


def test_get_adapter_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown benchmark adapter"):
        get_adapter("does-not-exist")


def test_registry_lists_every_adapter():
    assert set(ADAPTERS) == {"thirtycli", "aider", "opencode", "claude"}


def test_run_leaves_setup_files_alone(workspace):
    """The runner owns task.files; the adapter must not write them itself."""
    model = NamedScriptedModel(replies=[AIMessage(content="no changes")])
    before = (workspace / "calc.py").read_text(encoding="utf-8")
    outcome = ThirtyCLIAdapter(model=model).run(
        task(files={"calc.py": "def add(a, b):\n    return a + b\n"}), workspace, timeout=30
    )
    assert outcome.success is False
    assert (workspace / "calc.py").read_text(encoding="utf-8") == before


def test_workspace_argument_is_not_required_to_exist_for_skipped_runs(tmp_path):
    missing = Path(tmp_path) / "not-created"
    outcome = get_adapter("claude").run(task(), missing, timeout=30)
    assert outcome.skipped is True
    assert not missing.exists()
