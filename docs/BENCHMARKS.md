# Benchmarks

`thirtycli bench` is a reproducible benchmark engine for ThirtyCLI itself and for
other coding CLIs that are installed on the same machine. It runs every task in a
throwaway workspace, checks the real result (files and a verification command),
persists the run as JSON, and reports numbers it actually measured.

Nothing here is simulated. When a model, dataset or external CLI is missing, the
task is reported as **skipped with a reason** instead of being counted as a pass.

```
thirtycli bench list
thirtycli bench run all
thirtycli bench run agents
thirtycli bench history
thirtycli bench report <run-id>
thirtycli bench compare <run-a> <run-b>
thirtycli bench leaderboard
```

## Suites

| Suite | What it measures | Needs a model? |
|---|---|---|
| `coding` | 13 real repository tasks: three tiny ones plus ten repo-level tasks (bug fix, feature, API, unit-test writing, refactoring, debugging, multi-file) | yes |
| `cost` | token usage and estimated cost of the first coding task | yes |
| `agents` | scheduler mechanics: parallel speedup, dependency order, conflict denial, verification gate, retry recovery, cancellation | no |
| `performance` | startup latency, import latency, tool latency, memory and CPU of the current process | no |
| `reliability` | session recovery, provider-error handling, resume skipping finished tasks | no |
| `security` | path traversal, dangerous commands, read-only writes, approval bypass, secret redaction, env scrubbing, sandbox honesty | no |

`first-token` latency is listed but **skipped** unless a real provider call is made;
it is never estimated from local numbers.

### Coding tasks

The built-in `internal` dataset ships three tiny repositories (`calc-add`,
`slugify`, `parse-csv`). Each starts with a buggy or stubbed module and a pytest
test that fails; the task passes only when the check command succeeds and the
expected files exist.

Public datasets are read from disk. They are never downloaded automatically and
never invented:

| Dataset | Where to put it | Format |
|---|---|---|
| `repo` (built in) | ships with the package, nothing to install | 10 tasks with initial snapshots and pytest checks |
| HumanEval | `~/.thirtycli/benchmarks/data/humaneval.jsonl` | one JSON object per line (`task_id`, `prompt`, `test`, `entry_point`) |
| MBPP | `~/.thirtycli/benchmarks/data/mbpp.jsonl` | one JSON object per line (`task_id`, `text`, `test_list`) |
| SWE-bench | directory in `THIRTYCLI_BENCH_SWEBENCH_DIR` | installed harness layout |
| Terminal-Bench | directory in `THIRTYCLI_BENCH_TERMINALBENCH_DIR` | installed harness layout |
| Aider Polyglot | directory in `THIRTYCLI_BENCH_POLYGLOT_DIR` | installed harness layout |

The JSONL loaders run the dataset's own tests. The directory-based harnesses are a
convenience wrapper only: they are **not** the official evaluation procedure of
those benchmarks, and numbers from them should not be compared with published
leaderboards. `thirtycli bench list` prints the exact missing path for each dataset.

## Adapters

| Adapter | Value for `--adapter` | Configuration |
|---|---|---|
| ThirtyCLI (orchestrator) | `thirtycli` | normal provider config (`config set`, env keys) |
| Aider | `aider` | `THIRTYCLI_BENCH_AIDER_CMD` |
| OpenCode | `opencode` | `THIRTYCLI_BENCH_OPENCODE_CMD` |
| Claude Code | `claude` (`claude-code`) | `THIRTYCLI_BENCH_CLAUDE_CMD` |

External adapters are detected with `shutil.which` and their `--version` is recorded.
A benchmark task is executed through an external CLI only when you supply a command
template, because competitor flags change between releases and guessing would produce
fake numbers:

```sh
THIRTYCLI_BENCH_AIDER_CMD='aider --yes --no-auto-commits --message {prompt}' \
  thirtycli bench run coding --adapter aider
```

