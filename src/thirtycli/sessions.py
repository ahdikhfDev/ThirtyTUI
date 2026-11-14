"""Session storage: ~/.thirtycli/sessions/<id>.json"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import home


def sessions_dir() -> Path:
    path = home() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


TITLE_MAX = 48


def _derive_title(text: str) -> str:
    """A short human name for a session, taken from its first user line."""
    for line in str(text or "").splitlines():
        cleaned = " ".join(line.strip().lstrip("#>-* ").split())
        if not cleaned:
            continue
        if len(cleaned) > TITLE_MAX:
            return cleaned[:TITLE_MAX].rstrip() + "..."
        return cleaned
    return ""


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    model: str = ""
    title: str = ""
    messages: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def add(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content, "at": time.time()})
        if role == "user" and not self.title:
            self.title = _derive_title(content)
        self.updated = time.time()

    def add_usage(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.usage["input"] = self.usage.get("input", 0) + int(input_tokens or 0)
        self.usage["output"] = self.usage.get("output", 0) + int(output_tokens or 0)
        self.usage["total"] = self.usage.get("input", 0) + self.usage.get("output", 0)

    @property
    def path(self) -> Path:
        return sessions_dir() / f"{self.id}.json"

    def save(self) -> Path:
        self.updated = time.time()
        payload = {
            "id": self.id,
            "created": self.created,
            "updated": self.updated,
            "model": self.model,
            "title": self.title,
            "messages": self.messages,
            "usage": self.usage,
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, session_id: str) -> Session:
        path = sessions_dir() / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"No session '{session_id}'")
        data = json.loads(path.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
        title = str(data.get("title", ""))
        if not title:
            for message in messages:
                if message.get("role") == "user":
                    title = _derive_title(message.get("content", ""))
                    break
        return cls(
            id=data.get("id", session_id),
            created=data.get("created", time.time()),
            updated=data.get("updated", time.time()),
            model=data.get("model", ""),
            title=title,
            messages=messages,
            usage=data.get("usage", {}),
        )

    @classmethod
    def latest(cls) -> Session | None:
        files = sorted(sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        return cls.load(files[0].stem)

    def delete(self) -> bool:
        if self.path.exists():
            self.path.unlink()
            return True
        return False


def list_sessions() -> list[Session]:
    out = []
    for path in sorted(sessions_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(Session.load(path.stem))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def as_transcript(session: Session, limit: int | None = None) -> str:
    lines = []
    for msg in session.messages[-limit:] if limit else session.messages:
        role = msg.get("role")
        if role == "tool":
            lines.append(f"tool: {str(msg.get('content', ''))[:200]}")
            continue
        if role not in {"user", "assistant"}:
            continue
        who = "you" if role == "user" else "thirty"
        lines.append(f"{who}: {msg.get('content', '')}")
    return "\n\n".join(lines)
