"""Workspace tracking: snapshots, diffs, conflicts and path locks."""

import hashlib
import threading
import time

import pytest

from thirtycli.changes import FileChange, WorkspaceTracker


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\n", encoding="utf-8", newline="\n")
    return tmp_path


def read_all(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_snapshot_hashes_text_files_and_skips_noise(workspace):
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "dep.js").write_text("x", encoding="utf-8", newline="\n")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref", encoding="utf-8", newline="\n")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "mod.pyc").write_text("y", encoding="utf-8", newline="\n")
    (workspace / "binary.bin").write_bytes(b"\x00\x01")
    (workspace / "huge.txt").write_text("z" * 40, encoding="utf-8", newline="\n")

    tracker = WorkspaceTracker(workspace, max_file_bytes=32)

    assert tracker.snapshot() == {
        "src/app.py": hashlib.sha256(b"one\ntwo\n").hexdigest(),
    }


def test_note_records_new_modified_and_deleted_files(workspace):
    tracker = WorkspaceTracker(workspace)

    (workspace / "src" / "fresh.py").write_text("a\nb\n", encoding="utf-8", newline="\n")
    created = tracker.note("t1", "src/fresh.py")
    assert isinstance(created, FileChange)
    assert created.before is None
    assert created.after == hashlib.sha256(b"a\nb\n").hexdigest()
    assert (created.added, created.removed) == (2, 0)

    (workspace / "src" / "app.py").write_text("one\nthree\nfour\n", encoding="utf-8", newline="\n")
    modified = tracker.note("t1", "src/app.py")
    assert modified.before == hashlib.sha256(b"one\ntwo\n").hexdigest()
    assert (modified.added, modified.removed) == (2, 1)

    (workspace / "src" / "app.py").unlink()
    deleted = tracker.note("t1", "src/app.py", action="delete")
    assert deleted.action == "delete"
    assert deleted.after is None
    assert (deleted.added, deleted.removed) == (0, 3)

    assert tracker.changed_files() == ["src/fresh.py", "src/app.py"]
    assert tracker.changed_files("t1") == ["src/fresh.py", "src/app.py"]
    assert tracker.changed_files("t2") == []
    assert [change.action for change in tracker.changes("t1")] == ["write", "write", "delete"]
    assert tracker.changes("t2") == []


def test_unknown_action_is_rejected(workspace):
    tracker = WorkspaceTracker(workspace)
    with pytest.raises(ValueError):
        tracker.note("t1", "src/app.py", action="chmod")


def test_diff_shows_added_and_removed_lines(workspace):
    tracker = WorkspaceTracker(workspace)
    (workspace / "src" / "app.py").write_text("one\nthree\n", encoding="utf-8", newline="\n")
    tracker.note("t1", "src/app.py")
    (workspace / "src" / "fresh.py").write_text("hello\n", encoding="utf-8", newline="\n")
    tracker.note("t1", "src/fresh.py")

    text = tracker.diff("t1")

    assert "--- a/src/app.py" in text
    assert "+++ b/src/app.py" in text
    assert "-two" in text
    assert "+three" in text
    assert "--- /dev/null" in text
    assert "+hello" in text
    assert tracker.diff("t2") == ""


def test_diff_marks_deleted_files(workspace):
    tracker = WorkspaceTracker(workspace)
    (workspace / "src" / "app.py").unlink()
    tracker.note("t1", "src/app.py", action="delete")

    text = tracker.diff("t1")

    assert "-one" in text
    assert "-two" in text
    assert "+++ /dev/null" in text


def test_diff_caps_output(workspace):
    tracker = WorkspaceTracker(workspace)
    body = "\n".join(f"line {index}" for index in range(300)) + "\n"
    (workspace / "src" / "app.py").write_text(body, encoding="utf-8", newline="\n")
    tracker.note("t1", "src/app.py")

    text = tracker.diff(max_chars=400)

    assert len(text) <= 400
    assert text.endswith("(diff truncated)")


def test_check_write_flags_unrelated_overwrites(workspace):
    tracker = WorkspaceTracker(workspace)

    assert tracker.check_write("a", "src/app.py") is None
    reason = tracker.check_write("b", "src/app.py")

    assert reason is not None
    assert "src/app.py" in reason
    assert "task a" in reason
    assert tracker.check_write("a", "src/app.py") is None
    # The denied task never wrote, so there is no confirmed conflict to report.
    assert tracker.conflicts() == []


def test_check_write_allows_related_tasks(workspace):
    tracker = WorkspaceTracker(workspace)

    assert tracker.check_write("a", "src/app.py") is None
    related = tracker.check_write(
        "b", "src/app.py", related=lambda first, second: {first, second} == {"a", "b"}
    )

    assert related is None
    assert tracker.conflicts() == [{"path": "src/app.py", "tasks": ["a", "b"]}]


def test_check_write_honours_a_callback_that_returns_none(workspace):
    tracker = WorkspaceTracker(workspace)

    assert tracker.check_write("a", "src/app.py") is None
    assert tracker.check_write("b", "src/app.py", related=lambda first, second: None) is None


def test_check_write_rejects_a_callback_that_says_unrelated(workspace):
    tracker = WorkspaceTracker(workspace)

    assert tracker.check_write("a", "src/app.py") is None
    reason = tracker.check_write("b", "src/app.py", related=lambda first, second: False)

    assert reason is not None
    assert "task a" in reason


def test_check_write_without_a_callback_treats_tasks_as_unrelated(workspace):
    tracker = WorkspaceTracker(workspace)

    assert tracker.check_write("a", "src/app.py") is None
    assert tracker.check_write("b", "src/app.py") is not None


def test_lock_serializes_access_to_one_path(workspace):
    tracker = WorkspaceTracker(workspace)
    active = 0
    peak = 0
    started = threading.Barrier(4)

    def worker():
        nonlocal active, peak
        started.wait()
        with tracker.lock("src/app.py"):
            active += 1
            peak = max(peak, active)
            time.sleep(0.01)
            active -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1


def test_tracking_is_in_memory_only(workspace):
    before = read_all(workspace)

    tracker = WorkspaceTracker(workspace)
    tracker.check_write("a", "src/app.py")
    tracker.note("a", "src/app.py")
    tracker.diff()
    tracker.conflicts()

    assert read_all(workspace) == before
