from pathlib import Path

import pytest

from thirtycli.config import Config, config_path, load, save, set_value


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path


def test_defaults_when_missing():
    cfg = load()
    assert cfg.provider == "openai"
    assert cfg.model == ""
    assert cfg.tools is True


def test_roundtrip():
    cfg = load()
    cfg.provider = "ollama"
    cfg.model = "qwen2.5"
    cfg.temperature = 0.7
    cfg.base_url = "http://localhost:11434/v1"
    cfg.system = 'Be "brief" and use \\ backslashes'
    cfg.tools = True
    save(cfg)

    again = load()
    assert again.provider == "ollama"
    assert again.model == "qwen2.5"
    assert again.temperature == pytest.approx(0.7)
    assert again.base_url == "http://localhost:11434/v1"
    assert again.system == cfg.system
    assert again.tools is True


def test_set_value_coerces():
    cfg = Config()
    set_value(cfg, "temperature", "0.9")
    set_value(cfg, "tools", "yes")
    assert cfg.temperature == pytest.approx(0.9)
    assert cfg.tools is True
    with pytest.raises(KeyError):
        set_value(cfg, "nope", "1")


def test_config_path_uses_home(home):
    assert config_path() == Path(home) / "config.toml"
