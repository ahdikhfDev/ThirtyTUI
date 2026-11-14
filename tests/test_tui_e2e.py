"""End-to-end TUI tests driven through a pipe input."""

import asyncio
import time

import pytest
from langchain_core.messages import AIMessage
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tests.test_chat_loop import ScriptedModel
from thirtycli.chat import ChatState
from thirtycli.config import Config
from thirtycli.sessions import Session
from thirtycli.tools import build_tools
from thirtycli.tui import Tui

COMMANDS_TOTAL = 17


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


async def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return False


def build_tui(tmp_path, replies, tools=None):
    cfg = Config(provider="ollama", model="llama3.2")
    state = ChatState(
        cfg=cfg,
        session=Session(),
        model=ScriptedModel(replies=replies),
        tools=tools or [],
        root=tmp_path,
    )
    return cfg, state


def test_tui_banner_and_palette(tmp_path):
    (tmp_path / "demo.txt").write_text("x", encoding="utf-8")
    cfg, state = build_tui(tmp_path, [AIMessage(content="hello from tui")])

    async def scenario():
        with create_pipe_input() as pipe:
            tui = Tui(cfg, state, tmp_path, input=pipe, output=DummyOutput())
            app = asyncio.create_task(tui._main())
            try:
                assert await wait_for(lambda: "ThirtyCLI" in tui.buffer.text)
                assert "▄▄▄" in tui.buffer.text  # the mark is rendered

                pipe.send_text("/")
                assert await wait_for(lambda: tui.input.buffer.complete_state is not None)
                names = [c.text for c in tui.input.buffer.complete_state.completions]
                assert len(names) == COMMANDS_TOTAL
                assert "/help" in names and "/plan" in names

                pipe.send_text("\x15")  # clear the line
                pipe.send_text("@")
                assert await wait_for(
                    lambda: tui.input.buffer.complete_state is not None
                    and any("demo.txt" in c.text for c in tui.input.buffer.complete_state.completions)
                )

                pipe.send_text("\x15")
                pipe.send_text("hi\r")
                assert await wait_for(lambda: "hello from tui" in tui.buffer.text)
                assert ">_ hi" in tui.buffer.text
                assert await wait_for(lambda: not tui.busy)
            finally:
                await wait_for(lambda: not tui.busy)
                pipe.send_text("\x03")
                await asyncio.wait_for(app, 5)

    asyncio.run(scenario())


def test_tui_write_approval(tmp_path):
    target = tmp_path / "made.txt"
    tools = build_tools(tmp_path)
    cfg, state = build_tui(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": str(target), "content": "hi"}, "id": "c1"}
                ],
            ),
            AIMessage(content="written"),
        ],
        tools=tools,
    )

    async def scenario():
        with create_pipe_input() as pipe:
            tui = Tui(cfg, state, tmp_path, input=pipe, output=DummyOutput())
            app = asyncio.create_task(tui._main())
            try:
                assert await wait_for(lambda: "ThirtyCLI" in tui.buffer.text)
                pipe.send_text("write it\r")
                assert await wait_for(lambda: tui.pending_question is not None)

                pipe.send_text("y\r")
                assert await wait_for(lambda: target.exists())
                assert target.read_text(encoding="utf-8") == "hi"
                assert await wait_for(lambda: "written" in tui.buffer.text)
                assert await wait_for(lambda: not tui.busy)
            finally:
                await wait_for(lambda: not tui.busy)
                pipe.send_text("\x03")
                await asyncio.wait_for(app, 5)

    asyncio.run(scenario())


def test_tui_denied_write(tmp_path):
    target = tmp_path / "nope.txt"
    tools = build_tools(tmp_path)
    cfg, state = build_tui(
        tmp_path,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "write_file", "args": {"path": str(target), "content": "hi"}, "id": "c1"}
                ],
            ),
            AIMessage(content="stopped"),
        ],
        tools=tools,
    )

    async def scenario():
        with create_pipe_input() as pipe:
            tui = Tui(cfg, state, tmp_path, input=pipe, output=DummyOutput())
            app = asyncio.create_task(tui._main())
            try:
                assert await wait_for(lambda: "ThirtyCLI" in tui.buffer.text)
                pipe.send_text("write it\r")
                assert await wait_for(lambda: tui.pending_question is not None)
                pipe.send_text("n\r")
                assert await wait_for(lambda: "stopped" in tui.buffer.text)
                assert not target.exists()
                assert await wait_for(lambda: not tui.busy)
            finally:
                await wait_for(lambda: not tui.busy)
                pipe.send_text("\x03")
                await asyncio.wait_for(app, 5)

    asyncio.run(scenario())


def test_tui_dashboard_toggle(tmp_path):
    cfg, state = build_tui(tmp_path, [])

    async def scenario():
        with create_pipe_input() as pipe:
            tui = Tui(cfg, state, tmp_path, input=pipe, output=DummyOutput())
            app = asyncio.create_task(tui._main())
            try:
                assert await wait_for(lambda: "ThirtyCLI" in tui.buffer.text)
                pipe.send_text("\x04")  # ctrl+d
                assert await wait_for(lambda: tui.dashboard_open)
                pipe.send_text("\x04")
                assert await wait_for(lambda: not tui.dashboard_open)
            finally:
                await wait_for(lambda: not tui.busy)
                pipe.send_text("\x03")
                await asyncio.wait_for(app, 5)

    asyncio.run(scenario())


def test_tui_command_palette(tmp_path):
    cfg, state = build_tui(tmp_path, [AIMessage(content="unused")])

    async def scenario():
        with create_pipe_input() as pipe:
            tui = Tui(cfg, state, tmp_path, input=pipe, output=DummyOutput())
            app = asyncio.create_task(tui._main())
            try:
                assert await wait_for(lambda: "ThirtyCLI" in tui.buffer.text)

                pipe.send_text("\x10")  # ctrl+p
                assert await wait_for(lambda: tui.palette_open)

                pipe.send_text("model")
                assert await wait_for(lambda: len(tui._palette_matches()) == 1)
                assert tui._palette_matches()[0].label == "Switch model"

                pipe.send_text("\r")
                assert await wait_for(lambda: tui.picker_mode == "models" and tui.palette_open)

                pipe.send_text("llama3.2")
                assert await wait_for(
                    lambda: [entry.label for entry in tui._palette_matches()] == ["llama3.2"]
                )
                pipe.send_text("\r")
                assert await wait_for(lambda: not tui.palette_open)
                assert tui.state.cfg.model == "llama3.2"
                assert tui.state.cfg.provider == "ollama"

                pipe.send_text("\x10")
                assert await wait_for(lambda: tui.palette_open)
                pipe.send_text("\x1b")  # esc closes without running
                assert await wait_for(lambda: not tui.palette_open)

                pipe.send_text("\x15")
            finally:
                await wait_for(lambda: not tui.busy)
                pipe.send_text("\x03")
                await asyncio.wait_for(app, 5)

    asyncio.run(scenario())
