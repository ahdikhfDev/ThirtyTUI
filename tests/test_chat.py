from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from thirtycli.chat import build_messages, message_text
from thirtycli.config import DEFAULT_SYSTEM, Config
from thirtycli.sessions import Session


def test_message_text_variants():
    assert message_text("plain") == "plain"
    assert message_text(None) == ""
    assert message_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert message_text([{"type": "tool_use", "id": "1"}, "c"]) == "c"


def test_build_messages_starts_with_system():
    cfg = Config()
    session = Session()
    session.add("user", "hi")
    session.add("assistant", "hello")
    messages = build_messages(cfg, session)
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content == DEFAULT_SYSTEM
    assert isinstance(messages[1], HumanMessage)
    assert isinstance(messages[2], AIMessage)
    assert messages[2].content == "hello"
