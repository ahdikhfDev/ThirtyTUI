"""Built-in repository-level coding tasks for real-world validation.

Every task is a small but realistic repository snapshot plus a pytest check that
fails on the snapshot and passes once the task is solved. Ten categories are
covered: bug fix, feature, API, test writing, refactoring, debugging, a
multi-file move, argparse, a validating config loader and a JSON serializer.

The reference fixes live in ``SOLUTIONS`` at the bottom of this module and are
kept OUT of ``TaskSpec.metadata`` on purpose: ``benchmarks/adapters.py`` pastes
metadata into the model prompt, so answers stored there would be handed to the
agent under test. Answers belong in ``SOLUTIONS`` only.
"""

from __future__ import annotations

import sys

from .models import TaskSpec

DATASET = "repo"
CHECK = f"{sys.executable} -m pytest -q"

# -- 1. bug fix: money parsing -------------------------------------------------

_AMOUNT_FILES = {
    "money.py": r'''"""Money helpers used by the invoice reporter."""


def parse_amount(text: str) -> int:
    """Parse a decimal amount such as "1,234.50" into whole cents."""
    cleaned = text.strip().replace(",", "")
    return int(float(cleaned) * 100)


def total_cents(amounts: list[str]) -> int:
    """Sum decimal amount strings, in cents."""
    return sum(parse_amount(amount) for amount in amounts)
''',
    "test_money.py": r'''from money import parse_amount, total_cents


def test_parses_whole_amounts():
    assert parse_amount("12.34") == 1234


def test_parses_grouping_separators():
    assert parse_amount("1,234.50") == 123450


def test_parses_exact_cents():
    assert parse_amount("0.29") == 29
    assert parse_amount("19.99") == 1999


def test_totals_are_exact():
    assert total_cents(["0.10", "0.20", "19.99"]) == 2029
''',
}

_AMOUNT_FIX = {
    "money.py": r'''"""Money helpers used by the invoice reporter."""

from decimal import Decimal


def parse_amount(text: str) -> int:
    """Parse a decimal amount such as "1,234.50" into whole cents."""
    cleaned = text.strip().replace(",", "")
    return int(Decimal(cleaned) * 100)


def total_cents(amounts: list[str]) -> int:
    """Sum decimal amount strings, in cents."""
    return sum(parse_amount(amount) for amount in amounts)
''',
}

# -- 2. feature: interval helpers ---------------------------------------------

_INTERVAL_FILES = {
    "intervals.py": r'''"""Interval helpers for the scheduling report."""


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    raise NotImplementedError("merge_intervals is not implemented")


def total_covered(intervals: list[tuple[int, int]]) -> int:
    raise NotImplementedError("total_covered is not implemented")


def gaps(
    intervals: list[tuple[int, int]], start: int, end: int
) -> list[tuple[int, int]]:
    raise NotImplementedError("gaps is not implemented")
''',
    "test_intervals.py": r'''from intervals import gaps, merge_intervals, total_covered


def test_merges_overlapping():
    assert merge_intervals([(1, 4), (3, 6)]) == [(1, 6)]


def test_merges_touching():
    assert merge_intervals([(1, 2), (2, 5)]) == [(1, 5)]


def test_sorts_and_keeps_disjoint():
    assert merge_intervals([(10, 12), (1, 3)]) == [(1, 3), (10, 12)]


def test_empty_input():
    assert merge_intervals([]) == []


def test_total_covered_ignores_overlap():
    assert total_covered([(1, 4), (3, 6), (20, 22)]) == 7


def test_gaps_between_busy_blocks():
    assert gaps([(2, 4), (6, 7)], 0, 10) == [(0, 2), (4, 6), (7, 10)]


def test_gaps_when_nothing_is_busy():
    assert gaps([], 5, 8) == [(5, 8)]
''',
}

_INTERVAL_FIX = {
    "intervals.py": r'''"""Interval helpers for the scheduling report."""

Interval = tuple[int, int]


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def total_covered(intervals: list[Interval]) -> int:
    return sum(end - start for start, end in merge_intervals(intervals))


def gaps(intervals: list[Interval], start: int, end: int) -> list[Interval]:
    result: list[Interval] = []
    cursor = start
    for busy_start, busy_end in merge_intervals(intervals):
        if busy_start > cursor:
            result.append((cursor, min(busy_start, end)))
        cursor = max(cursor, busy_end)
        if cursor >= end:
            break
    if cursor < end:
        result.append((cursor, end))
    return result
''',
}

# -- 3. API: request router ----------------------------------------------------

