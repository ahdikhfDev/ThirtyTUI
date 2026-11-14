"""`thirtycli phases` — plan, fan the work out to sub-agents, merge the results."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from . import ui
from .agents import SubAgentResult, run_subagent
from .config import Config
from .models import build_model
from .presenters import as_emitter
from .sessions import Session
from .tools import build_readonly_tools

PLAN_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "You plan work for a team of read-only research agents. Reply with JSON only."),
        (
            "human",
            "Task:\n{task}\n\n"
            "Split it into at most {limit} independent subtasks that can run in parallel. "
            "Each subtask must be self-contained. Reply with a JSON array of strings and nothing else.",
        ),
    ]
)

MERGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You merge findings from sub-agents into one answer for the user. "
            "Be concise. Keep file paths, commands and concrete facts. "
            "Do not invent anything that is not in the results.",
        ),
        ("human", "Task:\n{task}\n\nSub-agent results:\n{results}"),
    ]
)


def parse_tasks(text: str, limit: int = 4) -> list[str]:
    """Pull a list of subtasks out of a model reply (JSON first, bullets as fallback)."""
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            items = [str(item).strip() for item in data if str(item).strip()]
            if items:
                return items[:limit]

    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        cleaned = re.sub(r"^(?:[-*]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    return items[:limit]


def plan_tasks(model, task: str, limit: int = 4) -> list[str]:
    reply = (PLAN_PROMPT | model | StrOutputParser()).invoke({"task": task, "limit": limit})
    return parse_tasks(reply, limit)


def _usage_total(results: list[SubAgentResult]) -> dict:
    total = {"input": 0, "output": 0, "total": 0}
    for result in results:
        total["input"] += int(result.usage.get("input", 0))
        total["output"] += int(result.usage.get("output", 0))
        total["total"] += int(result.usage.get("total", 0))
    return total


def run_phases(cfg: Config, task: str, workers: int = 3, root: Path | None = None) -> str:
    emit = as_emitter(ui.default_console)
    started = time.time()
    root = root or Path.cwd()
    model = build_model(cfg)

    emit.info("phase 1 · plan")
    tasks = plan_tasks(model, task, limit=max(1, min(workers * 2, 6)))
    if not tasks:
        tasks = [task]
    for index, item in enumerate(tasks, 1):
        emit.info(f"  {index}. {item}")

    emit.info(f"phase 2 · {len(tasks)} sub-agent(s)")
    tools = build_readonly_tools(root)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as pool:
        futures = [
            pool.submit(run_subagent, model, item, f"worker {index + 1}", tools)
            for index, item in enumerate(tasks)
        ]
        results: list[SubAgentResult] = []
        for index, future in enumerate(futures, 1):
            result = future.result()
            results.append(result)
            emit.ok(f"worker {index} · {result.steps} step(s) · {result.text[:60]}")

    emit.info("phase 3 · merge")
    merged = "\n\n".join(
        f"### worker {index + 1} — {item}\n{result.text}"
        for index, (item, result) in enumerate(zip(tasks, results, strict=True))
    )
    chain = MERGE_PROMPT | model | StrOutputParser()
    parts: list[str] = []
    for chunk in chain.stream({"task": task, "results": merged[:24000]}):
        parts.append(chunk)
        emit.assistant_delta(chunk)
    emit.assistant_end()
    final = "".join(parts).strip()

    usage = _usage_total(results)
    session = Session(model=cfg.model)
    session.add("user", f"phases · {task}")
    if final:
        session.add("assistant", final)
    session.add_usage(usage["input"], usage["output"])
    session.save()
    emit.footer(time.time() - started, usage, cfg.model)
    emit.info(f"saved · {session.path}")
    return final
