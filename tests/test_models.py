import pytest

from thirtycli.config import Config
from thirtycli.models import ModelError, build_model, default_model


def test_default_models():
    assert default_model("openai") == "gpt-4o-mini"
    assert default_model("ollama") == "llama3.2"
    assert default_model("anthropic").startswith("claude")


def test_local_provider_needs_no_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    model = build_model(Config(provider="ollama", model="llama3.2"))
    assert model.model_name == "llama3.2"


def test_openai_without_key_fails(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ModelError):
        build_model(Config(provider="openai", model="gpt-4o-mini"))


def test_unknown_provider():
    with pytest.raises(ModelError):
        build_model(Config(provider="nope"))


def test_anthropic_without_extra_or_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ModelError):
        build_model(Config(provider="anthropic"))