_ROUTER_FILES = {
    "router.py": r'''"""Minimal request router used by the internal HTTP layer."""


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, str, object]] = []

    def add(self, method: str, path: str, handler) -> None:
        raise NotImplementedError("Router.add is not implemented")

    def dispatch(self, method: str, path: str, body: str = "") -> tuple[int, dict, str]:
        raise NotImplementedError("Router.dispatch is not implemented")
''',
    "test_router.py": r'''import json

from router import Router


def build() -> Router:
    router = Router()

    def list_items(params, body):
        return 200, {"content-type": "application/json"}, json.dumps({"items": ["a", "b"]})

    def get_item(params, body):
        return 200, {"content-type": "application/json"}, json.dumps({"id": params["id"]})

    def create_item(params, body):
        payload = json.loads(body or "{}")
        return 201, {"content-type": "application/json"}, json.dumps({"created": payload.get("name")})

    router.add("GET", "/items", list_items)
    router.add("GET", "/items/{id}", get_item)
    router.add("POST", "/items", create_item)
    return router


def test_static_route():
    status, headers, body = build().dispatch("GET", "/items")
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert json.loads(body) == {"items": ["a", "b"]}


def test_parameter_route():
    status, _headers, body = build().dispatch("GET", "/items/42")
    assert status == 200
    assert json.loads(body) == {"id": "42"}


def test_unknown_path_is_404():
    status, _headers, body = build().dispatch("GET", "/nope")
    assert status == 404
    assert body == "not found"


def test_wrong_method_is_405():
    status, _headers, body = build().dispatch("DELETE", "/items")
    assert status == 405
    assert body == "method not allowed"


def test_body_reaches_the_handler():
    status, _headers, body = build().dispatch("POST", "/items", '{"name": "pen"}')
    assert status == 201
    assert json.loads(body) == {"created": "pen"}
''',
}

_ROUTER_FIX = {
    "router.py": r'''"""Minimal request router used by the internal HTTP layer."""

Route = tuple[str, str, object]


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []

    def add(self, method: str, path: str, handler) -> None:
        self.routes.append((method.upper(), path, handler))

    def dispatch(self, method: str, path: str, body: str = "") -> tuple[int, dict, str]:
        wanted = method.upper()
        path_matched = False
        for route_method, route_path, handler in self.routes:
            params = _match(route_path, path)
            if params is None:
                continue
            path_matched = True
            if route_method == wanted:
                return handler(params, body)
        if path_matched:
            return 405, {}, "method not allowed"
        return 404, {}, "not found"


def _match(route_path: str, path: str) -> dict | None:
    route_parts = [part for part in route_path.split("/") if part]
    path_parts = [part for part in path.split("/") if part]
    if len(route_parts) != len(path_parts):
        return None
    params = {}
    for route_part, path_part in zip(route_parts, path_parts):
        if route_part.startswith("{") and route_part.endswith("}"):
            params[route_part[1:-1]] = path_part
        elif route_part != path_part:
            return None
    return params
''',
}

# -- 4. test writing ----------------------------------------------------------

_SLUG_GIVEN_FILES = {
    "slug.py": r'''"""Slug helpers used by the article index."""

import re

JOINER = "-"


def slugify(text: str) -> str:
    lowered = text.lower()
    joined = re.sub(r"[^a-z0-9]+", JOINER, lowered)
    return joined.strip(JOINER)


def truncate_slug(text: str, limit: int = 20) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    return slug[:limit].rstrip(JOINER)
''',
    "check_tests.py": r'''"""Grading harness for the test-writing task.

The candidate tests must pass against the reference slug.py and fail against
every buggy variant below. A test suite that keeps passing on a buggy module
proves nothing, so that case exits non-zero with a clear reason.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATE = "test_slug.py"

MUTANTS = [
    (
        "text is never lowercased",
        """import re

JOINER = "-"


def slugify(text: str) -> str:
    lowered = text
    joined = re.sub(r"[^a-z0-9]+", JOINER, lowered)
    return joined.strip(JOINER)


def truncate_slug(text: str, limit: int = 20) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    return slug[:limit].rstrip(JOINER)
""",
    ),
    (
        "separator is an underscore",
        """import re

JOINER = "_"


def slugify(text: str) -> str:
    lowered = text.lower()
    joined = re.sub(r"[^a-z0-9]+", JOINER, lowered)
    return joined.strip(JOINER)


def truncate_slug(text: str, limit: int = 20) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    return slug[:limit].rstrip(JOINER)
""",
    ),
    (
        "edges are not trimmed",
        """import re

JOINER = "-"


def slugify(text: str) -> str:
    lowered = text.lower()
    joined = re.sub(r"[^a-z0-9]+", JOINER, lowered)
    return joined


def truncate_slug(text: str, limit: int = 20) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    return slug[:limit].rstrip(JOINER)
""",
    ),
    (
        "truncation keeps a trailing separator",
        """import re

JOINER = "-"


def slugify(text: str) -> str:
    lowered = text.lower()
    joined = re.sub(r"[^a-z0-9]+", JOINER, lowered)
    return joined.strip(JOINER)


def truncate_slug(text: str, limit: int = 20) -> str:
    slug = slugify(text)
    if len(slug) <= limit:
        return slug
    return slug[:limit]
""",
    ),
]

PYTEST = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", CANDIDATE]


def run_pytest(workdir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        PYTEST,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=120,
    )


def report_failure(message: str) -> int:
    print(f"CHECK FAILED: {message}")
    return 1


def main() -> int:
    candidate = HERE / CANDIDATE
    if not candidate.is_file():
        return report_failure(f"{CANDIDATE} is missing; write pytest tests for slug.py")
    if not candidate.read_text(encoding="utf-8", errors="replace").strip():
        return report_failure(f"{CANDIDATE} is empty; write pytest tests for slug.py")

    reference = run_pytest(HERE)
    if reference.returncode != 0:
        print(reference.stdout)
        print(reference.stderr)
        return report_failure("the tests fail against the reference slug.py")

    for name, source in MUTANTS:
        with tempfile.TemporaryDirectory() as raw:
            workdir = Path(raw)
            (workdir / "slug.py").write_text(source, encoding="utf-8")
            shutil.copyfile(candidate, workdir / CANDIDATE)
            mutated = run_pytest(workdir)
        if mutated.returncode == 0:
            print(mutated.stdout)
            return report_failure(f"the tests still pass with a bug injected: {name}")

    print(f"CHECK PASSED: tests fail on {len(MUTANTS)} injected bugs and pass on slug.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''',
}

