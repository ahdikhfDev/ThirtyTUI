"""Interactive input: command palette, @file completion, history, multiline."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .config import home
from .theme import DIM, INK, MUTED, ORANGE

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
MAX_FILES = 2000
MAX_SUGGESTIONS = 40


def slash_suggestions(fragment: str, commands: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Commands to show for the text before the cursor (only while typing the name)."""
    if not fragment.startswith("/") or " " in fragment:
        return []
    return [(name, desc) for name, desc in commands if name.startswith(fragment)]


def file_suggestions(fragment: str, files: list[str], limit: int = MAX_SUGGESTIONS) -> list[str]:
    if not fragment:
        return files[:limit]
    lower = fragment.lower()
    hits = [path for path in files if lower in path.lower()]
    # Prefer matches on the file name itself.
    hits.sort(key=lambda path: (not path.rsplit("/", 1)[-1].lower().startswith(lower), len(path)))
    return hits[:limit]


def _scan_files(root: Path) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            try:
                found.append(str(path.relative_to(root)).replace("\\", "/"))
            except ValueError:
                continue
            if len(found) >= MAX_FILES:
                return found
    return found


def make_completer(files: list[str] | None = None):
    """The palette used by the prompt: `/commands` with descriptions, `@files`."""
    try:
        from prompt_toolkit.completion import Completer, Completion
    except ImportError:  # pragma: no cover - optional dependency
        return None

    from .ui import COMMANDS

    class ThirtyCompleter(Completer):
        def __init__(self) -> None:
            self.files = files or []

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor

            for name, desc in slash_suggestions(text, COMMANDS):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=name,
                    display_meta=desc,
                )

            match = re.search(r"@([\w./-]*)$", text)
            if match:
                fragment = match.group(1)
                for path in file_suggestions(fragment, self.files):
                    yield Completion(
                        path,
                        start_position=-len(fragment),
                        display=path,
                        display_meta="file",
                    )

    return ThirtyCompleter()


def make_reader(hint: str = "", force: bool = False, input=None, output=None):
    """Returns a zero-arg callable that reads one message, or None when not a TTY."""
    if not force and not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:  # pragma: no cover - optional dependency
        return None

    bindings = KeyBindings()

    @bindings.add("enter")
    def _enter(event) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        if state and state.completions:
            completion = state.current_completion or state.completions[0]
            if completion.text == buffer.text:
                buffer.validate_and_handle()
                return
            buffer.apply_completion(completion)
            return
        buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add("c-o")
    def _expand_last_tool(event) -> None:
        buffer = event.current_buffer
        buffer.text = "/expand"
        buffer.validate_and_handle()

    style = Style.from_dict(
        {
            "prompt": f"bold {ORANGE}",
            "completion-menu.completion": f"bg:#212121 {MUTED}",
            "completion-menu.completion.current": f"bg:{ORANGE} {INK} bold",
            "completion-menu.meta.completion": f"bg:#212121 {DIM}",
            "completion-menu.meta.completion.current": f"bg:#D89A35 {INK}",
            "completion-menu.scrollbar.background": "bg:#2A2A2A",
            "completion-menu.scrollbar.button": f"bg:{ORANGE}",
            "bottom-toolbar": f"bg:#1A1A1A {DIM}",
        }
    )

    history_path = home() / "history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        session = PromptSession(
            history=FileHistory(str(history_path)),
            completer=make_completer(_scan_files(Path.cwd())),
            complete_while_typing=True,
            key_bindings=bindings,
            style=style,
            multiline=True,
            prompt_continuation="   ",
            reserve_space_for_menu=10,
            bottom_toolbar=(lambda: f"  {hint}  ·  Tab pick · Alt+Enter new line") if hint else None,
            input=input,
            output=output,
        )
    except Exception:  # no usable console: fall back to plain input
        return None

    def read() -> str:
        return session.prompt([("class:prompt", ">_ ")])

    return read
