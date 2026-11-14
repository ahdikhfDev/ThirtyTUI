"""Phases: planning, parallel sub-agents, merge."""

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from tests.test_chat_loop import ScriptedModel
from thirtycli.cli import app
from thirtycli.config import Config, save
from thirtycli.phases import parse_tasks, plan_tasks, run_phases


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def test_parse_tasks_json():
    assert parse_tasks('["a", "b"]') == ["a", "b"]
    assert parse_tasks('```json\n["x", "y"]\n```') == ["x", "y"]
    assert parse_tasks('["1","2","3","4","5"]', limit=2) == ["1", "2"]


def test_parse_tasks_bullets_fallback():
    assert parse_tasks("- one\n- two\n\n3. three") == ["one", "two", "three"]


def test_plan_tasks_uses_the_model():
    model = ScriptedModel(replies=[AIMessage(content='["look at a", "look at b"]')])
    assert plan_tasks(model, "task", limit=2) == ["look at a", "look at b"]


def test_run_phases_end_to_end(monkeypatch, tmp_path):
    model = ScriptedModel(
        replies=[
            AIMessage(content='["only task"]'),
            AIMessage(content="worker result"),
            AIMessage(content="final answer"),
        ]
    )
    monkeypatch.setattr("thirtycli.phases.build_model", lambda cfg: model)
    final = run_phases(Config(provider="ollama", model="x"), "do it", workers=1, root=tmp_path)
    assert final == "final answer"


def test_phases_cli(monkeypatch):
    save(Config(provider="ollama", model="llama3.2"))
    seen: dict = {}
    monkeypatch.setattr(
        "thirtycli.phases.run_phases",
        lambda cfg, text, workers: seen.update(text=text, workers=workers),
    )
    result = CliRunner().invoke(app, ["phases", "do", "it", "--workers", "2"])
    assert result.exit_code == 0, result.output
    assert seen == {"text": "do it", "workers": 2}
