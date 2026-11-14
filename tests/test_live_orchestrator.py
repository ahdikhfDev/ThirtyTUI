"""Opt-in live tests: real provider, real planner, real orchestrator.

Skipped unless THIRTYCLI_LIVE=1, so the default suite and CI stay offline:

    THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
        pytest tests/test_live_orchestrator.py -q

Every run is sandboxed: cwd and THIRTYCLI_HOME live under ``tmp_path``, and the
orchestrator runs with ``persist=False``. Nothing is written to the real repo.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from thirtycli.chat import stream_answer
from thirtycli.config import Config, load
from thirtycli.models import api_key_for, build_model, env_var_for
from thirtycli.orchestrator import Orchestrator

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("THIRTYCLI_LIVE") != "1", reason="THIRTYCLI_LIVE not set"),
]

PROVIDER = os.getenv("THIRTYCLI_PROVIDER", "deepseek")
MODEL = os.getenv("THIRTYCLI_MODEL", "deepseek-chat")

# Read the user config before the sandbox fixture repoints THIRTYCLI_HOME, so a key
# saved with `thirtycli config set api_key` can drive these tests too.
SAVED = load()

GOAL = "List the files in this workspace and summarize the project structure in two lines"

# A write-capable worker is exercised by ``test_live_coding_worker_writes_a_fix``
# below: fixed planner, one task, permission "autonomous" and an approver that says
# yes, so the model must actually edit the file inside the sandboxed workspace.


def _label() -> str:
    return f"{PROVIDER} · {MODEL}"


@pytest.fixture
def sandbox(tmp_path, monkeypatch) -> Path:
    """A throwaway workspace plus a throwaway THIRTYCLI_HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("THIRTYCLI_HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    return workspace


@pytest.fixture
def live_model(sandbox):
    cfg = Config(provider=PROVIDER, model=MODEL)
    if SAVED.provider == PROVIDER:
        cfg.api_key = SAVED.api_key
        cfg.base_url = SAVED.base_url
    if api_key_for(cfg.provider, cfg) is None:
        pytest.skip(
            f"{PROVIDER}: no API key in the environment or config "
            f"(set {env_var_for(PROVIDER)} or pick another THIRTYCLI_PROVIDER)"
        )
    return build_model(cfg)


def _text(message) -> str:
    """Flatten message content, which may be a string or a list of blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else str(block.get("text", "")) for block in content
        )
    return str(content)


def _snapshot(root: Path) -> set[str]:
    return {str(path.relative_to(root)) for path in root.rglob("*")}


class _Collector:
    """Minimal emitter: captures streamed deltas without touching a console."""

    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.ended = False

    def assistant_delta(self, text: str) -> None:
        self.deltas.append(text)

    def assistant_end(self) -> None:
        self.ended = True


def test_live_model_responds(live_model):
    began = time.perf_counter()
    reply = live_model.invoke("Reply with the single word: ok")
    seconds = time.perf_counter() - began
    text = _text(reply).strip()
    assert text, f"{_label()} · {seconds:.1f}s · outcome=empty reply"


def test_live_stream_answer_emits_chunks(live_model):
    collector = _Collector()
    messages = [HumanMessage(content="Count from one to five, digits only, separated by spaces.")]
    began = time.perf_counter()
    reply, _usage = stream_answer(live_model, messages, [], collector)
    seconds = time.perf_counter() - began
    text = _text(reply).strip()
    outcome = f"{len(collector.deltas)} chunk(s) · {len(text)} chars"
    assert collector.deltas, f"{_label()} · {seconds:.1f}s · outcome={outcome} (no chunks)"
    assert text, f"{_label()} · {seconds:.1f}s · outcome={outcome} (empty assembled text)"
    assert collector.ended, f"{_label()} · {seconds:.1f}s · outcome={outcome} (assistant_end missing)"


def test_live_orchestrator_read_only_run(live_model, sandbox):
    (sandbox / "app.py").write_text("def main():\n    print('hello')\n", encoding="utf-8")
    (sandbox / "README.md").write_text(
        "# Demo workspace\n\nA tiny project created by the live test.\n", encoding="utf-8"
    )
    package = sandbox / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "util.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    before = _snapshot(sandbox)

    orchestrator = Orchestrator(model=live_model, root=sandbox, workers=2, persist=False)
    began = time.perf_counter()
    final = orchestrator.run(GOAL)
    seconds = time.perf_counter() - began

    board = orchestrator.board
    assert board is not None, f"{_label()} · {seconds:.1f}s · outcome=no task board"
    assert board.tasks, f"{_label()} · {seconds:.1f}s · outcome=planner returned no tasks"

    completed = board.completed_count
    statuses = sorted({task.status.value for task in board.tasks})
    outcome = f"{len(board.tasks)} task(s) · {completed} completed · statuses={statuses}"
    trace = [f"{event['event']}:{event.get('task', '')}" for event in orchestrator.trace]
    assert completed >= 1, (
        f"{_label()} · {seconds:.1f}s · outcome={outcome} · final={str(final)[:120]!r} · "
        f"errors={[task.error for task in board.tasks if task.error]} · trace={trace}"
    )

    # Read-only workers must not create, edit or delete anything in the workspace.
    assert _snapshot(sandbox) == before, (
        f"{_label()} · {seconds:.1f}s · outcome={outcome} · read-only workers changed the workspace"
    )

    # persist=False must not write task state into THIRTYCLI_HOME either.
    home = Path(os.environ["THIRTYCLI_HOME"])
    assert not (home / "tasks").exists(), (
        f"{_label()} · {seconds:.1f}s · outcome={outcome} · persist=False still wrote task state"
    )


def test_live_coding_worker_writes_a_fix(live_model, sandbox):
    """A real model must edit a real file and pass an independent test run."""
    (sandbox / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (sandbox / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_adds():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    def planner(_goal: str, _limit: int) -> list[dict]:
        return [
            {
                "id": "t1",
                "title": "Fix add()",
                "description": (
                    "calc.py defines add(a, b) as a - b, so test_calc.py fails. Fix calc.py so "
                    "the test passes. Do not edit test_calc.py."
                ),
                "agent": "coder",
                "depends_on": [],
                "max_retries": 0,
            }
        ]

    orchestrator = Orchestrator(
        model=live_model,
        root=sandbox,
        workers=1,
        coding=True,
        permission="autonomous",
        approver=lambda _question: True,
        persist=False,
        memory=False,
        route=False,
        verify=True,
        planner=planner,
        max_tasks=1,
        max_retries=0,
    )
    began = time.perf_counter()
    orchestrator.run("Fix add() in calc.py so the test passes")
    seconds = time.perf_counter() - began

    board = orchestrator.board
    assert board is not None, f"{_label()} · {seconds:.1f}s · outcome=no task board"
    task = board.tasks[0]
    usage = dict(board.usage or {})
    verification = dict(task.verification or {})
    check = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=sandbox,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    tail = " | ".join((check.stdout or "").strip().splitlines()[-2:])
    outcome = (
        f"status={task.status.value} · verification={verification.get('status')} · "
        f"usage={usage} · {seconds:.1f}s"
    )
    assert check.returncode == 0, (
        f"{_label()} · {outcome} · outcome=failing tests after the worker ran · {tail} · "
        f"error={task.error!r}"
    )
    assert task.status.value == "completed", (
        f"{_label()} · {outcome} · outcome=task not completed · error={task.error!r}"
    )
    assert verification.get("status") in {"verified", "verified_with_warnings"}, (
        f"{_label()} · {outcome} · outcome=verification did not confirm the change"
    )
    assert int(usage.get("total", 0)) > 0, f"{_label()} · {outcome} · outcome=no token usage recorded"
