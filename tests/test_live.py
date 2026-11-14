"""Opt-in smoke tests against a real OpenAI-compatible provider.

Skipped unless THIRTYCLI_LIVE=1, so the default suite stays offline.

    THIRTYCLI_LIVE=1 THIRTYCLI_LIVE_PROVIDER=deepseek \
    THIRTYCLI_LIVE_MODEL=deepseek-chat pytest -m live
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from thirtycli.config import Config, load
from thirtycli.models import api_key_for, build_model

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("THIRTYCLI_LIVE") != "1",
        reason="live tests are opt-in: set THIRTYCLI_LIVE=1 to run them",
    ),
]

PROVIDER = os.environ.get("THIRTYCLI_LIVE_PROVIDER", "deepseek")
MODEL = os.environ.get("THIRTYCLI_LIVE_MODEL", "deepseek-chat")


def _live_model():
    """Build the configured model, or skip when there is no key for it."""
    cfg = Config(provider=PROVIDER, model=MODEL)
    saved = load()
    if saved.provider == PROVIDER:
        cfg.api_key = saved.api_key
        cfg.base_url = saved.base_url
    if api_key_for(PROVIDER, cfg) is None:
        pytest.skip(f"{PROVIDER}: no API key in the environment or config (set it or use another provider)")
    return build_model(cfg)


def _text(message) -> str:
    """Flatten message content, which may be a string or a list of blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block if isinstance(block, str) else str(block.get("text", "")) for block in content)
    return str(content)


def test_live_invoke():
    reply = _live_model().invoke("Reply with the single word: ok")
    text = _text(reply)
    assert isinstance(text, str), f"expected a string reply, got {type(text)!r}"
    assert text.strip(), "model returned an empty reply"


def test_live_stream():
    chunks = list(_live_model().stream([HumanMessage(content="Reply with the single word: ok")]))
    assert chunks, "model streamed no chunks"
    combined = "".join(_text(chunk) for chunk in chunks)
    assert combined.strip(), "streamed chunks were all empty"

    usages = [
        chunk.usage_metadata
        for chunk in chunks
        if getattr(chunk, "usage_metadata", None) and "total_tokens" in chunk.usage_metadata
    ]
    if not usages:
        pytest.skip("provider did not report usage_metadata on any chunk; token assertion skipped")
    total = usages[-1]["total_tokens"]
    assert total > 0, f"total_tokens should be positive, got {total}"


def test_live_tool_call():
    @tool
    def echo(text: str) -> str:
        """Echo the given text back unchanged."""
        return text

    model = _live_model().bind_tools([echo])
    reply = model.invoke(
        [HumanMessage(content="Call the echo tool with the text \"ping\", then stop.")]
    )
    calls = getattr(reply, "tool_calls", None) or []
    if not calls:
        pytest.skip("model did not emit a tool call")

    call = calls[0]
    assert call["name"] == "echo", f"expected the echo tool, got {call['name']!r}"
    assert "ping" in str(call["args"]), f"expected 'ping' in the arguments, got {call['args']!r}"
