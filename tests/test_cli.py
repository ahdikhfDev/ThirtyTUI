"""CLI-level regressions: commands must work when invoked with their defaults."""

import pytest
from typer.testing import CliRunner

from thirtycli.cli import app
from thirtycli.config import Config, load, save
from thirtycli.models import DETECT_ORDER, env_var_for


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def test_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "thirtycli" in result.output


def test_no_args_opens_chat_with_real_defaults(home):
    # Regression: defaults used to leak typer.OptionInfo into the chat state.
    save(Config(provider="ollama", model="llama3.2"))
    result = CliRunner().invoke(app, [], input="")
    assert result.exit_code == 0, result.output
    assert "ThirtyCLI" in result.output
    assert "ollama" in result.output


def test_missing_key_fails_cleanly(home, monkeypatch):
    for provider in (*DETECT_ORDER, "ollama", "lmstudio"):
        monkeypatch.delenv(env_var_for(provider), raising=False)
    save(Config(provider="openai", model="gpt-4o-mini"))
    result = CliRunner().invoke(app, [], input="")
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert "Traceback" not in result.output


def test_bare_autodetects_deepseek(home, monkeypatch):
    for provider in DETECT_ORDER:
        monkeypatch.delenv(env_var_for(provider), raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    result = CliRunner().invoke(app, [], input="")
    assert result.exit_code == 0, result.output
    assert "deepseek" in result.output
    assert load().provider == "deepseek"


def test_yolo_flag_skips_approvals(home):
    save(Config(provider="ollama", model="llama3.2"))
    result = CliRunner().invoke(app, ["--yolo"], input="")
    assert result.exit_code == 0, result.output
    assert "yolo mode" in result.output


def test_config_set_roundtrip(home):
    result = CliRunner().invoke(app, ["config", "set", "provider", "ollama"])
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(app, ["config", "set", "model", "qwen2.5"])
    assert result.exit_code == 0
    cfg = load()
    assert cfg.provider == "ollama"
    assert cfg.model == "qwen2.5"
