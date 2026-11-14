"""Project memory: durable notes per project root, ~/.thirtycli/memory/<hash>.json."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import home

KINDS = frozenset({"decision", "architecture", "constraint", "outcome", "note"})

_LOCK = threading.RLock()
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_.+-]*")
_LINE_MAX = 280
_MIN_TOKEN = 2


@dataclass(frozen=True)
class MemoryEntry:
    """One durable fact about a project."""

    id: str
    project: str
    kind: str
    text: str
    tags: tuple[str, ...] = ()
    created: float = 0.0
    updated: float = 0.0


def memory_path(root: Path) -> Path:
    """`~/.thirtycli/memory/<sha256(abs(root))[:12]>.json`."""
    resolved = Path(root).expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]
    return home() / "memory" / f"{digest}.json"


class ProjectMemory:
    """Per-project notes that survive restarts.

    A store is a small JSON document rewritten atomically on every change, so a
    crash mid-write leaves the previous version intact. A corrupt store reads as
    empty instead of raising: memory is helpful, never load-bearing.
    """

    def __init__(self, root: Path, store: Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self._store = Path(store) if store is not None else memory_path(self.root)

    @property
    def path(self) -> Path:
        return self._store

    @property
    def project(self) -> str:
        return str(self.root)

    def add(self, text: str, kind: str = "note", tags: tuple[str, ...] = ()) -> MemoryEntry:
        """Store a note. Identical text in the same project returns the old entry."""
        clean = _norm(text)
        if not clean:
            raise ValueError("memory text is empty")
        kind = _kind(kind)
        clean_tags = _clean_tags(tags)

        with _LOCK:
            entries = self._load()
            key = clean.casefold()
            for entry in entries:
                if entry.text.casefold() == key:
                    return entry
            now = time.time()
            entry = MemoryEntry(
                id=uuid.uuid4().hex[:12],
                project=self.project,
                kind=kind,
                text=clean,
                tags=clean_tags,
                created=now,
                updated=now,
            )
            entries.append(entry)
            self._save(entries)
            return entry

    def all(self) -> list[MemoryEntry]:
        """Every entry, oldest first."""
        with _LOCK:
            return sorted(self._load(), key=lambda entry: (entry.created, entry.id))

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """Keyword overlap over text and tags; ties go to the most recently updated."""
        if limit <= 0:
            return []
        entries = self.all()
        tokens = _tokens(query)
        if not tokens:
            return sorted(entries, key=_recent, reverse=True)[:limit]

        ranked: list[tuple[int, MemoryEntry]] = []
        for entry in entries:
            score = _score(entry, tokens)
            if score:
                ranked.append((score, entry))
        ranked.sort(key=lambda pair: (pair[0], *_recent(pair[1])), reverse=True)
        return [entry for _, entry in ranked][:limit]

    def context(self, query: str, limit: int = 3, max_chars: int = 2000) -> str:
        """A short markdown block a prompt can prepend, or "" when nothing fits."""
        header = "## Project memory"
        if limit <= 0 or max_chars <= len(header):
            return ""

        lines = [header]
        used = len(header)
        for entry in self.search(query, limit=limit):
            line = f"- [{entry.kind}] {_one_line(entry.text)}"
            room = max_chars - used - 1
            if len(line) > room:
                if room > 12:
                    lines.append(line[: room - 1].rstrip() + "…")
                break
            lines.append(line)
            used += 1 + len(line)
        if len(lines) == 1:
            return ""  # nothing relevant: no header-only block
        return "\n".join(lines)

    def remove(self, entry_id: str) -> bool:
        with _LOCK:
            entries = self._load()
            kept = [entry for entry in entries if entry.id != entry_id]
            if len(kept) == len(entries):
                return False
            self._save(kept)
            return True

    def update(
        self,
        entry_id: str,
        text: str | None = None,
        tags: tuple[str, ...] | None = None,
    ) -> MemoryEntry | None:
        """Edit one entry in place; returns None when the id is unknown."""
        changes: dict = {"updated": time.time()}
        if text is not None:
            clean = _norm(text)
            if not clean:
                raise ValueError("memory text is empty")
            changes["text"] = clean
        if tags is not None:
            changes["tags"] = _clean_tags(tags)

        with _LOCK:
            entries = self._load()
            for index, entry in enumerate(entries):
                if entry.id != entry_id:
                    continue
                updated = replace(entry, **changes)
                entries[index] = updated
                self._save(entries)
                return updated
            return None

    def _load(self) -> list[MemoryEntry]:
        try:
            raw = self._store.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        rows = data.get("entries")
        if not isinstance(rows, list):
            return []

        entries = []
        for row in rows:
            entry = _parse(row, self.project)
            if entry is not None:
                entries.append(entry)
        return entries

    def _save(self, entries: list[MemoryEntry]) -> Path:
        payload = {
            "version": 1,
            "project": self.project,
            "entries": [asdict(entry) for entry in entries],
        }
        self._store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store.with_name(f"{self._store.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._store)
        finally:
            tmp.unlink(missing_ok=True)
        return self._store


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _kind(kind: str) -> str:
    clean = str(kind or "note").strip().lower()
    if clean not in KINDS:
        raise ValueError(f"unknown memory kind {clean!r}; use one of {sorted(KINDS)}")
    return clean


def _clean_tags(tags: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for tag in tags or ():
        clean = _norm(tag)
        if clean and clean not in out:
            out.append(clean)
    return tuple(out)


def _tokens(query: str) -> list[str]:
    found = _TOKEN.findall(str(query or "").casefold())
    return [token for token in found if len(token) >= _MIN_TOKEN]


def _score(entry: MemoryEntry, tokens: list[str]) -> int:
    """Tag hits count double: tags were written on purpose."""
    text = entry.text.casefold()
    tags = [tag.casefold() for tag in entry.tags]
    score = 0
    for token in tokens:
        if any(token in tag for tag in tags):
            score += 2
        elif token in text:
            score += 1
    return score


def _recent(entry: MemoryEntry) -> tuple[float, float, str]:
    return (entry.updated, entry.created, entry.id)


def _one_line(text: str) -> str:
    line = _norm(text)
    if len(line) <= _LINE_MAX:
        return line
    return line[: _LINE_MAX - 1].rstrip() + "…"


def _parse(row: object, project: str) -> MemoryEntry | None:
    if not isinstance(row, dict):
        return None
    entry_id = row.get("id")
    text = row.get("text")
    if not isinstance(entry_id, str) or not entry_id:
        return None
    if not isinstance(text, str) or not text.strip():
        return None

    kind = str(row.get("kind") or "note").strip().lower()
    raw_tags = row.get("tags")
    tags = tuple(str(tag) for tag in raw_tags) if isinstance(raw_tags, list) else ()
    return MemoryEntry(
        id=entry_id,
        project=str(row.get("project") or project),
        kind=kind if kind in KINDS else "note",
        text=text,
        tags=tags,
        created=_num(row.get("created")),
        updated=_num(row.get("updated")),
    )


def _num(value: object) -> float:
    try:
        return float(value)  # stored as a number, but a hand-edited file may not
    except (TypeError, ValueError):
        return 0.0