_SLUG_TESTS_FIX = {
    "test_slug.py": r'''from slug import slugify, truncate_slug


def test_lowercases_text():
    assert slugify("Hello World") == "hello-world"


def test_collapses_runs_of_separators():
    assert slugify("Multiple   Spaces!!Here") == "multiple-spaces-here"


def test_trims_edge_separators():
    assert slugify("  --Hello, World!--  ") == "hello-world"


def test_keeps_digits():
    assert slugify("Release 2.0 Notes") == "release-2-0-notes"


def test_short_slug_is_not_truncated():
    assert truncate_slug("alpha beta") == "alpha-beta"


def test_truncation_drops_the_trailing_separator():
    assert truncate_slug("alpha beta gamma", limit=11) == "alpha-beta"


def test_truncation_respects_the_limit():
    assert truncate_slug("alpha beta gamma", limit=5) == "alpha"
''',
}

# -- 5. refactoring: extract a duplicated helper ------------------------------

_VALIDATION_FILES = {
    "users.py": r'''"""User registration helpers."""


def validate_email(value: str) -> str | None:
    if not value or value.count("@") != 1:
        return "email must contain exactly one @"
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        return "email domain must contain a dot"
    return None


def register_user(email: str, name: str) -> tuple[bool, str]:
    error = validate_email(email)
    if error is not None:
        return False, error
    if not name.strip():
        return False, "name is required"
    return True, f"registered {name.strip()} <{email}>"
''',
    "orders.py": r'''"""Order creation helpers."""


def validate_email(value: str) -> str | None:
    if not value or value.count("@") != 1:
        return "email must contain exactly one @"
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        return "email domain must contain a dot"
    return None


def create_order(email: str, sku: str, quantity: int = 1) -> tuple[bool, str]:
    error = validate_email(email)
    if error is not None:
        return False, error
    if quantity < 1:
        return False, "quantity must be positive"
    return True, f"order {sku} x{quantity} for {email}"
''',
    "test_users.py": r'''from users import register_user


def test_accepts_a_valid_signup():
    assert register_user("ada@example.com", "Ada") == (True, "registered Ada <ada@example.com>")


def test_rejects_a_bad_address():
    assert register_user("ada", "Ada") == (False, "email must contain exactly one @")


def test_rejects_a_missing_domain_dot():
    assert register_user("ada@localhost", "Ada") == (False, "email domain must contain a dot")


def test_rejects_an_empty_name():
    assert register_user("ada@example.com", "   ") == (False, "name is required")
''',
    "test_orders.py": r'''from orders import create_order


def test_accepts_a_valid_order():
    assert create_order("grace@example.com", "pencil", 3) == (True, "order pencil x3 for grace@example.com")


def test_rejects_a_bad_address():
    assert create_order("grace", "pencil") == (False, "email must contain exactly one @")


def test_rejects_a_bad_quantity():
    assert create_order("grace@example.com", "pencil", 0) == (False, "quantity must be positive")
''',
    "test_structure.py": r'''from pathlib import Path

import orders
import users
import validation


def test_validation_module_holds_the_helper():
    assert validation.validate_email("ada@example.com") is None
    assert validation.validate_email("ada") == "email must contain exactly one @"
    assert validation.validate_email("ada@localhost") == "email domain must contain a dot"


def test_both_modules_share_the_same_helper():
    assert users.validate_email is validation.validate_email
    assert orders.validate_email is validation.validate_email


def test_the_duplicate_definition_is_gone():
    for name in ("users.py", "orders.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "def validate_email" not in source, name
''',
}

