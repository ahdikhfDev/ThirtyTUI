"""Tool-pair-safe context trimming, token estimation, and tool-output capping."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

_TRUNCATED = "… (truncated)"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def estimate_tokens(messages: list) -> int:
    """Rough token estimate: sum of message text length // 4, minimum 1."""
    total = sum(len(_text(getattr(message, "content", ""))) for message in messages)
    return max(1, total // 4)


def trim_for_model(messages: list, max_tokens: int = 24_000) -> tuple[list, bool]:
    """Return (kept_messages, was_trimmed).

    Keeps leading system messages, the final human turn, and drops oldest
    whole blocks (a human message plus everything up to the next human) until
    the estimate fits. Tool-call/tool-message pairs are never split because
    block boundaries always fall on human messages.
    """
    if estimate_tokens(messages) <= max_tokens:
        return list(messages), False

    sys_end = 0
    while sys_end < len(messages) and isinstance(messages[sys_end], SystemMessage):
        sys_end += 1

    last_human = max(
        (index for index, message in enumerate(messages) if isinstance(message, HumanMessage)),
        default=-1,
    )

    if last_human < sys_end:
        return list(messages), False

    middle = messages[sys_end:last_human]
    block_starts = [index for index, message in enumerate(middle) if isinstance(message, HumanMessage)]

    if not block_starts:
        return list(messages), False

    head = messages[:sys_end] + middle[: block_starts[0]]
    suffix = messages[last_human:]

    for start in block_starts[1:]:
        kept = head + middle[start:] + suffix
        if estimate_tokens(kept) <= max_tokens:
            return kept, True

    return head + suffix, True


def clamp_tool_output(text: str, limit: int = 4000) -> str:
    """Hard-cap a tool result before it is stored in a session."""
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATED
