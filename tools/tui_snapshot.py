#!/usr/bin/env python
"""Render the real ThirtyCLI TUI (prompt_toolkit) to a PNG for visual QA.

The TUI is driven through a pipe input and a size-aware ``DummyOutput``, so the
screen buffer is produced by the actual prompt_toolkit renderer -- no mocks.
After the palette is opened and the screen has been drawn, the ``Screen`` char
grid is painted with Pillow.

Usage:
    .venv/Scripts/python tools/tui_snapshot.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from prompt_toolkit.data_structures import Size  # noqa: E402
from prompt_toolkit.input import create_pipe_input  # noqa: E402
from prompt_toolkit.output import ColorDepth, DummyOutput  # noqa: E402

from tests.test_chat_loop import ScriptedModel  # noqa: E402
from thirtycli.chat import ChatState  # noqa: E402
from thirtycli.config import Config  # noqa: E402
from thirtycli.sessions import Session  # noqa: E402
from thirtycli.tui import Tui, render_markdown_lines  # noqa: E402

ROWS = 36
COLUMNS = 120

DEFAULT_FG = "#E4E4E4"
DEFAULT_BG = "#1A1A1A"

ANSI_COLORS = {
    "ansiblack": "#0D0D0D",
    "ansired": "#FF6B6B",
    "ansigreen": "#61C985",
    "ansiyellow": "#FFB547",
    "ansiblue": "#42A0FF",
    "ansimagenta": "#C792EA",
    "ansicyan": "#42A0FF",
    "ansiwhite": "#E4E4E4",
    "ansigray": "#979797",
    "ansibrightblack": "#6A6A6A",
    "ansibrightred": "#FF8A8A",
    "ansibrightgreen": "#7FE0A0",
    "ansibrightyellow": "#FFC97A",
    "ansibrightblue": "#6FB8FF",
    "ansibrightmagenta": "#D9A6F2",
    "ansibrightcyan": "#7FD6FF",
    "ansibrightwhite": "#FFFFFF",
}

REPLY_MD = (
    "Sure — here is the plan:\n\n"
    "1. Split `parse()` into smaller helpers\n"
    "2. Add regression tests for trailing commas\n"
    "3. Run `pytest -q`\n\n"
    "```python\n"
    "def parse(text: str) -> list[str]:\n"
    "    return text.split()\n"
    "```\n"
)


class SnapshotOutput(DummyOutput):
    """Dummy output that reports a big terminal and true-colour styles."""

    def get_size(self) -> Size:
        return Size(rows=ROWS, columns=COLUMNS)

    def get_rows_below_cursor_position(self) -> int:
        return ROWS

    def get_default_color_depth(self) -> ColorDepth:
        return ColorDepth.TRUE_COLOR


def color_hex(value: str, default: str) -> str:
    if not value:
        return default
    name = value.lower()
    if name in {"ansidefault", "default"}:
        return default
    if name.startswith("ansi"):
        return ANSI_COLORS.get(name, default)
    raw = value.lstrip("#")
    if len(raw) == 3 and all(c in "0123456789abcdefABCDEF" for c in raw):
        raw = "".join(c * 2 for c in raw)
    if len(raw) == 6 and all(c in "0123456789abcdefABCDEF" for c in raw):
        return "#" + raw.upper()
    return default


def resolve_style(style, style_str: str) -> tuple[str, str, bool]:
    attrs = style.get_attrs_for_style_str(style_str or "")
    fg = color_hex(attrs.color, DEFAULT_FG)
    bg = color_hex(attrs.bgcolor, DEFAULT_BG)
    if attrs.reverse:
        fg, bg = bg, fg
    return fg, bg, attrs.bold


def screen_grid(screen) -> list[list]:
    """Materialise the rendered screen as rows of ``Char`` cells."""
    return [[screen.data_buffer[y][x] for x in range(COLUMNS)] for y in range(ROWS)]


def grid_text(grid: list[list]) -> list[str]:
    lines = []
    for row in grid:
        text = "".join(cell.char for cell in row if getattr(cell, "width", 1) != 0)
        lines.append(text.rstrip())
    return lines


def load_font(size: int, bold: bool = False):
    from PIL import ImageFont

    windir = os.environ.get("WINDIR", r"C:\Windows")
    names = ["consolab.ttf", "consola.ttf"] if bold else ["consola.ttf"]
    for name in names:
        for base in (Path(windir) / "Fonts", Path.cwd()):
            candidate = base / name
            if candidate.exists():
                try:
                    return ImageFont.truetype(str(candidate), size)
                except OSError:
                    pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_png(grid: list[list], style, path: Path) -> tuple[int, int]:
    from PIL import Image, ImageDraw

    font = load_font(16)
    bold_font = load_font(16, bold=True) or font
    cell_w = max(1, math.ceil(font.getlength("M")))
    bbox = font.getbbox("Ag")
    cell_h = (bbox[3] - bbox[1]) + 4

    image = Image.new("RGB", (COLUMNS * cell_w, ROWS * cell_h), DEFAULT_BG)
    draw = ImageDraw.Draw(image)
    cache: dict[str, tuple[str, str, bool]] = {}

    for y, row in enumerate(grid):
        py = y * cell_h
        for x, cell in enumerate(row):
            char = cell.char
            if not char or not char.strip():
                if char != " ":
                    continue
                px = x * cell_w
                # Still paint background for spaces that carry it.
                if cell.style not in cache:
                    cache[cell.style] = resolve_style(style, cell.style)
                _fg, bg, _bold = cache[cell.style]
                if bg != DEFAULT_BG:
                    draw.rectangle((px, py, px + cell_w - 1, py + cell_h - 1), fill=bg)
                continue
            if cell.style not in cache:
                cache[cell.style] = resolve_style(style, cell.style)
            fg, bg, is_bold = cache[cell.style]
            px = x * cell_w
            if bg != DEFAULT_BG:
                draw.rectangle((px, py, px + cell_w - 1, py + cell_h - 1), fill=bg)
            draw.text((px, py + 2), char, font=bold_font if is_bold else font, fill=fg)

    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return image.size


def seed_chat(tui: Tui) -> None:
    tui._add_text_ui("  >_ Refactor the parser and add tests", "class:tui.user")
    tui._add_frag_lines(render_markdown_lines(REPLY_MD, COLUMNS - 2))
    tui._add_text_ui("  :: read_file  src/parser.py", "class:tui.tool")
    tui._add_text_ui("  + read 42 lines", "class:tui.meta")
    tui._add_text_ui("  :: run  pytest -q", "class:tui.tool")
    tui._add_text_ui("  14 passed in 0.42s", "class:tui.meta")


async def capture(home: Path, picker: str | None = "commands") -> tuple[list[str], list[list], object]:
    cfg = Config(provider="deepseek", model="deepseek-chat")
    state = ChatState(
        cfg=cfg,
        session=Session(),
        model=ScriptedModel(replies=[]),
        tools=[],
        root=home,
    )

    with create_pipe_input() as pipe:
        tui = Tui(cfg, state, home, input=pipe, output=SnapshotOutput())
        task = asyncio.create_task(tui._main())
        try:
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and "ThirtyCLI" not in tui.buffer.text:
                await asyncio.sleep(0.02)

            seed_chat(tui)
            if picker == "commands":
                tui.open_palette()
            elif picker == "models":
                tui.open_model_picker()
            elif picker == "sessions":
                saved = Session(model="deepseek-chat")
                saved.add("user", "Fix the login redirect loop")
                saved.add("assistant", "Patched the callback and added a test.")
                saved.save()
                tui.open_session_picker()
            elif picker == "dashboard":
                from thirtycli.orchestrator import Task, TaskBoard, TaskStatus, tasks_dir

                board = TaskBoard(goal="Add authentication middleware")
                board.add(Task("t1", "Read the auth module", agent="explorer"))
                board.add(Task("t2", "Implement middleware", agent="backend", depends_on=["t1"]))
                board.add(Task("t3", "Write tests", agent="tester", depends_on=["t2"]))
                board.tasks[0].status = TaskStatus.COMPLETED
                board.tasks[1].status = TaskStatus.RUNNING
                board.tasks[1].attempts = 1
                board.tasks[1].started = time.time() - 8
                board.tasks[0].verification = {
                    "status": "verified",
                    "reason": "read-only task; no file changes expected",
                }
                board.save()
                events = [
                    {
                        "event": "plan",
                        "ts": time.time() - 9,
                        "run_id": board.run_id,
                        "goal": board.goal,
                        "tasks": ["t1 · explorer · Read the auth module", "t2 · backend · Implement middleware", "t3 · tester · Write tests"],
                    },
                    {"event": "task.start", "ts": time.time() - 8, "run_id": board.run_id, "task": "t1", "agent": "explorer", "attempt": 1},
                    {"event": "task.complete", "ts": time.time() - 5, "run_id": board.run_id, "task": "t1"},
                    {"event": "task.start", "ts": time.time() - 4, "run_id": board.run_id, "task": "t2", "agent": "backend", "attempt": 1},
                ]
                (tasks_dir() / f"{board.run_id}.events.jsonl").write_text(
                    "\n".join(json.dumps(event) for event in events) + "\n",
                    encoding="utf-8",
                )
                tui._size_dashboard()
                tui.dashboard_open = True
                tui.refresh_dashboard(reset=True)
            tui.app.invalidate()

            marker = {
                "commands": "Commands",
                "models": "Select model",
                "sessions": "Select session",
                "dashboard": "EXECUTION",
            }.get(picker)
            screen = None
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                screen = tui.app.renderer._last_screen
                if marker is None:
                    await asyncio.sleep(0.3)
                    break
                if screen is not None and marker in "\n".join(
                    "".join(screen.data_buffer[y][x].char for x in range(COLUMNS))
                    for y in range(ROWS)
                ):
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.3)

            screen = tui.app.renderer._last_screen
            if screen is None:
                raise RuntimeError("prompt_toolkit never produced a screen")
            grid = screen_grid(screen)
            style = tui.app.style
        finally:
            tui.close_palette()
            tui.close_dashboard()
            await asyncio.sleep(0.05)
            if not tui.busy:
                pipe.send_text("\x03")
            try:
                await asyncio.wait_for(task, 5)
            except (asyncio.TimeoutError, TimeoutError):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return grid_text(grid), grid, style


def main() -> int:
    import shutil

    home = Path(tempfile.mkdtemp(prefix="thirtycli-snapshot-"))
    os.environ["THIRTYCLI_HOME"] = str(home)
    previous = Path.cwd()
    os.chdir(home)
    try:
        palette_lines, palette_grid, palette_style = asyncio.run(capture(home, "commands"))
        model_lines, model_grid, model_style = asyncio.run(capture(home, "models"))
        session_lines, session_grid, session_style = asyncio.run(capture(home, "sessions"))
        dashboard_lines, dashboard_grid, dashboard_style = asyncio.run(capture(home, "dashboard"))
        chat_lines, chat_grid, chat_style = asyncio.run(capture(home, None))
    finally:
        os.chdir(previous)
        shutil.rmtree(home, ignore_errors=True)

    docs = ROOT / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    for name, lines, grid, style in (
        ("palette", palette_lines, palette_grid, palette_style),
        ("models", model_lines, model_grid, model_style),
        ("sessions", session_lines, session_grid, session_style),
        ("dashboard", dashboard_lines, dashboard_grid, dashboard_style),
        ("tui", chat_lines, chat_grid, chat_style),
    ):
        txt_path = docs / f"{name}.txt"
        txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        png_path = docs / f"{name}.png"
        width_px, height_px = render_png(grid, style, png_path)
        print(f"png:  {png_path} ({width_px}x{height_px} px)")
        print(f"txt:  {txt_path} ({len(lines)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