_VALIDATION_FIX = {
    "validation.py": r'''"""Shared validation helpers for users and orders."""


def validate_email(value: str) -> str | None:
    if not value or value.count("@") != 1:
        return "email must contain exactly one @"
    local, _, domain = value.partition("@")
    if not local or "." not in domain:
        return "email domain must contain a dot"
    return None
''',
    "users.py": r'''"""User registration helpers."""

from validation import validate_email


def register_user(email: str, name: str) -> tuple[bool, str]:
    error = validate_email(email)
    if error is not None:
        return False, error
    if not name.strip():
        return False, "name is required"
    return True, f"registered {name.strip()} <{email}>"
''',
    "orders.py": r'''"""Order creation helpers."""

from validation import validate_email


def create_order(email: str, sku: str, quantity: int = 1) -> tuple[bool, str]:
    error = validate_email(email)
    if error is not None:
        return False, error
    if quantity < 1:
        return False, "quantity must be positive"
    return True, f"order {sku} x{quantity} for {email}"
''',
}

# -- 6. debugging: state shared between rounds ---------------------------------

_QUEUE_FILES = {
    "tasks.py": r'''"""In-memory job queue used by the worker."""

PENDING = []


def enqueue(job):
    PENDING.append(job)
    return len(PENDING)


def take(count):
    batch = []
    for _ in range(count):
        batch.append(PENDING.pop(0))
    return batch
''',
    "worker.py": r'''from tasks import enqueue, take


def run_round(jobs, size=2):
    for job in jobs:
        enqueue(job)
    return take(size)
''',
    "test_worker.py": r'''from worker import run_round


def test_round_processes_its_jobs():
    assert run_round(["a", "b"]) == ["a", "b"]


def test_round_with_one_job():
    assert run_round(["solo"]) == ["solo"]


def test_round_honours_size():
    assert run_round(["a", "b", "c"], size=1) == ["a"]


def test_rounds_do_not_share_state():
    assert run_round(["a"]) == ["a"]
    assert run_round(["b"]) == ["b"]


def test_empty_round_returns_nothing():
    assert run_round([]) == []
''',
}

_QUEUE_FIX = {
    "tasks.py": r'''"""In-memory job queue used by the worker."""


class JobQueue:
    def __init__(self):
        self._pending = []

    def enqueue(self, job):
        self._pending.append(job)
        return len(self._pending)

    def take(self, count):
        batch = []
        while self._pending and len(batch) < count:
            batch.append(self._pending.pop(0))
        return batch

    def pending(self):
        return list(self._pending)
''',
    "worker.py": r'''from tasks import JobQueue


def run_round(jobs, size=2):
    queue = JobQueue()
    for job in jobs:
        queue.enqueue(job)
    return queue.take(size)
''',
}

# -- 7. multi-file move -------------------------------------------------------

_MOVE_FILES = {
    "formatting.py": r'''"""Display helpers shared by the command line and the report."""


def format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
''',
    "cli.py": r'''"""Command line helpers."""

from formatting import format_duration


def describe_runtime(total_seconds: int) -> str:
    return f"runtime: {format_duration(total_seconds)}"
''',
    "report.py": r'''"""Report rendering helpers."""

from formatting import format_bytes, format_duration


def summary_line(elapsed: int, size: int) -> str:
    return f"{format_duration(elapsed)} / {format_bytes(size)}"
''',
    "test_cli.py": r'''from cli import describe_runtime


def test_describes_seconds():
    assert describe_runtime(45) == "runtime: 45s"


def test_describes_hours():
    assert describe_runtime(3661) == "runtime: 1h 1m 1s"
''',
    "test_report.py": r'''from report import summary_line


def test_combines_duration_and_size():
    assert summary_line(90, 2048) == "1m 30s / 2.0KB"


def test_zero_seconds_is_explicit():
    assert summary_line(0, 512) == "0s / 512B"
''',
    "test_move.py": r'''from pathlib import Path

import cli
import formatting
import report
import timeutil


def test_duration_formatter_has_a_new_home():
    assert timeutil.humanize_duration(90) == "1m 30s"


def test_old_name_is_gone():
    assert not hasattr(formatting, "format_duration")


def test_other_formatters_stay_put():
    assert formatting.format_bytes(2048) == "2.0KB"


def test_callers_use_the_new_module():
    assert cli.describe_runtime(3661) == "runtime: 1h 1m 1s"
    assert report.summary_line(90, 2048) == "1m 30s / 2.0KB"


def test_callers_import_the_new_module():
    for name in ("cli.py", "report.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "from timeutil import" in source, name
        assert "format_duration" not in source, name
''',
}

