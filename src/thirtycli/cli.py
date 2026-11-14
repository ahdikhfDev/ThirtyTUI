"""Command line entry point."""

from __future__ import annotations

import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.table import Table

from . import __version__, ui
from .chat import ChatState, ask_once
from .chat import chat as run_chat
from .config import SETTABLE, Config, config_path, load, load_env_files, save, set_value
from .models import (
    PROVIDERS,
    ModelError,
    autodetect,
    build_model,
    default_model,
    key_status,
)
from .orchestrator import Orchestrator, TaskBoard, list_boards, tasks_dir
from .secrets import mask_key, redact
from .sessions import Session, list_sessions
from .team import run_team
from .theme import MUTED, ORANGE

app = typer.Typer(
    add_completion=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="[accent]ThirtyCLI[/accent] · a compact terminal agent built on LangChain.",
)
config_app = typer.Typer(help="Read and write ~/.thirtycli/config.toml.")
sessions_app = typer.Typer(help="Saved conversations in ~/.thirtycli/sessions.")
tasks_app = typer.Typer(help="Saved multi-agent runs.")
trace_app = typer.Typer(help="Read saved orchestration event traces.")
memory_app = typer.Typer(help="Project-scoped persistent notes.")
bench_app = typer.Typer(help="Reproducible benchmark engine.")
app.add_typer(config_app, name="config")
app.add_typer(sessions_app, name="sessions")
app.add_typer(tasks_app, name="tasks")
app.add_typer(trace_app, name="trace")
app.add_typer(memory_app, name="memory")
app.add_typer(bench_app, name="bench")


def _fail(message: str, code: int = 1) -> None:
    ui.error(message)
    raise typer.Exit(code)


def _print_agent_event(event: dict) -> None:
    """Render real orchestrator state; never invent progress."""
    kind = event.get("event")
    if kind == "plan":
        ui.rule("plan")
        for line in event.get("tasks") or []:
            ui.info(f"  [ ] {line}")
    elif kind == "route.decision":
        ui.info(
            f"  [route] {event.get('provider')}/{event.get('model')} · "
            f"{event.get('reason')}"
        )
    elif kind == "task.start":
        ui.info(
            f"  [~] {event.get('task')} · {event.get('agent')} · "
            f"{event.get('title')} (attempt {event.get('attempt')})"
        )
    elif kind == "task.retry":
        ui.info(f"  [!] retry {event.get('task')} · {event.get('error')}")
    elif kind == "task.verify":
        ui.info(
            f"  [v] {event.get('task')} · {event.get('verification')} · "
            f"{event.get('reason')}"
        )
    elif kind == "task.complete":
        ui.ok(f"{event.get('task')} · {event.get('agent')} · {float(event.get('seconds') or 0):.1f}s")
    elif kind == "task.fail":
        ui.error(f"{event.get('task')} failed · {event.get('error')}")
    elif kind == "task.cancel":
        ui.info(f"  [x] {event.get('task')} cancelled")
    elif kind == "task.conflict":
        ui.error(f"conflict {event.get('task')} · {event.get('error')}")
    elif kind == "run.conflicts":
        ui.error(f"{len(event.get('conflicts') or [])} file conflict(s) need review")
    elif kind == "run.finish":
        ui.rule("done")
        ui.info(f"status: {event.get('status')} · {event.get('completed')} task(s) completed")
    elif kind == "merge.error":
        ui.error(f"merge failed: {event.get('error')}")


def _apply_overrides(cfg: Config, provider: str, model: str) -> Config:
    if provider:
        cfg.provider = provider.lower()
        cfg.model = model or default_model(cfg.provider)
    elif model:
        cfg.model = model
    return cfg


def _prepare(cfg: Config, provider: str, model: str, quiet: bool = False) -> Config:
    """Apply this run's overrides, then fall back to a provider that has a key."""
    cfg = _apply_overrides(cfg, provider, model)
    if not cfg.model:
        cfg.model = default_model(cfg.provider)
    note = None if provider else autodetect(cfg)
    if note:
        save(cfg)
        if not quiet:
            ui.info(note)
    return cfg


