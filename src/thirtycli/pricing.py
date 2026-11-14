"""Rough cost estimate from token usage. Prices are per 1M tokens, USD."""

from __future__ import annotations

# (input, output) per 1,000,000 tokens. Extend freely; longest match wins.
PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "llama-3.3-70b": (0.59, 0.79),
}


def price_for(model: str) -> tuple[float, float] | None:
    name = (model or "").lower()
    matches = [key for key in PRICES if key in name]
    if not matches:
        return None
    return PRICES[max(matches, key=len)]


def estimate(model: str, usage: dict | None) -> float | None:
    if not usage or not usage.get("total"):
        return None
    price = price_for(model)
    if price is None:
        return None
    prompt, completion = price
    return (usage.get("input", 0) * prompt + usage.get("output", 0) * completion) / 1_000_000