_MOVE_FIX = {
    "timeutil.py": r'''"""Time formatting helpers."""


def humanize_duration(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, int(total_seconds)), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)
''',
    "formatting.py": r'''"""Display helpers shared by the command line and the report."""


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{int(value)}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"
''',
    "cli.py": r'''"""Command line helpers."""

from timeutil import humanize_duration


def describe_runtime(total_seconds: int) -> str:
    return f"runtime: {humanize_duration(total_seconds)}"
''',
    "report.py": r'''"""Report rendering helpers."""

from formatting import format_bytes
from timeutil import humanize_duration


def summary_line(elapsed: int, size: int) -> str:
    return f"{humanize_duration(elapsed)} / {format_bytes(size)}"
''',
}

# -- 8. bug fix in argparse ---------------------------------------------------

_ARGPARSE_FILES = {
    "tool.py": r'''"""Command line front end for the batch runner."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="batch")
    parser.add_argument("--jobs", type=int, default=4, help="maximum number of workers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one batch")
    run.add_argument("--limit", default="4", help="cap the number of workers")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        workers = min(args.jobs, args.limit)
        print(f"workers={workers}")
        return 0
    return 2
''',
    "test_tool.py": r'''import pytest

from tool import main


def test_default_run(capsys):
    assert main(["run"]) == 0
    assert "workers=4" in capsys.readouterr().out


def test_limit_caps_workers(capsys):
    assert main(["run", "--limit", "2"]) == 0
    assert "workers=2" in capsys.readouterr().out


def test_jobs_flag_lowers_workers(capsys):
    assert main(["--jobs", "2", "run"]) == 0
    assert "workers=2" in capsys.readouterr().out


def test_limit_cannot_exceed_jobs(capsys):
    assert main(["--jobs", "2", "run", "--limit", "8"]) == 0
    assert "workers=2" in capsys.readouterr().out


def test_non_integer_limit_is_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--limit", "many"])
    assert "invalid int value" in capsys.readouterr().err
''',
}

_ARGPARSE_FIX = {
    "tool.py": r'''"""Command line front end for the batch runner."""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="batch")
    parser.add_argument("--jobs", type=int, default=4, help="maximum number of workers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one batch")
    run.add_argument("--limit", type=int, default=4, help="cap the number of workers")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        workers = min(args.jobs, args.limit)
        print(f"workers={workers}")
        return 0
    return 2
''',
}

# -- 9. feature: validating config loader -------------------------------------

_CONFIG_FILES = {
    "config.py": r'''"""Configuration loading for the service."""


class ConfigError(ValueError):
    """Raised when a configuration document is invalid."""


def load_config_text(text: str) -> dict:
    raise NotImplementedError("load_config_text is not implemented")


def load_config(path) -> dict:
    raise NotImplementedError("load_config is not implemented")
''',
    "test_config.py": r'''import pytest

from config import ConfigError, load_config, load_config_text


def test_loads_a_minimal_document():
    assert load_config_text('{"host": "localhost", "port": 8080}') == {
        "host": "localhost",
        "port": 8080,
        "workers": 1,
        "debug": False,
    }


def test_keeps_the_optional_values():
    config = load_config_text('{"host": "h", "port": 1, "workers": 4, "debug": true}')
    assert config["workers"] == 4
    assert config["debug"] is True


def test_invalid_json_is_named():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text("{oops")
    assert "invalid JSON" in str(excinfo.value)


def test_non_object_document_is_rejected():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text("[1, 2]")
    assert "must be an object" in str(excinfo.value)


def test_missing_key_is_reported():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "h"}')
    message = str(excinfo.value)
    assert "missing required key" in message
    assert "port" in message


def test_wrong_type_names_the_key():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "h", "port": "eighty"}')
    assert "'port' must be an integer" in str(excinfo.value)


def test_port_range_is_enforced():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "h", "port": 70000}')
    assert "out of range" in str(excinfo.value)


def test_empty_host_is_rejected():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "   ", "port": 80}')
    assert "'host' must be a non-empty string" in str(excinfo.value)


def test_workers_must_be_positive():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "h", "port": 80, "workers": 0}')
    assert "'workers' must be at least 1" in str(excinfo.value)


def test_unknown_key_is_reported():
    with pytest.raises(ConfigError) as excinfo:
        load_config_text('{"host": "h", "port": 80, "cache": true}')
    assert "unknown key 'cache'" in str(excinfo.value)


def test_load_config_reads_a_file(tmp_path):
    path = tmp_path / "service.json"
    path.write_text('{"host": "h", "port": 80}', encoding="utf-8")
    assert load_config(path)["port"] == 80


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(tmp_path / "absent.json")
    assert "cannot read" in str(excinfo.value)
''',
}

