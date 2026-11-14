"""The interactive loop: stream markdown, run tools, keep the session."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from rich.console import Console

from . import ui
from .agents import run_subagent
from .config import Config, save
from .context import clamp_tool_output, trim_for_model
from .models import ModelError, build_model, default_model
from .presenters import as_emitter
from .pricing import estimate
from .sessions import Session, as_transcript
from .tools import build_readonly_tools, build_tools, resolve_path, run_git, tool_map

MAX_CONTEXT_TOKENS = 24_000
MENTION = re.compile(r"(?<![\w@])@([\w./\\:-]+)")
PER_FILE_CHARS = 8_000
WRITE_TOOLS = {"write_file", "edit_file"}
READ_PATH_TOOLS = {"read_file", "list_dir", "edit_file"}
MUTATING_TOOLS = WRITE_TOOLS | {"run_shell"}
SAFE_PARALLEL = {
    "read_file",
    "list_dir",
    "search_text",
    "web_fetch",
    "web_search",
    "git_status",
    "git_diff",
    "now",
    "delegate",
}
RULES_FILES = ("AGENTS.md", "THIRTYCLI.md", ".thirtycli/rules.md")


class ModelStopped(KeyboardInterrupt):
    """The user asked to stop the current answer."""


@dataclass
class ChatState:
    cfg: Config
    session: Session
    model: Any
    tools: list = field(default_factory=list)
    auto_yes: bool = False
    root: Path | None = None
    plan_mode: bool = False
    used_fallback: bool = False
    tool_log: list = field(default_factory=list)
    usage_lock: threading.Lock = field(default_factory=threading.Lock)
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def workspace(self) -> Path:
        return (self.root or Path.cwd()).resolve()

    @property
    def tool_map(self) -> dict:
        return tool_map(self.tools)

    def refresh_tools(self) -> None:
        self.tools = build_tools(self.workspace) if self.cfg.tools else []


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") in {"text", "output_text"}:
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return "" if content is None else str(content)


def project_rules(root: Path | None) -> str:
    if root is None:
        return ""
    chunks = []
    for name in RULES_FILES:
        path = root / name
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="replace")[:4000]
            chunks.append(f"Project rules from {name}:\n{body.strip()}")
    return "\n\n".join(chunks)


def build_messages(cfg: Config, session: Session, root: Path | None = None, plan_mode: bool = False) -> list:
    system = cfg.system
    rules = project_rules(root)
    if rules:
        system += "\n\n" + rules
    if root is not None:
        try:
            from .memory import ProjectMemory

            last_user = next(
                (msg.get("content", "") for msg in reversed(session.messages) if msg.get("role") == "user"),
                "",
            )
            memory = ProjectMemory(root).context(last_user, 3, 2000)
            if memory:
                system += "\n\n" + memory
        except Exception:
            pass
    if plan_mode:
        system += (
            "\n\nPLAN MODE: this run is read-only. Investigate with read/search tools, "
            "then propose a concrete plan. Do not write files, run mutating commands, or claim you changed anything."
        )

    messages: list = [SystemMessage(content=system)]
    for msg in session.messages:
        role = msg.get("role")
        if role == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif role == "assistant":
            messages.append(AIMessage(content=msg.get("content", ""), tool_calls=msg.get("tool_calls") or []))
        elif role == "tool":
            messages.append(ToolMessage(content=msg.get("content", ""), tool_call_id=msg.get("tool_call_id", "")))
    messages, trimmed = trim_for_model(messages, MAX_CONTEXT_TOKENS)
    if trimmed and messages and isinstance(messages[0], SystemMessage):
        messages[0] = SystemMessage(
            content=messages[0].content
            + "\n\n[Earlier conversation was trimmed to fit the context window. Ask if you lost something.]"
        )
    return messages


def _normalise_usage(raw: dict | None) -> dict:
    if not raw:
        return {}
    prompt = int(raw.get("input_tokens") or 0)
    completion = int(raw.get("output_tokens") or 0)
    total = int(raw.get("total_tokens") or (prompt + completion))
    return {"input": prompt, "output": completion, "total": total}


def stream_answer(
    model: Any,
    messages: list,
    tools: list,
    target,
    cancel: threading.Event | None = None,
) -> tuple[AIMessage, dict]:
    emitter = as_emitter(target)
    bound = model
    if tools:
        try:
            bound = model.bind_tools(tools)
        except NotImplementedError:
            pass  # model without tool calling: answer without tools

    agg = None
    try:
        if cancel is not None and cancel.is_set():
            raise ModelStopped
        for chunk in bound.stream(messages):
            if cancel is not None and cancel.is_set():
                raise ModelStopped
            agg = chunk if agg is None else agg + chunk
            piece = message_text(getattr(chunk, "content", ""))
            if piece:
                emitter.assistant_delta(piece)
    finally:
        emitter.assistant_end()

    if agg is None:
        return AIMessage(content=""), {}
    usage = _normalise_usage(getattr(agg, "usage_metadata", None))
    return AIMessage(content=message_text(agg.content), tool_calls=list(agg.tool_calls or [])), usage


def _stream_with_fallback(state: ChatState, messages: list, emitter) -> tuple[AIMessage, dict]:
    try:
        return stream_answer(state.model, messages, state.tools, emitter, cancel=state.cancel)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        cfg = state.cfg
        if not cfg.fallback_model or state.used_fallback:
            raise
        state.used_fallback = True
        fallback = Config(**{**cfg.__dict__, "model": cfg.fallback_model})
        if cfg.fallback_provider:
            fallback.provider = cfg.fallback_provider
        fallback.model = cfg.fallback_model
        emitter.error(f"{cfg.model} failed ({exc}); retrying on {fallback.provider}/{fallback.model}")
        state.model = build_model(fallback)
        return stream_answer(state.model, messages, state.tools, emitter, cancel=state.cancel)


def expand_mentions(text: str, root: Path) -> str:
    """`@path` in a message inlines the file for the model."""
    if "@" not in text:
        return text
    blocks = []
    from .tools import resolve_in_root

    for raw in MENTION.findall(text)[:5]:
        try:
            path = resolve_in_root(root, raw)
        except Exception:
            continue
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8", errors="replace")[:PER_FILE_CHARS]
        blocks.append(f'<file path="{raw}">\n{body}\n</file>')
    if not blocks:
        return text
    return text + "\n\n" + "\n\n".join(blocks)


def _approve(state: ChatState, name: str, args: dict, emit) -> bool:
    if state.auto_yes:
        return True

    if name == "run_shell":
        return emit.confirm(f"run: {args.get('command', '')}")

    if name == "write_file":
        path = str(args.get("path", ""))
        new = str(args.get("content", ""))
        target, inside = resolve_path(state.workspace, path)
        old = ""
        if target.is_file():
            old = target.read_text(encoding="utf-8", errors="replace")
        emit.diff(path, old, new)
        note = "" if inside else " · outside workspace"
        return emit.confirm(f"write {target}{note}")

    if name == "edit_file":
        path = str(args.get("path", ""))
        before = str(args.get("old", ""))
        after = str(args.get("new", ""))
        target, inside = resolve_path(state.workspace, path)
        old_text = ""
        new_text = after
        if target.is_file():
            old_text = target.read_text(encoding="utf-8", errors="replace")
            new_text = old_text.replace(before, after, max(1, int(args.get("count") or 1)))
        emit.diff(path, old_text, new_text)
        note = "" if inside else " · outside workspace"
        return emit.confirm(f"edit {target}{note}")

    if name in READ_PATH_TOOLS:
        target, inside = resolve_path(state.workspace, str(args.get("path") or "."))
        if inside:
            return True
        return emit.confirm(f"read {target} · outside workspace")

    return True


def _run_delegate(state: ChatState, args: dict, emit) -> str:
    task = str(args.get("task") or "").strip()
    role = str(args.get("role") or "worker")
    if not task:
        return "delegate needs a task"
    emit.info(f"  sub-agent ({role}) started")
    try:
        result = run_subagent(
            state.model,
            task,
            role=role,
            tools=build_readonly_tools(state.workspace),
        )
    except Exception as exc:
        return f"Sub-agent error: {exc}"
    with state.usage_lock:
        state.session.add_usage(result.usage.get("input", 0), result.usage.get("output", 0))
    emit.info(f"  sub-agent ({role}) done · {result.steps} step(s)")
    return f"[{role}] {result.text or '(no result)'}"


def _call_tool(state: ChatState, call: dict, emit, spinner: bool = True) -> tuple[str, float]:
    """Approve, run one tool, return (result, seconds)."""
    name = call.get("name", "")
    args = call.get("args") or {}
    if state.plan_mode and name in MUTATING_TOOLS:
        return "Plan mode is on: read-only. Describe the change instead of making it.", 0.0
    if name == "delegate":
        began = time.time()
        return _run_delegate(state, args, emit), time.time() - began
    if not _approve(state, name, args, emit):
        return "The user denied this action.", 0.0
    tool = state.tool_map.get(name)
    if tool is None:
        return f"Unknown tool: {name}", 0.0

    began = time.time()
    try:
        if spinner:
            with emit.status(f"  running {name}..."):
                result = str(tool.invoke(args))
        else:
            result = str(tool.invoke(args))
    except Exception as exc:
        result = f"Tool error: {exc}"
    return result, time.time() - began


def _log_tool(state: ChatState, call: dict, result: str, seconds: float) -> None:
    state.tool_log.append(
        {"name": call.get("name", "tool"), "args": call.get("args") or {}, "result": result, "seconds": seconds}
    )
    del state.tool_log[:-30]


def run_turn(state: ChatState, user_text: str, target) -> str:
    emit = as_emitter(target)
    state.cancel.clear()
    state.session.add("user", user_text)
    emit.user_message(user_text)
    messages = build_messages(state.cfg, state.session, state.workspace, state.plan_mode)
    expanded = expand_mentions(user_text, state.workspace)
    if expanded != user_text and messages and isinstance(messages[-1], HumanMessage):
        messages[-1] = HumanMessage(content=expanded)

    started = time.time()
    turn_usage: dict = {}
    while True:
        if state.cancel.is_set():
            state.cancel.clear()
            emit.blank()
            emit.info("interrupted")
            return ""
        try:
            ai, usage = _stream_with_fallback(state, messages, emit)
        except KeyboardInterrupt:
            state.cancel.clear()
            emit.blank()
            emit.info("interrupted")
            return ""
        except Exception as exc:
            emit.error(f"model failed: {exc}")
            return ""
        for key, value in usage.items():
            turn_usage[key] = turn_usage.get(key, 0) + value
        messages.append(ai)
        if ai.tool_calls:
            state.session.messages.append(
                {"role": "assistant", "content": ai.content, "tool_calls": ai.tool_calls, "at": time.time()}
            )
        else:
            break

        calls = ai.tool_calls
        parallel = len(calls) > 1 and all(c.get("name") in SAFE_PARALLEL for c in calls)
        if parallel:
            with emit.status(f"  running {len(calls)} tools..."):
                with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
                    results = list(pool.map(lambda c: _call_tool(state, c, emit, spinner=False), calls))
            for call, (result, seconds) in zip(calls, results, strict=True):
                emit.tool_call(call.get("name", ""), call.get("args") or {})
                emit.tool_summary(call.get("name", ""), result, seconds)
                _log_tool(state, call, result, seconds)
                messages.append(ToolMessage(content=result, tool_call_id=call.get("id") or ""))
                state.session.messages.append(
                    {
                        "role": "tool",
                        "content": clamp_tool_output(result),
                        "tool_call_id": call.get("id") or "",
                        "at": time.time(),
                    }
                )
            continue

        for index, call in enumerate(calls):
            name = call.get("name", "")
            args = call.get("args") or {}
            call_id = call.get("id") or f"call_{index}"
            emit.tool_call(name, args)
            result, seconds = _call_tool(state, call, emit)
            emit.tool_summary(name, result, seconds)
            _log_tool(state, call, result, seconds)
            messages.append(ToolMessage(content=result, tool_call_id=call_id))
            state.session.messages.append(
                {
                    "role": "tool",
                    "content": clamp_tool_output(result),
                    "tool_call_id": call_id,
                    "at": time.time(),
                }
            )

    answer = message_text(ai.content)
    if answer:
        state.session.add("assistant", answer)
    state.session.add_usage(turn_usage.get("input", 0), turn_usage.get("output", 0))
    state.session.save()
    emit.footer(
        time.time() - started,
        turn_usage,
        state.cfg.model,
        cost=estimate(state.cfg.model, turn_usage),
    )
    return answer


def run_shell_direct(command: str, state: ChatState, target) -> None:
    emit = as_emitter(target)
    began = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=state.workspace,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        output = ((proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")).strip()
        status = proc.returncode
    except Exception as exc:
        output, status = str(exc), 1
    emit.rule(f"! {command}")
    for line in (output or "(no output)").splitlines()[:20]:
        emit.output(line + "\n")
    emit.footer(time.time() - began, {}, f"exit {status}")
    if not state.session.title:
        state.session.title = f"shell: {command[:40]}"
    state.session.add(
        "user",
        f"I ran this shell command in the workspace:\n```\n{command}\n```\n"
        f"exit={status}\noutput:\n```\n{output[:4000]}\n```",
    )
    state.session.save()


def compact(state: ChatState, target) -> None:
    emit = as_emitter(target)
    transcript = as_transcript(state.session)
    if not transcript.strip():
        emit.info("nothing to compact")
        return
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Summarise this terminal-assistant transcript for future context. "
                "Keep: the user's goals, decisions, file paths, commands and their results, "
                "open questions. Drop pleasantries. Markdown bullets, under 300 words.",
            ),
            ("human", "{transcript}"),
        ]
    )
    emit.info("compacting...")
    began = time.time()
    try:
        summary = (prompt | state.model | StrOutputParser()).invoke({"transcript": transcript[:24000]})
    except Exception as exc:
        emit.error(f"compact failed: {exc}")
        return
    state.session.messages = [
        {"role": "assistant", "content": "Summary of the conversation so far:\n" + str(summary).strip()}
    ]
    state.session.save()
    emit.ok(f"compacted in {time.time() - began:.1f}s")


def _rebuild(state: ChatState, emit) -> bool:
    try:
        state.model = build_model(state.cfg)
        return True
    except ModelError as exc:
        emit.error(str(exc))
        return False


def _commit(state: ChatState, message: str, emit) -> None:
    if not message:
        emit.error("usage: /commit <message>")
        return
    if not state.auto_yes and not emit.confirm(f"git add -A && git commit -m {message!r}"):
        emit.info("cancelled")
        return
    try:
        run_git(["add", "-A"], state.workspace)
        out = run_git(["commit", "-m", message], state.workspace)
    except Exception as exc:
        emit.error(f"commit failed: {exc}")
        return
    for line in out.splitlines()[:10]:
        emit.output(line + "\n")
    emit.ok("committed")


def handle_command(raw: str, state: ChatState, con, root) -> bool:
    """Returns False when the user wants to leave."""
    emit = as_emitter(con)
    parts = raw.strip().split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in {"/exit", "/quit", "/q"}:
        return False
    if cmd == "/help":
        emit.help()
        return True
    if cmd == "/model":
        if not arg:
            emit.info(f"model: {state.cfg.model}")
            return True
        state.cfg.model = arg
        if _rebuild(state, emit):
            state.session.model = arg
            save(state.cfg)
            emit.ok(f"model -> {arg}")
        return True
    if cmd == "/provider":
        provider = arg.lower()
        if not provider:
            emit.info(f"provider: {state.cfg.provider}")
            return True
        state.cfg.provider = provider
        state.cfg.model = default_model(provider)
        if _rebuild(state, emit):
            save(state.cfg)
            emit.ok(f"provider -> {provider} · {state.cfg.model}")
        return True
    if cmd == "/agents":
        from .agent_catalog import list_agents

        for profile in list_agents():
            emit.info(f"  {profile.id:<12} {profile.permission:<11} {profile.description}")
        return True
    if cmd == "/skills":
        from .skills import all_skills

        for skill in all_skills():
            emit.info(f"  {skill.name} · {', '.join(skill.keywords[:4])}")
        return True
    if cmd == "/tasks":
        from .orchestrator import list_boards

        rows = list_boards()
        if not rows:
            emit.info("no agent runs yet")
        for row in rows[:20]:
            emit.info(
                f"  {row['run_id']} · {row['status']} · "
                f"{row['completed']}/{row['tasks']} · {row['goal'][:60]}"
            )
        return True
    if cmd == "/tools":
        if arg in {"on", "off"}:
            state.cfg.tools = arg == "on"
            state.refresh_tools()
            save(state.cfg)
            emit.ok(f"tools {'on' if state.cfg.tools else 'off'}")
        else:
            emit.info(f"tools: {'on' if state.cfg.tools else 'off'}")
        return True
    if cmd == "/plan":
        if arg in {"on", "off"}:
            state.plan_mode = arg == "on"
        else:
            state.plan_mode = not state.plan_mode
        emit.ok(f"plan mode {'on' if state.plan_mode else 'off'}")
        return True
    if cmd == "/yolo":
        if arg in {"on", "off"}:
            state.auto_yes = arg == "on"
        else:
            state.auto_yes = not state.auto_yes
        emit.ok(f"yolo mode {'on' if state.auto_yes else 'off'}")
        return True
    if cmd == "/diff":
        try:
            out = run_git(["diff", "--stat"], state.workspace) + "\n" + run_git(
                ["diff", "--color=never"], state.workspace
            )
        except Exception as exc:
            emit.error(str(exc))
            return True
        for line in out.splitlines()[:40]:
            emit.output(line + "\n")
        return True
    if cmd == "/commit":
        _commit(state, arg, emit)
        return True
    if cmd == "/expand":
        if not state.tool_log:
            emit.info("nothing to expand yet")
            return True
        index = int(arg) if arg.isdigit() else 1
        index = min(max(index, 1), len(state.tool_log))
        emit.expand_tool(state.tool_log[-index])
        return True
    if cmd == "/session":
        usage = state.session.usage
        cost = estimate(state.cfg.model, usage)
        tokens = f" · {usage.get('total', 0)} tok" if usage.get("total") else ""
        money = f" · ~${cost:.4f}" if cost else ""
        mode = " · plan" if state.plan_mode else ""
        emit.info(
            f"session {state.session.id} · {state.cfg.provider}/{state.cfg.model} · "
            f"{len(state.session.messages)} messages{tokens}{money}{mode}",
        )
        return True
    if cmd == "/undo":
        messages = state.session.messages
        while messages and messages[-1].get("role") != "user":
            messages.pop()
        if messages:
            messages.pop()
            state.session.save()
            emit.ok("last exchange removed")
        else:
            emit.info("nothing to undo")
        return True
    if cmd == "/compact":
        compact(state, emit)
        return True
    if cmd == "/clear":
        # Keep the old chat on disk so it can be reopened from Select session.
        if state.session.messages:
            try:
                state.session.save()
            except OSError:
                pass
        state.session = Session(model=state.cfg.model)
        emit.ok(f"fresh conversation · session {state.session.id} (previous chat kept)")
        return True

    emit.error(f"unknown command {cmd} — try /help")
    return True


def chat(cfg: Config, state: ChatState, root, con: Console | None = None) -> None:
    con = con or ui.default_console
    from .repl import make_reader

    reader = make_reader(f"{cfg.provider} · {cfg.model} · tools {'on' if cfg.tools else 'off'}")
    ui.banner(cfg, state.session, con, plan=state.plan_mode, yolo=state.auto_yes)
    if state.auto_yes:
        ui.info("yolo mode · writes and shell commands run without asking", con)
    ui.info("type a message · @file · !cmd · /help", con)
    con.print()

    try:
        while True:
            try:
                raw = reader() if reader else con.input(ui.user_prompt(con))
            except (EOFError, KeyboardInterrupt):
                con.print()
                break
            except Exception as exc:  # prompt_toolkit trouble: fall back to plain input
                ui.error(f"input failed ({exc}); using plain prompt", con)
                reader = None
                continue
            text = raw.strip()
            if not text:
                continue
            if text.startswith("/"):
                if not handle_command(text, state, con, root):
                    break
                continue
            if text.startswith("!"):
                run_shell_direct(text[1:].strip(), state, con)
                continue
            run_turn(state, text, con)
            con.print()
    finally:
        if state.session.messages:
            state.session.save()
            ui.info(f"saved · {state.session.path}", con)


def ask_once(
    cfg: Config,
    text: str,
    con: Console | None = None,
    tools_enabled: bool = False,
    root=None,
) -> str:
    con = con or ui.default_console
    session = Session(model=cfg.model)
    tools = build_tools(root or Path.cwd()) if tools_enabled else []
    state = ChatState(
        cfg=cfg,
        session=session,
        model=build_model(cfg),
        tools=tools,
        root=Path(root) if root else Path.cwd(),
    )
    return run_turn(state, text, con)


def dump_session(session: Session) -> str:
    return json.dumps(
        {"id": session.id, "model": session.model, "messages": session.messages},
        indent=2,
        ensure_ascii=False,
    )


__all__ = [
    "ChatState",
    "ask_once",
    "build_messages",
    "chat",
    "compact",
    "expand_mentions",
    "handle_command",
    "project_rules",
    "run_shell_direct",
    "run_turn",
]
