# Contributing to ThirtyCLI

Thanks for helping out. This project is a small Python 3.12 + LangChain
terminal agent, so the rules are intentionally light.

## Setup

Create a virtual environment at `.venv` and install the project in editable
mode together with the dev tools:

```bash
python -m venv .venv
.venv/Scripts/activate   # Windows (bash); on macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
```

## Running the checks

Run the tests:

```bash
pytest
```

Run the linter (config lives in `pyproject.toml`):

```bash
ruff check src tests
```

Both must pass before you open a pull request. CI runs the same commands on
Ubuntu and Windows against Python 3.11 and 3.12, so a green local run is a good
signal.

## Expectations

- All tests must pass. Do not open a PR with a red suite, skipped tests that
  used to run, or commented-out assertions.
- New behavior needs tests. Bug fixes should come with a test that fails
  without the fix.
- Keep changes focused. Unrelated refactors or formatting churn make review
  harder; split them into their own PR.

## Commit style

- Write the subject line in the imperative mood, capitalized, no trailing
  period, ideally within 50 characters, e.g.
  `Add streaming output to the REPL`.
- Separate the subject from the body with a blank line.
- Use the body only when it adds information the subject cannot carry — why
  the change is needed, tradeoffs, or follow-up work. Wrap it at 72
  characters.
- Keep each commit one logical change.

## Pull requests

Describe what changed and how you verified it. Link the relevant issue when
there is one. Small, reviewable PRs land fastest.
