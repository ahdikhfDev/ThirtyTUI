"""Output adapters: the agent engine talks to one of these, not to the console."""

from __future__ import annotations

from rich.console import Console

from . import ui
from .render import AssistantStream


class ConsoleEmitter:
    """Plain scrollback output (the default REPL)."""

    def __init__(self, con: Console):
        self.con = con
        self._stream: AssistantStream | None = None

    def user_message(self, text: str) -> None:
        return None  # the prompt already shows it

    def assistant_delta(self, text: str) -> None:
        if self._stream is None:
            self._stream = AssistantStream(self.con)
        self._stream.feed(text)

    def assistant_end(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def tool_call(self, name: str, args: dict) -> None:
        ui.tool_call(name, args, self.con)

    def tool_summary(self, name: str, result: str, seconds: float) -> None:
        ui.tool_result_summary(name, result, seconds, self.con)

    def footer(self, seconds: float, usage: dict, model: str, cost: float | None = None) -> None:
        ui.footer(seconds, usage, model, self.con, cost=cost)

    def info(self, message: str) -> None:
        ui.info(message, self.con)

    def ok(self, message: str) -> None:
        ui.ok(message, self.con)

    def error(self, message: str) -> None:
        ui.error(message, self.con)

    def output(self, text: str) -> None:
        ui.stream_text(text, self.con)

    def confirm(self, question: str) -> bool:
        return ui.approve(question, self.con)

    def diff(self, path: str, old: str, new: str) -> None:
        ui.diff_preview(path, old, new, self.con)

    def expand_tool(self, entry: dict) -> None:
        ui.expand_tool(entry, self.con)

    def help(self) -> None:
        ui.help_panel(self.con)

    def rule(self, label: str = "") -> None:
        ui.rule(label, self.con)

    def status(self, label: str):
        return self.con.status(label, spinner="line", spinner_style="accent")

    def blank(self) -> None:
        self.con.print()


def as_emitter(target) -> ConsoleEmitter:
    if hasattr(target, "assistant_delta"):
        return target
    return ConsoleEmitter(target)
