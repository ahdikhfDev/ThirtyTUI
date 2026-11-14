"""`thirtycli team` — planner → builder → reviewer, streamed like the site demo."""

from __future__ import annotations

import time

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from rich.console import Console
from rich.text import Text

from . import ui
from .config import Config
from .models import build_model
from .render import AssistantStream
from .sessions import Session

TEAM = [
    (
        "Ada",
        "planner",
        "#8B7BFF",
        "You are Ada, the planner on a small agent team. "
        "Turn the task into at most five concrete steps. "
        "Name the files or commands involved when they are obvious. Be brief.",
    ),
    (
        "Forge",
        "builder",
        "#61C985",
        "You are Forge, the builder on a small agent team. "
        "Do the task using the planner's steps. Produce the actual work: code, copy, "
        "commands. Skip small talk. If a step needs a decision, pick one and say why in one line.",
    ),
    (
        "Grace",
        "reviewer",
        "#FFB547",
        "You are Grace, the reviewer on a small agent team. "
        "Check the work against the task. List real problems only, then give the final answer "
        "the user should take away, cleaned up and ready to use.",
    ),
]

PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "{system}"),
        (
            "human",
            "Task:\n{task}\n\nEarlier work from the team (may be empty):\n{context}",
        ),
    ]
)


def run_team(cfg: Config, task: str, con: Console | None = None) -> str:
    con = con or ui.default_console
    model = build_model(cfg)
    context = ""
    final = ""

    for name, role, colour, system in TEAM:
        con.print()
        con.print(Text.assemble((f"{name}", f"bold {colour}"), (f" · {role}", "muted")))
        chain = PROMPT | model | StrOutputParser()  # LCEL
        pieces: list[str] = []
        stream = AssistantStream(con)
        began = time.time()
        try:
            for chunk in chain.stream({"system": system, "task": task, "context": context or "(nothing yet)"}):
                pieces.append(chunk)
                stream.feed(chunk)
        except Exception as exc:
            stream.close()
            ui.error(f"{name} failed: {exc}", con)
            break
        stream.close()
        output = "".join(pieces).strip()
        ui.footer(time.time() - began, {}, role, con)
        if not output:
            continue
        context = f"{context}\n\n{name} ({role}):\n{output}".strip()
        final = output

    session = Session(model=cfg.model)
    session.add("user", f"team · {task}")
    if final:
        session.add("assistant", final)
    session.save()
    con.print()
    ui.info(f"saved · {session.path}", con)
    return final
