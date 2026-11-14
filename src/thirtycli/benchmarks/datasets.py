"""Benchmark datasets: a small internal coding suite plus optional public ones.

Only the internal and repository suites ship with the package, and their tasks
are real: each one is a tiny repository with a failing test that a coding agent
has to fix. The public datasets are never fabricated. HumanEval and MBPP are
read from files the user placed in ``~/.thirtycli/benchmarks/data``; SWE-bench,
Terminal-Bench and Polyglot are read from an installed directory. When they are
missing or unusable, they report themselves as unavailable instead of inventing
tasks, and the harnesses here are a convenience, not the official evaluation
procedure of those benchmarks.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from .models import TaskSpec
from .storage import data_dir

INTERNAL = "internal"
REPO = "repo"
HUMANEVAL = "humaneval"
MBPP = "mbpp"
OFFICIAL = ("swebench", "terminalbench", "polyglot")
KNOWN = (INTERNAL, REPO, HUMANEVAL, MBPP, *OFFICIAL)

# Upstream release metadata, included for provenance only. Check the upstream
# repository before redistributing or quoting these numbers as authoritative.
META = {
    INTERNAL: ("builtin", "thirtycli", "MIT"),
    REPO: (f"thirtycli-{__version__}", "builtin", "MIT"),
    HUMANEVAL: ("1.0", "openai/human-eval", "MIT"),
    MBPP: ("sanitized", "google-research/google-research", "CC-BY-4.0"),
    "swebench": ("2.0", "princeton-nlp/SWE-bench", "MIT"),
    "terminalbench": ("1.0", "laude-institute/terminal-bench", "Apache-2.0"),
    "polyglot": ("1.0", "Aider-AI/polyglot-benchmark", "Apache-2.0"),
}

DATASET_ENV = "THIRTYCLI_BENCH_{name}_DIR"


@dataclass(frozen=True)
class DatasetInfo:
    name: str
    version: str
    source: str
    license: str
    available: bool
    reason: str = ""
    task_count: int = 0


class DatasetUnavailable(RuntimeError):
    """Raised when a dataset is not installed or has no usable records."""


def datasets() -> list[DatasetInfo]:
    """Describe every known dataset; only installed ones are available."""
    return [_info_for(name) for name in KNOWN]


def get_dataset(name: str) -> DatasetInfo:
    key = _key(name)
    if key not in KNOWN:
        raise KeyError(f"unknown dataset '{name}' (known: {', '.join(KNOWN)})")
    return _info_for(key)


def load_dataset(name: str) -> list[TaskSpec]:
    """Load tasks, raising DatasetUnavailable rather than returning nothing."""
    key = _key(name)
    if key not in KNOWN:
        raise DatasetUnavailable(f"unknown dataset '{name}' (known: {', '.join(KNOWN)})")
    if key == INTERNAL:
        return internal_tasks()
    if key == REPO:
        return repo_tasks()
    if key == HUMANEVAL:
        return _load_humaneval()
    if key == MBPP:
        return _load_mbpp()
    return _load_official(key)


# -- internal suite -----------------------------------------------------------


def internal_tasks() -> list[TaskSpec]:
    """Three small but real repository tasks with pytest checks."""
    check = f"{sys.executable} -m pytest -q"
    return [
        TaskSpec(
            id="calc-add",
            suite="coding",
            prompt=(
                "The add() function in calc.py subtracts instead of adding, so test_calc.py "
                "fails. Fix calc.py so the tests pass. Do not change the tests."
            ),
            files={
                "calc.py": "def add(a, b):\n    return a - b\n",
                "test_calc.py": (
                    "from calc import add\n\n\n"
                    "def test_adds_positive_numbers():\n"
                    "    assert add(2, 3) == 5\n\n\n"
                    "def test_adds_negative_numbers():\n"
                    "    assert add(-1, 1) == 0\n"
                ),
            },
            check_command=check,
            expected_files=("calc.py",),
            timeout=120,
            metadata={"kind": "internal", "language": "python"},
        ),
        TaskSpec(
            id="slugify",
            suite="coding",
            prompt=(
                "slug.py contains a stub slugify() that returns its input unchanged. "
                "Implement it so it lowercases the text, replaces runs of non-alphanumeric "
                "characters with a single hyphen and trims leading/trailing hyphens. "
                "Make test_slug.py pass without editing the tests."
            ),
            files={
                "slug.py": 'def slugify(text: str) -> str:\n    return text\n',
                "test_slug.py": (
                    "from slug import slugify\n\n\n"
                    "def test_lowercases_and_uses_hyphens():\n"
                    '    assert slugify("Hello World") == "hello-world"\n\n\n'
                    "def test_strips_separators_and_punctuation():\n"
                    '    assert slugify("  Multiple   Spaces!  ") == "multiple-spaces"\n'
                ),
            },
            check_command=check,
            expected_files=("slug.py",),
            timeout=120,
            metadata={"kind": "internal", "language": "python"},
        ),
        TaskSpec(
            id="parse-csv",
            suite="coding",
            prompt=(
                "parse.py contains a stub parse_csv() that returns an empty list. Implement it "
                "so it turns simple CSV text with a header row into a list of dictionaries, one "
                "per data row. Make test_parse.py pass without editing the tests."
            ),
            files={
                "parse.py": "def parse_csv(text: str) -> list[dict]:\n    return []\n",
                "test_parse.py": (
                    "from parse import parse_csv\n\n\n"
                    'SAMPLE = "name,role\\nada,engineer\\ngrace,admiral\\n"\n\n\n'
                    "def test_returns_one_dict_per_row():\n"
                    "    assert parse_csv(SAMPLE) == [\n"
                    '        {"name": "ada", "role": "engineer"},\n'
                    '        {"name": "grace", "role": "admiral"},\n'
                    "    ]\n\n\n"
                    "def test_handles_trailing_newline():\n"
                    '    assert parse_csv("a,b\\n1,2\\n") == [{"a": "1", "b": "2"}]\n'
                ),
            },
            check_command=check,
            expected_files=("parse.py",),
            timeout=120,
            metadata={"kind": "internal", "language": "python"},
        ),
    ]


# -- dataset info -------------------------------------------------------------


def _key(name: str) -> str:
    return str(name or "").strip().lower()


def _meta(name: str) -> tuple[str, str, str]:
    return META.get(name, ("", "unknown", "unknown"))


def _info_for(name: str) -> DatasetInfo:
    if name == INTERNAL:
        return _internal_info()
    if name == REPO:
        return _repo_info()
    if name == HUMANEVAL:
        return _humaneval_info()
    if name == MBPP:
        return _mbpp_info()
    return _official_info(name)


def _internal_info() -> DatasetInfo:
    version, source, license = META[INTERNAL]
    tasks = internal_tasks()
    return DatasetInfo(
        name=INTERNAL,
        version=f"thirtycli-{__version__}",
        source=source,
        license=license,
        available=True,
        reason="built-in suite; no download needed",
        task_count=len(tasks),
    )


# -- built-in repository suite ------------------------------------------------


def repo_tasks() -> list[TaskSpec]:
    """The built-in repository suite; imported lazily to keep this module light."""
    from .repotasks import repo_tasks as build

    return build()


def _repo_info() -> DatasetInfo:
    version, source, license = META[REPO]
    return DatasetInfo(
        name=REPO,
        version=version,
        source=source,
        license=license,
        available=True,
        reason="built-in repository suite; no download needed",
        task_count=len(repo_tasks()),
    )


# -- HumanEval ----------------------------------------------------------------


def _humaneval_path() -> Path:
    base = data_dir()
    for name in ("humaneval.jsonl", "humaneval.json"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return base / "humaneval.jsonl"


def _humaneval_records(path: Path) -> list[dict]:
    return [
        record
        for record in _read_records(path)
        if str(record.get("prompt") or "").strip()
        and str(record.get("test") or "").strip()
        and str(record.get("entry_point") or "").strip()
    ]


def _humaneval_info() -> DatasetInfo:
    version, source, license = META[HUMANEVAL]
    path = _humaneval_path()
    if not path.is_file():
        return DatasetInfo(
            name=HUMANEVAL,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"official dataset not installed; download HumanEval and save it to {path}",
        )
    try:
        records = _humaneval_records(path)
    except OSError as exc:
        return DatasetInfo(
            name=HUMANEVAL,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"cannot read {path}: {exc}",
        )
    if not records:
        return DatasetInfo(
            name=HUMANEVAL,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"{path} has no records with prompt, test and entry_point",
        )
    return DatasetInfo(
        name=HUMANEVAL,
        version=version,
        source=source,
        license=license,
        available=True,
        reason=f"read from {path}",
        task_count=len(records),
    )


def _load_humaneval() -> list[TaskSpec]:
    info = _humaneval_info()
    if not info.available:
        raise DatasetUnavailable(info.reason)
    path = _humaneval_path()
    tasks: list[TaskSpec] = []
    for index, record in enumerate(_humaneval_records(path)):
        task_id = str(record.get("task_id") or f"HumanEval/{index}")
        entry = str(record["entry_point"]).strip()
        harness = (
            f"from solution import {entry}\n"
            f"{str(record['test']).rstrip()}\n\n"
            "if __name__ == '__main__':\n"
            f"    check({entry})\n"
            "    print('OK')\n"
        )
        statement = str(record["prompt"]).strip()
        tasks.append(
            TaskSpec(
                id=task_id,
                suite=HUMANEVAL,
                prompt=(
                    f"HumanEval task {task_id}: write a complete implementation into solution.py. "
                    f"The test harness imports `{entry}` from solution.py.\n\n{statement}"
                ),
                files={"solution.py": "", "test_solution.py": harness},
                check_command=f"{sys.executable} test_solution.py",
                expected_files=("solution.py",),
                timeout=120,
                metadata={
                    "kind": HUMANEVAL,
                    "task_id": task_id,
                    "entry_point": entry,
                    "source": str(path),
                },
            )
        )
    return tasks


# -- MBPP ---------------------------------------------------------------------


def _mbpp_path() -> Path:
    base = data_dir()
    for name in ("mbpp.jsonl", "mbpp.json"):
        candidate = base / name
        if candidate.is_file():
            return candidate
    return base / "mbpp.jsonl"


def _mbpp_records(path: Path) -> list[dict]:
    records = []
    for record in _read_records(path):
        tests = record.get("test_list") or []
        if isinstance(tests, str):
            tests = [tests]
        if str(record.get("text") or "").strip() and tests:
            records.append(record)
    return records


def _mbpp_info() -> DatasetInfo:
    version, source, license = META[MBPP]
    path = _mbpp_path()
    if not path.is_file():
        return DatasetInfo(
            name=MBPP,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"official dataset not installed; download MBPP and save it to {path}",
        )
    try:
        records = _mbpp_records(path)
    except OSError as exc:
        return DatasetInfo(
            name=MBPP,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"cannot read {path}: {exc}",
        )
    if not records:
        return DatasetInfo(
            name=MBPP,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"{path} has no records with text and test_list",
        )
    return DatasetInfo(
        name=MBPP,
        version=version,
        source=source,
        license=license,
        available=True,
        reason=f"read from {path}",
        task_count=len(records),
    )


def _load_mbpp() -> list[TaskSpec]:
    info = _mbpp_info()
    if not info.available:
        raise DatasetUnavailable(info.reason)
    path = _mbpp_path()
    tasks: list[TaskSpec] = []
    for index, record in enumerate(_mbpp_records(path)):
        task_id = str(record.get("task_id") or f"mbpp-{index}")
        tests = record.get("test_list") or []
        if isinstance(tests, str):
            tests = [tests]
        asserts = "\n".join(str(item).strip() for item in tests if str(item).strip())
        harness = f"from solution import *\n\n{asserts}\n\nprint('OK')\n"
        text = str(record["text"]).strip()
        tasks.append(
            TaskSpec(
                id=task_id,
                suite=MBPP,
                prompt=(
                    f"MBPP task {task_id}: write a complete implementation into solution.py. "
                    f"The harness imports everything from solution.py and runs assertions.\n\n{text}"
                ),
                files={"solution.py": "", "test_solution.py": harness},
                check_command=f"{sys.executable} test_solution.py",
                expected_files=("solution.py",),
                timeout=120,
                metadata={"kind": MBPP, "task_id": task_id, "source": str(path)},
            )
        )
    return tasks


# -- official directory datasets ---------------------------------------------


def _official_dir(name: str) -> Path:
    key = DATASET_ENV.format(name=name.upper())
    override = str(os.environ.get(key, "") or "").strip()
    return Path(override).expanduser() if override else data_dir() / name


def _generic_ready(record: dict) -> bool:
    return bool(str(record.get("prompt") or "").strip()) and bool(
        str(record.get("check_command") or "").strip()
    )


def _official_source(directory: Path) -> Path | None:
    """The manifest the tasks are read from, if the directory has one."""
    for name in ("manifest.json", "tasks.jsonl"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _official_records(directory: Path) -> list[dict]:
    source = _official_source(directory)
    if source is None:
        return []
    records = _read_records(source)
    # A manifest.json may wrap the task list: {"version": ..., "tasks": [...]}.
    if len(records) == 1 and isinstance(records[0].get("tasks"), list):
        records = [item for item in records[0]["tasks"] if isinstance(item, dict)]
    return [record for record in records if _generic_ready(record)]


def _official_info(name: str) -> DatasetInfo:
    version, source, license = _meta(name)
    directory = _official_dir(name)
    env_key = DATASET_ENV.format(name=name.upper())
    if not directory.is_dir():
        return DatasetInfo(
            name=name,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=(
                f"official dataset not installed; set {env_key} or place it at {directory}"
            ),
        )
    manifest = _official_source(directory)
    if manifest is None:
        return DatasetInfo(
            name=name,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=(
                f"{directory} has no manifest.json or tasks.jsonl; "
                f"set {env_key} to an installed dataset directory"
            ),
        )
    try:
        records = _official_records(directory)
    except OSError as exc:
        return DatasetInfo(
            name=name,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"cannot read {manifest}: {exc}",
        )
    if not records:
        return DatasetInfo(
            name=name,
            version=version,
            source=source,
            license=license,
            available=False,
            reason=f"{manifest} has no records with both prompt and check_command",
        )
    return DatasetInfo(
        name=name,
        version=_manifest_version(manifest, version),
        source=source,
        license=license,
        available=True,
        reason=f"read {len(records)} task(s) from {manifest}",
        task_count=len(records),
    )


def _load_official(name: str) -> list[TaskSpec]:
    info = _official_info(name)
    if not info.available:
        raise DatasetUnavailable(info.reason)
    directory = _official_dir(name)
    manifest = _official_source(directory)
    tasks: list[TaskSpec] = []
    for index, record in enumerate(_official_records(directory)):
        files = record.get("files") or {}
        if not isinstance(files, dict):
            files = {}
        expected = record.get("expected_files") or ()
        if isinstance(expected, str):
            expected = (expected,)
        try:
            timeout = int(record.get("timeout") or 120)
        except (TypeError, ValueError):
            timeout = 120
        metadata = {"kind": name, "source": str(manifest)}
        extra = record.get("metadata") or {}
        if isinstance(extra, dict):
            metadata.update({str(key): value for key, value in extra.items()})
        tasks.append(
            TaskSpec(
                id=str(record.get("id") or record.get("task_id") or f"{name}-{index}"),
                suite=name,
                prompt=str(record["prompt"]).strip(),
                files={str(key): str(value) for key, value in files.items()},
                check_command=str(record["check_command"]).strip(),
                expected_files=tuple(str(item) for item in expected),
                timeout=max(1, timeout),
                metadata=metadata,
            )
        )
    return tasks


def _manifest_version(source: Path, fallback: str) -> str:
    if source.name != "manifest.json":
        return fallback
    try:
        data = json.loads(source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return fallback
    if isinstance(data, dict) and str(data.get("version") or "").strip():
        return str(data["version"]).strip()
    return fallback


# -- shared parsing -----------------------------------------------------------


def _read_records(path: Path) -> list[dict]:
    """Read a JSON array or one JSON object per line, skipping broken lines."""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
        except ValueError:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    records: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records
