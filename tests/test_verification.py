"""Independent verification: only evidence earns a pass."""

from dataclasses import dataclass

from thirtycli.changes import FileChange
from thirtycli.verification import (
    CODE_HINTS,
    VerificationResult,
    VerificationStatus,
    verify_task,
)


@dataclass
class FakeTask:
    title: str
    description: str = ""
    agent: str = "coder"


def change(path="src/app.py", action="write", after="deadbeef", added=1, removed=0):
    return FileChange(
        task_id="t1",
        path=path,
        action=action,
        before="before" if action == "write" else None,
        after=after,
        added=added,
        removed=removed,
    )


def test_result_is_frozen():
    result = VerificationResult(VerificationStatus.VERIFIED, "ok")
    assert result.checks == ()
    assert result.files == ()
    try:
        result.reason = "changed"  # type: ignore[misc]
    except Exception as error:
        assert "frozen" in str(error).lower() or "cannot assign" in str(error).lower()
    else:  # pragma: no cover - a mutable result would be a bug
        raise AssertionError("VerificationResult should be frozen")


def test_code_hints_cover_common_change_verbs():
    assert {"add", "fix", "delete", "test"} <= CODE_HINTS


def test_read_only_task_is_verified_without_changes():
    result = verify_task(FakeTask("Review the diff", agent="reviewer"), [], permission="read-only")

    assert result.status is VerificationStatus.VERIFIED
    assert result.reason == "read-only task; no file changes expected"
    assert result.checks == ("no writes required",)
    assert result.files == ()


def test_read_only_agent_is_detected_from_the_catalog():
    result = verify_task(FakeTask("Map the codebase", agent="explorer"), [])

    assert result.status is VerificationStatus.VERIFIED
    assert "read-only" in result.reason


def test_code_task_without_changes_fails():
    result = verify_task(FakeTask("Add retry logic", "Wrap the client call"), [])

    assert result.status is VerificationStatus.FAILED
    assert result.reason == "no files changed for a code task"


def test_non_code_task_without_evidence_is_inconclusive():
    result = verify_task(FakeTask("Summarise the meeting notes"), [])

    assert result.status is VerificationStatus.INCONCLUSIVE
    assert "no files changed" in result.reason


def test_task_can_be_a_mapping():
    result = verify_task({"title": "Implement login"}, [])

    assert result.status is VerificationStatus.FAILED


def test_changes_with_a_diff_are_verified():
    result = verify_task(FakeTask("Add retry logic"), [change()], diff_text="--- a\n+++ b\n+x\n")

    assert result.status is VerificationStatus.VERIFIED
    assert result.reason == "files changed; run project checks to confirm"
    assert result.files == ("src/app.py",)


def test_changes_without_a_diff_warn():
    result = verify_task(FakeTask("Add retry logic"), [change()], diff_text="")

    assert result.status is VerificationStatus.VERIFIED_WITH_WARNINGS
    assert result.reason == "diff unavailable"


def test_passing_checks_verify_the_task():
    result = verify_task(
        FakeTask("Fix the parser"),
        [change()],
        diff_text="--- a\n+++ b\n+x\n",
        checks_runner=lambda: (True, "pytest: 42 passed"),
    )

    assert result.status is VerificationStatus.VERIFIED
    assert result.reason == "checks passed"
    assert "pytest: 42 passed" in result.checks


def test_passing_checks_win_over_an_empty_diff():
    result = verify_task(
        FakeTask("Fix the parser"),
        [change()],
        diff_text="",
        checks_runner=lambda: (True, "ruff: clean"),
    )

    assert result.status is VerificationStatus.VERIFIED


def test_failed_checks_fail_the_task():
    result = verify_task(
        FakeTask("Fix the parser"),
        [change()],
        diff_text="--- a\n+++ b\n+x\n",
        checks_runner=lambda: (False, "pytest: 2 failed"),
    )

    assert result.status is VerificationStatus.FAILED
    assert result.reason == "checks failed"
    assert "pytest: 2 failed" in result.checks


def test_checks_that_raise_count_as_a_failure():
    def boom():
        raise RuntimeError("no interpreter")

    result = verify_task(FakeTask("Fix the parser"), [change()], checks_runner=boom)

    assert result.status is VerificationStatus.FAILED
    assert result.reason == "checks failed"
    assert any("no interpreter" in check for check in result.checks)


def test_write_claim_without_a_file_fails():
    result = verify_task(FakeTask("Add a module"), [change(after=None)])

    assert result.status is VerificationStatus.FAILED
    assert "missing" in result.reason
    assert result.files == ("src/app.py",)


def test_one_missing_file_warns_but_does_not_claim_clean_success():
    changes = [change(path="src/app.py"), change(path="src/gone.py", after=None)]

    result = verify_task(FakeTask("Add a feature"), changes, diff_text="--- a\n+++ b\n+x\n")

    assert result.status is VerificationStatus.VERIFIED_WITH_WARNINGS
    assert result.reason == "file missing after write"
    assert "src/gone.py" in " ".join(result.checks)


def test_delete_change_is_valid_without_a_file():
    deleted = change(path="old.py", action="delete", after=None, removed=3)

    result = verify_task(FakeTask("Remove old.py"), [deleted], diff_text="--- a/old.py\n+++ /dev/null\n-x\n")

    assert result.status is VerificationStatus.VERIFIED
    assert result.files == ("old.py",)
