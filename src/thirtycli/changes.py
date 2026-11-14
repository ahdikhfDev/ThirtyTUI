"""Per-task file change tracking for a shared workspace.

The tracker snapshots the workspace once, records what each task wrote or
deleted, serialises writes to the same path, and refuses an overwrite when a
different task already owns the file and the caller does not declare the two
tasks related. ``diff`` replays the recorded changes as a unified diff so the
orchestrator can show evidence instead of a claim.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import ndiff, unified_diff
from pathlib import Path

NOISE_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".next"}
ACTIONS = {"write", "delete"}
TRUNCATED = "\n… (diff truncated)"


@dataclass
class FileChange:
    task_id: str
    path: str
    action: str
    before: str | None
    after: str | None
    added: int = 0
    removed: int = 0


@dataclass
class _Record:
    """A change plus the text needed to rebuild its diff later."""

    change: FileChange
    before_text: str
    after_text: str


def _read_text_file(path: Path, max_file_bytes: int) -> tuple[str, str] | None:
    """Return (sha256, text) for a small text file, or None.

    Directories, unreadable files, files at/over the size cap and files with a
    NUL byte are skipped: they are not diffable sources.
    """
    try:
        if path.stat().st_size >= max_file_bytes:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    return hashlib.sha256(data).hexdigest(), data.decode("utf-8", "replace")


def _line_counts(before: str, after: str) -> tuple[int, int]:
    added = removed = 0
    for line in ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return added, removed


def _diff_header(change: FileChange) -> tuple[str, str]:
    if change.before is None:
        return "/dev/null", f"b/{change.path}"
    if change.action == "delete":
        return f"a/{change.path}", "/dev/null"
    return f"a/{change.path}", f"b/{change.path}"


class WorkspaceTracker:
    def __init__(self, root: Path, max_file_bytes: int = 1_000_000) -> None:
        self.root = Path(root).resolve()
        self.max_file_bytes = max(1, int(max_file_bytes))
        self._guard = threading.RLock()
        self._locks: dict[str, threading.RLock] = {}
        self._writers: dict[str, list[str]] = {}
        self._records: list[_Record] = []
        self._snapshot_hashes: dict[str, str] = {}
        self._snapshot_text: dict[str, str] = {}
        self._state_hashes: dict[str, str] = {}
        self._state_text: dict[str, str] = {}
        self.snapshot()

    # -- workspace -------------------------------------------------------

    def _relative(self, path: str | Path) -> str:
        raw = Path(path)
        target = raw if raw.is_absolute() else self.root / raw
        try:
            return target.relative_to(self.root).as_posix()
        except ValueError:
            text = target.as_posix()
            return text[2:] if text.startswith("./") else text

    def _iter_files(self) -> Iterator[Path]:
        for current, dirs, files in os.walk(self.root):
            dirs[:] = sorted(name for name in dirs if name not in NOISE_DIRS)
            for name in sorted(files):
                yield Path(current) / name

    def _read(self, relative: str) -> tuple[str, str] | None:
        return _read_text_file(self.root / relative, self.max_file_bytes)

    def snapshot(self) -> dict[str, str]:
        """Hash every small text file in the workspace and keep its text."""
        hashes: dict[str, str] = {}
        texts: dict[str, str] = {}
        for path in self._iter_files():
            read = _read_text_file(path, self.max_file_bytes)
            if read is None:
                continue
            relative = self._relative(path)
            hashes[relative] = read[0]
            texts[relative] = read[1]
        with self._guard:
            self._snapshot_hashes = hashes
            self._snapshot_text = texts
            self._state_hashes = dict(hashes)
            self._state_text = dict(texts)
        return dict(hashes)

    # -- concurrency -----------------------------------------------------

    def _lock_for(self, relative: str) -> threading.RLock:
        with self._guard:
            lock = self._locks.get(relative)
            if lock is None:
                lock = self._locks[relative] = threading.RLock()
            return lock

    @contextmanager
    def lock(self, path: str) -> Iterator[None]:
        """Serialise access to one path; other paths stay independent."""
        with self._lock_for(self._relative(path)):
            yield

    def check_write(
        self,
        task_id: str,
        path: str,
        related: Callable[[str, str], bool] | None = None,
    ) -> str | None:
        """Return a conflict reason when another unrelated task owns ``path``.

        ``related(a, b)`` is the caller's escape hatch for tasks that may
        legitimately share a file: return ``True`` (or ``None``) to allow the
        write. Without a callback every other writer counts as unrelated.
        """
        relative = self._relative(path)
        with self._guard:
            writers = self._writers.setdefault(relative, [])
            for other in writers:
                if other == task_id:
                    continue
                if related is not None and related(other, task_id) in (True, None):
                    continue
                return (
                    f"{relative} is already being written by task {other}; "
                    "unrelated tasks may not overwrite the same file"
                )
            if task_id not in writers:
                writers.append(task_id)
        return None

    # -- recording -------------------------------------------------------

    def note(self, task_id: str, path: str, action: str = "write") -> FileChange:
        """Record a write or delete for ``task_id`` using the current state."""
        relative = self._relative(path)
        action = (action or "write").strip().lower()
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")

        with self.lock(relative):
            with self._guard:
                before_hash = self._state_hashes.get(relative)
                before_text = self._state_text.get(relative, "")
                after_hash: str | None = None
                after_text = ""
                if action == "write":
                    read = self._read(relative)
                    if read is not None:
                        after_hash, after_text = read

                added, removed = _line_counts(before_text, after_text)
                change = FileChange(
                    task_id=task_id,
                    path=relative,
                    action=action,
                    before=before_hash,
                    after=after_hash,
                    added=added,
                    removed=removed,
                )
                self._records.append(_Record(change, before_text, after_text))

                if action == "delete" or after_hash is None:
                    self._state_hashes.pop(relative, None)
                    self._state_text.pop(relative, None)
                else:
                    self._state_hashes[relative] = after_hash
                    self._state_text[relative] = after_text

                writers = self._writers.setdefault(relative, [])
                if task_id not in writers:
                    writers.append(task_id)
                return change

    # -- queries ---------------------------------------------------------

    def changed_files(self, task_id: str | None = None) -> list[str]:
        seen: dict[str, None] = {}
        with self._guard:
            for record in self._records:
                if task_id is None or record.change.task_id == task_id:
                    seen[record.change.path] = None
        return list(seen)

    def changes(self, task_id: str | None = None) -> list[FileChange]:
        with self._guard:
            return [
                record.change
                for record in self._records
                if task_id is None or record.change.task_id == task_id
            ]

    def diff(self, task_id: str | None = None, max_chars: int = 8000) -> str:
        """Render recorded changes as a unified diff, capped at ``max_chars``."""
        parts: list[str] = []
        with self._guard:
            records = list(self._records)
        for record in records:
            change = record.change
            if task_id is not None and change.task_id != task_id:
                continue
            old = [] if change.before is None else record.before_text.splitlines(keepends=True)
            new = [] if change.action == "delete" else record.after_text.splitlines(keepends=True)
            if old == new:
                continue
            fromfile, tofile = _diff_header(change)
            lines = unified_diff(old, new, fromfile=fromfile, tofile=tofile)
            segment = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
            if segment:
                parts.append(segment)

        text = "".join(parts)
        if max_chars > 0 and len(text) > max_chars:
            keep = max(0, max_chars - len(TRUNCATED))
            text = text[:keep].rstrip() + TRUNCATED
        return text

    def conflicts(self) -> list[dict]:
        with self._guard:
            rows = [
                {"path": path, "tasks": list(dict.fromkeys(tasks))}
                for path, tasks in self._writers.items()
                if len(dict.fromkeys(tasks)) > 1
            ]
        return sorted(rows, key=lambda row: row["path"])

    def capture(self, task_id: str) -> list[str]:
        """Record every file a shell/commit call changed since the last capture."""
        current: dict[str, str] = {}
        for path in self._iter_files():
            read = _read_text_file(path, self.max_file_bytes)
            if read is None:
                continue
            current[self._relative(path)] = read[0]
        with self._guard:
            before = dict(self._state_hashes)
        changed = sorted(
            name
            for name in set(before) | set(current)
            if before.get(name) != current.get(name)
        )
        for name in changed[:200]:
            action = "delete" if name not in current else "write"
            self.note(task_id, name, action)
        return changed
