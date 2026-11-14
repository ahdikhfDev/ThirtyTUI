"""Sub-agents: small, read-only workers the main agent can fan out to."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .tools import build_readonly_tools, tool_map

SUBAGENT_SYSTEM = (
    "You are a focused sub-agent inside ThirtyCLI, working for a parent agent. "
    "Role: {role}. Do only the task you are given. "
    "You have read-only tools: inspect files, search, fetch pages, check git. "
    "Never ask questions. Never describe what you would do: do it. "
    "Finish with a compact result (plain text, under 200 words)."
)

MAX_RESULT_CHARS = 4000


class AgentCancelled(RuntimeError):
    """Raised when the user cancels a sub-agent run."""


@dataclass
class SubAgentResult:
    text: str
    usage: dict = field(default_factory=dict)
    steps: int = 0


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content
        )
    return "" if content is None else str(content)


def _add_usage(total: dict, message: Any) -> None:
    raw = getattr(message, "usage_metadata", None) or {}
    prompt = int(raw.get("input_tokens") or 0)
    completion = int(raw.get("output_tokens") or 0)
    total["input"] = total.get("input", 0) + prompt
    total["output"] = total.get("output", 0) + completion
    total["total"] = total.get("input", 0) + total.get("output", 0)


def run_subagent(
    model,
    task: str,
    role: str = "worker",
    tools: list | None = None,
    max_steps: int = 4,
    system_prompt: str | None = None,
    cancel=None,
    execute_tool=None,
) -> SubAgentResult:
    """Run a short tool loop for one self-contained task."""
    tools = tools or []
    by_name = tool_map(tools)
    system = system_prompt or SUBAGENT_SYSTEM.format(role=role)
    messages: list = [
        SystemMessage(system),
        HumanMessage(f"Task: {task}"),
    ]
    usage: dict = {}

    for step in range(1, max_steps + 1):
        if cancel is not None and cancel.is_set():
            raise AgentCancelled("cancelled by the user")
        bound = model.bind_tools(tools) if tools else model
        ai = bound.invoke(messages)
        _add_usage(usage, ai)
        messages.append(ai)
        calls = list(getattr(ai, "tool_calls", None) or [])
        if not calls:
            return SubAgentResult(_text(ai.content).strip()[:MAX_RESULT_CHARS], usage, step)

        for index, call in enumerate(calls):
            if cancel is not None and cancel.is_set():
                raise AgentCancelled("cancelled by the user")
            name = call.get("name", "")
            args = call.get("args") or {}
            if execute_tool is not None:
                try:
                    result = str(execute_tool(name, args))
                except Exception as exc:  # the executor reports policy/tool errors
                    result = f"Tool error: {exc}"
            else:
                tool = by_name.get(name)
                if tool is None:
                    result = f"Unknown tool: {name}"
                else:
                    try:
                        result = str(tool.invoke(args))
                    except Exception as exc:  # report back, keep the loop alive
                        result = f"Tool error: {exc}"
            messages.append(
                ToolMessage(content=result[:MAX_RESULT_CHARS], tool_call_id=call.get("id") or f"sub{step}{index}")
            )

    if cancel is not None and cancel.is_set():
        raise AgentCancelled("cancelled by the user")
    final = model.invoke(messages + [HumanMessage("Stop using tools. Return your result now.")])
    _add_usage(usage, final)
    return SubAgentResult(_text(final.content).strip()[:MAX_RESULT_CHARS], usage, max_steps + 1)


def fan_out(model, tasks: list[str], tools: list | None = None, workers: int = 3) -> list[SubAgentResult]:
    """Run several sub-agents in parallel and keep the input order."""
    from concurrent.futures import ThreadPoolExecutor

    if not tasks:
        return []
    results: list[SubAgentResult | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as pool:
        futures = {
            pool.submit(run_subagent, model, task, f"worker {index + 1}", tools): index
            for index, task in enumerate(tasks)
        }
        for future, index in futures.items():
            results[index] = future.result()
    return [result for result in results if result is not None]


def readonly_tools(root) -> list:
    return build_readonly_tools(root)