`{prompt}` is replaced with the task prompt. Until a template is set, the adapter
reports `skipped: no verified invocation configured` and the run stays honest.

`bench run` only allows external adapters for `coding` and `cost`; the mechanics
suites exercise ThirtyCLI's own scheduler, policy and sandbox and cannot be measured
through another CLI.

## Fair comparison

Every persisted run records:

- run id and timestamp
- CLI name and version (for ThirtyCLI: package version)
- model name and provider
- suite and dataset
- per-task id, trial, success, verification, duration, exit code, tokens, cost
- the full configuration: timeout, concurrency, trials, seed, temperature, max tokens
- adapter/benchmark notes explaining anything that was skipped

`bench compare` prints runs side by side and warns when they differ in suite, model,
provider or dataset. There is no weighted composite score; `bench leaderboard` sorts
by `success_rate`, then `verified_rate`, then lower mean duration, and shows the
columns it used.

Run more than one trial to see variance:

```sh
thirtycli bench run agents --trials 3 --concurrency 3
```

## Multi-agent scaling

`bench multi-agent` measures scheduling, not model quality: a fixed 8-task DAG (six
independent writes, one join, one final) runs once per worker count with the same
model and provider, each in a fresh workspace.

```sh
thirtycli bench multi-agent --workers 1,2,4,6 --timeout 600
```

Columns: wall time, tasks completed, speedup against the 1-worker point, utilization
(sum of task durations divided by wall time times workers, clamped to 1), conflicts,
verification failures, tokens and cost. The JSON copy is written to
`~/.thirtycli/benchmarks/scaling/scaling-<timestamp>.json`. Speedup plateaus when the
DAG has a serial join or when each worker turn is a single synchronous model call;
the harness reports what it measured and never smooths it.

Token totals cover sub-agent turns only; the final merge call is not counted, and the
command prints that note so the number is not mistaken for a full billing figure.

## Storage

```
~/.thirtycli/benchmarks/
  runs/<run-id>.json          one file per suite run (secrets redacted)
  scaling/scaling-<ts>.json   one file per multi-agent scaling report
  data/                       optional public datasets
  work/                       disposable workspaces, removed after each task
```

Set `THIRTYCLI_BENCH_KEEP=1` to keep the workspaces of the current run for debugging.
Workspaces live under `~/.thirtycli`, never inside the project being developed, and
nothing in the repository is modified by a benchmark run.

## Machine-readable output

`bench list --json`, `bench history --json`, `bench leaderboard --json` and
`bench report <id> --format json` print a single JSON document. `bench run --json`
prints:

```json
{
  "warnings": ["no model: No key for deepseek ..."],
  "runs": [
    {
      "run_id": "bench-20261115-004209-0815aa",
      "suite": "agents",
      "cli": "thirtycli",
      "model": "deepseek-chat",
      "provider": "deepseek",
      "config": {"timeout": 120, "concurrency": 2, "trials": 1, "seed": 0},
      "tasks": [{"task": "parallel-speedup", "success": true, "verified": true}],
      "summary": {"ran": 6, "skipped": 0, "success_rate": 1.0, "verified_rate": 1.0}
    }
  ]
}
```

Warnings go inside the JSON (not to stdout as prose) so scripts can parse it.

## What it does not do

- It does not estimate token counts or prices. Unknown models report `n/a`.
- It does not claim a competitor ran unless the adapter actually executed.
- It does not produce a single "score" that hides which axis improved.
- It does not run benchmarks inside your project or against your real repository.

## Live validation

Live tests are opt-in and read a key from the environment or from
`thirtycli config set api_key`:

```sh
THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
  pytest tests/test_live.py tests/test_live_orchestrator.py -q
```

They call the real provider, stream real chunks, run a read-only orchestrator, and
exercise a coding worker that must edit a file, pass `pytest`, be `verified` by the
independent verifier, and report non-zero token usage. See
`docs/implementation/PHASE4_VALIDATION.md` for the measured runs.
