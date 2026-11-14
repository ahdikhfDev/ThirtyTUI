"""Terminal rendering: banner, prompt, tool blocks, diffs, help."""

from __future__ import annotations

import difflib
import time

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .secrets import redact
from .theme import (
    DIM,
    GREEN,
    LINE,
    MARK_ROWS,
    MUTED,
    ORANGE,
    RED,
    THEME,
    console,
    prompt_text,
)

default_console = console

COMMANDS = [
    ("/help", "show this list"),
    ("/model", "switch model — /model claude-sonnet-4-5"),
    ("/provider", "switch provider — /provider deepseek"),
    ("/agents", "list agent profiles"),
    ("/skills", "list skills the planner can load"),
    ("/tasks", "list saved multi-agent runs"),
    ("/tools", "local tools on or off — /tools on"),
    ("/plan", "read-only plan mode on or off — /plan on"),
    ("/yolo", "skip approvals on or off — /yolo on"),
    ("/diff", "show git diff for the workspace"),
    ("/commit", "stage everything and commit — /commit fix: typo"),
    ("/compact", "summarise the conversation to free context"),
    ("/undo", "drop the last exchange"),
    ("/expand", "re-open the last tool output — /expand 2 for older"),
    ("/session", "session id, model, messages and tokens"),
    ("/clear", "start a fresh conversation; the old chat stays saved"),
    ("/exit", "leave (session is saved)"),
]

HINTS = [
    ("@file", "attach a file to your message"),
    ("!cmd", "run a shell command, output goes to the model"),
    ("Alt+Enter", "new line"),
    ("Ctrl+C", "stop the answer, or quit"),
]


def _wordmark() -> Text:
    return Text.assemble(("Thirty", f"bold {ORANGE}"), ("CLI", "bold #FFFFFF"), (f"  {__version__}", f"{DIM}"))


def _model_line(cfg) -> Text:
    return Text(f"{cfg.provider} · {cfg.model}", style=MUTED)


def banner(cfg, session, con: Console | None = None, plan: bool = False, yolo: bool = False) -> None:
    con = con or default_console
    tools = "tools on" if cfg.tools else "tools off"
    if plan:
        tools += " · plan"
    if yolo:
        tools += " · yolo"
    right = [
        _wordmark(),
        _model_line(cfg),
        Text(f"session {session.id} · {len(session.messages)} messages", style=DIM),
        Text(f"/help · {tools}", style=MUTED),
        Text("run a team of agents, stay in the terminal", style=DIM),
        Text(""),
    ]
    grid = Table.grid(padding=(0, 3))
    grid.add_column(no_wrap=True)
    grid.add_column(no_wrap=True)
    for mark_row, text in zip(MARK_ROWS, right, strict=True):
        grid.add_row(Text(mark_row, style=f"bold {ORANGE}"), text)

    if con.width < 52:
        con.print(Text("\n".join(MARK_ROWS), style=f"bold {ORANGE}"))
        con.print(_wordmark())
        con.print(Text(f"{cfg.provider} · {cfg.model} · session {session.id}", style=MUTED))
        return

    con.print(Panel(grid, box=box.ROUNDED, border_style=LINE, padding=(1, 2), expand=False))


def user_prompt(con: Console | None = None) -> Text:
    return prompt_text()


def stream_text(text: str, con: Console | None = None) -> None:
    con = con or default_console
    con.print(redact(text), end="", markup=False, highlight=False, soft_wrap=True)


def end_stream(con: Console | None = None) -> None:
    (con or default_console).print()


def _fmt_arg(value) -> str:
    if isinstance(value, str):
        return f"'{value}'"
    return repr(value)


def tool_call(name: str, args: dict, con: Console | None = None) -> None:
    con = con or default_console
    rendered = ", ".join(f"{key}={_fmt_arg(value)}" for key, value in args.items())
    if len(rendered) > 110:
        rendered = rendered[:107] + "..."
    rendered = redact(rendered)
    con.print()
    con.print(Text.assemble(("  > ", ORANGE), (name, f"bold {ORANGE}"), (f"({rendered})", DIM)))


def tool_result_summary(name: str, text: str, seconds: float = 0.0, con: Console | None = None) -> None:
    """One compact line per tool call; `/expand` or Ctrl+O shows the full output."""
    con = con or default_console
    lines = redact(text).splitlines() or [""]
    first = lines[0]
    failed = first.startswith(
        ("Tool error", "The user denied", "Unknown tool", "Plan mode", "HTTP ")
    )
    if failed:
        con.print(Text("  | " + first[:160], style=DIM))

    count = len(lines)
    tail = "1 line" if count == 1 else f"{count} lines"
    if seconds:
        tail += f" · {seconds:.2f}s"
    if count > 1 or len(first) > 160:
        tail += "  (ctrl+o)"
    con.print(Text("  -- " + tail, style=LINE))


