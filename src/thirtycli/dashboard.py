"""Dashboard state for the TUI: real orchestrator events, no invented progress.

`DashboardState.apply()` consumes the same event dicts the orchestrator writes to
`~/.thirtycli/tasks/<run>.events.jsonl`. `lines()` renders a compact ASCII panel
suitable for a terminal. Nothing is added unless an event arrived.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

MAX_ACTIVITY = 60
STATUS_MARK = {
    "pending": "[ ]",
    "ready": "[ ]",
    "running": "[~]",
    "waiting": "[~]",
    "completed": "[x]",
    "verified": "[x]",
    "verified_with_warnings": "[x]",
    "failed": "[!]",
    "verification_failed": "[!]",
    "cancelled": "[x]",
    "blocked": "[!]",
}


def _mark(status: str) -> str:
    return STATUS_MARK.get((status or "").lower(), "[ ]")


def _short(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


@dataclass
class DashboardState:
    goal: str = ""
    run_id: str = ""
    session_id: str = ""
    provider: str = ""
    model: str = ""
    tasks: dict[str, dict] = field(default_factory=dict)
    activity: deque = field(default_factory=lambda: deque(maxlen=MAX_ACTIVITY))
    usage: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    status: str = "pending"
    started: float | None = None
    finished: float | None = None

    def apply(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        kind = str(event.get("event") or "")
        now = float(event.get("ts") or time.time())
        if event.get("run_id"):
            self.run_id = str(event["run_id"])
        if kind == "plan":
            self.goal = str(event.get("goal") or self.goal)
            self.status = "running"
            self.started = self.started or now
            for raw in event.get("tasks") or []:
                self._add_task(raw)
            self._activity(now, "plan", self.goal)
            return
        if kind == "route.decision":
            self.routing = {
                "provider": event.get("provider", ""),
                "model": event.get("model", ""),
                "reason": event.get("reason", ""),
                "fallback": bool(event.get("fallback")),
                "task_type": event.get("task_type", ""),
            }
            self.provider = str(event.get("provider") or self.provider)
            self.model = str(event.get("model") or self.model)
            self._activity(now, "route", f"{self.provider}/{self.model}")
            return
        if kind == "run.finish":
            self.status = str(event.get("status") or "finished")
            self.finished = now
            self._activity(now, "finish", self.status)
            return
        if kind == "run.conflicts":
            count = len(event.get("conflicts") or [])
            self._activity(now, "conflict", f"{count} file conflict(s)")
            return

        task_id = str(event.get("task") or "")
        if not task_id:
            return
        task = self.tasks.setdefault(
            task_id,
            {
                "id": task_id,
                "title": str(event.get("title") or ""),
                "agent": str(event.get("agent") or ""),
                "status": "pending",
                "depends_on": [],
                "attempts": 0,
                "error": "",
                "started": None,
                "finished": None,
                "verification": "",
            },
        )
        if event.get("agent"):
            task["agent"] = str(event["agent"])
        if event.get("title"):
            task["title"] = str(event["title"])
        if event.get("attempt"):
            task["attempts"] = int(event["attempt"])

        if kind == "task.start":
            task["status"] = "running"
            task["started"] = task.get("started") or now
            self.status = "running"
            self._activity(now, "start", f"{task_id} · {task['agent']}")
        elif kind == "task.retry":
            task["status"] = "waiting"
            task["error"] = str(event.get("error") or "")
            self._activity(now, "retry", f"{task_id} · {_short(task['error'], 60)}")
        elif kind == "task.complete":
            task["status"] = "completed"
            task["finished"] = now
            task["error"] = ""
            self._activity(now, "done", f"{task_id} · {task['agent']}")
        elif kind == "task.fail":
            task["status"] = str(event.get("status") or "failed")
            task["finished"] = now
            task["error"] = str(event.get("error") or "")
            self._activity(now, "fail", f"{task_id} · {_short(task['error'], 60)}")
        elif kind == "task.cancel":
            task["status"] = "cancelled"
            task["finished"] = now
            self._activity(now, "cancel", task_id)
        elif kind == "task.verify":
            task["verification"] = str(event.get("verification") or "")
            task["reason"] = str(event.get("reason") or "")
            self._activity(now, "verify", f"{task_id} · {task['verification']}")
        elif kind == "task.conflict":
            task["error"] = str(event.get("error") or "")
            self._activity(now, "conflict", f"{task_id} · {_short(task['error'], 60)}")
        if kind not in {
            "task.start",
            "task.retry",
            "task.complete",
            "task.fail",
            "task.cancel",
            "task.verify",
            "task.conflict",
        }:
            self._activity(now, kind, task_id)

    def _add_task(self, raw: object) -> None:
        if isinstance(raw, dict):
            task_id = str(raw.get("id") or "")
            if not task_id:
                return
            self.tasks[task_id] = {
                "id": task_id,
                "title": str(raw.get("title") or ""),
                "agent": str(raw.get("agent") or ""),
                "status": str(raw.get("status") or "pending"),
                "depends_on": [str(item) for item in raw.get("depends_on") or []],
                "attempts": int(raw.get("attempts") or 0),
                "error": str(raw.get("error") or ""),
                "started": raw.get("started"),
                "finished": raw.get("finished"),
                "verification": str(raw.get("verification") or ""),
            }
            return
        parts = [part.strip() for part in str(raw).split("·")]
        if not parts or not parts[0]:
            return
        task_id = parts[0]
        self.tasks[task_id] = {
            "id": task_id,
            "title": parts[2] if len(parts) > 2 else "",
            "agent": parts[1] if len(parts) > 1 else "",
            "status": "pending",
            "depends_on": [],
            "attempts": 0,
            "error": "",
            "started": None,
            "finished": None,
            "verification": "",
        }

    def _activity(self, ts: float, kind: str, text: str) -> None:
        self.activity.append({"ts": ts, "kind": kind, "text": _short(text, 90)})

    def snapshot(self) -> dict:
        return {
            "goal": self.goal,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "started": self.started,
            "finished": self.finished,
            "tasks": self.tasks,
            "activity": list(self.activity),
            "usage": self.usage,
            "routing": self.routing,
        }

    # -- rendering ------------------------------------------------------
    def counts(self) -> dict[str, int]:
        counts = {"completed": 0, "running": 0, "failed": 0, "blocked": 0, "cancelled": 0, "pending": 0}
        for task in self.tasks.values():
            status = str(task.get("status") or "pending")
            if status in {"completed", "verified", "verified_with_warnings"}:
                counts["completed"] += 1
            elif status in {"running", "waiting"}:
                counts["running"] += 1
            elif status in {"failed", "verification_failed"}:
                counts["failed"] += 1
            elif status in {"blocked", "cancelled"}:
                counts[status] += 1
            else:
                counts["pending"] += 1
        return counts

    def lines(self, width: int = 96, max_tasks: int = 12) -> list[str]:
        width = max(40, int(width))
        out: list[str] = []
        counts = self.counts()
        total = len(self.tasks)
        out.append("EXECUTION")
        out.append(f"  goal    {_short(self.goal or '-', width - 10)}")
        out.append(f"  run     {self.run_id or '-'}   status {self.status}")
        out.append(f"  session {self.session_id or '-'}")
        out.append(
            "  tasks   "
            f"{counts['completed']}/{total} completed · {counts['running']} running · "
            f"{counts['failed']} failed · {counts['blocked']} blocked"
        )
        out.append("")
        out.append("AGENTS")
        agents = {}
        for task in self.tasks.values():
            agents.setdefault(task.get("agent") or "unknown", []).append(task)
        if not agents:
            out.append("  (waiting for a run)")
        for agent, tasks in agents.items():
            statuses = ", ".join(sorted({str(task.get("status") or "pending") for task in tasks}))
            ids = ", ".join(str(task.get("id")) for task in tasks[:4])
            out.append(f"  {agent:<12} {statuses:<20} {ids}")
        out.append("")
        out.append("TASKS")
        shown = 0
        for task in self.tasks.values():
            if shown >= max_tasks:
                out.append(f"  ... {max(0, total - max_tasks)} more")
                break
            deps = f" <- {','.join(task.get('depends_on') or [])}" if task.get("depends_on") else ""
            verification = f" · {task.get('verification')}" if task.get("verification") else ""
            line = (
                f"  {_mark(str(task.get('status') or 'pending'))} {task.get('id'):<4} "
                f"{str(task.get('agent') or ''):<11} {_short(task.get('title'), width - 38)}{deps}{verification}"
            )
            out.append(line[:width])
            shown += 1
        if not self.tasks:
            out.append("  (no tasks yet)")
        out.append("")
        out.append("LIVE ACTIVITY")
        if not self.activity:
            out.append("  (no events yet)")
        for item in list(self.activity)[-8:]:
            out.append(f"  {item['kind']:<8} {_short(item['text'], width - 12)}")
        out.append("")
        out.append("RESOURCES")
        usage = self.usage or {}
        tokens = usage.get("total", 0)
        out.append(f"  model   {self.provider or '-'} / {self.model or '-'}")
        out.append(f"  tokens  {tokens}")
        if self.routing:
            reason = _short(self.routing.get("reason") or "", width - 14)
            out.append(
                f"  route   {self.routing.get('provider', '')}/"
                f"{self.routing.get('model', '')} · {reason}"
            )
        return [line[:width] for line in out]
