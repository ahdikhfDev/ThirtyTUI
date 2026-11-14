"""Full-screen TUI: rendered markdown on top, pinned input at the bottom.

Output is rendered with Rich (real markdown + syntax highlighting), converted to
prompt_toolkit fragments, and stored in a scrolling buffer. The agent runs in a
worker thread; the UI thread only appends rendered lines and repaints.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import queue
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Dimension,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.styles.pygments import style_from_pygments_cls
from prompt_toolkit.widgets import TextArea
from pygments.styles import get_style_by_name
from rich.console import Console
from rich.markdown import Markdown

from . import __version__, chat
from .config import home, save
from .dashboard import DashboardState
from .models import (
    MODEL_CHOICES,
    PROVIDER_LABELS,
    PROVIDERS,
    default_model,
    key_source,
)
from .render import ASSISTANT_CODE_THEME, split_complete
from .repl import _scan_files, make_completer
from .secrets import redact
from .sessions import Session, list_sessions
from .theme import DIM, INK, LINE, MARK_ROWS, MUTED, ORANGE, THEME
from .ui import COMMANDS, _fmt_arg

MAX_LINES = 4000
PREVIEW_LINES = 8
FLUSH_INTERVAL = 0.10
PALETTE_WIDTH_MIN = 52
PALETTE_WIDTH_MAX = 64
PALETTE_HEIGHT_MIN = 14
PALETTE_HEIGHT_MAX = 24
PALETTE_ROWS = 17
DASHBOARD_WIDTH = 96
DASHBOARD_HEIGHT = 30

STYLE = merge_styles(
    [
        Style.from_dict(
            {
                "output": "#E4E4E4",
                "tui.mark": f"bold {ORANGE}",
                "tui.user": f"bold {ORANGE}",
                "tui.tool": ORANGE,
                "tui.meta": DIM,
                "tui.result": MUTED,
                "tui.ok": "#61C985",
                "tui.err": "#FF6B6B",
                "tui.diff": "#42A0FF",
                "tui.stream": "#CFCFCF",
                "divider": "#2A2A2A",
                "tui.frame": LINE,
                "palette": "bg:#212121 #E4E4E4",
                "palette.title": "bg:#212121 bold #FFFFFF",
                "palette.hint": "bg:#212121 #6A6A6A",
                "palette.search": "bg:#212121 #979797",
                "palette.input": "bg:#212121 #E4E4E4",
                "palette.section": "bg:#212121 bold #42A0FF",
                "palette.row": "bg:#212121 #D0D0D0",
                "palette.selected": f"bg:{ORANGE} {INK} bold",
                "palette.empty": "bg:#212121 #6A6A6A",
                "palette.border": "bg:#212121 #4A4A4A",
                "input": f"bg:#141414 {ORANGE}",
                "input.prompt": f"bold {ORANGE}",
                "status": f"bg:#101010 {MUTED}",
                "status.busy": f"bg:#101010 bold {ORANGE}",
                "status.ask": "bg:#101010 bold #FFFFFF",
                "status.hint": f"bg:#101010 {DIM}",
                "completion-menu.completion": "bg:#212121 #979797",
                "completion-menu.completion.current": f"bg:{ORANGE} {INK} bold",
                "completion-menu.meta.completion": "bg:#212121 #6A6A6A",
                "completion-menu.meta.completion.current": f"bg:#D89A35 {INK}",
            }
        ),
        style_from_pygments_cls(get_style_by_name("monokai")),
    ]
)


def ansi_to_lines(ansi: str) -> list[list[tuple[str, str]]]:
    """Convert Rich ANSI output into per-line prompt_toolkit fragments."""
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in ANSI(ansi).__pt_formatted_text__():
        parts = text.split("\n")
        for index, part in enumerate(parts):
            if index:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    while lines and not lines[-1]:
        lines.pop()
    return lines


def render_markdown_lines(text: str, width: int) -> list[list[tuple[str, str]]]:
    buffer = StringIO()
    console = Console(
        file=buffer,
        force_terminal=True,
        width=max(20, width),
        color_system="standard",
        theme=THEME,
        highlight=False,
        soft_wrap=False,
    )
    console.print(Markdown(text, code_theme=ASSISTANT_CODE_THEME))
    return ansi_to_lines(buffer.getvalue())


@dataclass
class PaletteEntry:
    label: str
    section: str
    hint: str = ""
    command: str = ""
    action: Callable[[], None] | None = None


class StoredLinesLexer(Lexer):
    """Renders pre-styled lines regardless of the buffer text."""

    def __init__(self, lines_getter):
        self.lines_getter = lines_getter

    def lex_document(self, document):
        def get_line(lineno: int):
            lines = self.lines_getter()
            if 0 <= lineno < len(lines):
                return lines[lineno]
            return []

        return get_line


class TuiEmitter:
    """Turns agent output into styled lines / streamed markdown."""

    def __init__(self, tui: Tui):
        self.tui = tui

    def _line(self, text: str, style: str) -> None:
        self.tui.add_text(redact(text), style)

    def user_message(self, text: str) -> None:
        self._line(f"\n  >_ {text}\n", "class:tui.user")

    def assistant_delta(self, text: str) -> None:
        self.tui.stream_text(text)

    def assistant_end(self) -> None:
        self.tui.end_stream()

    def tool_call(self, name: str, args: dict) -> None:
        rendered = ", ".join(f"{key}={_fmt_arg(value)}" for key, value in args.items())
        if len(rendered) > 110:
            rendered = rendered[:107] + "..."
        self._line(f"\n  :: {name}({rendered})\n", "class:tui.tool")

    def tool_summary(self, name: str, result: str, seconds: float) -> None:
        lines = redact(result).splitlines() or [""]
        first = lines[0]
        if first.startswith(("Tool error", "The user denied", "Unknown tool", "Plan mode", "HTTP ")):
            self._line(f"  | {first[:160]}\n", "class:tui.err")
        tail = "1 line" if len(lines) == 1 else f"{len(lines)} lines"
        if seconds:
            tail += f" · {seconds:.2f}s"
        if len(lines) > 1:
            tail += "  (ctrl+o)"
        self._line(f"  -- {tail}\n", "class:tui.meta")

    def footer(self, seconds: float, usage: dict, model: str, cost: float | None = None) -> None:
        bits = [f"{seconds:.1f}s"]
        if usage and usage.get("total"):
            bits.append(f"{usage['total']} tok")
        if cost:
            bits.append(f"~${cost:.4f}")
        if model:
            bits.append(model)
        self._line(f"\n  {' · '.join(bits)}\n", "class:tui.meta")

    def info(self, message: str) -> None:
        self._line(f"  {message}\n", "class:tui.meta")

    def ok(self, message: str) -> None:
        self._line(f"  + {message}\n", "class:tui.ok")

    def error(self, message: str) -> None:
        self._line(f"  ! {message}\n", "class:tui.err")

    def output(self, text: str) -> None:
        self._line(text, "class:tui.result")

    def confirm(self, question: str) -> bool:
        return self.tui.confirm(question)

    def diff(self, path: str, old: str, new: str) -> None:
        lines = list(
            difflib.unified_diff(
                old.splitlines(), new.splitlines(), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
            )
        )
        self._line(f"\n  ~ {path}\n", "class:tui.diff")
        for line in lines[:40]:
            self._line(f"  {line}\n", "class:tui.diff")
        if len(lines) > 40:
            self._line(f"  ... {len(lines) - 40} more diff lines\n", "class:tui.meta")

    def expand_tool(self, entry: dict) -> None:
        self._line(f"\n  :: {entry.get('name', 'tool')} · {entry.get('seconds', 0):.2f}s\n", "class:tui.tool")
        for line in redact(str(entry.get("result", ""))).splitlines()[:400]:
            self._line(f"  | {line}\n", "class:tui.result")

    def help(self) -> None:
        self._line("\n  commands\n", "class:tui.meta")
        for command, desc in COMMANDS:
            self._line(f"  {command:<11} {desc}\n", "class:tui.meta")
        self._line("\n  input\n", "class:tui.meta")
        self._line("  @file        inline a file\n", "class:tui.meta")
        self._line("  !cmd         run a shell command, output goes to the model\n", "class:tui.meta")
        self._line("  Alt+Enter    new line\n", "class:tui.meta")
        self._line("  Ctrl+O       re-open the last tool output\n", "class:tui.meta")
        self._line("  Ctrl+P       command palette (search, arrows, enter)\n", "class:tui.meta")
        self._line("  Ctrl+X M/P/L model / provider / session picker\n", "class:tui.meta")
        self._line("  Ctrl+D       task dashboard (live events from the latest run)\n", "class:tui.meta")
        self._line("  /agents      list agent profiles (also /skills, /tasks)\n", "class:tui.meta")
        self._line("  Ctrl+C       stop the answer (quit when idle)\n", "class:tui.meta")

    def status(self, label: str):
        return nullcontext()

    def blank(self) -> None:
        self._line("\n", "class:tui.meta")

    def rule(self, label: str = "") -> None:
        self._line(f"\n  -- {label or '----'} --\n", "class:tui.meta")


class Tui:
    def __init__(self, cfg, state: chat.ChatState, root: Path, input=None, output=None):
        self.cfg = cfg
        self.state = state
        self.root = Path(root)
        self.emitter = TuiEmitter(self)
        self.queue: queue.Queue = queue.Queue()
        self.busy = False
        self.frame = 0
        self.closing = False
        self.pending_question: str | None = None
        self._answer = False
        self._answer_event = threading.Event()
        self._stream_chunks: list[str] = []
        self._stream_lock = threading.Lock()
        self.loop = None

        self.lines: list[list[tuple[str, str]]] = []
        self.pending = ""
        self._follow = True
        self.buffer = Buffer(read_only=True)
        self.output_control = BufferControl(
            buffer=self.buffer,
            lexer=StoredLinesLexer(self._display_lines),
            focusable=True,
        )
        self.output = Window(
            self.output_control,
            wrap_lines=True,
            height=Dimension(weight=1),
            style="class:output",
        )

        self.input = TextArea(
            multiline=True,
            completer=make_completer(_scan_files(Path.cwd())),
            complete_while_typing=True,
            history=FileHistory(str(home() / "history")),
            height=Dimension(min=2, max=6, preferred=2),
            prompt=">_ ",
            style="class:input",
        )

        self.input.buffer.on_text_changed += self._resize_input
        # HSplit would otherwise stretch the box to its max height even when empty.
        self.input.window.dont_extend_height = Condition(lambda: True)
        self.status_control = FormattedTextControl(self._status)
        self.status_window = Window(self.status_control, height=1, style="class:status")

        self.divider = Window(height=1, char="─", style="class:divider")
        self.palette_open = False
        self.palette_index = 0
        self.palette_width = PALETTE_WIDTH_MAX
        self.palette_list_rows = PALETTE_ROWS
        self.picker_mode = "commands"
        self.picker_title = "Commands"
        self.picker_provider: str | None = None
        self.palette_input = TextArea(
            multiline=False,
            prompt=[("class:palette.search", " Search  ")],
            style="class:palette.input",
            height=1,
            accept_handler=self._palette_accept,
        )
        self.palette_input.buffer.on_text_changed += self._palette_changed
        self.palette_list = Window(
            FormattedTextControl(self._palette_rows, focusable=False),
            height=Dimension(preferred=PALETTE_ROWS),
            style="class:palette",
            wrap_lines=False,
        )
        self.palette_body = HSplit(
            [
                Window(FormattedTextControl(self._palette_title), height=1, style="class:palette"),
                self.palette_input,
                Window(height=1, style="class:palette"),
                self.palette_list,
                Window(height=1, style="class:palette"),
            ],
            width=Dimension(preferred=PALETTE_WIDTH_MAX - 2),
            height=Dimension(preferred=PALETTE_ROWS + 4),
            style="class:palette",
        )
        self.palette_panel = HSplit(
            [
                Window(
                    FormattedTextControl(lambda: self._palette_edge("┌", "┐")),
                    height=1,
                    style="class:palette.border",
                ),
                VSplit(
                    [
                        Window(width=1, char="│", style="class:palette.border"),
                        self.palette_body,
                        Window(width=1, char="│", style="class:palette.border"),
                    ]
                ),
                Window(
                    FormattedTextControl(lambda: self._palette_edge("└", "┘")),
                    height=1,
                    style="class:palette.border",
                ),
            ],
            width=Dimension(preferred=PALETTE_WIDTH_MAX),
            height=Dimension(preferred=PALETTE_HEIGHT_MAX),
            style="class:palette",
        )
        self.palette_float = Float(
            content=ConditionalContainer(
                content=HSplit(
                    [
                        Window(height=Dimension(weight=1)),
                        VSplit(
                            [
                                Window(width=Dimension(weight=1)),
                                self.palette_panel,
                                Window(width=Dimension(weight=1)),
                            ]
                        ),
                        Window(height=Dimension(weight=1)),
                    ]
                ),
                filter=Condition(lambda: self.palette_open),
            ),
            z_index=10,
        )
        self.dashboard = DashboardState()
        self.dashboard_open = False
        self.dashboard_run_id = ""
        self.dashboard_offset = 0
        self.dashboard_width = DASHBOARD_WIDTH
        self.dashboard_height = DASHBOARD_HEIGHT
        self._last_dashboard = 0.0
        self.dashboard_list = Window(
            FormattedTextControl(self._dashboard_rows, focusable=False),
            height=Dimension(preferred=DASHBOARD_HEIGHT - 2),
            style="class:palette",
            wrap_lines=False,
        )
        self.dashboard_panel = HSplit(
            [
                Window(
                    FormattedTextControl(lambda: self._dashboard_edge("╭", "╮")),
                    height=1,
                    style="class:palette.border",
                ),
                VSplit(
                    [
                        Window(width=1, char="│", style="class:palette.border"),
                        self.dashboard_list,
                        Window(width=1, char="│", style="class:palette.border"),
                    ]
                ),
                Window(
                    FormattedTextControl(lambda: self._dashboard_edge("╰", "╯")),
                    height=1,
                    style="class:palette.border",
                ),
            ],
            width=Dimension(preferred=DASHBOARD_WIDTH),
            height=Dimension(preferred=DASHBOARD_HEIGHT),
            style="class:palette",
        )
        self.dashboard_float = Float(
            content=ConditionalContainer(
                content=HSplit(
                    [
                        Window(height=Dimension(weight=1)),
                        VSplit(
                            [
                                Window(width=Dimension(weight=1)),
                                self.dashboard_panel,
                                Window(width=Dimension(weight=1)),
                            ]
                        ),
                        Window(height=Dimension(weight=1)),
                    ]
                ),
                filter=Condition(lambda: self.dashboard_open),
            ),
            z_index=11,
        )
        self.layout = Layout(
            FloatContainer(
                content=HSplit([self.output, self.divider, self.input, self.status_window]),
                floats=[
                    Float(
                        content=CompletionsMenu(max_height=10, scroll_offset=1),
                        xcursor=True,
                        ycursor=True,
                    ),
                    self.palette_float,
                    self.dashboard_float,
                ],
            ),
            focused_element=self.input,
        )

        bindings = KeyBindings()
        palette_open = Condition(lambda: self.palette_open)
        dashboard_open = Condition(lambda: self.dashboard_open)

        @bindings.add("enter", filter=has_focus(self.input))
        def _submit(event) -> None:
            buffer = self.input.buffer
            state = buffer.complete_state
            if state and state.completions:
                completion = state.current_completion or state.completions[0]
                if completion.text != buffer.text:
                    buffer.apply_completion(completion)
                    return
            self.submit()

        @bindings.add("escape", "enter", filter=has_focus(self.input))
        def _newline(event) -> None:
            self.input.buffer.insert_text("\n")

        @bindings.add("c-o", filter=~palette_open)
        def _expand(event) -> None:
            if self.state.tool_log:
                self.emitter.expand_tool(self.state.tool_log[-1])

        @bindings.add("c-c")
        def _interrupt(event) -> None:
            if self.busy:
                self.state.cancel.set()
                if self.pending_question is not None:
                    self.answer("n")
                self._add_text_ui("\n  ! stopping...\n", "class:tui.err")
                return
            if self.palette_open:
                self.close_palette()
                return
            if self.dashboard_open:
                self.close_dashboard()
                return
            event.app.exit()

        @bindings.add("c-d")
        def _dashboard(event) -> None:
            self.toggle_dashboard()

        @bindings.add("escape", filter=dashboard_open)
        def _dashboard_close(event) -> None:
            self.close_dashboard()

        @bindings.add("c-p")
        def _palette(event) -> None:
            self.toggle_palette()

        @bindings.add("escape", filter=palette_open)
        def _palette_close(event) -> None:
            self.close_palette()

        @bindings.add("up", filter=palette_open)
        def _palette_up(event) -> None:
            self.move_palette(-1)

        @bindings.add("down", filter=palette_open)
        def _palette_down(event) -> None:
            self.move_palette(1)

        @bindings.add("pageup", filter=palette_open)
        def _palette_page_up(event) -> None:
            self.move_palette(-5)

        @bindings.add("pagedown", filter=palette_open)
        def _palette_page_down(event) -> None:
            self.move_palette(5)

        @bindings.add("c-x", "m")
        def _quick_model(event) -> None:
            self.open_model_picker()

        @bindings.add("c-x", "p")
        def _quick_provider(event) -> None:
            self.open_provider_picker()

        @bindings.add("c-x", "n")
        def _quick_new(event) -> None:
            self._new_session()

        @bindings.add("c-x", "l")
        def _quick_session(event) -> None:
            self.open_session_picker()

        @bindings.add("c-t", filter=~palette_open)
        def _focus_output(event) -> None:
            self.scroll(0)

        @bindings.add("escape", filter=has_focus(self.output))
        def _focus_input(event) -> None:
            event.app.layout.focus(self.input)

        @bindings.add("pageup", filter=~palette_open)
        def _page_up(event) -> None:
            self.scroll(-1)

        @bindings.add("pagedown", filter=~palette_open)
        def _page_down(event) -> None:
            self.scroll(1)

        @bindings.add("home", filter=~palette_open)
        def _top(event) -> None:
            self.scroll(-9999)

        @bindings.add("end", filter=~palette_open)
        def _bottom(event) -> None:
            self.scroll(9999)

        self.app = Application(
            layout=self.layout,
            key_bindings=bindings,
            style=STYLE,
            full_screen=True,
            mouse_support=True,
            input=input,
            output=output,
        )

    # -- rendering -----------------------------------------------------
    def add_text(self, text: str, style: str) -> None:
        """Append plain text lines with one style (thread-safe)."""
        self._call(self._add_text_ui, text, style)

    def _add_text_ui(self, text: str, style: str) -> None:
        for line in redact(text).split("\n"):
            if line:
                self.lines.append([(style, line)])
        self._trim()
        self._refresh()

    def _add_frag_lines(self, lines: list[list[tuple[str, str]]]) -> None:
        for line in lines:
            line = [(style, redact(text)) for style, text in line]
            if line:
                self.lines.append(line)
        self._trim()
        self._refresh()

    def _trim(self) -> None:
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    def _resize_input(self, _buffer) -> None:
        rows = min(6, max(2, self.input.buffer.document.line_count))
        if self.input.window.height.preferred != rows:
            self.input.window.height = Dimension(min=2, max=6, preferred=rows)
            self.app.invalidate()

    # -- command palette -----------------------------------------------
    def _palette_entries(self) -> list[PaletteEntry]:
        return [
            PaletteEntry("Switch model", "Suggested", "ctrl+x m", action=self.open_model_picker),
            PaletteEntry("Switch provider", "Suggested", "ctrl+x p", action=self.open_provider_picker),
            PaletteEntry("Switch session", "Suggested", "ctrl+x l", action=self.open_session_picker),
            PaletteEntry("New session", "Session", "ctrl+x n", action=self._new_session),
            PaletteEntry("Clear conversation", "Session", command="/clear"),
            PaletteEntry("Compact conversation", "Session", command="/compact"),
            PaletteEntry("Quit", "Session", command="/exit"),
            PaletteEntry("Show help", "Commands", command="/help"),
            PaletteEntry("Show agents", "Commands", command="/agents"),
            PaletteEntry("Show skills", "Commands", command="/skills"),
            PaletteEntry("Show task runs", "Commands", command="/tasks"),
            PaletteEntry("Toggle tools", "Commands", command="/tools"),
            PaletteEntry("Toggle plan mode", "Commands", command="/plan"),
            PaletteEntry("Show diff", "Commands", command="/diff"),
            PaletteEntry("Expand last tool output", "Commands", command="/expand"),
        ]

    def _model_entries(self) -> list[PaletteEntry]:
        cfg = self.state.cfg
        providers = [self.picker_provider] if self.picker_provider else [
            cfg.provider,
            *[provider for provider in PROVIDERS if provider != cfg.provider],
        ]
        entries: list[PaletteEntry] = []
        seen: set[tuple[str, str]] = set()
        for provider in providers:
            section = PROVIDER_LABELS.get(provider, provider)
            models = list(MODEL_CHOICES.get(provider, ()))
            fallback = default_model(provider)
            if fallback not in models:
                models.insert(0, fallback)
            if provider == cfg.provider and cfg.model and cfg.model not in models:
                models.insert(0, cfg.model)
            for model in models:
                if (section, model) in seen:
                    continue
                seen.add((section, model))
                hint = "current" if (provider == cfg.provider and model == cfg.model) else ""
                entries.append(
                    PaletteEntry(
                        model,
                        section,
                        hint,
                        action=lambda p=provider, m=model: self._choose_model(p, m),
                    )
                )
        return entries

    def _provider_entries(self) -> list[PaletteEntry]:
        cfg = self.state.cfg
        entries: list[PaletteEntry] = []
        for provider in PROVIDERS:
            if provider == cfg.provider:
                hint = "current"
            else:
                source = key_source(provider, cfg)
                hint = "local" if source == "not needed" else ("no key" if source == "missing" else "")
            entries.append(
                PaletteEntry(
                    provider,
                    "Providers",
                    hint,
                    action=lambda p=provider: self._choose_provider(p),
                )
            )
        return entries

    def _session_entries(self) -> list[PaletteEntry]:
        entries: list[PaletteEntry] = []
        for session in list_sessions()[:40]:
            if not session.messages and session.id != self.state.session.id:
                continue
            current = session.id == self.state.session.id
            hint = (
                f"current · {session.id}"
                if current
                else f"{session.id} · {len(session.messages)} msgs"
            )
            entries.append(
                PaletteEntry(
                    session.title or session.id,
                    "Sessions",
                    hint,
                    action=lambda sid=session.id: self._choose_session(sid),
                )
            )
        return entries

    def _picker_items(self) -> list[PaletteEntry]:
        if self.picker_mode == "models":
            return self._model_entries()
        if self.picker_mode == "providers":
            return self._provider_entries()
        if self.picker_mode == "sessions":
            return self._session_entries()
        return self._palette_entries()

    def _palette_matches(self) -> list[PaletteEntry]:
        query = self.palette_input.text.strip().lower()
        entries = self._picker_items()
        if not query:
            return entries
        return [
            entry
            for entry in entries
            if query in f"{entry.label} {entry.command} {entry.hint} {entry.section}".lower()
        ]

    def _palette_changed(self, _buffer) -> None:
        self.palette_index = 0
        self.app.invalidate()

    def _palette_inner_width(self) -> int:
        return max(10, self.palette_width - 2)

    def _palette_edge(self, left: str, right: str) -> list[tuple[str, str]]:
        return [("class:palette.border", left + "─" * self._palette_inner_width() + right)]

    def _palette_title(self) -> list[tuple[str, str]]:
        left = f"  {self.picker_title}"
        right = "esc  "
        pad = max(1, self._palette_inner_width() - len(left) - len(right))
        return [
            ("class:palette.title", left),
            ("class:palette", " " * pad),
            ("class:palette.hint", right),
        ]

    def _palette_lines(self) -> list[tuple[str, str, int | None]]:
        matches = self._palette_matches()
        lines: list[tuple[str, str, int | None]] = []
        inner = self._palette_inner_width()
        text_width = max(1, inner - 2)  # keep two cells clear of the right border
        position = 0
        sections: list[str] = []
        for entry in matches:
            if entry.section not in sections:
                sections.append(entry.section)
        for section in sections:
            items = [entry for entry in matches if entry.section == section]
            if lines:
                lines.append(("class:palette", "", None))
            lines.append(("class:palette.section", f"  {section}", None))
            for entry in items:
                style = (
                    "class:palette.selected"
                    if position == self.palette_index
                    else "class:palette.row"
                )
                room = max(1, text_width - len(entry.hint))
                label = f"  {entry.label}"[:room]
                pad = max(0, text_width - len(label) - len(entry.hint))
                line = (label + " " * pad + entry.hint).ljust(text_width)
                lines.append((style, line + "  ", position))
                position += 1
        if not matches:
            empty = {
                "models": "no matching model",
                "providers": "no matching provider",
                "sessions": "no saved sessions",
            }.get(self.picker_mode, "no matching command")
            lines.append(("class:palette.empty", f"  {empty}", None))
        return lines

    def _palette_rows(self) -> list[tuple[str, str]]:
        lines = self._palette_lines()
        rows = max(1, self.palette_list_rows)
        if len(lines) > rows:
            selected = next(
                (
                    index
                    for index, (_style, _text, entry) in enumerate(lines)
                    if entry == self.palette_index
                ),
                0,
            )
            start = min(max(0, selected - rows + 1), len(lines) - rows)
            lines = lines[start : start + rows]
        fragments: list[tuple[str, str]] = []
        for index, (style, text, _entry) in enumerate(lines):
            if index:
                fragments.append(("class:palette", "\n"))
            fragments.append((style, text))
        return fragments

    def _size_palette(self) -> None:
        size = self.app.output.get_size()
        width = max(PALETTE_WIDTH_MIN, min(PALETTE_WIDTH_MAX, int(size.columns * 0.60)))
        height = max(PALETTE_HEIGHT_MIN, min(PALETTE_HEIGHT_MAX, int(size.rows * 0.66)))
        list_rows = max(1, height - 6)
        self.palette_width = width
        self.palette_list_rows = list_rows
        self.palette_panel.width = Dimension(preferred=width)
        self.palette_panel.height = Dimension(preferred=list_rows + 6)
        self.palette_body.width = Dimension(preferred=width - 2)
        self.palette_body.height = Dimension(preferred=list_rows + 4)
        self.palette_list.height = Dimension(preferred=list_rows)

    def open_palette(self) -> None:
        if self.pending_question is not None:
            return
        self.picker_mode = "commands"
        self.picker_title = "Commands"
        self.picker_provider = None
        self._open_picker()

    def open_model_picker(self, provider: str | None = None) -> None:
        if self.pending_question is not None:
            return
        self.picker_mode = "models"
        self.picker_title = "Select model"
        self.picker_provider = provider
        self._open_picker()

    def open_provider_picker(self) -> None:
        if self.pending_question is not None:
            return
        self.picker_mode = "providers"
        self.picker_title = "Select provider"
        self.picker_provider = None
        self._open_picker()

    def open_session_picker(self) -> None:
        if self.pending_question is not None:
            return
        self.picker_mode = "sessions"
        self.picker_title = "Select session"
        self.picker_provider = None
        self._open_picker()

    def _open_picker(self) -> None:
        self._size_palette()
        self.palette_open = True
        self.palette_index = 0
        self.palette_input.text = ""
        self._focus(self.palette_input)
        self.app.invalidate()

    def close_palette(self) -> None:
        if not self.palette_open:
            return
        self.palette_open = False
        self.palette_input.text = ""
        self._focus(self.input)
        self.app.invalidate()

    def toggle_palette(self) -> None:
        if self.palette_open:
            self.close_palette()
        else:
            self.open_palette()

    def move_palette(self, delta: int) -> None:
        matches = self._palette_matches()
        if not matches:
            return
        self.palette_index = (self.palette_index + delta) % len(matches)
        self.app.invalidate()

    def run_palette(self) -> None:
        matches = self._palette_matches()
        entry = matches[self.palette_index] if matches else None
        if entry is None:
            self.close_palette()
            return
        if entry.action is not None:
            entry.action()
            return
        self.close_palette()
        if entry.command.endswith(" "):
            self.input.text = entry.command
            self._focus(self.input)
        else:
            self.queue.put(entry.command)

    def _choose_model(self, provider: str, model: str) -> None:
        cfg = self.state.cfg
        previous = (cfg.provider, cfg.model)
        cfg.provider = provider
        cfg.model = model
        if chat._rebuild(self.state, self.emitter):
            save(cfg)
            self.close_palette()
            self.emitter.ok(f"model -> {provider} · {model}")
            return
        cfg.provider, cfg.model = previous
        self.app.invalidate()

    def _choose_provider(self, provider: str) -> None:
        cfg = self.state.cfg
        previous = (cfg.provider, cfg.model)
        cfg.provider = provider
        cfg.model = default_model(provider)
        if chat._rebuild(self.state, self.emitter):
            save(cfg)
            self.emitter.ok(f"provider -> {provider} · {cfg.model}")
            self.open_model_picker(provider)
            return
        cfg.provider, cfg.model = previous
        self.app.invalidate()

    def _choose_session(self, session_id: str) -> None:
        if self.busy:
            self.emitter.error("stop the current turn before switching sessions")
            return
        if session_id == self.state.session.id:
            self.close_palette()
            return
        try:
            session = Session.load(session_id)
        except FileNotFoundError:
            self.emitter.error(f"session not found: {session_id}")
            return
        if self.state.session.messages:
            try:
                self.state.session.save()
            except Exception:
                pass
        self.state.session = session
        self.state.tool_log.clear()
        self._follow = True
        self.close_palette()
        self._add_text_ui(
            f"\n  + session {session.id} · {len(session.messages)} messages\n",
            "class:tui.ok",
        )

    def _palette_accept(self, _buffer) -> bool:
        self.run_palette()
        return True

    def _new_session(self) -> None:
        if self.busy:
            self._add_text_ui("\n  ! stop the current turn first\n", "class:tui.err")
            return
        self.close_palette()
        if self.state.session.messages:
            try:
                self.state.session.save()
            except Exception:
                pass
        self.state.session = Session(model=self.state.cfg.model)
        self.state.tool_log.clear()
        self._add_text_ui(f"\n  + new session {self.state.session.id}\n", "class:tui.ok")

    # -- dashboard -----------------------------------------------------
    def _dashboard_edge(self, left: str, right: str) -> list[tuple[str, str]]:
        inner = max(10, self.dashboard_width - 2)
        return [("class:palette.border", left + "─" * inner + right)]

    def _dashboard_rows(self) -> list[tuple[str, str]]:
        width = max(20, self.dashboard_width - 4)
        lines = self.dashboard.lines(width=width, max_tasks=max(4, self.dashboard_height - 12))
        fragments: list[tuple[str, str]] = []
        for index, line in enumerate(lines):
            if index:
                fragments.append(("class:palette", "\n"))
            header = bool(line) and not line.startswith(" ") and line.upper() == line
            style = "class:palette.section" if header else "class:palette.row"
            fragments.append((style, line.ljust(width)))
        return fragments

    def _size_dashboard(self) -> None:
        size = self.app.output.get_size()
        self.dashboard_width = max(56, min(110, size.columns - 10))
        self.dashboard_height = max(16, min(34, size.rows - 4))
        self.dashboard_panel.width = Dimension(preferred=self.dashboard_width)
        self.dashboard_panel.height = Dimension(preferred=self.dashboard_height)
        self.dashboard_list.height = Dimension(preferred=self.dashboard_height - 2)

    def toggle_dashboard(self) -> None:
        if self.dashboard_open:
            self.close_dashboard()
            return
        self.close_palette()
        self._size_dashboard()
        self.dashboard_open = True
        self.refresh_dashboard(reset=True)
        self._focus(self.output)
        self.app.invalidate()

    def close_dashboard(self) -> None:
        if not self.dashboard_open:
            return
        self.dashboard_open = False
        self._focus(self.input)
        self.app.invalidate()

    def refresh_dashboard(self, reset: bool = False) -> None:
        """Tail the latest run's real events file; never invent progress."""
        from .orchestrator import list_boards, tasks_dir

        self._last_dashboard = time.time()
        rows = list_boards()
        if not rows:
            if reset:
                self.dashboard = DashboardState()
                self.dashboard_run_id = ""
                self.dashboard_offset = 0
            self.app.invalidate()
            return
        run_id = str(rows[0]["run_id"])
        if reset or run_id != self.dashboard_run_id:
            self.dashboard = DashboardState()
            self.dashboard_run_id = run_id
            self.dashboard_offset = 0
        path = tasks_dir() / f"{run_id}.events.jsonl"
        try:
            size = path.stat().st_size
            if size < self.dashboard_offset:
                self.dashboard = DashboardState()
                self.dashboard_offset = 0
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self.dashboard_offset)
                for line in handle:
                    try:
                        self.dashboard.apply(json.loads(line))
                    except ValueError:
                        continue
                self.dashboard_offset = handle.tell()
        except OSError:
            pass
        self.app.invalidate()

    def _display_lines(self) -> list[list[tuple[str, str]]]:
        pending = [line for line in self.pending.split("\n") if line.strip()]
        extra: list[list[tuple[str, str]]] = []
        if pending:
            if len(pending) > PREVIEW_LINES:
                extra.append([("class:tui.meta", "  ...")])
                pending = pending[-PREVIEW_LINES:]
            extra.extend([[("class:tui.stream", line)] for line in pending])
        return self.lines + extra

    def _body(self) -> str:
        return "\n".join("".join(text for _style, text in line) for line in self._display_lines())

    def _refresh(self) -> None:
        body = self._body()
        if self._follow:
            self.buffer.set_document(Document(body, len(body)), bypass_readonly=True)
        else:
            # Keep the reader's spot: new output must not yank the view down.
            row = self.buffer.document.cursor_position_row
            text = Document(body)
            row = max(0, min(row, text.line_count - 1))
            position = text.translate_row_col_to_index(row, 0)
            self.buffer.set_document(Document(body, position), bypass_readonly=True)
        self.app.invalidate()

    # -- streaming -----------------------------------------------------
    def stream_text(self, text: str) -> None:
        with self._stream_lock:
            self._stream_chunks.append(text)

    def end_stream(self) -> None:
        self._call(self._end_stream_ui)

    def _end_stream_ui(self) -> None:
        self._drain_stream(final=True)
        self._refresh()

    def _drain_stream(self, final: bool = False) -> None:
        with self._stream_lock:
            if not self._stream_chunks:
                if not final or not self.pending:
                    return
                data = ""
            else:
                data = "".join(self._stream_chunks)
                self._stream_chunks.clear()
        self.pending += redact(data)
        width = max(20, self.app.output.get_size().columns - 2)
        while True:
            chunk, rest = split_complete(self.pending, max_block=800, max_fence=2500)
            if not chunk:
                break
            self._add_frag_lines(render_markdown_lines(chunk, width))
            self.pending = rest
        if final and self.pending.strip():
            self._add_frag_lines(render_markdown_lines(self.pending, width))
            self.pending = ""
        self._trim()

    # -- status --------------------------------------------------------
    def _status(self):
        cfg = self.state.cfg
        model = cfg.model or default_model(cfg.provider)
        parts = [("class:status", f" {cfg.provider} · {model} · tools {'on' if cfg.tools else 'off'}")]
        if self.state.plan_mode:
            parts.append(("class:status", " · plan"))
        if self.state.auto_yes:
            parts.append(("class:status", " · yolo"))
        if self.pending_question:
            parts.append(("class:status.ask", f"   {self.pending_question} [y/N]"))
        elif self.busy:
            spinner = "|/-\\"[self.frame % 4]
            parts.append(("class:status.busy", f"   {spinner} working"))
            parts.append(("class:status.hint", " · Ctrl+C stop"))
        else:
            parts.append(("class:status.hint", "   Enter send · Ctrl+P commands · Ctrl+D dashboard · PgUp scroll"))
        return parts

    # -- scrolling -----------------------------------------------------
    def scroll(self, pages: int) -> None:
        self.app.layout.focus(self.output)
        document = self.buffer.document
        rows = self.app.output.get_size().rows
        step = max(3, rows - 4) * pages
        row = document.cursor_position_row + step
        # Negative rows mean "go to the top", huge rows mean "go to the bottom".
        if step > 0 and row >= document.line_count:
            row = document.line_count - 1
        if step < 0 and row <= 0:
            row = 0
        row = max(0, min(document.line_count - 1, row))
        self._follow = row >= document.line_count - 1
        self.buffer.cursor_position = document.translate_row_col_to_index(row, 0)
        self.app.invalidate()

    # -- approvals -----------------------------------------------------
    def confirm(self, question: str) -> bool:
        self._follow = True
        self.pending_question = question
        self._answer = False
        self._answer_event.clear()
        self._call(self._add_text_ui, f"  {question} [y/N] ", "class:tui.tool")
        self._call(self._focus, self.input)
        self._answer_event.wait()
        self.pending_question = None
        self._call(self._focus, self.output)
        return self._answer

    def answer(self, text: str) -> None:
        self._answer = text.strip().lower() in {"y", "yes", "ya"}
        self._call(self._add_text_ui, text + "\n", "class:tui.meta")
        self._answer_event.set()

    # -- input ---------------------------------------------------------
    def submit(self) -> None:
        text = self.input.text.strip()
        self.input.text = ""
        if not text:
            return
        if self.pending_question is not None:
            self.answer(text)
            return
        self._follow = True
        self.queue.put(text)

    def _worker(self) -> None:
        while True:
            text = self.queue.get()
            if text is None:
                return
            self.busy = True
            self.frame = 0
            self.state.cancel.clear()
            self._call(self._focus, self.output)
            try:
                if text.startswith("/"):
                    if not chat.handle_command(text, self.state, self.emitter, self.root):
                        self.closing = True
                        self._call(self._exit)
                        return
                elif text.startswith("!"):
                    self._add_text_ui_now("\n  :: shell\n", "class:tui.tool")
                    chat.run_shell_direct(text[1:].strip(), self.state, self.emitter)
                else:
                    chat.run_turn(self.state, text, self.emitter)
            except Exception as exc:  # keep the UI alive whatever happens
                self.emitter.error(f"failed: {exc}")
            finally:
                self.busy = False
                self._call(self._focus, self.input)
                self._call(self.app.invalidate)

    def _add_text_ui_now(self, text: str, style: str) -> None:
        self._call(self._add_text_ui, text, style)

    # -- plumbing ------------------------------------------------------
    def _call(self, fn, *args) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(fn, *args)

    def _focus(self, window) -> None:
        try:
            self.app.layout.focus(window)
        except Exception:
            pass

    def _exit(self) -> None:
        try:
            self.app.exit()
        except Exception:
            pass

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.12)
            if self.dashboard_open and time.time() - self._last_dashboard > 0.4:
                self.refresh_dashboard()
            if self.busy:
                self.frame += 1
                self.app.invalidate()

    async def _flusher(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            if self._stream_chunks:
                self._drain_stream()
                self._refresh()

    def _banner(self) -> list[list[tuple[str, str]]]:
        cfg = self.state.cfg
        model = cfg.model or default_model(cfg.provider)
        flags = ""
        if self.state.plan_mode:
            flags += " · plan"
        if self.state.auto_yes:
            flags += " · yolo"
        info = [
            f"ThirtyCLI {__version__}",
            f"{cfg.provider} · {model} · tools {'on' if cfg.tools else 'off'}{flags}",
            f"session {self.state.session.id} · {len(self.state.session.messages)} messages",
            "/help · @file · !cmd",
            "run a team of agents, stay in the terminal",
            "",
        ]
        width = self.app.output.get_size().columns
        text_width = max(len(text) for text in info)
        inner = 2 + len(MARK_ROWS[0]) + 3 + text_width + 2
        if width < inner + 4:
            return self._plain_banner(info)

        boxed: list[list[tuple[str, str]]] = [
            [("class:tui.frame", "╭" + "─" * inner + "╮")],
            [("class:tui.frame", "│" + " " * inner + "│")],
        ]
        for row, text in zip(MARK_ROWS, info, strict=True):
            pad = inner - (2 + len(row) + 3 + len(text) + 2)
            boxed.append(
                [
                    ("class:tui.frame", "│"),
                    ("class:tui.meta", "  "),
                    ("class:tui.mark", row),
                    ("class:tui.meta", "   " + text + " " * pad + "  "),
                    ("class:tui.frame", "│"),
                ]
            )
        boxed.append([("class:tui.frame", "│" + " " * inner + "│")])
        boxed.append([("class:tui.frame", "╰" + "─" * inner + "╯")])
        boxed.append([])
        return boxed

    def _plain_banner(self, info: list[str]) -> list[list[tuple[str, str]]]:
        lines = []
        for row, text in zip(MARK_ROWS, info, strict=True):
            frags: list[tuple[str, str]] = [("class:tui.mark", f"  {row}   ")]
            if text:
                frags.append(("class:tui.meta", text))
            lines.append(frags)
        lines.append([])
        return lines

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        ticker = asyncio.create_task(self._ticker())
        flusher = asyncio.create_task(self._flusher())
        self._add_frag_lines(self._banner())
        try:
            await self.app.run_async()
        except EOFError:
            pass
        finally:
            self.closing = True
            ticker.cancel()
            flusher.cancel()
            self._drain_stream(final=True)
            self.queue.put(None)
            if self.state.session.messages:
                self.state.session.save()

    def run_app(self) -> None:
        asyncio.run(self._main())


def run_tui(cfg, state: chat.ChatState, root: Path) -> None:
    Tui(cfg, state, root).run_app()
