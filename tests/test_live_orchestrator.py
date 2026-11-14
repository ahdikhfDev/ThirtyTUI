"""Opt-in live tests: real provider, real planner, real orchestrator.

Skipped unless THIRTYCLI_LIVE=1, so the default suite and CI stay offline:

    THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
        pytest tests/test_live_orchestrator.py -q

Every run is sandboxed: cwd and THIRTYCLI_HOME live under ``tmp_path``, and the
orchestrator runs with ``persist=False``. Nothing is written to the real repo.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage

from thirtycli.chat import stream_answer
from thirtycli.config import Config
from thirtycli.models import api_key_for, build_model, env_var_for
from thirtycli.orchestrator import Orchestrator

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("THIRTYCLI_LIVE") != "1", reason="THIRTYCLI_LIVE not set"),
]

PROVIDER = os.getenv("THIRTYCLI_PROVIDER", "deepseek")
MODEL = os.getenv("THIRTYCLI_MODEL", "deepseek-chat")

GOAL = "List the files in this workspace and summarize the project structure in two lines"

# coding-worker live test pending integration: Orchestrator exposes model, root,
# workers, max_steps, max_retries, max_tasks, planner, runner, on_event, cancel,
# persist and task_dir only. There is no coding= or permission= parameter yet, and
# default workers always get build_readonly_tools(). Add a live write test here
# once a write-capable worker and an approval mode exist.


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
    if api_key_for(cfg.provider, cfg) is None:
        pytest.skip(
            f"{PROVIDER}: no API key in the environment "
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
