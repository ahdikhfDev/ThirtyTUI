from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from thirtycli.context import clamp_tool_output, estimate_tokens, trim_for_model


def _assert_no_orphan_tool_messages(messages: list) -> None:
    pending_tool_calls = False
    for message in messages:
        if isinstance(message, HumanMessage):
            pending_tool_calls = False
        elif isinstance(message, AIMessage) and message.tool_calls:
            pending_tool_calls = True
        elif isinstance(message, ToolMessage):
            assert pending_tool_calls, "ToolMessage without a preceding AIMessage(tool_calls)"


def test_no_trimming_when_under_budget():
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content="hello"),
        AIMessage(content="hi"),
    ]
    kept, was_trimmed = trim_for_model(messages, max_tokens=10_000)
    assert kept == messages
    assert was_trimmed is False


def test_trimming_drops_oldest_block_keeps_system_and_last_human():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="first question" * 50),
        AIMessage(content="first answer" * 50),
        HumanMessage(content="second question"),
        AIMessage(content="second answer"),
    ]
    kept, was_trimmed = trim_for_model(messages, max_tokens=8)
    assert was_trimmed is True
    assert isinstance(kept[0], SystemMessage)
    assert kept[0].content == "sys"
    assert isinstance(kept[1], HumanMessage)
    assert kept[1].content == "second question"
    assert kept[-1].content == "second answer"
    assert all(getattr(message, "content", "") != "first question" * 50 for message in kept)


def test_trimmed_result_never_splits_tool_pair():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="do first task"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "c1"}]),
        ToolMessage(content="first tool result", tool_call_id="c1"),
        AIMessage(content="first done"),
        HumanMessage(content="do second task"),
        AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": "c2"}]),
        ToolMessage(content="second tool result", tool_call_id="c2"),
        AIMessage(content="second done"),
    ]
    kept, was_trimmed = trim_for_model(messages, max_tokens=4)
    assert was_trimmed is True
    _assert_no_orphan_tool_messages(kept)


def test_final_human_and_following_assistant_survive():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="old question" * 50),
        AIMessage(content="old answer" * 50),
        HumanMessage(content="current question"),
        AIMessage(content="current answer"),
    ]
    kept, was_trimmed = trim_for_model(messages, max_tokens=8)
    assert was_trimmed is True
    assert isinstance(kept[-2], HumanMessage)
    assert kept[-2].content == "current question"
    assert isinstance(kept[-1], AIMessage)
    assert kept[-1].content == "current answer"


def test_estimate_tokens_list_of_dicts_content():
    messages = [
        HumanMessage(content=[{"type": "text", "text": "abcd"}]),
        AIMessage(content=[{"type": "text", "text": "ef"}, {"type": "text", "text": "gh"}]),
    ]
    assert estimate_tokens(messages) == 2


def test_clamp_tool_output_caps_and_leaves_short_text():
    assert clamp_tool_output("short") == "short"
    assert clamp_tool_output("x" * 100, limit=10) == "x" * 10 + "… (truncated)"
