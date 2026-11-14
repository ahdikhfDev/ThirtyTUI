"""Provider resolution: one Config in, one LangChain chat model out."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .config import Config
from .secrets import mask_key


class ModelError(RuntimeError):
    """Raised when a provider cannot be built, with a fix hint."""


# provider -> (base_url, api-key env var, default model, needs a real key)
OPENAI_COMPATIBLE = {
    "openai": (None, "OPENAI_API_KEY", "gpt-4o-mini", True),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openai/gpt-4o-mini", True),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", "deepseek-chat", True),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile", True),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo", True),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY", "grok-2-latest", True),
    "ollama": ("http://localhost:11434/v1", "OLLAMA_API_KEY", "llama3.2", False),
    "lmstudio": ("http://localhost:1234/v1", "LMSTUDIO_API_KEY", "local-model", False),
    "custom": (None, "OPENAI_API_KEY", "gpt-4o-mini", True),
}

NATIVE = {
    "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-5"),
    "google": ("GOOGLE_API_KEY", "gemini-2.5-flash"),
    "gemini": ("GOOGLE_API_KEY", "gemini-2.5-flash"),
}

PROVIDERS = sorted({*OPENAI_COMPATIBLE, *NATIVE})

# Curated choices for the interactive pickers. The configured model is always
# added on top, so a custom name is never lost.
MODEL_CHOICES = {
    "openai": ("gpt-4o-mini", "gpt-4o", "o3-mini", "o1-mini"),
    "openrouter": (
        "openai/gpt-4o-mini",
        "anthropic/claude-sonnet-4.5",
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
    ),
    "deepseek": ("deepseek-chat", "deepseek-reasoner"),
    "groq": ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"),
    "together": (
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "Qwen/Qwen2.5-72B-Instruct-Turbo",
    ),
    "xai": ("grok-2-latest", "grok-beta"),
    "anthropic": ("claude-sonnet-4-5", "claude-opus-4-1", "claude-3-5-haiku-latest"),
    "google": ("gemini-2.5-flash", "gemini-2.5-pro"),
    "gemini": ("gemini-2.5-flash", "gemini-2.5-pro"),
    "ollama": ("llama3.2", "qwen2.5-coder", "mistral", "deepseek-r1"),
    "lmstudio": ("local-model",),
    "custom": ("gpt-4o-mini",),
}

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "groq": "Groq",
    "together": "Together",
    "xai": "xAI",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "custom": "Custom",
    "anthropic": "Anthropic",
    "google": "Google",
    "gemini": "Google",
}


def default_model(provider: str) -> str:
    if provider in OPENAI_COMPATIBLE:
        return OPENAI_COMPATIBLE[provider][2]
    if provider in NATIVE:
        return NATIVE[provider][1]
    return "gpt-4o-mini"


def needs_key(provider: str) -> bool:
    if provider in OPENAI_COMPATIBLE:
        return OPENAI_COMPATIBLE[provider][3]
    return provider in NATIVE


def env_var_for(provider: str) -> str:
    if provider in OPENAI_COMPATIBLE:
        return OPENAI_COMPATIBLE[provider][1]
    return NATIVE.get(provider, ("", ""))[0]


def api_key_for(provider: str, cfg: Config | None = None) -> str | None:
    """Environment wins; a key stored in the config file is the fallback."""
    key = os.environ.get(env_var_for(provider)) or None
    if not key and cfg is not None and cfg.api_key:
        key = cfg.api_key
    return key


def key_source(provider: str, cfg: Config | None = None) -> str:
    if not needs_key(provider):
        return "not needed"
    if os.environ.get(env_var_for(provider)):
        return "set (env)"
    if cfg is not None and cfg.api_key:
        return "set (config)"
    return "missing"


def key_status(provider: str, cfg: Config | None = None) -> str:
    """Human answer for `config show` / `doctor`, with only a key hint."""
    source = key_source(provider, cfg)
    if source == "set (env)":
        return f"{source} · {mask_key(os.environ.get(env_var_for(provider), ''))}"
    if source == "set (config)" and cfg is not None:
        return f"{source} · {mask_key(cfg.api_key)}"
    return source


# In order of preference when the configured provider has no key.
DETECT_ORDER = (
    "deepseek",
    "openrouter",
    "anthropic",
    "groq",
    "together",
    "xai",
    "google",
    "openai",
)


def autodetect(cfg: Config) -> str | None:
    """If the configured provider has no key, switch to one that does.

    Mutates cfg and returns a one-line note for the UI, or None.
    """
    if not needs_key(cfg.provider) or api_key_for(cfg.provider, cfg):
        return None
    for provider in DETECT_ORDER:
        if provider == cfg.provider:
            continue
        if os.environ.get(env_var_for(provider)):
            old = cfg.provider
            cfg.provider = provider
            cfg.model = default_model(provider)
            return (
                f"{old} has no key — using {provider} · {cfg.model} "
                f"(found {env_var_for(provider)}). Saved to config."
            )
    return None


def build_model(cfg: Config):
    provider = cfg.provider.lower()
    model = cfg.model or default_model(provider)

    if provider in NATIVE:
        key = api_key_for(provider, cfg)
        env = NATIVE[provider][0]
        if not key:
            raise ModelError(
                f"{env} is not set. Add it to your shell, or run: thirtycli config set api_key <key>"
            )
        try:
            if provider == "anthropic":
                from langchain_anthropic import ChatAnthropic
            else:
                from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError as exc:  # pragma: no cover - depends on extras
            extra = "anthropic" if provider == "anthropic" else "google"
            raise ModelError(f"Install the extra first: pip install 'thirtycli[{extra}]'") from exc
        cls = ChatAnthropic if provider == "anthropic" else ChatGoogleGenerativeAI
        return cls(model=model, api_key=key, temperature=cfg.temperature)

    if provider not in OPENAI_COMPATIBLE:
        raise ModelError(f"Unknown provider '{provider}'. Known: {', '.join(PROVIDERS)}")

    from langchain_openai import ChatOpenAI

    default_base, key_env, _default, requires_key = OPENAI_COMPATIBLE[provider]
    base_url = cfg.base_url or default_base
    key = api_key_for(provider, cfg)
    if requires_key and not key:
        raise ModelError(
            f"No key for {provider}. Set {key_env}, or run: thirtycli config set api_key <key> "
            "(a local base_url needs none)."
        )
    if not key:
        key = "not-needed"

    kwargs = {
        "model": model,
        "api_key": key,
        "temperature": cfg.temperature,
        "timeout": 120,
        "max_retries": 2,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


def describe(cfg: Config) -> str:
    return f"{cfg.provider} · {cfg.model}"


# ---------------------------------------------------------------------------
# Capability metadata
# ---------------------------------------------------------------------------
# Prices are USD per 1M tokens and deliberately coarse: they rank candidates,
# they are not an invoice. `latency` is one of "low", "medium" or "high".
MODEL_CAPABILITIES: dict[str, dict] = {
    "gpt-4o-mini": {
        "context_window": 128_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 0.15,
        "cost_out": 0.60,
        "latency": "low",
    },
    "gpt-4o": {
        "context_window": 128_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 2.50,
        "cost_out": 10.00,
        "latency": "medium",
    },
    "o3-mini": {
        "context_window": 200_000,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 1.10,
        "cost_out": 4.40,
        "latency": "high",
    },
    "o1-mini": {
        "context_window": 128_000,
        "tools": False,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 1.10,
        "cost_out": 4.40,
        "latency": "high",
    },
    "openai/gpt-4o-mini": {
        "context_window": 128_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 0.15,
        "cost_out": 0.60,
        "latency": "low",
    },
    "anthropic/claude-sonnet-4.5": {
        "context_window": 200_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 3.00,
        "cost_out": 15.00,
        "latency": "medium",
    },
    "google/gemini-2.5-flash": {
        "context_window": 1_048_576,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 0.30,
        "cost_out": 2.50,
        "latency": "low",
    },
    "deepseek/deepseek-chat": {
        "context_window": 64_000,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.27,
        "cost_out": 1.10,
        "latency": "medium",
    },
    "meta-llama/llama-3.3-70b-instruct": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 0.12,
        "cost_out": 0.30,
        "latency": "medium",
    },
    "deepseek-chat": {
        "context_window": 64_000,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.27,
        "cost_out": 1.10,
        "latency": "medium",
    },
    "deepseek-reasoner": {
        "context_window": 64_000,
        "tools": False,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.55,
        "cost_out": 2.19,
        "latency": "high",
    },
    "llama-3.3-70b-versatile": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 0.59,
        "cost_out": 0.79,
        "latency": "low",
    },
    "llama-3.1-8b-instant": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 0.05,
        "cost_out": 0.08,
        "latency": "low",
    },
    "mixtral-8x7b-32768": {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.24,
        "cost_out": 0.24,
        "latency": "low",
    },
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 0.88,
        "cost_out": 0.88,
        "latency": "medium",
    },
    "Qwen/Qwen2.5-72B-Instruct-Turbo": {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 1.20,
        "cost_out": 1.20,
        "latency": "medium",
    },
    "grok-2-latest": {
        "context_window": 131_072,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 2.00,
        "cost_out": 10.00,
        "latency": "medium",
    },
    "grok-beta": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 5.00,
        "cost_out": 15.00,
        "latency": "medium",
    },
    "claude-sonnet-4-5": {
        "context_window": 200_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 3.00,
        "cost_out": 15.00,
        "latency": "medium",
    },
    "claude-opus-4-1": {
        "context_window": 200_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 15.00,
        "cost_out": 75.00,
        "latency": "high",
    },
    "claude-3-5-haiku-latest": {
        "context_window": 200_000,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 0.80,
        "cost_out": 4.00,
        "latency": "low",
    },
    "gemini-2.5-flash": {
        "context_window": 1_048_576,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 0.30,
        "cost_out": 2.50,
        "latency": "low",
    },
    "gemini-2.5-pro": {
        "context_window": 1_048_576,
        "tools": True,
        "vision": True,
        "parallel_tools": True,
        "cost_in": 1.25,
        "cost_out": 10.00,
        "latency": "medium",
    },
    "llama3.2": {
        "context_window": 131_072,
        "tools": True,
        "vision": False,
        "parallel_tools": True,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "low",
    },
    "qwen2.5-coder": {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "medium",
    },
    "mistral": {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "low",
    },
    "deepseek-r1": {
        "context_window": 65_536,
        "tools": False,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "high",
    },
    "local-model": {
        "context_window": 32_768,
        "tools": True,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "medium",
    },
}

_CAPABILITIES_BY_LOWER = {name.lower(): caps for name, caps in MODEL_CAPABILITIES.items()}
_TOOL_PROVIDERS = frozenset(PROVIDERS)
_LOCAL_PROVIDERS = frozenset(
    provider for provider, entry in OPENAI_COMPATIBLE.items() if not entry[3]
)
_LATENCY_RANK = {"low": 3, "medium": 2, "high": 1}
_PREFERENCES = frozenset({"balanced", "cost", "latency", "quality"})


# Models are unique across providers, so a canonical home keeps routing simple.
# The first provider that lists a model wins; OPENAI_COMPATIBLE happens to list
# the common names before the NATIVE providers.
def _build_provider_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for provider in (*OPENAI_COMPATIBLE, *NATIVE):
        models = list(MODEL_CHOICES.get(provider, ()))
        default = default_model(provider)
        if default not in models:
            models.insert(0, default)
        for model in models:
            index.setdefault(model, provider)
    return index


_MODEL_PROVIDER = _build_provider_index()


def _default_capabilities(provider: str) -> dict:
    return {
        "context_window": 32_768,
        "tools": provider in _TOOL_PROVIDERS,
        "vision": False,
        "parallel_tools": False,
        "cost_in": 0.0,
        "cost_out": 0.0,
        "latency": "medium",
    }


def capabilities(provider: str, model: str) -> dict:
    """Metadata for a model, or a conservative provider default.

    Unknown models never raise: they get a small context window, no vision,
    no cost and medium latency so routing stays deterministic.
    """
    name = (model or "").strip()
    caps = MODEL_CAPABILITIES.get(name) or _CAPABILITIES_BY_LOWER.get(name.lower())
    if caps is None:
        return _default_capabilities((provider or "").strip().lower())
    return dict(caps)


def provider_available(provider: str, cfg: Config) -> bool:
    """True when the provider can be called: local providers need no key."""
    provider = (provider or "").strip().lower()
    if provider not in PROVIDERS:
        return False
    if not needs_key(provider):
        return True
    return bool(api_key_for(provider, cfg))


@dataclass(frozen=True)
class RouteDecision:
    provider: str
    model: str
    reason: str
    score: float
    fallback: bool = False


def _blended_cost(caps: dict) -> float:
    return float(caps.get("cost_in", 0.0)) + float(caps.get("cost_out", 0.0))


def _quality_score(caps: dict) -> float:
    """Bigger context and richer features win; list price breaks close ties."""
    return (
        float(caps.get("context_window", 0))
        + 500_000 * bool(caps.get("tools"))
        + 250_000 * bool(caps.get("vision"))
        + 100_000 * bool(caps.get("parallel_tools"))
        + 1_000 * float(caps.get("cost_out", 0.0))
    )


def _preference_score(caps: dict, prefer: str) -> float:
    if prefer == "cost":
        return -_blended_cost(caps)
    if prefer == "latency":
        return float(_LATENCY_RANK.get(caps.get("latency"), 2))
    return _quality_score(caps)


def _normalize_prefer(prefer: str) -> str:
    value = (prefer or "balanced").strip().lower()
    return value if value in _PREFERENCES else "balanced"


def _candidate_provider(model: str, cfg: Config, current_provider: str) -> str | None:
    if cfg.fallback_model and model == cfg.fallback_model:
        return (
            (cfg.fallback_provider or _MODEL_PROVIDER.get(model) or current_provider).strip().lower()
        )
    return _MODEL_PROVIDER.get(model)


def _candidate_rows(
    cfg: Config,
    current_provider: str,
    requires_tools: bool,
    allow_switch: bool,
) -> list[tuple[str, str, dict]]:
    """Eligible (provider, model, caps) rows, filtered by key and tools."""
    rows: list[tuple[str, str, dict]] = []
    seen: set[tuple[str, str]] = set()
    fallback_model = cfg.fallback_model or ""

    for model in MODEL_CAPABILITIES:
        provider = _candidate_provider(model, cfg, current_provider)
        if not provider:
            continue
        if (provider, model) in seen:
            continue
        if provider != current_provider:
            is_fallback = bool(fallback_model) and model == fallback_model
            if not allow_switch and not is_fallback:
                continue
            # Local servers are a deliberate choice, not an automatic target.
            if provider in _LOCAL_PROVIDERS and not is_fallback:
                continue
        caps = capabilities(provider, model)
        if requires_tools and not caps.get("tools", False):
            continue
        if not provider_available(provider, cfg):
            continue
        seen.add((provider, model))
        rows.append((provider, model, caps))

    # A custom fallback name may not be in the curated table; it still counts.
    if fallback_model and fallback_model not in MODEL_CAPABILITIES:
        provider = (cfg.fallback_provider or current_provider).strip().lower()
        caps = capabilities(provider, fallback_model)
        if not requires_tools or caps.get("tools", False):
            if provider_available(provider, cfg):
                seen.add((provider, fallback_model))
                rows.append((provider, fallback_model, caps))

    return rows


def _keep_reason(
    provider: str,
    model: str,
    caps: dict,
    available: bool,
    tools_ok: bool,
    allow_switch: bool,
    task_type: str,
) -> str:
    if not available:
        detail = f"no API key for {provider}"
    elif not tools_ok:
        detail = f"{model} does not support tools (requires_tools=True)"
    else:
        detail = (
            f"tools={'yes' if caps.get('tools') else 'no'}, "
            f"context={caps.get('context_window')}, latency={caps.get('latency')}, "
            f"cost=${_blended_cost(caps):.2f}/1M blended"
        )
    mode = "switching allowed" if allow_switch else "switching disabled"
    return f"keeping configured {provider}/{model} for {task_type}: {detail} ({mode})"


def _switch_reason(
    provider: str,
    model: str,
    caps: dict,
    current_provider: str,
    current_model: str,
    current_caps: dict,
    prefer: str,
    task_type: str,
) -> str:
    if prefer == "cost":
        detail = (
            f"${_blended_cost(caps):.2f}/1M blended is cheaper than "
            f"${_blended_cost(current_caps):.2f}/1M for {current_provider}/{current_model}"
        )
    elif prefer == "latency":
        detail = (
            f"latency {caps.get('latency')} beats {current_caps.get('latency')} for "
            f"{current_provider}/{current_model}"
        )
    else:
        detail = (
            f"quality score {_quality_score(caps):.0f} beats "
            f"{_quality_score(current_caps):.0f} for {current_provider}/{current_model}"
        )
    return f"prefer={prefer} picked {provider}/{model} for {task_type}: {detail}"


def route_model(
    cfg: Config,
    task_type: str = "coding",
    requires_tools: bool = True,
    prefer: str = "balanced",
    allow_switch: bool = False,
) -> RouteDecision:
    """Pick a provider/model for a task without surprising the user.

    The configured pair is kept unless it cannot do the job (missing key, no
    tool support) or the caller asks for a preference *and* permits a change.
    Cross-provider moves need ``allow_switch`` or a configured fallback model.
    """
    provider = (cfg.provider or "openai").strip().lower()
    model = cfg.model or default_model(provider)
    caps = capabilities(provider, model)
    available = provider_available(provider, cfg)
    tools_ok = bool(caps.get("tools")) or not requires_tools
    preference = _normalize_prefer(prefer)
    scoring = "quality" if preference == "balanced" else preference
    score = _preference_score(caps, scoring)
    keep_reason = _keep_reason(
        provider, model, caps, available, tools_ok, allow_switch, task_type
    )
    keep = RouteDecision(provider, model, keep_reason, score)

    must_fallback = not available or not tools_ok
    switching_allowed = allow_switch or bool(cfg.fallback_model)
    if preference == "balanced" and not must_fallback:
        return keep
    if must_fallback and not switching_allowed:
        return keep

    rows = _candidate_rows(cfg, provider, requires_tools, allow_switch)
    if not rows:
        return RouteDecision(
            provider,
            model,
            f"{keep_reason}; no eligible alternative found",
            score,
        )

    best_provider, best_model, best_caps = min(
        rows,
        key=lambda row: (-_preference_score(row[2], scoring), row[1], row[0]),
    )
    best_score = _preference_score(best_caps, scoring)
    if (best_provider, best_model) == (provider, model):
        return RouteDecision(
            provider,
            model,
            f"{keep_reason}; prefer={preference} confirms it",
            best_score,
        )

    if preference == "balanced":
        why = "has no key" if not available else "does not support tools"
        reason = (
            f"fallback to {best_provider}/{best_model} for {task_type}: "
            f"{provider}/{model} {why}"
        )
    else:
        reason = _switch_reason(
            best_provider,
            best_model,
            best_caps,
            provider,
            model,
            caps,
            preference,
            task_type,
        )
    return RouteDecision(best_provider, best_model, reason, best_score, fallback=True)


def explain_route(decision: RouteDecision) -> str:
    """One line a human can read in a log or a `/why` command."""
    kind = "fallback" if decision.fallback else "primary"
    text = (
        f"{decision.provider}/{decision.model} [{kind}, score {decision.score:.2f}] "
        f"— {decision.reason}"
    )
    return " ".join(text.split())
