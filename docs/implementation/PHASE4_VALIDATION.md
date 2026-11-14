# Phase 4 — Real-World Validation

Validated with a real provider (`deepseek · deepseek-chat`) on 2026-11-15. No number in
this document is estimated or simulated: every figure comes from a persisted run under
`~/.thirtycli/benchmarks/` or from a command whose output is quoted here.

## 1. Audit

Already present before this phase: the benchmark engine (suites, adapters, datasets,
runner, storage, report), opt-in live tests, provider configuration, and the
`bench list/run/history/report/compare/leaderboard` commands.

Gaps found and closed in this phase:

| Gap | Fix |
|---|---|
| Only 3 tiny coding tasks; no repository-level pack | `benchmarks/repotasks.py`: 10 tasks across bug fix, feature, API, test writing, refactor, debugging, multi-file |
| No worker-count measurement | `benchmarks/scaling.py` + `bench multi-agent` (fixed 8-task DAG, 1/2/4/6 workers) |
| Live tests only accepted environment keys | live fixtures now also accept a key saved with `thirtycli config set api_key` |
| No live proof of the coding worker + verification gate | `test_live_coding_worker_writes_a_fix` in `tests/test_live_orchestrator.py` |
| Scaling results were printed but not persisted | `bench multi-agent` writes JSON to `~/.thirtycli/benchmarks/scaling/` |
| Competitor templates undocumented | verified `opencode run --auto --model …` and `claude -p …` from their real `--help`; see section 5 |

## 2. Live provider validation

```
thirtycli doctor --ping        -> model replied: ok
thirtycli ask "Reply with the single word: ok" --no-tools
                              -> ok · 1.1s · 72 tok · ~$0.0000 · deepseek-chat
THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
  pytest tests/test_live.py tests/test_live_orchestrator.py -q
                              -> 7 passed in 32.31s
```

The seven live tests cover: real invoke, real streaming, read-only orchestrator run,
and a coding worker that must edit `calc.py`, pass `pytest`, come back `verified` from
the independent verifier, and report non-zero token usage.

## 3. Repository-level coding tasks

Ten new tasks in `benchmarks/repotasks.py`, each a real snapshot with a failing check
and a reference solution kept out of the model prompt:

| Task | Category |
|---|---|
| `repo-interval-merge` | bug fix |
| `repo-amount-parsing` | bug fix |
| `repo-job-queue` | feature implementation |
| `repo-request-router` | API development |
| `repo-slug-tests` | unit-test writing (check mutates the module and requires the new tests to fail) |
| `repo-extract-validation` | refactoring |
| `repo-config-loader` | feature with validation |
| `repo-json-serializer` | API edge cases |
| `repo-move-duration-format` | multi-file change |
| `repo-argparse-limit` | CLI argument parsing |

Pilot run (`thirtycli bench run coding --timeout 180 --concurrency 2`), run id
`bench-20261115-010306-b3b4a2`:

```
Tasks: 13  Ran: 13  Skipped: 0  Errors: 0
Success: 13/13 (100.0%)  Verified: 13/13 (100.0%)
Mean duration: 8.30s   Total duration: 107.93s
Tokens: 179,152   Cost: $0.0562
```

Every task also had its independent check command pass after the worker finished, so
success and verification are not self-reported. The generated Markdown report is at
`~/.thirtycli/benchmarks/reports/coding-deepseek-20261115.md`; the raw JSON is at
`~/.thirtycli/benchmarks/runs/bench-20261115-010306-b3b4a2.json`.

## 4. Multi-agent validation (1, 2, 4, 6 workers)

Fixed 8-task DAG (six independent writes, one join, one final), same model and
provider for every point, one fresh workspace per point:

Run A:

| Workers | Wall (s) | Done | Speedup | Utilization | Conflicts | Verify fails | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|
| 1 | 18.14 | 8/8 | 1.00 | 0.90 | 0 | 0 | 37,731 | $0.0111 |
| 2 | 12.97 | 8/8 | 1.40 | 0.66 | 0 | 0 | 37,764 | $0.0111 |
| 4 | 12.89 | 8/8 | 1.41 | 0.37 | 0 | 0 | 40,517 | $0.0120 |
| 6 | 10.46 | 8/8 | 1.74 | 0.28 | 0 | 0 | 37,745 | $0.0111 |

