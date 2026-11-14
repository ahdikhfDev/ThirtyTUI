"""TUI rendering: styled lines, markdown, approvals, status."""

import threading
import time

import pytest
from langchain_core.messages import AIMessage
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from tests.test_chat_loop import ScriptedModel
from thirtycli.chat import ChatState, handle_command, run_turn
from thirtycli.config import Config
from thirtycli.sessions import Session
from thirtycli.tools import build_tools
from thirtycli.tui import PREVIEW_LINES, Tui, TuiEmitter


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_tui(tmp_path):
    state = ChatState(cfg=Config(), session=Session(), model=ScriptedModel(), tools=[], root=tmp_path)
    tui = Tui(Config(), state, tmp_path, input=DummyInput(), output=DummyOutput())
    tui._call = lambda fn, *args: fn(*args)  # run inline instead of via the loop
    return tui


def body(tui: Tui) -> str:
    return tui._body()


def test_emitter_formats_tool_lines(tmp_path):
    tui = make_tui(tmp_path)
    emit = TuiEmitter(tui)
    emit.user_message("hi")
    emit.tool_call("read_file", {"path": "a.py"})
    emit.tool_summary("read_file", "one\ntwo", 0.1)
    emit.footer(1.0, {"total": 42}, "gpt-4o-mini", cost=0.001)
    text = body(tui)
    assert ">_ hi" in text
    assert ":: read_file(path='a.py')" in text
    assert "2 lines" in text
    assert "42 tok" in text


def test_emitter_shows_errors_inline(tmp_path):
    tui = make_tui(tmp_path)
    emit = TuiEmitter(tui)
    emit.tool_summary("web_fetch", "HTTP 403 for https://x.dev/", 0.2)
    assert "HTTP 403" in body(tui)


def test_stream_renders_markdown(tmp_path):
    tui = make_tui(tmp_path)
    tui.state.model = ScriptedModel(replies=[AIMessage(content="**halo** dan `kode`")])
    run_turn(tui.state, "hi", TuiEmitter(tui))
    text = body(tui)
    assert ">_ hi" in text
    assert "halo" in text and "kode" in text
    assert "**" not in text  # rendered, not syntax-highlighted source


def test_tool_turn_appends_log(tmp_path):
    tui = make_tui(tmp_path)
    tui.state.tools = build_tools(tmp_path)
    tui.state.model = ScriptedModel(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "now", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
    )
    run_turn(tui.state, "time?", TuiEmitter(tui))
    text = body(tui)
    assert ":: now(" in text
    assert "done" in text
    assert tui.state.tool_log


def test_slash_command_through_emitter(tmp_path):
    tui = make_tui(tmp_path)
    assert handle_command("/tools on", tui.state, TuiEmitter(tui), tmp_path) is True
    assert "tools on" in body(tui)


def test_add_text_writes_into_readonly_buffer(tmp_path):
    tui = make_tui(tmp_path)
    tui._add_text_ui("hello\nworld", "class:tui.meta")
    assert tui.buffer.text == "hello\nworld"
    assert tui.buffer.cursor_position == len("hello\nworld")


def test_banner_has_mark_and_version(tmp_path):
    tui = make_tui(tmp_path)
    text = "".join(part for line in tui._banner() for _style, part in line)
    assert "ThirtyCLI" in text
    assert "▄▄▄" in text
    assert "gpt-4o-mini" in text


