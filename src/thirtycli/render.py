"""Incremental markdown rendering.

Blocks are committed to the scrollback as soon as they are complete; only the
block still being written is previewed live. That keeps every redraw small, so
long code blocks no longer stall, and nothing already printed is lost.
"""

from __future__ import annotations

import os
import time

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.text import Text

from .secrets import redact

CODE_THEME = "monokai"
ASSISTANT_CODE_THEME = CODE_THEME
MIN_INTERVAL = 0.08
REFRESH = 8
MAX_BLOCK = 3_000
MAX_FENCE_BLOCK = 6_000


def wants_markdown(con: Console) -> bool:
    if os.environ.get("THIRTYCLI_PLAIN") or os.environ.get("NO_COLOR"):
        return False
    return bool(con.is_terminal and con.is_interactive)


def split_complete(
    text: str, max_block: int = MAX_BLOCK, max_fence: int = MAX_FENCE_BLOCK
) -> tuple[str, str]:
    """Split off a finished block: ends at a blank line or a closed code fence.

    Returns (complete_prefix, remainder). If nothing is finished yet the prefix
    is empty. Very long unfinished blocks are cut (fences are stitched) so the
    live preview never grows without bound.
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return "", text

    in_fence = False
    fence = "```"
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence = marker
            elif stripped.startswith(fence):
                consumed = index + 1
                return "".join(lines[:consumed]), "".join(lines[consumed:])
            continue
        if not in_fence and stripped == "" and index > 0:
            consumed = index + 1
            return "".join(lines[:consumed]), "".join(lines[consumed:])

    limit = max_fence if in_fence else max_block
    if len(text) >= limit:
        if in_fence:
            return text + f"\n{fence}\n", f"{fence}\n"
        return text, ""
    return "", text


class AssistantStream:
    """Renders streamed markdown block by block."""

    def __init__(self, con: Console, live_ok: bool | None = None):
        self.con = con
        self.parts: list[str] = []
        self.pending = ""
        self.live: Live | None = None
        self._last = 0.0
        self._flushed = False
        self._printed_raw = False
        self._interactive = wants_markdown(con) if live_ok is None else live_ok

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def feed(self, text: str) -> None:
        if not text:
            return
        self.parts.append(text)

        if not self._interactive:
            self.con.print(redact(text), end="", markup=False, highlight=False, soft_wrap=True)
            return

        self.pending += text
        self._drain()
        self._paint()

    def _drain(self) -> None:
        while True:
            chunk, rest = split_complete(self.pending)
            if not chunk:
                break
            self._flush(chunk)
            self.pending = rest

    def _flush(self, chunk: str) -> None:
        if not chunk.strip():
            return
        self._stop_live()
        self.con.print(Markdown(redact(chunk), code_theme=CODE_THEME))
        self._flushed = True

    def _paint(self, force: bool = False) -> None:
        if not self.pending.strip():
            if not self._flushed:
                self._paint_spinner()
            return

        now = time.time()
        if not force and self.live is not None and now - self._last < MIN_INTERVAL:
            return
        self._last = now

        renderable = Markdown(redact(self.pending), code_theme=CODE_THEME)
        if self.live is None:
            self.live = Live(
                renderable,
                console=self.con,
                refresh_per_second=REFRESH,
                vertical_overflow="visible",
                transient=True,
            )
            self.live.start()
        else:
            self.live.update(renderable)

    def _paint_spinner(self) -> None:
        renderable = Spinner("line", text=Text(" thinking...", style="dim"), style="accent")
        if self.live is None:
            self.live = Live(
                renderable,
                console=self.con,
                refresh_per_second=REFRESH,
                vertical_overflow="visible",
                transient=True,
            )
            self.live.start()
        else:
            self.live.update(renderable)

    def _stop_live(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None

    def close(self) -> None:
        if self._interactive:
            self._drain()
            if self.pending.strip():
                self._flush(self.pending)
                self.pending = ""
        self._stop_live()
        if self.parts or self._interactive:
            self.con.print()