def _open_session(continue_: bool, session_id: str) -> Session:
    if session_id:
        try:
            return Session.load(session_id)
        except FileNotFoundError as exc:
            _fail(str(exc))
    if continue_:
        return Session.latest() or Session()
    return Session()


def _run_tui(cfg: Config, state: ChatState) -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _fail("The TUI needs an interactive terminal. Run it without pipes.")
    from .tui import run_tui

    run_tui(cfg, state, Path.cwd())


def _state(cfg: Config, session: Session, yes: bool, tools: bool | None, plan: bool = False) -> ChatState:
    try:
        model = build_model(cfg)
    except ModelError as exc:
        _fail(str(exc))
    if tools is not None:
        cfg.tools = tools
    state = ChatState(
        cfg=cfg,
        session=session,
        model=model,
        auto_yes=yes or cfg.auto_yes,
        root=Path.cwd(),
        plan_mode=plan,
    )
    state.refresh_tools()
    return state


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", "-V", help="Print the version and exit.")] = False,
    continue_: Annotated[bool, typer.Option("--continue", "-c", help="Resume the latest session.")] = False,
    session_id: Annotated[str, typer.Option("--session", "-s", help="Resume a session id.")] = "",
    model: Annotated[str, typer.Option("--model", "-m", help="Model for this run.")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p", help="Provider for this run.")] = "",
    tools: Annotated[
        bool | None,
        typer.Option("--tools/--no-tools", help="Enable or disable local tools for this run."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", "--yolo", help="Skip every approval: writes and shell commands run as-is (yolo mode).")] = False,
    plan: Annotated[bool, typer.Option("--plan", help="Read-only plan mode for this run.")] = False,
    tui: Annotated[bool, typer.Option("--tui", help="Full-screen UI with a pinned input line.")] = False,
) -> None:
    """With no subcommand, opens the chat."""
    if version:
        ui.console.print(f"thirtycli {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        chat(
            continue_=continue_,
            session_id=session_id,
            model=model,
            provider=provider,
            tools=tools,
            yes=yes,
            plan=plan,
            tui=tui,
        )


@app.command()
def chat(
    continue_: Annotated[bool, typer.Option("--continue", "-c", help="Resume the latest session.")] = False,
    session_id: Annotated[str, typer.Option("--session", "-s", help="Resume a session id.")] = "",
    model: Annotated[str, typer.Option("--model", "-m", help="Model for this run.")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p", help="Provider for this run.")] = "",
    tools: Annotated[
        bool | None,
        typer.Option("--tools/--no-tools", help="Enable or disable local tools for this run."),
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", "--yolo", help="Skip every approval: writes and shell commands run as-is (yolo mode).")] = False,
    plan: Annotated[bool, typer.Option("--plan", help="Read-only plan mode for this run.")] = False,
    tui: Annotated[bool, typer.Option("--tui", help="Full-screen UI with a pinned input line.")] = False,
) -> None:
    """Talk to a model, keep the session, use tools."""
    cfg = _prepare(load(), provider, model)
    session = _open_session(continue_, session_id)
    state = _state(cfg, session, yes, tools, plan)
    if tui:
        _run_tui(cfg, state)
    else:
        run_chat(cfg, state, Path.cwd())


@app.command()
def ask(
    text: Annotated[list[str] | None, typer.Argument(help="Prompt. Reads stdin when omitted.")] = None,
    model: Annotated[str, typer.Option("--model", "-m")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p")] = "",
    tools: Annotated[
        bool | None,
        typer.Option("--tools/--no-tools", help="Enable or disable local tools for this run."),
    ] = None,
) -> None:
    """One prompt, one answer, no session."""
    prompt = " ".join(text).strip() if text else ""
    if not prompt:
        if sys.stdin.isatty():
            _fail("No prompt. Pass text or pipe it in: echo 'hi' | thirtycli ask")
        prompt = sys.stdin.read().strip()
    if not prompt:
        _fail("Empty prompt.")
    cfg = _prepare(load(), provider, model)
    try:
        ask_once(cfg, prompt, tools_enabled=cfg.tools if tools is None else tools, root=Path.cwd())
    except ModelError as exc:
        _fail(str(exc))


@app.command()
def team(
    task: Annotated[list[str] | None, typer.Argument(help="Task for the planner → builder → reviewer team.")] = None,
) -> None:
    """Run a three-agent team over one task."""
    text = " ".join(task).strip() if task else ""
    if not text:
        if sys.stdin.isatty():
            _fail("Give the team a task: thirtycli team 'ship a changelog page'")
        text = sys.stdin.read().strip()
    if not text:
        _fail("Empty task.")
    try:
        run_team(_prepare(load(), "", ""), text)
    except ModelError as exc:
        _fail(str(exc))


@app.command()
def run(
    task: Annotated[list[str] | None, typer.Argument(help="Goal for the agent team. Omit with --resume.")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Tasks running at once.")] = 3,
    max_tasks: Annotated[int, typer.Option("--max-tasks", help="Planner task limit.")] = 6,
    resume: Annotated[str, typer.Option("--resume", "-r", help="Resume a saved run id.")] = "",
    model: Annotated[str, typer.Option("--model", "-m")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p")] = "",
    coding: Annotated[
        bool,
        typer.Option("--coding/--no-coding", help="Let workers write files and run project commands."),
    ] = False,
    permission: Annotated[
        str,
        typer.Option("--permission", help="read-only | controlled | autonomous"),
    ] = "controlled",
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Approve coding tool calls without prompting."),
    ] = False,
    route: Annotated[bool, typer.Option("--route/--no-route", help="Use the model router.")] = True,
    route_prefer: Annotated[
        str,
        typer.Option("--route-prefer", help="balanced | cost | latency | quality"),
    ] = "balanced",
    allow_switch: Annotated[
        bool,
        typer.Option("--allow-switch", help="Allow routing to another provider."),
    ] = False,
    verify: Annotated[
        bool,
        typer.Option("--verify/--no-verify", help="Verify task results against real files."),
    ] = True,
    verify_checks: Annotated[
        bool,
        typer.Option("--verify-checks", help="Run project tests during verification."),
    ] = False,
) -> None:
    """Plan a goal, run agents in parallel by dependency, then merge the result."""
    text = " ".join(task).strip() if task else ""
    if not text and not resume:
        if sys.stdin.isatty():
            _fail("Give run a goal: thirtycli run 'build the auth flow'")
        text = sys.stdin.read().strip()
    if not text and not resume:
        _fail("Empty goal.")

    if permission not in {"read-only", "controlled", "autonomous"}:
        _fail("--permission must be read-only, controlled or autonomous")
    cfg = _prepare(load(), provider, model)
    try:
        llm = build_model(cfg)
    except ModelError as exc:
        _fail(str(exc))

    approver = None
    if coding:
        if yes or permission == "autonomous":
            approver = lambda _question: True  # noqa: E731 - explicit opt-in
        elif sys.stdin.isatty():
            approver = lambda question: ui.approve(question)  # noqa: E731

    orchestrator = Orchestrator(
        model=llm,
        cfg=cfg,
        root=Path.cwd(),
        workers=workers,
        max_tasks=max_tasks,
        on_event=_print_agent_event,
        coding=coding,
        permission=permission,
        approver=approver,
        route=route,
        route_prefer=route_prefer,
        allow_switch=allow_switch,
        verify=verify,
        verify_checks=verify_checks,
    )
    report = orchestrator.sandbox_report
    ui.info(f"sandbox: {report.kind} · {report.reason}")
    try:
        final = orchestrator.run(text or "resume", resume=resume or None)
    except FileNotFoundError as exc:
        _fail(str(exc))
    except KeyboardInterrupt:
        ui.error("cancelled · task state saved; resume with --resume <id>")
        raise typer.Exit(130) from None
    except Exception as exc:
        _fail(f"run failed: {exc}")

    if final:
        ui.rule("result")
        ui.stream_text(final)
        ui.end_stream()

    summary = orchestrator.board.summary()
    ui.console.print()
    ui.ok(
        f"run {summary['run_id']} · {summary['completed']}/{summary['tasks']} tasks · "
        f"{summary['status']}"
    )
    ui.info(f"state: {tasks_dir() / (summary['run_id'] + '.json')}")
    try:
        session = Session(model=cfg.model)
        session.add("user", f"run · {text or summary['goal']}")
        if final:
            session.add("assistant", final)
        session.add_usage(summary["usage"].get("input", 0), summary["usage"].get("output", 0))
        session.save()
        orchestrator.board.session_id = session.id
        orchestrator.board.save(orchestrator.task_dir)
        ui.info(f"session: {session.path}")
    except OSError:
        pass


@app.command()
def agents() -> None:
    """List the built-in and custom agent profiles."""
    from .agent_catalog import list_agents

    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("id", no_wrap=True)
    table.add_column("name", no_wrap=True)
    table.add_column("permission", no_wrap=True)
    table.add_column("description", style=MUTED)
    for profile in list_agents():
        table.add_row(profile.id, profile.name, profile.permission, profile.description)
    ui.console.print(table)


@app.command()
def skills() -> None:
    """List the skills the planner can load into an agent prompt."""
    from .skills import all_skills

    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("skill", no_wrap=True)
    table.add_column("keywords", style=MUTED)
    table.add_column("description", style=MUTED)
    for skill in all_skills():
        table.add_row(skill.name, ", ".join(skill.keywords[:6]), skill.description)
    ui.console.print(table)


@tasks_app.callback(invoke_without_command=True)
def tasks_root(ctx: typer.Context, json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List saved orchestration runs when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    _tasks_list(json_out)


def _tasks_list(json_out: bool = False) -> None:
    rows = list_boards()
    if not rows:
        if json_out:
            ui.console.print("[]")
        else:
            ui.info("no agent runs yet")
        return
    if json_out:
        ui.console.print(json.dumps(rows, indent=2))
        return
    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("run", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("done", justify="right")
    table.add_column("goal", style=MUTED)
    for row in rows[:30]:
        table.add_row(
            row["run_id"],
            row["status"],
            f"{row['completed']}/{row['tasks']}",
            row["goal"],
        )
    ui.console.print(table)
    ui.info("resume with: thirtycli run --resume <run>")


@tasks_app.command("list")
def tasks_list(json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List saved orchestration runs."""
    _tasks_list(json_out)


@tasks_app.command("show")
def tasks_show(
    run_id: Annotated[str, typer.Argument(help="Run id from `thirtycli tasks`.")],
    session: Annotated[str, typer.Option("--session", help="Find a run by session id.")] = "",
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show tasks, dependencies, verification and conflicts for one run."""
    if session:
        match = next((row for row in list_boards() if row.get("session_id") == session), None)
        if match is None:
            _fail(f"no run for session {session}")
        run_id = str(match["run_id"])
    try:
        board = TaskBoard.load(run_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    if json_out:
        ui.console.print(json.dumps(board.to_dict(), indent=2))
        return
    ui.rule(f"run {board.run_id}")
    ui.info(f"goal: {board.goal}")
    ui.info(f"status: {board.overall().value} · {board.completed_count}/{len(board.tasks)} completed")
    if board.session_id:
        ui.info(f"session: {board.session_id}")
    for task in board.tasks:
        deps = f" <- {','.join(task.depends_on)}" if task.depends_on else ""
        ui.info(f"  [{task.status.value[:4]}] {task.id} · {task.agent} · {task.title}{deps}")
        if task.verification:
            ui.info(
                f"        verify: {task.verification.get('status')} · "
                f"{task.verification.get('reason')}"
            )
        if task.error:
            ui.error(f"        {task.error}")
    if board.conflicts:
        ui.error("conflicts:")
        for row in board.conflicts:
            ui.error(f"  {row.get('path')} · {', '.join(row.get('tasks') or [])}")
    if board.final:
        ui.rule("result")
        ui.stream_text(board.final)
        ui.end_stream()


@trace_app.command("list")
def trace_list() -> None:
    """List runs that have an event trace."""
    rows = list_boards()
    if not rows:
        ui.info("no traces yet")
        raise typer.Exit()
    for row in rows[:30]:
        path = tasks_dir() / f"{row['run_id']}.events.jsonl"
        count = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                count = sum(1 for _line in handle)
        except OSError:
            pass
        ui.info(f"  {row['run_id']} · {row['status']} · {count} events · {row['goal'][:60]}")


@trace_app.command("show")
def trace_show(
    run_id: Annotated[str, typer.Argument(help="Run id from `thirtycli trace list`.")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum events to print.")] = 200,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Print one run's structured event trace."""
    path = tasks_dir() / f"{run_id}.events.jsonl"
    if not path.is_file():
        _fail(f"no trace for {run_id}")
    events = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        _fail(str(exc))
    events = events[-max(1, limit) :]
    if json_out:
        ui.console.print(json.dumps(events, indent=2))
        return
    for event in events:
        bits = [str(event.get("event", ""))]
        for key in ("task", "agent", "status", "verification"):
            if event.get(key):
                bits.append(str(event[key]))
        if event.get("reason"):
            bits.append(str(event["reason"])[:80])
        if event.get("error"):
            bits.append(str(event["error"])[:80])
        ui.info("  " + " · ".join(bits))


@memory_app.command("list")
def memory_list() -> None:
    """List persistent notes for the current project."""
    from .memory import ProjectMemory

    entries = ProjectMemory(Path.cwd()).all()
    if not entries:
        ui.info("no project memory yet")
        return
    for entry in entries:
        ui.info(f"  {entry.id} · {entry.kind} · {entry.text}")


@memory_app.command("add")
def memory_add(
    text: Annotated[list[str], typer.Argument(help="Note to remember.")],
    kind: Annotated[str, typer.Option("--kind", help="decision|architecture|constraint|outcome|note")] = "note",
    tags: Annotated[str, typer.Option("--tags", help="Comma-separated tags.")] = "",
) -> None:
    """Add a note to the current project's memory."""
    from .memory import ProjectMemory

    body = " ".join(text).strip()
    if not body:
        _fail("Memory text is empty.")
    try:
        entry = ProjectMemory(Path.cwd()).add(
            body,
            kind=kind,
            tags=tuple(tag.strip() for tag in tags.split(",") if tag.strip()),
        )
    except ValueError as exc:
        _fail(str(exc))
    ui.ok(f"remembered {entry.id} · {entry.kind} · {entry.text}")


@memory_app.command("search")
def memory_search(query: Annotated[list[str], typer.Argument(help="Search words.")]) -> None:
    """Search project memory by relevance."""
    from .memory import ProjectMemory

    words = " ".join(query).strip()
    entries = ProjectMemory(Path.cwd()).search(words, 5)
    if not entries:
        ui.info("no relevant memory")
        return
    for entry in entries:
        ui.info(f"  {entry.id} · [{entry.kind}] {entry.text}")


@memory_app.command("edit")
def memory_edit(
    entry_id: Annotated[str, typer.Argument()],
    text: Annotated[list[str], typer.Argument(help="Replacement text.")],
) -> None:
    """Replace one memory entry's text."""
    from .memory import ProjectMemory

    body = " ".join(text).strip()
    if not body:
        _fail("Memory text is empty.")
    entry = ProjectMemory(Path.cwd()).update(entry_id, text=body)
    if entry is None:
        _fail(f"no memory entry {entry_id}")
    ui.ok(f"updated {entry.id} · {entry.text}")


@memory_app.command("rm")
def memory_rm(entry_id: Annotated[str, typer.Argument()]) -> None:
    """Delete one memory entry."""
    from .memory import ProjectMemory

    if not ProjectMemory(Path.cwd()).remove(entry_id):
        _fail(f"no memory entry {entry_id}")
    ui.ok(f"deleted {entry_id}")


@bench_app.command("list")
def bench_list(json_out: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List suites, datasets and available CLI adapters."""
    from .benchmarks.adapters import ADAPTERS, get_adapter
    from .benchmarks.datasets import datasets
    from .benchmarks.suites import SUITE_NAMES

    cfg = load()
    try:
        model = build_model(cfg)
    except ModelError:
        model = None
    adapters = [get_adapter(name, model=model, cfg=cfg).info() for name in ADAPTERS]
    dataset_rows = datasets()
    if json_out:
        ui.console.print(
            json.dumps(
                {
                    "suites": list(SUITE_NAMES),
                    "datasets": [
                        {
                            "name": item.name,
                            "version": item.version,
                            "license": item.license,
                            "available": item.available,
                            "reason": item.reason,
                            "tasks": item.task_count,
                        }
                        for item in dataset_rows
                    ],
                    "adapters": [
                        {
                            "name": item.name,
                            "available": item.available,
                            "version": item.version,
                            "reason": item.reason,
                        }
                        for item in adapters
                    ],
                },
                indent=2,
            )
        )
        return

    ui.rule("suites")
    ui.info("  " + ", ".join(SUITE_NAMES))
    ui.rule("datasets")
    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("dataset", no_wrap=True)
    table.add_column("version", no_wrap=True)
    table.add_column("available", no_wrap=True)
    table.add_column("reason", style=MUTED)
    for item in dataset_rows:
        table.add_row(item.name, item.version, "yes" if item.available else "no", item.reason or "-")
    ui.console.print(table)
    ui.rule("adapters")
    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("adapter", no_wrap=True)
    table.add_column("available", no_wrap=True)
    table.add_column("version", no_wrap=True)
    table.add_column("reason", style=MUTED)
    for item in adapters:
        table.add_row(item.name, "yes" if item.available else "no", item.version or "-", item.reason or "-")
    ui.console.print(table)


@bench_app.command("run")
def bench_run(
    suite: Annotated[str, typer.Argument(help="coding|agents|performance|cost|reliability|security|all")] = "all",
    timeout: Annotated[int, typer.Option("--timeout", help="Per-task timeout in seconds.")] = 120,
    concurrency: Annotated[int, typer.Option("--concurrency", "-c", help="Tasks running at once.")] = 2,
    trials: Annotated[int, typer.Option("--trials", help="Trials per task.")] = 1,
    seed: Annotated[int, typer.Option("--seed", help="Recorded random seed.")] = 0,
    model: Annotated[str, typer.Option("--model", "-m")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p")] = "",
    adapter_name: Annotated[str, typer.Option("--adapter", help="thirtycli|aider|opencode|claude")] = "thirtycli",
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run one benchmark suite (or all) and persist the result."""
    from .benchmarks.adapters import get_adapter
    from .benchmarks.models import RunConfig
    from .benchmarks.report import render_run
    from .benchmarks.runner import parse_suite_names, run_suites
    from .benchmarks.storage import runs_dir

    try:
        names = parse_suite_names(suite)
    except KeyError as exc:
        _fail(str(exc))
    if adapter_name != "thirtycli" and any(name not in {"coding", "cost"} for name in names):
        _fail("mechanics suites (agents/performance/reliability/security) only support the thirtycli adapter")

    cfg = _prepare(load(), provider, model, quiet=json_out)
    llm = None
    warnings: list[str] = []
    if adapter_name == "thirtycli":
        try:
            llm = build_model(cfg)
        except ModelError as exc:
            warnings.append(f"no model: {exc} (coding and cost suites will be skipped)")
    try:
        adapter = get_adapter(adapter_name, model=llm, cfg=cfg)
    except ValueError as exc:
        _fail(str(exc))
    config = RunConfig(
        suite="all",
        cli=adapter.name,
        model=cfg.model or default_model(cfg.provider),
        provider=cfg.provider,
        timeout=timeout,
        concurrency=max(1, concurrency),
        trials=max(1, trials),
        seed=seed,
        temperature=cfg.temperature,
    )
    runs = run_suites(names, adapter, config)
    if json_out:
        payload = {"warnings": warnings, "runs": [run.to_dict() for run in runs]}
        ui.console.print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    for warning in warnings:
        ui.info(warning)
    for run in runs:
        ui.console.print(render_run(run))
        ui.info(f"saved: {runs_dir() / (run.run_id + '.json')}")


@bench_app.command("compare")
def bench_compare(
    run_ids: Annotated[list[str] | None, typer.Argument(help="Run ids; default: two latest.")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare persisted runs without inventing a composite score."""
    from .benchmarks.report import render_comparison
    from .benchmarks.storage import list_runs, load_run

    ids = list(run_ids or [])
    if not ids:
        ids = [row["run_id"] for row in list_runs(limit=2)]
    if len(ids) < 2:
        _fail("compare needs at least two runs; run `thirtycli bench run` first")
    runs = []
    for run_id in ids:
        try:
            runs.append(load_run(run_id))
        except FileNotFoundError as exc:
            _fail(str(exc))
    ui.console.print(render_comparison(runs, fmt="json" if json_out else "table"))


@bench_app.command("leaderboard")
def bench_leaderboard(
    suite: Annotated[str, typer.Option("--suite", help="Filter by suite.")] = "",
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rank runs by success rate, verified rate, then lower duration."""
    from .benchmarks.report import leaderboard_rows, render_leaderboard
    from .benchmarks.storage import list_runs, load_run

    runs = [load_run(row["run_id"]) for row in list_runs(limit=100)]
    if suite:
        runs = [run for run in runs if run.suite == suite]
    if json_out:
        ui.console.print(json.dumps(leaderboard_rows(runs, suite), indent=2))
        return
    ui.console.print(render_leaderboard(runs, suite))


@bench_app.command("report")
def bench_report(
    run_id: Annotated[str, typer.Argument(help="Run id from `thirtycli bench history`.")],
    fmt: Annotated[str, typer.Option("--format", help="markdown|json|table")] = "markdown",
) -> None:
    """Render a full report for one run."""
    from .benchmarks.report import render_report, render_run
    from .benchmarks.storage import load_run

    try:
        run = load_run(run_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    if fmt == "json":
        ui.console.print(json.dumps(run.to_dict(), indent=2))
    elif fmt == "table":
        ui.console.print(render_run(run))
    else:
        ui.console.print(render_report(run))


@bench_app.command("history")
def bench_history(
    suite: Annotated[str, typer.Option("--suite", help="Filter by suite.")] = "",
    limit: Annotated[int, typer.Option("--limit")] = 20,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show persisted benchmark history."""
    from .benchmarks.report import render_history
    from .benchmarks.storage import history

    rows = history(suite=suite, limit=max(1, limit))
    if json_out:
        ui.console.print(json.dumps(rows, indent=2))
        return
    ui.console.print(render_history(rows))


@app.command()
def phases(
    task: Annotated[list[str] | None, typer.Argument(help="Task split into parallel sub-agent jobs.")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Sub-agents running at once.")] = 3,
    model: Annotated[str, typer.Option("--model", "-m")] = "",
    provider: Annotated[str, typer.Option("--provider", "-p")] = "",
) -> None:
    """Plan, fan out to sub-agents, then merge the result."""
    text = " ".join(task).strip() if task else ""
    if not text:
        if sys.stdin.isatty():
            _fail("Give phases a task: thirtycli phases 'research X and Y'")
        text = sys.stdin.read().strip()
    if not text:
        _fail("Empty task.")
    cfg = _prepare(load(), provider, model)
    try:
        from .phases import run_phases

        run_phases(cfg, text, max(1, workers))
    except ModelError as exc:
        _fail(str(exc))


@config_app.command("show")
def config_show() -> None:
    """Print the current config."""
    cfg = load()
    path = config_path()
    table = Table.grid(padding=(0, 2))
    table.add_column(style=ORANGE, no_wrap=True)
    table.add_column(style=MUTED)
    for key, value in (
        ("provider", cfg.provider),
        ("model", cfg.model or default_model(cfg.provider)),
        ("temperature", cfg.temperature),
        ("base_url", cfg.base_url or "(provider default)"),
        ("tools", str(cfg.tools).lower()),
        ("auto_yes", str(cfg.auto_yes).lower()),
        ("api_key", key_status(cfg.provider, cfg)),
        ("fallback", f"{cfg.fallback_provider or cfg.provider}/{cfg.fallback_model}" if cfg.fallback_model else "-"),
    ):
        table.add_row(key, str(value))
    ui.console.print(table)
    ui.console.print()
    ui.info(f"file: {path} {'(missing)' if not path.exists() else ''}")


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help=f"One of: {', '.join(sorted(SETTABLE))}")],
    value: Annotated[list[str], typer.Argument(help="New value.")],
) -> None:
    """Set one key in the config file."""
    cfg = load()
    raw = " ".join(value)
    previous = cfg.provider
    try:
        set_value(cfg, key, raw)
    except KeyError:
        _fail(f"Unknown key '{key}'. Use one of: {', '.join(sorted(SETTABLE))}")
    except ValueError:
        _fail(f"Bad value for {key}: {raw!r}")
    if key == "provider" and cfg.provider != previous:
        cfg.model = default_model(cfg.provider)
    path = save(cfg)
    shown = mask_key(cfg.api_key) if key == "api_key" else repr(getattr(cfg, key))
    ui.ok(f"{key} = {shown} → {path}")


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the config file path."""
    ui.console.print(str(config_path()))


@sessions_app.command("list")
def sessions_list() -> None:
    """List saved sessions."""
    items = list_sessions()
    if not items:
        ui.info("no sessions yet")
        raise typer.Exit()
    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE, expand=False)
    table.add_column("id", no_wrap=True)
    table.add_column("title")
    table.add_column("model")
    table.add_column("msgs", justify="right")
    table.add_column("updated", no_wrap=True)
    for session in items[:30]:
        from datetime import datetime

        table.add_row(
            session.id,
            session.title or "-",
            session.model or "-",
            str(len(session.messages)),
            datetime.fromtimestamp(session.updated).strftime("%Y-%m-%d %H:%M"),
        )
    ui.console.print(table)


@sessions_app.command("show")
def sessions_show(session_id: Annotated[str, typer.Argument()]) -> None:
    """Print one session as plain text."""
    try:
        session = Session.load(session_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    if session.title:
        ui.console.print(
            f"[{ORANGE}]{session.title}[/{ORANGE}] · {session.id} · {len(session.messages)} messages"
        )
        ui.console.print()
    for msg in session.messages:
        who = "you" if msg["role"] == "user" else "thirty"
        ui.console.print(f"[{ORANGE}]{who}[/{ORANGE}] {redact(msg['content'])}\n")


@sessions_app.command("rename")
def sessions_rename(
    session_id: Annotated[str, typer.Argument()],
    name: Annotated[list[str], typer.Argument(help="New session name.")],
) -> None:
    """Rename a saved session."""
    try:
        session = Session.load(session_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    title = " ".join(name).strip()
    if not title:
        _fail("New session name is empty.")
    session.title = title
    path = session.save()
    ui.ok(f"renamed {session.id} -> {title!r} · {path}")


@sessions_app.command("rm")
def sessions_rm(session_id: Annotated[str, typer.Argument()]) -> None:
    """Delete one session."""
    try:
        session = Session.load(session_id)
    except FileNotFoundError as exc:
        _fail(str(exc))
    session.delete()
    ui.ok(f"deleted {session_id}")


@app.command()
def tools() -> None:
    """List the local tools the model can call."""
    from .tools import build_tools

    table = Table(box=box.SIMPLE_HEAD, border_style=MUTED, header_style=ORANGE)
    table.add_column("tool", no_wrap=True)
    table.add_column("description", style=MUTED)
    for tool in build_tools(Path.cwd()):
        table.add_row(tool.name, (tool.description or "").strip().splitlines()[0])
    ui.console.print(table)


@app.command()
def doctor(ping: Annotated[bool, typer.Option("--ping", help="Send one test message.")] = False) -> None:
    """Check the install, config and provider."""
    cfg = load()
    rows = [("python", sys.version.split()[0]), ("thirtycli", __version__)]
    for package in ("langchain-core", "langchain-openai", "typer", "rich"):
        try:
            rows.append((package, metadata.version(package)))
        except metadata.PackageNotFoundError:
            rows.append((package, "missing"))
    rows += [
        ("config", str(config_path())),
        ("provider", cfg.provider),
        ("model", cfg.model or default_model(cfg.provider)),
        ("api_key", key_status(cfg.provider, cfg)),
        ("providers", ", ".join(PROVIDERS)),
    ]
    table = Table.grid(padding=(0, 2))
    table.add_column(style=ORANGE, no_wrap=True)
    table.add_column(style=MUTED)
    for key, value in rows:
        table.add_row(key, value)
    ui.console.print(table)

    if not ping:
        return
    ui.console.print()
    try:
        model = build_model(cfg)
        reply = model.invoke("Reply with the single word: ok")
        content = reply.content if isinstance(reply.content, str) else str(reply.content)
        ui.ok(f"model replied: {content.strip()[:60]}")
    except Exception as exc:
        _fail(f"ping failed: {exc}")


def main() -> None:
    load_env_files()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    app()


if __name__ == "__main__":
    main()