def test_dashboard_reads_the_latest_real_events(tmp_path):
    import json

    from thirtycli.orchestrator import Task, TaskBoard, TaskStatus, tasks_dir

    board = TaskBoard(goal="fix the login redirect")
    board.add(Task("t1", "fix the login redirect", agent="coder"))
    board.tasks[0].status = TaskStatus.COMPLETED
    board.save()
    events = [
        {"event": "plan", "ts": 1.0, "run_id": board.run_id, "goal": board.goal, "tasks": ["t1 · coder · fix the login redirect"]},
        {"event": "task.start", "ts": 2.0, "run_id": board.run_id, "task": "t1", "agent": "coder", "attempt": 1},
        {"event": "task.verify", "ts": 3.0, "run_id": board.run_id, "task": "t1", "verification": "verified", "reason": "files changed"},
        {"event": "task.complete", "ts": 4.0, "run_id": board.run_id, "task": "t1"},
    ]
    (tasks_dir() / f"{board.run_id}.events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    tui = make_tui(tmp_path)
    tui.toggle_dashboard()

    assert tui.dashboard_open
    assert tui.dashboard.run_id == board.run_id
    assert tui.dashboard.tasks["t1"]["status"] == "completed"
    assert tui.dashboard.tasks["t1"]["verification"] == "verified"
    text = "".join(part for _style, part in tui._dashboard_rows())
    assert "EXECUTION" in text and "TASKS" in text and "fix the login redirect" in text
    tui.close_dashboard()
    assert not tui.dashboard_open


def test_banner_is_drawn_inside_a_box(tmp_path):
    tui = make_tui(tmp_path)
    lines = tui._banner()
    top = "".join(part for _style, part in lines[0])
    bottom = "".join(part for _style, part in lines[-2])
    text = "".join(part for line in lines for _style, part in line)
    assert top.startswith("╭") and top.endswith("╮")
    assert bottom.startswith("╰") and bottom.endswith("╯")
    assert "session " in text and "messages" in text


def test_pending_preview_is_capped(tmp_path):
    tui = make_tui(tmp_path)
    tui.pending = "\n".join(f"line {i}" for i in range(50))
    lines = tui._display_lines()
    assert len(lines) <= PREVIEW_LINES + 1
    assert "line 49" in "".join(text for _style, text in lines[-1])
    assert lines[0][0][0] == "class:tui.meta"


def test_stream_does_not_duplicate(tmp_path):
    tui = make_tui(tmp_path)
    emit = TuiEmitter(tui)
    emit.assistant_delta("alpha\n\nbeta")
    emit.assistant_end()
    text = body(tui)
    assert text.count("alpha") == 1
    assert text.count("beta") == 1


def test_stream_preview_is_not_committed(tmp_path):
    tui = make_tui(tmp_path)
    tui.stream_text("H")
    tui._drain_stream()
    tui.stream_text("ai! Ada yang bisa saya bantu?\n")
    tui._drain_stream()
    tui.end_stream()
    assert body(tui).count("Hai!") == 1


def test_completion_menu_is_attached(tmp_path):
    from prompt_toolkit.layout import FloatContainer
    from prompt_toolkit.layout.menus import CompletionsMenu

    tui = make_tui(tmp_path)
    assert isinstance(tui.layout.container, FloatContainer)
    assert any(isinstance(f.content, CompletionsMenu) for f in tui.layout.container.floats)


def test_input_height_grows_with_lines(tmp_path):
    tui = make_tui(tmp_path)
    tui.input.buffer.text = "a\nb\nc"
    assert tui.input.window.height.preferred == 3


def test_input_box_never_grows_past_six_lines(tmp_path):
    tui = make_tui(tmp_path)
    tui.input.buffer.text = "\n".join(f"line {index}" for index in range(12))
    assert tui.input.window.height.preferred == 6


def test_input_box_stays_at_least_two_lines(tmp_path):
    tui = make_tui(tmp_path)
    tui.input.buffer.text = ""
    tui._resize_input(tui.input.buffer)
    assert tui.input.window.height.min == 2
    assert tui.input.window.height.preferred == 2
    assert tui.input.window.dont_extend_height() is True


def test_divider_sits_between_output_and_input(tmp_path):
    from prompt_toolkit.layout import Window

    tui = make_tui(tmp_path)
    assert isinstance(tui.divider, Window)
    assert tui.divider.height == 1
    assert tui.divider in tui.layout.container.content.children


def test_tool_summary_redacts_api_keys(tmp_path):
    tui = make_tui(tmp_path)
    entry = {"name": "read_file", "seconds": 0.1, "result": "OPENAI_API_KEY=sk-abcdef1234567890"}
    TuiEmitter(tui).expand_tool(entry)
    text = body(tui)
    assert "sk-abcdef1234567890" not in text
    assert "sk-****7890" in text


def test_plain_lines_redact_api_keys(tmp_path):
    tui = make_tui(tmp_path)
    tui._add_text_ui("using sk-abcdef1234567890", "class:tui.meta")
    assert "sk-abcdef1234567890" not in body(tui)
    assert "sk-****7890" in body(tui)


def press_ctrl_c(tui: Tui):
    """Run the real Ctrl+C binding against a fake Application."""
    from prompt_toolkit.keys import Keys

    binding = next(b for b in tui.app.key_bindings.bindings if b.keys == (Keys.ControlC,))

    class FakeApplication:
        exited = False

        def exit(self) -> None:
            self.exited = True

    class FakeEvent:
        app = FakeApplication()

    binding.handler(FakeEvent())
    return FakeEvent.app


def test_ctrl_c_cancels_generation_when_busy(tmp_path):
    tui = make_tui(tmp_path)
    tui.busy = True
    app = press_ctrl_c(tui)
    assert tui.state.cancel.is_set()
    assert not app.exited
    assert "stopping" in body(tui)


def test_ctrl_c_denies_a_pending_approval(tmp_path):
    tui = make_tui(tmp_path)
    tui.busy = True
    tui.pending_question = "write file?"
    press_ctrl_c(tui)
    assert tui._answer_event.is_set()
    assert tui._answer is False


def test_ctrl_c_exits_when_idle(tmp_path):
    tui = make_tui(tmp_path)
    tui.busy = False
    app = press_ctrl_c(tui)
    assert app.exited


def palette_binding(tui: Tui, *keys):
    from prompt_toolkit.keys import Keys

    def key(value):
        try:
            return Keys(value)
        except ValueError:
            return value

    wanted = tuple(key(value) for value in keys)
    return next(binding for binding in tui.app.key_bindings.bindings if binding.keys == wanted)


def test_ctrl_p_opens_and_closes_the_palette(tmp_path):
    tui = make_tui(tmp_path)
    binding = palette_binding(tui, "c-p")
    binding.handler(object())
    assert tui.palette_open
    binding.handler(object())
    assert not tui.palette_open


def test_palette_renders_sections_and_shortcuts(tmp_path):
    tui = make_tui(tmp_path)
    text = "".join(part for _style, part in tui._palette_rows())
    assert "Suggested" in text and "Session" in text and "Commands" in text
    assert "Switch model" in text and "ctrl+x m" in text
    assert any(style == "class:palette.selected" for style, _part in tui._palette_rows())


def test_palette_search_filters_and_resets_selection(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    tui.palette_index = 4
    tui.palette_input.text = "model"
    assert [entry.label for entry in tui._palette_matches()] == ["Switch model"]
    assert tui.palette_index == 0

    tui.palette_input.text = "zzzz"
    assert tui._palette_matches() == []
    text = "".join(part for _style, part in tui._palette_rows())
    assert "no matching command" in text


def test_palette_navigation_wraps(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    total = len(tui._palette_matches())
    tui.move_palette(-1)
    assert tui.palette_index == total - 1
    tui.move_palette(1)
    assert tui.palette_index == 0


def test_switch_model_opens_the_model_picker(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    tui.run_palette()  # first entry: Switch model
    assert tui.palette_open
    assert tui.picker_mode == "models"
    assert tui.picker_title == "Select model"
    assert tui._palette_matches()


def test_model_picker_selects_a_local_model(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_model_picker("ollama")
    tui.palette_input.text = "llama3.2"
    assert [entry.label for entry in tui._palette_matches()] == ["llama3.2"]

    tui.run_palette()

    assert not tui.palette_open
    assert tui.state.cfg.provider == "ollama"
    assert tui.state.cfg.model == "llama3.2"


def test_provider_picker_then_opens_the_model_picker(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_provider_picker()
    tui.palette_input.text = "ollama"
    tui.run_palette()

    assert tui.palette_open
    assert tui.picker_mode == "models"
    assert tui.picker_provider == "ollama"
    assert tui.state.cfg.provider == "ollama"
    assert tui.state.cfg.model == "llama3.2"


def test_palette_enter_queues_plain_commands(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    tui.palette_input.text = "help"
    tui.run_palette()
    assert tui.queue.get_nowait() == "/help"


def test_palette_new_session_swaps_the_session(tmp_path):
    tui = make_tui(tmp_path)
    before = tui.state.session.id
    tui.open_palette()
    tui.palette_index = [entry.label for entry in tui._palette_entries()].index("New session")
    tui.run_palette()
    assert tui.state.session.id != before
    assert "new session" in body(tui)


def test_session_picker_switches_sessions(tmp_path):
    from thirtycli.sessions import Session

    other = Session(model="fake")
    other.add("user", "from another session")
    other.save()

    tui = make_tui(tmp_path)
    tui.open_session_picker()
    tui.palette_input.text = other.id
    assert [entry.label for entry in tui._palette_matches()] == ["from another session"]

    tui.run_palette()

    assert not tui.palette_open
    assert tui.state.session.id == other.id


def test_session_picker_shows_chat_titles(tmp_path):
    from thirtycli.sessions import Session

    other = Session(model="fake")
    other.add("user", "Fix the login redirect loop")
    other.save()

    tui = make_tui(tmp_path)
    tui.open_session_picker()
    tui.palette_input.text = "login redirect"

    assert [entry.label for entry in tui._palette_matches()] == ["Fix the login redirect loop"]


def test_palette_quick_chords(tmp_path):
    tui = make_tui(tmp_path)
    palette_binding(tui, "c-x", "m").handler(object())
    assert tui.palette_open and tui.picker_mode == "models"
    tui.close_palette()
    palette_binding(tui, "c-x", "p").handler(object())
    assert tui.palette_open and tui.picker_mode == "providers"
    tui.close_palette()
    palette_binding(tui, "c-x", "l").handler(object())
    assert tui.palette_open and tui.picker_mode == "sessions"


def test_palette_is_a_centered_overlay_that_scales_with_the_terminal(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    size = tui.app.output.get_size()
    expected_width = max(52, min(64, int(size.columns * 0.60)))
    expected_height = max(14, min(24, int(size.rows * 0.66)))
    assert tui.palette_width == expected_width
    assert tui.palette_list_rows == expected_height - 6
    assert tui.palette_panel.width.preferred == expected_width
    assert tui.palette_panel.height.preferred == expected_height
    assert tui.palette_body.width.preferred == expected_width - 2
    assert tui.palette_float.z_index == 10


def test_palette_has_a_closed_border(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    top = "".join(text for _style, text in tui._palette_edge("┌", "┐"))
    bottom = "".join(text for _style, text in tui._palette_edge("└", "┘"))
    assert len(top) == tui.palette_width
    assert top.startswith("┌") and top.endswith("┐")
    assert bottom.startswith("└") and bottom.endswith("┘")
    assert set(top) <= {"┌", "─", "┐"}


def test_palette_rows_keep_clear_of_the_right_border(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    inner = tui._palette_inner_width()
    rows = [
        (style, text)
        for style, text in tui._palette_rows()
        if style in {"class:palette.row", "class:palette.selected"}
    ]
    assert rows
    for _style, text in rows:
        assert len(text) == inner
        assert text.endswith("  ")


def test_palette_window_keeps_selection_visible_on_short_terminals(tmp_path):
    tui = make_tui(tmp_path)
    tui.open_palette()
    tui.palette_list_rows = 5
    tui.palette_index = len(tui._palette_matches()) - 1
    rows = "".join(text for _style, text in tui._palette_rows()).split("\n")
    assert len(rows) <= 5
    assert "Expand last tool output" in rows[-1]


def test_refresh_keeps_reading_position_when_scrolled_up(tmp_path):
    tui = make_tui(tmp_path)
    tui._add_text_ui("\n".join(f"line {index}" for index in range(40)), "class:tui.meta")
    tui.buffer.cursor_position = tui.buffer.document.translate_row_col_to_index(5, 0)
    tui._follow = False

    tui._add_text_ui("new output", "class:tui.meta")

    assert tui.buffer.document.cursor_position_row == 5
    assert "new output" in tui._body()


def test_refresh_sticks_to_the_bottom_while_following(tmp_path):
    tui = make_tui(tmp_path)
    tui._add_text_ui("one\ntwo", "class:tui.meta")
    assert tui.buffer.document.cursor_position_row == 1

    tui._add_text_ui("three", "class:tui.meta")

    assert tui.buffer.document.cursor_position_row == 2


def test_scroll_up_stops_following_and_end_resumes(tmp_path):
    tui = make_tui(tmp_path)
    tui._add_text_ui("\n".join(f"line {index}" for index in range(60)), "class:tui.meta")

    tui.scroll(-1)
    assert not tui._follow
    row = tui.buffer.document.cursor_position_row

    tui._add_text_ui("more", "class:tui.meta")
    assert tui.buffer.document.cursor_position_row == row

    tui.scroll(9999)
    assert tui._follow
    assert tui.buffer.document.cursor_position_row == tui.buffer.document.line_count - 1


def test_palette_waits_for_a_pending_approval(tmp_path):
    tui = make_tui(tmp_path)
    tui.pending_question = "write file?"
    palette_binding(tui, "c-p").handler(object())
    assert not tui.palette_open


def test_confirm_blocks_until_answered(tmp_path):
    tui = make_tui(tmp_path)
    result: dict = {}

    thread = threading.Thread(target=lambda: result.update(value=tui.confirm("allow?")))
    thread.start()
    for _ in range(100):
        if tui.pending_question:
            break
        time.sleep(0.01)

    assert tui.pending_question == "allow?"
    tui.answer("y")
    thread.join(2)
    assert not thread.is_alive()
    assert result["value"] is True


def test_status_line(tmp_path):
    tui = make_tui(tmp_path)
    tui.busy = True
    text = "".join(fragment[1] for fragment in tui._status())
    assert "openai · gpt-4o-mini" in text
    assert "working" in text

    tui.busy = False
    tui.pending_question = "write file?"
    text = "".join(fragment[1] for fragment in tui._status())
    assert "write file? [y/N]" in text