Run B (repeated for variance, persisted at
`~/.thirtycli/benchmarks/scaling/scaling-20261115-010543.json`):

| Workers | Wall (s) | Done | Speedup | Utilization | Conflicts | Verify fails | Tokens | Cost |
|---|---|---|---|---|---|---|---|---|
| 1 | 18.43 | 8/8 | 1.00 | 0.91 | 0 | 0 | 37,759 | $0.0111 |
| 2 | 14.68 | 8/8 | 1.26 | 0.64 | 0 | 0 | 38,474 | $0.0114 |
| 4 | 12.01 | 8/8 | 1.53 | 0.38 | 0 | 0 | 37,769 | $0.0111 |
| 6 | 12.04 | 8/8 | 1.53 | 0.27 | 0 | 0 | 40,583 | $0.0120 |

Interpretation: 8/8 completion, zero dependency violations, zero conflicts and zero
verification failures at every worker count. Speedup plateaus between 2 and 6 workers
because the DAG is a 6-wide fan-out followed by a serial join and finish, and each
worker turn is a single synchronous model call. Token totals stay flat, so worker
count adds no token overhead in this DAG. Token counts cover sub-agent turns only; the
final merge call is not included, and this is stated in the command output.

## 5. Competitor comparison

Detected on this machine: `opencode v2.2.1`, `claude 2.1.325 (Claude Code)`; `aider`
is not installed.

Non-interactive invocations verified from each tool's own `--help`:

```sh
opencode run --auto --model <provider>/<model> "<prompt>"
claude -p "<prompt>"
```

A controlled comparison requires an identical model, provider, task and starting
repository. That was **not** possible here:

- `opencode` is configured against a `9router` gateway whose models (`deepseek-v4-flash`,
  `deepseek-v4-pro`, …) are not the `deepseek-chat` model used by ThirtyCLI. A direct
  attempt with `--model deepseek/deepseek-chat` failed with
  `Error: Model unavailable: deepseek/deepseek-chat`, and the task workspace was left
  unchanged (`pytest` still failed). No competitor result is claimed.
- `claude` requires Anthropic credentials; the DeepSeek key is unrelated and running it
  would spend an account the user did not authorize for this test.
- `aider` is not installed.

The adapter plumbing is real and ready: setting
`THIRTYCLI_BENCH_OPENCODE_CMD='opencode run --auto --model <provider>/<model> "{prompt}"'`
(or the `AIDER`/`CLAUDE` equivalents) makes `bench run coding --adapter <name>` execute
the external CLI. Until then the adapter reports `skipped: no verified invocation
configured` instead of inventing a result.

## 6. Reporting

- `bench report <run> --format markdown|table|json` and
  `bench history/compare/leaderboard` cover per-task results, aggregate ranking, cost
  and duration columns, and failure analysis.
- `bench multi-agent` writes machine-readable JSON to
  `~/.thirtycli/benchmarks/scaling/`.
- Missed metrics are printed as `n/a` or explained in notes; no composite score is
  produced and no missing data is filled in.

## 7. Final validation

```
pytest -q                                  -> 558 passed, 7 skipped
ruff check src tests tools                 -> All checks passed
THIRTYCLI_LIVE=1 pytest tests/test_live*.py -> 7 passed
bench run coding (13 tasks)                -> 13/13 pass, 13/13 verified
bench multi-agent 1,2,4,6                  -> 8/8 tasks at every point
```

The 7 skips are the opt-in live tests in the default (offline) run.

## 8. Known limitations

- One provider and one model were available; no cross-model comparison.
- Token totals exclude the final merge call, as noted by the harness.
- The coding suite ran with `concurrency=2`; higher concurrency was not measured.
- No OS-level sandbox (unchanged from Phase 2): `ProcessSandbox` pins cwd, scrubs
  secret environment variables and enforces timeouts, but is not an OS jail.
- The live key lives in `~/.thirtycli/config.toml` (outside this repository). Remove it
  with `thirtycli config set api_key ""` when finished, and rotate it if it was shared
  in plain text.

## Reproduction

```sh
# offline suites
thirtycli bench run agents,performance,reliability,security

# repository-level coding with a real provider
thirtycli bench run coding --timeout 180 --concurrency 2

# worker scaling with a real provider
thirtycli bench multi-agent --workers 1,2,4,6 --timeout 600

# live tests
THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
  pytest tests/test_live.py tests/test_live_orchestrator.py -q
```
