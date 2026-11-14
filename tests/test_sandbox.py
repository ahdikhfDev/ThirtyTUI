import os
import sys
from pathlib import Path

from thirtycli.sandbox import (
    CommandResult,
    ProcessSandbox,
    capability_report,
    restricted_env,
)


def test_restricted_env_drops_api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-test")
    monkeypatch.setenv("DB_PASSWORD", "hunter2")
    monkeypatch.setenv("CLOUD_CREDENTIALS", "/tmp/creds.json")

    env = restricted_env()

    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD"):
        assert name not in env
    assert "/tmp/creds.json" not in env.values()
    assert env.get("PATH"), "PATH must survive, otherwise commands cannot start"
    assert env["THIRTYCLI_SANDBOX"] == "1"


def test_restricted_env_keeps_only_the_allowlist(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/tmp/should-go")
    monkeypatch.setenv("MY_RANDOM_SETTING", "x")

    env = restricted_env({"EXTRA_FLAG": "1"})

    assert "PYTHONPATH" not in env
    assert "MY_RANDOM_SETTING" not in env
    assert env["EXTRA_FLAG"] == "1"
    assert env.get("HOME") or env.get("USERPROFILE")


def test_capability_report_makes_no_false_claims():
    report = capability_report()
    if report.available:
        assert report.kind == "bwrap"
        return
    assert report.kind == "process"
    assert report.available is False
    assert "cwd+env+timeout only" in report.reason
    assert "not confined" in report.network
    assert "not enforced" in report.filesystem


def test_process_sandbox_runs_a_command_in_the_root(tmp_path):
    sandbox = ProcessSandbox(root=tmp_path)
    result = sandbox.run("echo ok")

    assert isinstance(result, CommandResult)
    assert result.returncode == 0
    assert "ok" in result.stdout
    assert not result.timed_out


def test_process_sandbox_uses_the_workspace_as_cwd(tmp_path):
    sandbox = ProcessSandbox(root=tmp_path)
    command = f'"{sys.executable}" -c "import os, sys; sys.stdout.write(os.getcwd())"'
    result = sandbox.run(command)

    assert result.returncode == 0
    assert os.path.normcase(os.path.realpath(result.stdout.strip())) == os.path.normcase(
        os.path.realpath(str(tmp_path))
    )


def test_process_sandbox_times_out(tmp_path):
    sandbox = ProcessSandbox(root=tmp_path, timeout=30)
    result = sandbox.run(f'"{sys.executable}" -c "import time; time.sleep(5)"', timeout=1)

    assert result.timed_out is True
    assert result.returncode == -1
    assert "timed out" in result.stderr


def test_process_sandbox_refuses_an_empty_command(tmp_path):
    result = ProcessSandbox(root=tmp_path).run("   ")

    assert result.returncode == 2
    assert result.stderr
    assert not result.timed_out


def test_process_sandbox_reports_a_missing_root_instead_of_raising(tmp_path):
    result = ProcessSandbox(root=tmp_path / "missing").run("echo ok")

    assert result.returncode == 2
    assert "could not run" in result.stderr


def test_process_sandbox_does_not_leak_secrets_to_the_child(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    script = tmp_path / "dump_env.py"
    script.write_text("import os\nprint(os.environ.get('OPENAI_API_KEY', 'absent'))\n", encoding="utf-8")

    result = ProcessSandbox(root=tmp_path).run(f'"{sys.executable}" dump_env.py')

    assert result.returncode == 0
    assert "absent" in result.stdout
    assert "sk-should-not-leak" not in result.stdout


def test_describe_reports_the_honest_limits(tmp_path):
    sandbox = ProcessSandbox(root=tmp_path, timeout=15, allow_network=False)
    described = sandbox.describe()

    assert described["root"] == str(Path(tmp_path).resolve())
    assert described["timeout"] == 15
    assert described["allow_network"] is False
    assert described["sandbox"]["available"] is False or described["sandbox"]["kind"] == "bwrap"
    assert described["sandbox"]["reason"]
