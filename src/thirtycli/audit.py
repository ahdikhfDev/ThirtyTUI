"""Structured security audit trail: ~/.thirtycli/audit/audit.jsonl (one JSON object per line)."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, fields
from pathlib import Path

from .config import home
from .secrets import redact

# One lock for every audit writer: two logs may point at the same file.
_WRITE_LOCK = threading.Lock()

_TEXT_FIELDS = (
    "session_id",
    "task_id",
    "agent_id",
    "tool",
    "action",
    "decision",
    "approval",
    "result",
    "error",
)


def audit_path() -> Path:
    """Where the default audit log lives."""
    return home() / "audit" / "audit.jsonl"


@dataclass
class AuditEvent:
    """One security-relevant thing that happened, as it will be stored."""

    ts: float
    event: str
    session_id: str = ""
    task_id: str = ""
    agent_id: str = ""
    tool: str = ""
    action: str = ""
    decision: str = ""
    approval: str = ""
    result: str = ""
    paths: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict:
        """JSON-ready payload: empty fields dropped, every string redacted."""
        data: dict = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "paths":
                paths = tuple(redact(str(item)) for item in value if str(item).strip())
                if paths:
                    data["paths"] = list(paths)
                continue
            if isinstance(value, str):
                value = redact(value)
            if value is None or value == "":
                continue
            data[field.name] = value
        return data


class AuditLog:
    """Append-only audit log. Never stores anything that looks like a credential."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else audit_path()

    @property
    def path(self) -> Path:
        return self._path

    def record(self, event: str, **fields: object) -> AuditEvent:
        """Append one event and return it.

        Unknown keywords are ignored so callers can pass a whole context dict.
        `ts` may be supplied explicitly; otherwise it is now.
        """
        data: dict[str, object] = {}
        for name in _TEXT_FIELDS:
            value = fields.get(name)
            if value is not None:
                data[name] = str(value)

        paths = fields.get("paths")
        if paths is not None:
            if isinstance(paths, (str, Path)):
                paths = (paths,)
            data["paths"] = tuple(str(item) for item in paths)

        ts = fields.get("ts")
        entry = AuditEvent(
            ts=float(ts) if ts is not None else time.time(),
            event=str(event),
            **data,
        )
        self._append(entry.as_dict())
        return entry

    def tail(self, limit: int = 50, event: str = "", task_id: str = "") -> list[dict]:
        """Newest-first events, optionally filtered by event name or task."""
        return _read(self._path, limit=limit, event=event, task_id=task_id)

    def _append(self, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _WRITE_LOCK:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def read_audit(
    limit: int = 50,
    path: Path | None = None,
    event: str = "",
    task_id: str = "",
) -> list[dict]:
    """Convenience reader over the default (or given) audit log."""
    return AuditLog(path).tail(limit=limit, event=event, task_id=task_id)


def _read(path: Path, limit: int, event: str, task_id: str) -> list[dict]:
    if limit <= 0:
        return []
    rows: deque[dict] = deque(maxlen=limit)
    try:
        if not path.is_file():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a half-written or hand-edited line is skipped
                if not isinstance(row, dict):
                    continue
                if event and row.get("event") != event:
                    continue
                if task_id and row.get("task_id") != task_id:
                    continue
                rows.append(row)
    except OSError:
        return []

    ordered = list(reversed(rows))
    ordered.sort(key=_ts, reverse=True)
    return ordered


def _ts(row: dict) -> float:
    try:
        return float(row.get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0
