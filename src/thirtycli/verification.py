"""Independent verification for a finished task.

The verifier is deliberately separate from the orchestrator: it duck-types the
task, looks only at evidence (recorded changes, a diff, command results) and
never lets a claim stand in for a file that is not there. A task with code
intent and no recorded change is never reported as verified.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

CODE_HINTS = {
    "add",
    "create",
    "implement",
    "fix",
    "refactor",
    "update",
    "delete",
    "remove",
    "migrate",
    "build",
    "write",
    "patch",
    "rename",
    "test",
    "tests",
}
READ_ONLY_PERMISSIONS = {"read-only"}

_WORDS = re.compile(r"[a-z]+")


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    VERIFIED_WITH_WARNINGS = "verified_with_warnings"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    checks: tuple[str, ...] = ()
    files: tuple[str, ...] = ()


def _field(task: object, name: str) -> str:
    if isinstance(task, dict):
        value = task.get(name)
    else:
        value = getattr(task, name, "")
    return "" if value is None else str(value)


def _agent_permission(agent: str) -> str:
    if not agent:
        return ""
    try:  # local import: keep verification free of the orchestrator and its models
        from .agent_catalog import get_agent
    except Exception:  # pragma: no cover - the catalog is optional here
        return ""
    profile = get_agent(agent)
    if profile is None:
        return ""
    return str(getattr(profile, "permission", "") or "").strip().lower()


def _code_intent(title: str, description: str) -> bool:
    words = set(_WORDS.findall(f"{title} {description}".lower()))
    return bool(words & CODE_HINTS)


def _is_read_only(permission: str, task: object) -> bool:
    declared = {
        str(permission or "").strip().lower(),
        _field(task, "permission").strip().lower(),
    }
    agent = task.get("agent") if isinstance(task, dict) else getattr(task, "agent", None)
    if isinstance(agent, str):
        declared.add(_agent_permission(agent.strip().lower()))
    else:
        declared.add(str(getattr(agent, "permission", "") or "").strip().lower())
    return bool(declared & READ_ONLY_PERMISSIONS)


def verify_task(
    task: object,
    changes: list,
    diff_text: str = "",
    permission: str = "controlled",
    checks_runner: Callable[[], tuple[bool, str]] | None = None,
) -> VerificationResult:
    title = _field(task, "title")
    description = _field(task, "description")
    files = tuple(dict.fromkeys(str(getattr(change, "path", "")) for change in changes))

    if _is_read_only(permission, task):
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            reason="read-only task; no file changes expected",
            checks=("no writes required",),
            files=files,
        )

    if not changes:
        if _code_intent(title, description):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                reason="no files changed for a code task",
                checks=("no file changes recorded",),
            )
        return VerificationResult(
            status=VerificationStatus.INCONCLUSIVE,
            reason="no files changed and no checks supplied to verify the result",
            checks=("no file changes recorded",),
        )

    written_paths = [
        str(getattr(change, "path", ""))
        for change in changes
        if str(getattr(change, "action", "write")).lower() != "delete"
    ]
    missing = tuple(
        dict.fromkeys(
            str(getattr(change, "path", ""))
            for change in changes
            if str(getattr(change, "action", "write")).lower() != "delete"
            and getattr(change, "after", None) is None
        )
    )

    if missing and len(missing) == len(dict.fromkeys(written_paths)):
        return VerificationResult(
            status=VerificationStatus.FAILED,
            reason="claimed files are missing; no write evidence for a code task",
            checks=("claimed file(s) not found: " + ", ".join(missing),),
            files=files,
        )

    checks: list[str] = []
    if missing:
        checks.append("file missing after write: " + ", ".join(missing))

    if checks_runner is not None:
        try:
            ok, text = checks_runner()
        except Exception as error:  # a crashing runner is a failed check, not a pass
            checks.append(f"checks raised: {error}")
            return VerificationResult(
                status=VerificationStatus.FAILED,
                reason="checks failed",
                checks=tuple(checks),
                files=files,
            )
        text = str(text or "").strip()
        if text:
            checks.append(text)
        if not ok:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                reason="checks failed",
                checks=tuple(checks),
                files=files,
            )
        return VerificationResult(
            status=(VerificationStatus.VERIFIED_WITH_WARNINGS if missing else VerificationStatus.VERIFIED),
            reason="checks passed with warnings" if missing else "checks passed",
            checks=tuple(checks),
            files=files,
        )

    if missing:
        return VerificationResult(
            status=VerificationStatus.VERIFIED_WITH_WARNINGS,
            reason="file missing after write",
            checks=tuple(checks),
            files=files,
        )

    if not str(diff_text or "").strip():
        return VerificationResult(
            status=VerificationStatus.VERIFIED_WITH_WARNINGS,
            reason="diff unavailable",
            checks=tuple(checks),
            files=files,
        )

    return VerificationResult(
        status=VerificationStatus.VERIFIED,
        reason="files changed; run project checks to confirm",
        checks=tuple(checks),
        files=files,
    )