_CONFIG_FIX = {
    "config.py": r'''"""Configuration loading for the service."""

import json
from pathlib import Path

_OPTIONAL = {"workers": 1, "debug": False}


class ConfigError(ValueError):
    """Raised when a configuration document is invalid."""


def load_config_text(text: str) -> dict:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON: {exc.msg} at line {exc.lineno}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config document must be an object")
    for key in sorted(raw):
        if key not in ("host", "port") and key not in _OPTIONAL:
            raise ConfigError(f"unknown key '{key}'")
    if "host" not in raw:
        raise ConfigError("missing required key 'host'")
    host = raw["host"]
    if not isinstance(host, str) or not host.strip():
        raise ConfigError("'host' must be a non-empty string")
    if "port" not in raw:
        raise ConfigError("missing required key 'port'")
    port = raw["port"]
    if isinstance(port, bool) or not isinstance(port, int):
        raise ConfigError(f"'port' must be an integer, got {type(port).__name__}")
    if not 1 <= port <= 65535:
        raise ConfigError(f"'port' out of range: {port}")
    workers = raw.get("workers", _OPTIONAL["workers"])
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ConfigError(f"'workers' must be at least 1, got {workers!r}")
    debug = raw.get("debug", _OPTIONAL["debug"])
    if not isinstance(debug, bool):
        raise ConfigError("'debug' must be a boolean")
    return {"host": host, "port": port, "workers": workers, "debug": debug}


def load_config(path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from exc
    return load_config_text(text)
''',
}

# -- 10. API: JSON serializer -------------------------------------------------

_SERIALIZER_FILES = {
    "serializer.py": r'''"""Deterministic JSON serialization for cache payloads."""


def dumps(value: object) -> str:
    raise NotImplementedError("dumps is not implemented")
''',
    "test_serializer.py": r'''import json

import pytest

from serializer import dumps


def test_empty_containers():
    assert dumps({}) == "{}"
    assert dumps([]) == "[]"


def test_scalars():
    assert dumps(True) == "true"
    assert dumps(False) == "false"
    assert dumps(None) == "null"
    assert dumps(7) == "7"
    assert dumps(1.5) == "1.5"


def test_keys_are_sorted():
    assert dumps({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_nested_structures():
    payload = {"users": [{"name": "ada", "tags": ["x", "y"]}, {}], "total": 2}
    assert dumps(payload) == '{"total":2,"users":[{"name":"ada","tags":["x","y"]},{}]}'


def test_strings_are_escaped():
    assert dumps('a"b\\c') == '"a\\"b\\\\c"'


def test_control_characters_are_escaped():
    assert dumps("line\nbreak\ttab") == '"line\\nbreak\\ttab"'


def test_other_control_characters_use_u_escapes():
    assert dumps("\x01") == '"\\u0001"'


def test_non_ascii_is_preserved():
    assert dumps({"name": "Zo\u00eb"}) == '{"name":"Zo\u00eb"}'


def test_tuples_become_arrays():
    assert dumps((1, 2)) == "[1,2]"


def test_output_round_trips_through_json():
    payload = {"b": [1, 2.5, None, True], "a": {"nested": ["x"], "empty": {}}}
    assert json.loads(dumps(payload)) == payload


def test_non_string_keys_are_rejected():
    with pytest.raises(TypeError):
        dumps({1: "value"})


def test_unsupported_values_are_rejected():
    with pytest.raises(TypeError):
        dumps(object())
''',
}

_SERIALIZER_FIX = {
    "serializer.py": r'''"""Deterministic JSON serialization for cache payloads."""

_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\b": "\\b",
    "\f": "\\f",
}


def dumps(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError("cannot serialize a non-finite float")
        return repr(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise TypeError("keys must be strings")
        pairs = (f"{_quote(key)}:{dumps(value[key])}" for key in sorted(value))
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _quote(text: str) -> str:
    parts = ['"']
    for char in text:
        if char in _ESCAPES:
            parts.append(_ESCAPES[char])
        elif ord(char) < 0x20:
            parts.append(f"\\u{ord(char):04x}")
        else:
            parts.append(char)
    parts.append('"')
    return "".join(parts)
''',
}


