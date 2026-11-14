"""The audit trail must be structured, filterable, redacted, and crash-proof."""

import threading

from thirtycli.audit import AuditEvent, AuditLog, audit_path, read_audit


def test_record_and_read(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    event = log.record(
        "tool.run",
        tool="read_file",
        decision="allowed",
        result="ok",
        paths=[tmp_path / "a.txt"],
    )

    assert isinstance(event, AuditEvent)
    assert event.ts > 0
    rows = log.tail()
    assert len(rows) == 1
    assert rows[0]["event"] == "tool.run"
    assert rows[0]["tool"] == "read_file"
    assert rows[0]["decision"] == "allowed"
    assert rows[0]["paths"] == [str(tmp_path / "a.txt")]
    assert "session_id" not in rows[0]  # empty fields are not written


def test_read_audit_reads_a_path(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("approval", action="write", approval="denied")

    rows = read_audit(path=path)
    assert len(rows) == 1
    assert rows[0]["approval"] == "denied"


def test_filters_and_newest_first(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("tool.run", task_id="t1", tool="a", ts=1.0)
    log.record("approval", task_id="t2", tool="b", ts=2.0)
    log.record("tool.run", task_id="t2", tool="c", ts=3.0)

    assert [row["tool"] for row in log.tail()] == ["c", "b", "a"]
    assert [row["tool"] for row in log.tail(event="tool.run")] == ["c", "a"]
    assert [row["tool"] for row in log.tail(task_id="t1")] == ["a"]
    assert [row["tool"] for row in log.tail(event="tool.run", task_id="t2")] == ["c"]
    assert [row["tool"] for row in log.tail(limit=2)] == ["c", "b"]
    assert log.tail(limit=0) == []


def test_api_keys_never_reach_the_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record(
        "tool.error",
        tool="curl",
        error="request failed with key sk-abcdef1234567890",
        paths=["/tmp/sk-abcdef1234567890"],
    )

    raw = path.read_text(encoding="utf-8")
    assert "sk-abcdef1234567890" not in raw
    row = log.tail()[0]
    assert "sk-****7890" in row["error"]
    assert "sk-****7890" in row["paths"][0]


def test_default_log_honours_thirtycli_home(tmp_path, monkeypatch):
    monkeypatch.setenv("THIRTYCLI_HOME", str(tmp_path / "home"))

    assert audit_path() == tmp_path / "home" / "audit" / "audit.jsonl"

    log = AuditLog()
    log.record("session.start", session_id="abc123")
    assert log.path.exists()
    assert read_audit()[0]["event"] == "session.start"


def test_every_thread_gets_its_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)

    def worker(worker_id: int) -> None:
        for index in range(25):
            log.record("tool.run", task_id=f"t{worker_id}", tool=f"call-{index}")

    threads = [threading.Thread(target=worker, args=(worker_id,)) for worker_id in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    assert len(log.tail(limit=100)) == 50


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("tool.run", tool="good")
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "broken"\n')
        handle.write("not json at all\n")
    log.record("tool.run", tool="good-again")

    assert [row["tool"] for row in log.tail()] == ["good-again", "good"]


def test_missing_log_reads_empty(tmp_path):
    assert AuditLog(tmp_path / "missing.jsonl").tail() == []
    assert read_audit(path=tmp_path / "missing.jsonl") == []


def test_unknown_fields_are_dropped(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    event = log.record("tool.run", tool="x", nonsense="ignored", ts=5.0)

    assert event.ts == 5.0
    row = log.tail()[0]
    assert "nonsense" not in row
    assert row["tool"] == "x"


def test_as_dict_keeps_only_relevant_fields():
    event = AuditEvent(ts=1.5, event="tool.run", tool="read", paths=("/a", ""))
    assert event.as_dict() == {"ts": 1.5, "event": "tool.run", "tool": "read", "paths": ["/a"]}
