"""Capability metadata and deterministic, explainable model routing."""

from dataclasses import FrozenInstanceError

import pytest

from thirtycli.config import Config
from thirtycli.models import (
    MODEL_CAPABILITIES,
    PROVIDERS,
    RouteDecision,
    build_model,
    capabilities,
    default_model,
    env_var_for,
    explain_route,
    key_status,
    provider_available,
    route_model,
)

ALL_ENV = [env_var_for(provider) for provider in PROVIDERS]
CAPABILITY_FIELDS = {
    "context_window",
    "tools",
    "vision",
    "parallel_tools",
    "cost_in",
    "cost_out",
    "latency",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL_ENV:
        if name:
            monkeypatch.delenv(name, raising=False)


def _blended(caps: dict) -> float:
    return caps["cost_in"] + caps["cost_out"]


# ---------------------------------------------------------------------------
# Capability metadata
# ---------------------------------------------------------------------------


def test_capabilities_known_model():
    caps = capabilities("openai", "gpt-4o-mini")
    assert CAPABILITY_FIELDS <= set(caps)
    assert caps["context_window"] == 128_000
    assert caps["tools"] is True
    assert caps["vision"] is True
    assert caps["cost_in"] < caps["cost_out"]
    assert caps["latency"] in {"low", "medium", "high"}


def test_capabilities_lookup_is_independent_of_configured_provider():
    assert capabilities("openai", "claude-sonnet-4-5") == MODEL_CAPABILITIES["claude-sonnet-4-5"]
    assert capabilities("anthropic", "gemini-2.5-pro")["context_window"] == 1_048_576


def test_capabilities_unknown_model_is_conservative():
    caps = capabilities("openai", "definitely-not-a-real-model")
    assert caps == {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "medium",
    }


def test_capabilities_unknown_provider_does_not_crash():
    caps = capabilities("acme", "anything")
    assert caps["tools"] is False
    assert caps["context_window"] == 32_768


def test_capabilities_returns_a_copy():
    caps = capabilities("openai", "gpt-4o")
    caps["context_window"] = 1
    assert MODEL_CAPABILITIES["gpt-4o"]["context_window"] == 128_000


def test_known_default_models_have_metadata():
    for provider in PROVIDERS:
        assert default_model(provider) in MODEL_CAPABILITIES


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_provider_available_uses_env_and_config(monkeypatch):
    cfg = Config(provider="openai")
    assert not provider_available("openai", cfg)
    assert provider_available("ollama", cfg)
    assert provider_available("lmstudio", cfg)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert provider_available("openai", cfg)
    assert not provider_available("deepseek", cfg)

    cfg_with_key = Config(provider="deepseek", api_key="sk-config")
    assert provider_available("deepseek", cfg_with_key)


def test_provider_available_rejects_unknown_provider():
    assert not provider_available("acme", Config())


# ---------------------------------------------------------------------------
# Default routing: never surprising
# ---------------------------------------------------------------------------


def test_route_keeps_default_setting_without_switch(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    decision = route_model(cfg)

    assert isinstance(decision, RouteDecision)
    assert (decision.provider, decision.model) == ("openai", "gpt-4o")
    assert decision.fallback is False
    assert "keeping configured" in decision.reason
    assert "openai/gpt-4o" in decision.reason
    assert decision.score > 0


def test_route_uses_provider_default_model_when_cfg_is_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    decision = route_model(Config(provider="openai", model=""))
    assert decision.model == default_model("openai")


def test_route_unknown_model_does_not_crash(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    decision = route_model(Config(provider="openai", model="my-private-model"))
    assert decision.model == "my-private-model"
    assert decision.reason


def test_route_does_not_mutate_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    route_model(cfg, prefer="cost", allow_switch=True)
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4o"


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_allow_switch_cost_picks_cheaper_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    decision = route_model(cfg, prefer="cost", allow_switch=True)

    chosen = capabilities(decision.provider, decision.model)
    configured = capabilities("openai", "gpt-4o")
    assert decision.provider == "openai"
    assert decision.model == "gpt-4o-mini"
    assert _blended(chosen) < _blended(configured)
    assert "prefer=cost" in decision.reason
    assert decision.fallback is True


def test_allow_switch_quality_picks_better_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o-mini")
    balanced = route_model(cfg)
    decision = route_model(cfg, prefer="quality", allow_switch=True)

    chosen = capabilities(decision.provider, decision.model)
    configured = capabilities("openai", "gpt-4o-mini")
    assert decision.model == "gpt-4o"
    assert chosen["context_window"] >= configured["context_window"]
    assert decision.score > balanced.score
    assert "prefer=quality" in decision.reason


def test_allow_switch_quality_considers_other_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    cfg = Config(provider="openai", model="gpt-4o-mini")
    decision = route_model(cfg, prefer="quality", allow_switch=True)

    assert decision.provider == "anthropic"
    assert capabilities(decision.provider, decision.model)["context_window"] == 200_000


def test_allow_switch_latency_picks_low_latency(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    decision = route_model(cfg, prefer="latency", allow_switch=True)

    assert decision.model == "gpt-4o-mini"
    assert capabilities(decision.provider, decision.model)["latency"] == "low"
    assert "prefer=latency" in decision.reason


def test_prefer_without_allow_switch_stays_on_the_same_provider(monkeypatch):
    # A local provider has no key, so it must never be an automatic target.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    decision = route_model(cfg, prefer="cost", allow_switch=False)

    assert decision.provider == "openai"
    assert decision.model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Fallback model
# ---------------------------------------------------------------------------


def test_fallback_model_switches_provider_when_keyed(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    cfg = Config(provider="openai", model="gpt-4o", fallback_model="deepseek-chat")

    decision = route_model(cfg, prefer="cost")

    assert (decision.provider, decision.model) == ("deepseek", "deepseek-chat")
    assert decision.fallback is True


def test_fallback_model_is_not_used_when_its_provider_has_no_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o", fallback_model="deepseek-chat")

    decision = route_model(cfg, prefer="cost")

    assert decision.provider == "openai"
    assert decision.model != "deepseek-chat"


def test_no_provider_switch_without_allow_switch_or_fallback(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    cfg = Config(provider="openai", model="gpt-4o")

    decision = route_model(cfg, prefer="cost")

    assert (decision.provider, decision.model) == ("openai", "gpt-4o")
    assert decision.fallback is False
    assert "no API key" in decision.reason


# ---------------------------------------------------------------------------
# Tool requirements
# ---------------------------------------------------------------------------


def test_model_without_tools_stays_when_switching_is_disabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="o1-mini")

    decision = route_model(cfg, requires_tools=True, allow_switch=False)

    assert (decision.provider, decision.model) == ("openai", "o1-mini")
    assert decision.fallback is False
    assert "tool" in decision.reason.lower()


def test_model_without_tools_switches_when_allowed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="o1-mini")

    decision = route_model(cfg, requires_tools=True, allow_switch=True)

    assert capabilities(decision.provider, decision.model)["tools"] is True
    assert decision.fallback is True
    assert "tool" in decision.reason.lower()


def test_tools_not_required_can_keep_a_tool_less_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="o1-mini")

    decision = route_model(cfg, requires_tools=False)

    assert decision.model == "o1-mini"
    assert decision.fallback is False


# ---------------------------------------------------------------------------
# Determinism and explanation
# ---------------------------------------------------------------------------


def test_routing_is_deterministic(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
    cfg = Config(provider="openai", model="gpt-4o-mini")

    first = route_model(cfg, prefer="quality", allow_switch=True)
    second = route_model(cfg, prefer="quality", allow_switch=True)

    assert first == second
    assert (first.provider, first.model) == (second.provider, second.model)


def test_tie_break_is_model_name(monkeypatch):
    # gpt-4o and gpt-4o-mini tie on context/features; the shorter name wins.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o-mini")
    decision = route_model(cfg, prefer="quality", allow_switch=True)
    assert decision.model == "gpt-4o"


def test_explain_route_is_one_line(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    decision = route_model(Config(provider="openai", model="gpt-4o"))

    line = explain_route(decision)

    assert "\n" not in line
    assert decision.provider in line
    assert decision.model in line
    assert decision.reason in line
    assert "primary" in line


def test_route_decision_is_frozen():
    decision = RouteDecision("openai", "gpt-4o-mini", "why", 1.0)
    with pytest.raises(FrozenInstanceError):
        decision.model = "gpt-4o"


def test_prefer_value_is_normalized(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    cfg = Config(provider="openai", model="gpt-4o")
    # An unknown preference falls back to balanced, i.e. no surprise switch.
    decision = route_model(cfg, prefer="fastest-cheapest", allow_switch=True)
    assert (decision.provider, decision.model) == ("openai", "gpt-4o")


# ---------------------------------------------------------------------------
# Existing API stays intact
# ---------------------------------------------------------------------------


def test_build_model_and_key_status_still_work():
    cfg = Config(provider="deepseek", api_key="sk-config")
    assert key_status("deepseek", cfg) == "set (config) · sk-****"
    assert build_model(cfg).model_name == "deepseek-chat"
