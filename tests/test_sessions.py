import pytest

from thirtycli.sessions import Session, as_transcript, list_sessions


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path))


def test_save_and_reload():
    session = Session(model="gpt-4o-mini")
    session.add("user", "hello")
    session.add("assistant", "hi")
    path = session.save()
    assert path.exists()

    again = Session.load(session.id)
    assert [m["role"] for m in again.messages] == ["user", "assistant"]
    assert again.messages[0]["content"] == "hello"
    assert again.model == "gpt-4o-mini"


def test_list_and_latest():
    first = Session()
    first.add("user", "one")
    first.save()
    second = Session()
    second.add("user", "two")
    second.save()

    ids = [s.id for s in list_sessions()]
    assert set(ids) == {first.id, second.id}
    assert Session.latest().id in {first.id, second.id}


def test_delete_and_missing():
    session = Session()
    session.save()
    assert session.delete() is True
    assert session.delete() is False
    with pytest.raises(FileNotFoundError):
        Session.load("does-not-exist")


def test_title_comes_from_the_first_user_message():
    session = Session()
    session.add("user", "Refactor the parser and add tests")
    session.add("user", "and then ship it")
    assert session.title == "Refactor the parser and add tests"

    session.save()
    assert Session.load(session.id).title == "Refactor the parser and add tests"


def test_title_is_derived_when_an_old_file_has_none(home):
    import json

    from thirtycli.sessions import sessions_dir

    path = sessions_dir() / "legacy01.json"
    path.write_text(
        json.dumps(
            {
                "id": "legacy01",
                "model": "fake",
                "messages": [{"role": "user", "content": "Fix the login redirect loop"}],
            }
        ),
        encoding="utf-8",
    )
    assert Session.load("legacy01").title == "Fix the login redirect loop"


def test_transcript_roles():
    session = Session()
    session.add("user", "yo")
    session.add("assistant", "hey")
    text = as_transcript(session)
    assert "you: yo" in text
    assert "thirty: hey" in text
