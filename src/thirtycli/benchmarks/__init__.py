"""ThirtyCLI benchmark engine."""

from .adapters import ADAPTERS, AdapterInfo, ExternalCLIAdapter, ThirtyCLIAdapter, get_adapter
from .datasets import DatasetInfo, DatasetUnavailable, datasets, load_dataset
from .models import RunConfig, RunResult, TaskOutcome, TaskResult, TaskSpec
from .runner import BenchmarkRunner, parse_suite_names, run_suites
from .storage import history, list_runs, load_run, save_run

__all__ = [
    "ADAPTERS",
    "AdapterInfo",
    "BenchmarkRunner",
    "DatasetInfo",
    "DatasetUnavailable",
    "ExternalCLIAdapter",
    "RunConfig",
    "RunResult",
    "TaskOutcome",
    "TaskResult",
    "TaskSpec",
    "ThirtyCLIAdapter",
    "datasets",
    "get_adapter",
    "history",
    "list_runs",
    "load_dataset",
    "load_run",
    "parse_suite_names",
    "run_suites",
    "save_run",
]
