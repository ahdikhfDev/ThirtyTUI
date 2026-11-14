"""API keys must never reach the terminal in full."""

import pytest

from thirtycli.config import Config
from thirtycli.models import key_status
from thirtycli.secrets import mask_token, redact


def test_masks_openai_key():
    text = redact("using sk-abcdef1234567890 now")
    assert "sk-abcdef1234567890" not in text
    assert "sk-****7890" in text


def test_masks_anthropic_key():
    text = redact("key=sk-ant-api03-abcdef1234567890")
    assert "abcdef1234567890" not in text
    assert "sk-****7890" in text


def test_masks_authorization_header():
    assert redact("Authorization: Bearer abcdef1234567890") == "Authorization: Bearer ****"


def test_masks_key_assignment():
    out = redact('api_key = "sk-abcdef1234567890"')
    assert "sk-abcdef1234567890" not in out
    assert out.startswith('api_key = "')


def test_masks_google_and_groq_tokens():
    assert "AIzaSyB1234567890abcdef" not in redact("AIzaSyB1234567890abcdef")
    assert "gsk_1234567890abcdef" not in redact("gsk_1234567890abcdef")


def test_leaves_normal_text_alone():
    text = "the quick brown fox jumps over the lazy dog"
    assert redact(text) == text


def test_mask_token_short_hides_everything_after_prefix():
    assert mask_token("sk-test") == "sk-****"
    assert mask_token("") == "-"


def test_key_status_masks_env_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abcdef1234567890")
    status = key_status("deepseek")
    assert status == "set (env) · sk-****7890"
    assert "abcdef1234567890" not in status


def test_key_status_masks_config_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = Config(provider="deepseek", api_key="sk-abcdef1234567890")
    status = key_status("deepseek", cfg)
    assert status == "set (config) · sk-****7890"
    assert "abcdef1234567890" not in status


@pytest.mark.parametrize("source", ["env", "config"])
def test_config_show_never_prints_the_full_key(home, monkeypatch, source):
    from typer.testing import CliRunner

    from thirtycli.cli import app
    from thirtycli.config import save

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = Config(provider="deepseek", api_key="")
    if source == "env":
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abcdef1234567890")
    else:
        cfg.api_key = "sk-abcdef1234567890"
    save(cfg)

    result = CliRunner().invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "sk-****7890" in result.output
    assert "sk-abcdef1234567890" not in result.output


def test_config_set_api_key_is_masked(home):
    from typer.testing import CliRunner

    from thirtycli.cli import app

    result = CliRunner().invoke(app, ["config", "set", "api_key", "sk-abcdef1234567890"])
    assert result.exit_code == 0, result.output
    assert "sk-****7890" in result.output
    assert "sk-abcdef1234567890" not in result.output


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    return tmp_path