def _meta(kind: str) -> dict:
    return {"dataset": DATASET, "kind": kind, "language": "python"}


def repo_tasks() -> list[TaskSpec]:
    """Ten repository-level tasks, each with a failing snapshot and a check."""
    return [
        TaskSpec(
            id="repo-amount-parsing",
            suite="coding",
            prompt=(
                "money.py converts decimal amount strings into whole cents for the invoice "
                "reporter, and test_money.py is failing: the reported total is off by one cent "
                "for amounts such as 0.29 and 19.99. Make parse_amount() and total_cents() "
                "return exact cents for any amount string with at most two decimals, including "
                "values that use thousands separators. Do not change the tests; the fix belongs "
                "in money.py."
            ),
            files=dict(_AMOUNT_FILES),
            check_command=CHECK,
            expected_files=("money.py",),
            timeout=180,
            metadata=_meta("bugfix"),
        ),
        TaskSpec(
            id="repo-interval-merge",
            suite="coding",
            prompt=(
                "intervals.py backs the scheduling report and holds three stubs. Implement them: "
                "merge_intervals() collapses overlapping and touching half-open intervals and "
                "returns the result sorted by start; total_covered() returns the number of units "
                "covered after merging; gaps(intervals, start, end) returns the uncovered "
                "half-open ranges inside [start, end) in ascending order. test_intervals.py "
                "pins the expected behavior; make it pass without editing it."
            ),
            files=dict(_INTERVAL_FILES),
            check_command=CHECK,
            expected_files=("intervals.py",),
            timeout=180,
            metadata=_meta("feature"),
        ),
        TaskSpec(
            id="repo-request-router",
            suite="coding",
            prompt=(
                "router.py is the request router of the internal HTTP layer; its two methods are "
                "stubs. Implement Router.add(method, path, handler) and "
                "Router.dispatch(method, path, body=''). Routes may contain parameter segments "
                "such as '/items/{id}', which match exactly one non-empty segment; the handler is "
                "called as handler(params, body) and its three-tuple is returned unchanged. When "
                "no route path matches, dispatch returns (404, {}, 'not found'); when a path "
                "matches but the method does not, it returns (405, {}, 'method not allowed'). "
                "Make test_router.py pass without editing it."
            ),
            files=dict(_ROUTER_FILES),
            check_command=CHECK,
            expected_files=("router.py",),
            timeout=180,
            metadata=_meta("api"),
        ),
        TaskSpec(
            id="repo-slug-tests",
            suite="coding",
            prompt=(
                "slug.py is a finished module and must not be edited. Write test_slug.py with "
                "pytest tests that pin its behavior: slugify() lowercases the text, replaces "
                "every run of characters outside a-z and 0-9 with a single '-', and trims leading "
                "and trailing '-'; truncate_slug(text, limit=20) returns the slug unchanged when "
                "it fits, otherwise the first limit characters with any trailing '-' removed. "
                "Your tests must pass against the current slug.py. The grading harness "
                "check_tests.py also re-runs your tests against deliberately broken copies of "
                "slug.py; a suite that still passes on a broken copy is rejected, so cover each "
                "rule with a real assertion. Do not edit slug.py or check_tests.py."
            ),
            files=dict(_SLUG_GIVEN_FILES),
            check_command=f"{sys.executable} check_tests.py",
            expected_files=("test_slug.py",),
            timeout=180,
            metadata=_meta("tests"),
        ),
        TaskSpec(
            id="repo-extract-validation",
            suite="coding",
            prompt=(
                "users.py and orders.py each carry their own copy of validate_email() with "
                "identical logic. Move that helper into a new module validation.py and have both "
                "modules import it, leaving no duplicated definition behind. Behavior, return "
                "values and error messages must stay exactly as they are today, so that "
                "test_users.py, test_orders.py and test_structure.py all pass. Do not edit the "
                "tests."
            ),
            files=dict(_VALIDATION_FILES),
            check_command=CHECK,
            expected_files=("validation.py", "users.py", "orders.py"),
            timeout=180,
            metadata=_meta("refactor"),
        ),
        TaskSpec(
            id="repo-job-queue",
            suite="coding",
            prompt=(
                "The worker tests fail with this traceback:\n"
                "\n"
                "Traceback (most recent call last):\n"
                '  File "test_worker.py", line 9, in test_round_with_one_job\n'
                '    assert run_round(["solo"]) == ["solo"]\n'
                '  File "worker.py", line 7, in run_round\n'
                "    return take(size)\n"
                '  File "tasks.py", line 14, in take\n'
                "    batch.append(PENDING.pop(0))\n"
                "IndexError: pop from empty list\n"
                "\n"
                "Each call to run_round() must process only the jobs passed to it, at most size "
                "of them, and separate rounds must not influence each other. Fix the code so that "
                "test_worker.py passes; do not change the tests."
            ),
            files=dict(_QUEUE_FILES),
            check_command=CHECK,
            expected_files=("tasks.py", "worker.py"),
            timeout=180,
            metadata=_meta("debug"),
        ),
        TaskSpec(
            id="repo-move-duration-format",
            suite="coding",
            prompt=(
                "The duration formatter is moving to its own module. Create timeutil.py holding "
                "humanize_duration(total_seconds) with exactly the current behavior of "
                "formatting.format_duration(), remove format_duration from formatting.py while "
                "keeping the other helpers there, and update cli.py and report.py to import from "
                "timeutil so that neither file mentions the old name any more. Behavior must not "
                "change: test_cli.py, test_report.py and test_move.py must pass. Do not edit the "
                "tests."
            ),
            files=dict(_MOVE_FILES),
            check_command=CHECK,
            expected_files=("timeutil.py", "formatting.py", "cli.py", "report.py"),
            timeout=180,
            metadata=_meta("multi-file"),
        ),
        TaskSpec(
            id="repo-argparse-limit",
            suite="coding",
            prompt=(
                "tool.py is the command line front end of the batch runner and its run command is "
                "broken: `batch run --limit 2` raises a TypeError instead of printing workers=2. "
                "The intended behavior is that --jobs (default 4) is the maximum worker count, "
                "run --limit N caps the workers at min(jobs, N), default 4, and the tool prints "
                "`workers=<n>` and exits 0. A non-integer --limit must be rejected by the argument "
                "parser with the usual usage error rather than reaching main(). Make test_tool.py "
                "pass without editing it."
            ),
            files=dict(_ARGPARSE_FILES),
            check_command=CHECK,
            expected_files=("tool.py",),
            timeout=180,
            metadata=_meta("bugfix"),
        ),
        TaskSpec(
            id="repo-config-loader",
            suite="coding",
            prompt=(
                "config.py must load a JSON service configuration and validate it. Implement "
                "load_config_text(text) and load_config(path) on top of the existing ConfigError. "
                "The document must be a JSON object; 'host' (non-empty string) and 'port' "
                "(integer from 1 to 65535) are required; 'workers' (integer of at least 1, "
                "default 1) and 'debug' (boolean, default False) are optional; any other key is an "
                "error. The returned dict carries all four keys with defaults applied. Every "
                "problem must raise ConfigError with a message that names the key and the "
                "problem, for example \"'port' must be an integer, got str\", \"'port' out of "
                "range: 70000\", \"unknown key 'cache'\", \"missing required key 'port'\", "
                "\"invalid JSON: ...\", \"config document must be an object\", \"cannot read ...\". "
                "Make test_config.py pass without editing it."
            ),
            files=dict(_CONFIG_FILES),
            check_command=CHECK,
            expected_files=("config.py",),
            timeout=180,
            metadata=_meta("feature"),
        ),
        TaskSpec(
            id="repo-json-serializer",
            suite="coding",
            prompt=(
                "serializer.py must provide a deterministic JSON serializer in dumps(value). "
                "Objects serialize with their keys sorted ascending and compact separators, so "
                "{'b': 1, 'a': 2} becomes '{\"a\":2,\"b\":1}'; keys must be strings or a "
                "TypeError is raised. Lists and tuples become arrays, None becomes null, booleans "
                "become true/false, integers use their decimal form and floats use repr(). Strings "
                "are quoted and embedded quotes and backslashes are backslash-escaped. Newlines, "
                "tabs, carriage returns, backspaces and form feeds use their short escape forms, "
                "other control characters use four-digit \\u escapes, and non-ASCII characters "
                "are kept as they are. "
                "Unsupported values raise TypeError naming the type. Make test_serializer.py pass "
                "without editing it."
            ),
            files=dict(_SERIALIZER_FILES),
            check_command=CHECK,
            expected_files=("serializer.py",),
            timeout=180,
            metadata=_meta("api"),
        ),
    ]


SOLUTIONS: dict[str, dict[str, str]] = {
    "repo-amount-parsing": _AMOUNT_FIX,
    "repo-interval-merge": _INTERVAL_FIX,
    "repo-request-router": _ROUTER_FIX,
    "repo-slug-tests": _SLUG_TESTS_FIX,
    "repo-extract-validation": _VALIDATION_FIX,
    "repo-job-queue": _QUEUE_FIX,
    "repo-move-duration-format": _MOVE_FIX,
    "repo-argparse-limit": _ARGPARSE_FIX,
    "repo-config-loader": _CONFIG_FIX,
    "repo-json-serializer": _SERIALIZER_FIX,
}
