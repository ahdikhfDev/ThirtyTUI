"""Provider auto-detection, config-stored keys and .env loading."""

import os

import pytest

from thirtycli.config import Config, load_env_files
from thirtycli.models import (
    DETECT_ORDER,
    autodetect,
    build_model,
    env_var_for,
    key_status,
)

ALL_ENV = [env_var_for(p) for p in (*DETECT_ORDER, "ollama", "lmstudio")]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL_ENV:
        if name:
            monkeypatch.delenv(name, raising=False)


def test_autodetect_picks_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    cfg = Config()
    note = autodetect(cfg)
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-chat"
    assert note and "DEEPSEEK_API_KEY" in note


def test_autodetect_keeps_provider_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    cfg = Config(provider="openai")
    assert autodetect(cfg) is None
    assert cfg.provider == "openai"


def test_autodetect_does_nothing_without_keys():
    cfg = Config()
    assert autodetect(cfg) is None
    assert cfg.provider == "openai"


def test_autodetect_skips_local_providers(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk")
    cfg = Config(provider="ollama", model="llama3.2")
    assert autodetect(cfg) is None
    assert cfg.provider == "ollama"


def test_key_in_config_is_used():
    cfg = Config(provider="deepseek", api_key="sk-config")
    assert key_status("deepseek", cfg) == "set (config) · sk-****"
    model = build_model(cfg)
    assert model.model_name == "deepseek-chat"


def test_env_wins_over_config(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    cfg = Config(provider="deepseek", api_key="sk-config")
    assert key_status("deepseek", cfg) == "set (env) · sk-****"


def test_dotenv_loading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("THIRTYCLI_TEST_KEY", raising=False)
    (tmp_path / ".env").write_text('THIRTYCLI_TEST_KEY="abc"\n# comment\n', encoding="utf-8")
    load_env_files()
    assert os.environ["THIRTYCLI_TEST_KEY"] == "abc"