def expand_tool(entry: dict, con: Console | None = None, limit: int = 400) -> None:
    """Print the full output of a stored tool call."""
    con = con or default_console
    name = entry.get("name", "tool")
    seconds = entry.get("seconds", 0.0)
    con.print()
    con.print(Text.assemble(("  > ", ORANGE), (name, f"bold {ORANGE}"), (f" · {seconds:.2f}s", DIM)))
    lines = redact(str(entry.get("result", ""))).splitlines() or [""]
    for line in lines[:limit]:
        con.print(Text("  | " + line, style=DIM), markup=False, highlight=False)
    if len(lines) > limit:
        con.print(Text(f"  | ... {len(lines) - limit} more lines", style=LINE))
    con.print(Text(f"  -- {len(lines)} lines", style=LINE))


def diff_preview(path: str, old: str, new: str, con: Console | None = None, limit: int = 32) -> None:
    con = con or default_console
    lines = list(
        difflib.unified_diff(
            old.splitlines(),
            new.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    body = Text()
    for line in lines[:limit]:
        line = redact(line)
        if line.startswith(("+++", "---")):
            style = DIM
        elif line.startswith("@@"):
            style = ORANGE
        elif line.startswith("+"):
            style = GREEN
        elif line.startswith("-"):
            style = RED
        else:
            style = MUTED
        body.append(line + "\n", style=style)
    if len(lines) > limit:
        body.append(f"… {len(lines) - limit} more diff lines\n", style=DIM)
    if not lines:
        body.append("(new file)\n" + "\n".join(new.splitlines()[:limit]), style=MUTED)
    con.print(Panel(body, box=box.SIMPLE, border_style=LINE, padding=(0, 1), title=path, title_align="left"))


def approve(question: str, con: Console | None = None) -> bool:
    con = con or default_console
    try:
        answer = con.input(Text(f"  {redact(question)} [y/N] ", style="accent"))
    except (EOFError, KeyboardInterrupt):
        con.print()
        return False
    return answer.strip().lower() in {"y", "yes"}


def info(message: str, con: Console | None = None) -> None:
    (con or default_console).print(Text(redact(message), style=MUTED))


def ok(message: str, con: Console | None = None) -> None:
    (con or default_console).print(Text.assemble(("+ ", "ok"), (redact(message), MUTED)))


def error(message: str, con: Console | None = None) -> None:
    (con or default_console).print(Text.assemble(("! ", "err"), (redact(message), MUTED)))


def rule(label: str = "", con: Console | None = None) -> None:
    con = con or default_console
    con.rule(Text(redact(label), style=MUTED) if label else "", style=LINE)


def help_panel(con: Console | None = None) -> None:
    con = con or default_console
    table = Table.grid(padding=(0, 2))
    table.add_column(no_wrap=True)
    table.add_column()
    for cmd, desc in COMMANDS:
        table.add_row(Text(cmd, style=ORANGE), Text(desc, style=MUTED))
    con.print(Panel(table, box=box.ROUNDED, border_style=LINE, padding=(1, 2), title="commands", title_align="left"))

    hints = Table.grid(padding=(0, 2))
    hints.add_column(no_wrap=True)
    hints.add_column()
    for key, desc in HINTS:
        hints.add_row(Text(key, style=ORANGE), Text(desc, style=MUTED))
    con.print(Panel(hints, box=box.ROUNDED, border_style=LINE, padding=(0, 2), title="input", title_align="left"))


def footer(
    seconds: float,
    usage: dict | None = None,
    model: str = "",
    con: Console | None = None,
    cost: float | None = None,
) -> None:
    con = con or default_console
    bits = [f"{seconds:.1f}s"]
    if usage and usage.get("total"):
        bits.append(f"{usage['total']} tok")
    if cost:
        bits.append(f"~${cost:.4f}")
    if model:
        bits.append(model)
    con.print(Text("  " + " · ".join(bits), style=DIM))


def elapsed(start: float, con: Console | None = None) -> None:
    con = con or default_console
    con.print(Text(f"  {time.time() - start:.1f}s", style=DIM))


def make_console() -> Console:
    """A console for tests and non-interactive output."""
    return Console(theme=THEME, highlight=False, soft_wrap=True)
