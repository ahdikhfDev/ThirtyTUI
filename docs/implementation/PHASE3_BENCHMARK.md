# Phase 3 — Benchmark Engine Progress

Baseline before this phase: **447 passed, 6 skipped**, `ruff check src tests tools` clean.

Final state after Phase 3: **523 passed, 6 skipped**, `ruff` clean, version `0.14.0`.

| ID | Workstream | Owner | Status | Files | Tests | Review |
|---|---|---|---|---|---|---|
| B1 | Data model and storage | Lead | COMPLETED | benchmarks/models.py, benchmarks/storage.py | test_bench_storage via runner tests | reviewed |
| B2 | Adapters (built-in + external) | Sub-agent | COMPLETED | benchmarks/adapters.py | test_bench_adapters.py | reviewed |
| B3 | Datasets (internal + public loaders) | Sub-agent | COMPLETED | benchmarks/datasets.py | test_bench_datasets.py | reviewed |
| B4 | Reporting | Sub-agent | COMPLETED | benchmarks/report.py | test_bench_report.py | reviewed |
| B5 | Suites (coding/agents/performance/cost/reliability/security) | Lead | COMPLETED | benchmarks/suites.py | test_bench_runner.py | reviewed |
| B6 | Runner (workspaces, checks, trials, persistence) | Lead | COMPLETED | benchmarks/runner.py | test_bench_runner.py | reviewed |
| B7 | CLI surface (`thirtycli bench ...`) | Lead | COMPLETED | cli.py | test_bench_cli.py | reviewed |
| B8 | Documentation | Lead | COMPLETED | docs/BENCHMARKS.md, README.md | — | reviewed |

## What was actually executed

Offline smoke run of `thirtycli bench run all` in this environment (no provider
key, so `coding` and `cost` were skipped with a reason):

| Suite | Tasks | Result |
|---|---|---|
| coding | 3 internal | skipped — no model configured |
| agents | 6 | 6/6 pass, 6/6 verified |
| performance | 5 | 4 pass, 1 skipped (`first-token` needs a real provider call) |
| cost | 1 | skipped — no model configured |
| reliability | 3 | 3/3 pass, 3/3 verified |
| security | 7 | 7/7 pass, 7/7 verified |

Measured performance numbers from that run (this machine, Windows, CPython from
`.venv`): startup latency ~5s (process spawn plus imports), import latency ~0.24s,
tool latency ~0.06s, memory/CPU sample ~0.17s. These are real measurements, not
targets.

## Design decisions

- **No composite score.** Leaderboard order is success rate, then verified rate,
  then lower mean duration, and the columns are shown. A weighted score would hide
  which axis moved.
- **Skipped is a first-class status.** A missing model, dataset or external CLI
  produces `skipped` plus a reason, excluded from success rates.
- **Workspaces are disposable.** Each task gets a fresh directory under
  `~/.thirtycli/benchmarks/work`; it is removed afterwards unless
  `THIRTYCLI_BENCH_KEEP=1`.
- **External CLIs need an explicit template.** `THIRTYCLI_BENCH_<NAME>_CMD` because
  competitor flags change; the adapter reports `skipped` until then.
- **`bench run --json` is pure JSON.** Warnings are emitted inside the payload
  (`{"warnings": [...], "runs": [...]}`) and `_prepare(..., quiet=True)` suppresses
  informational lines, so scripts can parse stdout directly.
- **Public dataset harnesses are convenience only.** They are not the official
  evaluation procedures and the docs say so.

## Remaining blockers

- HumanEval, MBPP, SWE-bench, Terminal-Bench and Polyglot are not installed on this
  machine, so their loaders are tested against synthetic fixtures rather than the
  real corpora.
- `aider`, `opencode` and `claude` were not executed for real; `opencode` and
  `claude` are on PATH here but no command template was verified, so they report
  `skipped`.
- `coding` and `cost` suites were not executed end to end because no provider key is
  configured in this environment.
- Only one trial was used for the smoke numbers; variance across trials is not
  reported yet.
