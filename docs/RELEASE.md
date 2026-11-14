# Releasing ThirtyCLI

## Versioning

ThirtyCLI uses semantic versioning (`MAJOR.MINOR.PATCH`) and is still pre-1.0, so minor
bumps may change behaviour.

The version lives in **two files** and they must match:

| File | Field |
|---|---|
| `pyproject.toml` | `[project] version` |
| `src/thirtycli/__init__.py` | `__version__` |

`thirtycli --version` and the doctor output read the second one; the wheel is built from
the first. Bump both in the same commit, then tag `vX.Y.Z`.

## Test and lint commands

From the project root, in the virtual environment:

```sh
python -m pytest -q
python -m ruff check src tests tools
```

The suite is offline and safe to run anywhere: no network calls, all session, task and
config paths redirected under `tmp_path` through `THIRTYCLI_HOME`.

### Live tests (opt-in, never in CI)

Live tests hit a real provider and each test skips itself unless `THIRTYCLI_LIVE=1` **and**
a key for the chosen provider is present.

```sh
# PowerShell
$env:THIRTYCLI_LIVE="1"; $env:THIRTYCLI_PROVIDER="deepseek"; $env:THIRTYCLI_MODEL="deepseek-chat"
python -m pytest tests/test_live.py tests/test_live_orchestrator.py -m live -q
```

```sh
# sh
THIRTYCLI_LIVE=1 THIRTYCLI_PROVIDER=deepseek THIRTYCLI_MODEL=deepseek-chat \
  python -m pytest tests/test_live.py tests/test_live_orchestrator.py -m live -q
```

`tests/test_live.py` covers invoke, streaming and tool calling.
`tests/test_live_orchestrator.py` covers a live planner plus a read-only orchestrator run
inside a throwaway workspace; it asserts that nothing was written and that `persist=False`
left no task state behind.

Never put a key in a committed file. CI does not set `THIRTYCLI_LIVE`, so live tests are
always skipped there.

## CI

`.github/workflows/ci.yml` runs on every push and pull request to `main`:

- matrix: `ubuntu-latest` and `windows-latest`, Python `3.11` and `3.12`
- `pip install -e ".[dev]"`
- `ruff check src tests tools`
- `pytest -q -m "not live"` — live tests are deselected even if a key were present

`fail-fast: false`, so one platform failing still reports the other. Treat any red cell as
a blocker.

## Build

Packaging uses `hatchling` (see `[build-system]`), with the wheel built only from
`src/thirtycli`:

```sh
pip install build            # not part of the dev extra
python -m build              # writes dist/*.whl and dist/*.tar.gz
```

Sanity-check the artifacts before uploading:

```sh
python -m venv .venv-check
.venv-check/Scripts/activate          # source .venv-check/bin/activate on macOS/Linux
pip install dist/thirtycli-X.Y.Z-py3-none-any.whl
thirtycli --version
thirtycli doctor
```

`dist/` and `build/` are git-ignored.

## Packaging surface

- **Console scripts:** `thirtycli` and `thirty`, both pointing at `thirtycli.cli:main`.
- **Base dependencies:** `langchain-core`, `langchain-openai`, `typer`, `rich`,
  `prompt_toolkit`, `certifi`, `pygments`.
- **Extras:** `[anthropic]`, `[google]`, `[dev]` (pytest, ruff).
- **Python:** `requires-python = ">=3.10"`; CI proves 3.11 and 3.12.
- **License:** MIT, declared in `pyproject.toml`; `LICENSE` ships in the sdist.

Adding a provider means adding its table entry in `src/thirtycli/models.py` and its
env var to the provider docs in `docs/CONFIGURATION.md`.

## Pre-release checklist

1. `git status` is clean; no `.env`, keys, `dist/` or generated screenshots staged.
2. Version bumped in **both** files and they match.
3. `python -m pytest -q` green: no failures, and the only skips are the opt-in live
   tests.
4. `python -m ruff check src tests tools` clean.
5. Live smoke run once on the default provider (Windows and, if available, Linux/macOS).
6. `docs/USER_GUIDE.md`, `docs/CONFIGURATION.md` and `docs/TROUBLESHOOTING.md` describe
   what the code actually does — no roadmap features presented as shipped.
7. `README.md` command table still matches `thirtycli --help`.
8. Build the wheel, install it in a clean venv, run `thirtycli doctor --ping`.
9. Tag `vX.Y.Z`, push the tag, and attach the wheel and sdist to the release.

## Known limitations

State these honestly in release notes and support replies:

- **Read-only by default.** Orchestrated workers get only the read-only tool set unless
  `run --coding` is used. With `--coding`, `controlled` (default) asks before each write
  or command and `autonomous` skips prompts but still enforces workspace paths and
  dangerous-command policy. There is no background daemon; every run is started
  explicitly and cancelled runs are resumed with `--resume`.
- **No OS isolation.** `sandbox.py` reports what is actually enforced instead of
  pretending: on Windows there is no OS sandbox, and on POSIX it reports `bubblewrap`
  only when `bwrap` is on `PATH`. Otherwise a command is a plain subprocess with a
  pinned cwd, a scrubbed environment and a timeout. Approvals are policy checks
  in-process; an approved command runs with the invoking user's permissions, and
  workspace boundaries are not enforced by the operating system.
- **No web UI.** The interfaces are the scrollback REPL, the TUI and the `Ctrl+D`
  dashboard, which tails the latest run's real event file. There is no hosted UI.
- **One fallback attempt per answer.** `fallback_model` is used once, then the error is
  shown.
- **Audit and memory are plain files.** `~/.thirtycli/audit/audit.jsonl` (append-only,
  redacted) and one `~/.thirtycli/memory/<hash>.json` per workspace are the durable
  records. There is no database, vector store or hosted service behind them.
- **Persisted run data is JSON + JSONL**, with secrets redacted.
- **Context is trimmed** to roughly 24k tokens per request by whole conversation blocks.
- **The TUI needs an interactive console.** On Windows, redirecting or piping output
  can raise `NoConsoleScreenBufferError`; see `docs/TROUBLESHOOTING.md`.
- **Cost figures are estimates** for known models and are labelled with `~`; unknown
  models show no cost.

## After the release

- Watch CI on `main` and the first issues for regressions in provider defaults or key
  handling.
- Record any deferred work in `docs/architecture/IMPLEMENTATION_ROADMAP.md`, not in
  shipped docs.
