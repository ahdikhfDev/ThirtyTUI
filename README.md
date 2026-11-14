# ThirtyCLI

A compact terminal agent built on LangChain, wearing the ThirtyCLI theme:
orange `#FFB547` mark, `>_` prompt, dark panels.

```
 ▄▄▄▄▄▄▄▄▄   ThirtyCLI 0.15.0
 █       █   openai · gpt-4o-mini
 █ ●   ● █   session 3f9a · 0 messages
 █  >_   █   /help for commands
 █       █
 ▀▀▀▀▀▀▀▀▀
```

## Install

```sh
cd thirtycli-cli
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux
pip install -e .
```

Then:

```sh
thirtycli                      # chat (default)
thirtycli ask "explain this repo"
thirtycli team "add a /health endpoint"
```

## Providers

Any OpenAI-compatible endpoint works out of the box:

```sh
set OPENAI_API_KEY=...         # PowerShell: $env:OPENAI_API_KEY="..."
thirtycli                      # openai · gpt-4o-mini

thirtycli config set provider ollama
thirtycli config set model llama3.2
thirtycli                      # talks to http://localhost:11434/v1, no key needed
```

Built in: `openai`, `openrouter`, `deepseek`, `groq`, `together`, `xai`,
`ollama`, `lmstudio`, `custom` (set `base_url` yourself).
Anthropic and Google need extras:

```sh
pip install -e ".[anthropic]"    # ANTHROPIC_API_KEY
pip install -e ".[google]"       # GOOGLE_API_KEY
```

Switch mid-chat with `/provider anthropic` or `/model claude-sonnet-4-5`.

### Keys

Any one of these works, in this order of precedence:

1. environment variable (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …)
2. a `.env` file in the folder you run from (or in `~/.thirtycli/`)
3. `thirtycli config set api_key <key>` — stored in `~/.thirtycli/config.toml`

If the configured provider has no key but another one does, ThirtyCLI switches to
that provider, says so, and saves it. So this just works:

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
thirtycli                       # → deepseek · deepseek-chat
```

`thirtycli config set provider deepseek` also resets the model to that provider's
default; set `model` afterwards if you want a different one.

Keys are never echoed back: `config show`, `doctor`, tool output, diffs, errors and
`config set api_key` print only a hint such as `set (env) · sk-****abcd`.

## Commands

| Command | What it does |
|---|---|
| `thirtycli` / `chat` | interactive session, saved under `~/.thirtycli/sessions` |
| `chat -c` | resume the latest session |
| `ask "..."` | one-shot, also reads stdin |
| `team "..."` | planner (Ada) → builder (Forge) → reviewer (Grace), streamed |
| `phases "..." -w 3` | plan → parallel sub-agents → merged answer |
| `run "..."` | task graph → dependency-aware parallel agents → merged answer |
| `run --coding --yes` | coding workers may write files and run approved commands |
| `agents` / `skills` | agent profiles and loadable skills |
| `tasks list` / `tasks show <run>` | saved runs, dependencies, verification, conflicts |
| `trace list` / `trace show <run>` | structured event traces (JSONL) |
| `memory list` / `add` / `search` / `edit` / `rm` | project-scoped persistent notes |
| `bench list` | benchmark suites, datasets and CLI adapters on this machine |
| `bench run all` / `run agents` | real benchmark run in a disposable workspace, persisted as JSON |
| `bench run coding` | 3 tiny tasks + 10 repository-level tasks with pytest checks |
| `bench multi-agent` | fixed 8-task DAG measured at 1, 2, 4 and 6 workers |
| `bench history` / `report <run>` | saved runs and a full report (markdown, table or JSON) |
| `bench compare` / `leaderboard` | side-by-side runs and a ranked table, no composite score |
| `config show` / `set` / `path` | read and write `~/.thirtycli/config.toml` |
| `sessions list` / `show` / `rename` / `rm` | saved conversations; rename keeps the transcript |
| `tools` | local tools the model can call |
| `doctor [--ping]` | versions, key presence, optional live check |

In-chat: `/help`, `/model`, `/provider`, `/tools on|off`, `/plan on|off`, `/diff`,
`/commit <msg>`, `/compact`, `/undo`, `/session`, `/clear`, `/exit`, `/agents`,
`/skills`, `/tasks`.

### Sessions

Each session is named from your first message (for example `Fix the login redirect
loop`), so `sessions list` and `Select session` stay readable. Starting a new session
or running `/clear` keeps the old chat on disk: reopen it from `Ctrl+P` → `Switch
session`, or with `chat --session <id>`. Nothing is deleted unless you run
`sessions rm`.

## Approvals and YOLO mode

By default: writes show a diff and wait, tools that leave the workspace ask first,
and `run_shell` always asks. Skip all of it when you trust the run:

```sh
thirtycli --yolo        # alias for --yes / -y
```

Mid-session use `/yolo on` and `/yolo off`. YOLO mode runs writes and shell
commands without prompts (the banner shows `· yolo`), so keep the work in git and
use plan mode when you want the opposite.

## Plan mode

`thirtycli --plan` (or `/plan on`) makes the run read-only: write tools and shell
commands are refused, and the system prompt asks for a plan instead of changes. Reads,
search, fetch and git inspection still work, so it is safe to point at a big repo.

## Fallback, cost, rules

```sh
thirtycli config set fallback_model deepseek-chat
thirtycli config set fallback_provider deepseek   # optional
```

If the primary model errors mid-request, ThirtyCLI retries once on the fallback and
says so. Answers end with a footer that includes elapsed time, tokens and an estimated
cost (`~$0.0012`) for known models.

Drop `AGENTS.md`, `THIRTYCLI.md` or `.thirtycli/rules.md` in the workspace and its
contents are added to the system prompt (first 4k chars each).

## In the prompt

| Input | What it does |
|---|---|
| `/` | opens the command palette with descriptions next to each command |
| `@` | opens a file picker; choosing one inlines the file into your message |
| `!git status` | runs the command now, output goes to the model |
| `↑` `↓` / `Tab` | move through suggestions |
| `Enter` | pick the highlighted suggestion, or send when the palette is closed |
| `Alt+Enter` | new line |
| `Ctrl+O` | re-open the last tool output (`/expand`, or `/expand 2` for older) |

While the palette is open it looks like this (the description comes from the same
list that `/help` prints):

```
>_ /
┌───────────────────────────────────────────────────────────────┐
│ /help          show this list                                 │
│ /model         switch model — /model claude-sonnet-4-5        │
│ /provider      switch provider — /provider deepseek           │
│ /tools         local tools on or off — /tools on              │
│ /compact       summarise the conversation to free context     │
│ …                                                             │
└───────────────────────────────────────────────────────────────┘
```

History lives in `~/.thirtycli/history`; ↑/↓ walks it when no palette is open.

## Responses

Answers stream as Markdown and are rendered **block by block**: each finished
paragraph or code fence is committed to the scrollback immediately, and only the block
being written is previewed live. Long code blocks never stall the terminal and
nothing already printed is cleared. Each answer ends with a dim footer: elapsed time,
tokens used and an estimated cost.

Tool calls stay compact: a spinner while the tool runs, then one summary line, so a
long output never floods the screen.

```
> read_file(path='src/app.py')
  -- 40 lines · 0.02s  (ctrl+o)

> web_fetch(url='https://thirtystore.com/')
  | HTTP 403 for https://thirtystore.com/. The site may block automated requests.
  -- 1 line · 0.40s
```

Errors and denials are always shown inline. Press `Ctrl+O` (or type `/expand`) to
print the full output again; `/expand 2` goes one tool further back.

Long answers switch to plain streaming after 8k characters to avoid repainting the
whole screen. Piped output (`thirtycli ask ... | less`) is always plain text; set
`THIRTYCLI_PLAIN=1` to force plain mode in a terminal.

## Sub-agents and phases

The main agent can hand self-contained work to **sub-agents**:

```
>_ research how auth works here and how tests cover it
  :: delegate(task='map the auth flow', role='scout')
  :: delegate(task='find auth tests', role='tester')
```

Both calls run at the same time (one worker thread each), and their results come
back as tool messages with usage counted into the session. Sub-agents get only
read-only tools: `read_file`, `list_dir`, `search_text`, `web_fetch`, `web_search`,
`git_status`, `git_diff`, `now`. No writes, no shell, no nested delegation.

For a whole task, use the three-phase pipeline:

```sh
thirtycli phases "audit the CLI for obvious bugs" --workers 3
```

1. **plan** — the model splits the task into independent subtasks (JSON).
2. **workers** — subtasks go to sub-agents in a thread pool.
3. **merge** — one model call turns the results into the final answer.

### Multi-agent runs

```sh
thirtycli run "add authentication and tests" --workers 3
thirtycli tasks                       # list saved runs
thirtycli run --resume <run-id>       # continue an interrupted run
```

`run` is the dependency-aware path: the planner returns a JSON task graph with
`id`, `agent`, `depends_on`, and retry budget. Independent tasks start together,
dependent tasks wait, failures retry and block their dependents without stopping
independent work, and Ctrl+C cancels cleanly. Every task stores status, attempts,
output, error, start/finish time, verification and token usage in
`~/.thirtycli/tasks/<run-id>.json`; events go to `<run-id>.events.jsonl` with keys
redacted. Resume skips completed tasks.

Agent profiles live in `agent_catalog.py` (19 engineering/quality/research roles,
overridable with `~/.thirtycli/agents/*.toml`). Skills live in `skills.py` (12
playbooks + `~/.thirtycli/skills/*.md`) and are only loaded when the task matches
their keywords.

### Autonomous workers and safety

Workers are **read-only by default**. Opt into coding tools explicitly:

```sh
thirtycli run "add the auth middleware and tests" --coding --yes --workers 2
thirtycli run "refactor the parser" --coding --permission autonomous --yes
```

- `--permission read-only|controlled|autonomous` controls writes and shell.
  `controlled` asks for each write/command; `autonomous` skips prompts but still
  runs the same path, command and sandbox policy checks.
- `--yes` is the non-interactive approval for `controlled`; without it, a
  non-interactive controlled run fails closed and tools are denied.
- Writes are workspace-only. `delete_file` refuses the root, symlinks and paths
  outside the workspace. Dangerous commands (`rm -rf /`, `git reset --hard`,
  `curl | sh`, privilege escalation, credential dumps) are refused at every level.
- `ProcessSandbox` pins cwd, scrubs secret env vars and enforces timeouts. It is
  **not an OS jail**; `run` prints the honest sandbox report before work starts.
- Independent workers cannot overwrite a file another unrelated task already
  wrote: the second write is denied and recorded as a conflict. Usage of shell
  commands is captured into the change tracker.
- Verification is independent of the worker claim. A code task with no recorded
  file change is `verification_failed`; a verified task only becomes `completed`
  after the verifier sees real changes. Use `--no-verify` only when you know why.
- Security decisions are written to `~/.thirtycli/audit/audit.jsonl` with secrets
  redacted. `tasks show` and `trace show` display verification and conflicts.
- Model routing can be requested with `--route-prefer cost|latency|quality`;
  `--allow-switch` is required before routing may move to another provider.

Project memory:

```sh
thirtycli memory add "Auth lives in src/auth/" --kind architecture --tags auth
thirtycli memory search auth
thirtycli memory list
```

## Benchmarks

`thirtycli bench` measures the CLI instead of describing it. Every task runs in a
throwaway workspace, is checked against real files and a real test command, and is
stored in `~/.thirtycli/benchmarks/runs/<run-id>.json`.

```sh
thirtycli bench list                          # suites, datasets, adapters
thirtycli bench run agents                    # scheduler mechanics, no model needed
thirtycli bench run all                       # every suite; unavailable parts skip honestly
thirtycli bench report <run-id>               # markdown report
thirtycli bench compare <run-a> <run-b>       # side by side, warns when configs differ
```

Six suites: `coding`, `agents`, `performance`, `cost`, `reliability`, `security`.
Coding tasks need a model; agents/performance/reliability/security run offline and
exercise the real scheduler, permission policy, change tracker and sandbox helpers.
`coding` now runs 13 tasks: three tiny ones plus ten repository-level tasks (bug fix,
feature, API, unit-test writing, refactoring, debugging, multi-file).
`bench multi-agent --workers 1,2,4,6` measures the same 8-task DAG at each worker
count and persists its JSON under `~/.thirtycli/benchmarks/scaling/`.
Skipped work is reported as skipped with a reason, never counted as a pass. There is
no invented composite score, no fabricated pricing and no claim that another CLI was
tested unless its adapter actually executed.

Full details, dataset install paths and the external adapter variables
(`THIRTYCLI_BENCH_AIDER_CMD`, `THIRTYCLI_BENCH_OPENCODE_CMD`,
`THIRTYCLI_BENCH_CLAUDE_CMD`) are in [docs/BENCHMARKS.md](docs/BENCHMARKS.md).

## TUI mode

```sh
thirtycli --tui
```

Full-screen layout, like opencode: the output pane scrolls above and the input
stays pinned at the bottom (two lines minimum, growing to six as you type, with a
thin divider). The opening banner is drawn inside the same rounded box as the
scrollback banner. Answers are
rendered as real Markdown (headings, bold, tables, highlighted code) with the same
palette as the scrollback mode, and the mark is drawn in orange. While a long answer
is still being written only its last eight lines are previewed, so the UI keeps up. The status bar shows `provider · model · tools` plus a
spinner while the agent works. Messages typed while it is busy are queued; `Ctrl+C`
stops the current answer (and quits when idle).

`Ctrl+P` opens a centered command palette — the no-slash way to drive the app.
Type to filter ("model", "session", "diff", …), move with the arrow keys, press
Enter to run and Esc to close. It is a bordered card roughly half the terminal,
uses the same surface and accent as the rest of the theme, and the highlighted row
is the brand orange with dark ink.

Picking `Switch model`, `Switch provider` or `Switch session` opens a second
popup picker ("Select model" / "Select provider" / "Select session") with its own
search, grouped sections and the same highlight. Choosing a provider goes straight
to its model list; choosing a model applies and saves it; Esc closes the popup.

Scrolling up pins the view: new output keeps arriving below without yanking you
back to the bottom. `End` (or sending a message) returns to the live tail.

| Key | Action |
|---|---|
| `Enter` | send (or answer an approval) |
| `Alt+Enter` | new line |
| `Ctrl+P` | command palette: search, arrows, Enter, Esc |
| `Ctrl+D` | task dashboard fed by the latest run's real events |
| `Ctrl+X` `M` / `P` | model picker / provider picker |
| `Ctrl+X` `L` / `N` | session picker / new session |
| `Ctrl+O` | re-open the last tool output |
| `Ctrl+T` | jump to the output pane (arrow keys scroll) |
| `PgUp` / `PgDn` | scroll the chat one page |
| `Home` / `End` | jump to the top / bottom of the chat |
| `Esc` | back to the input |
| `Ctrl+C` | stop the answer; quit when idle |

Approvals print in the output and the status bar asks `[y/N]`; type the answer and
press Enter. `/help`, `@file`, `!cmd`, `/plan`, `/yolo`, `/compact` all work the same.

## Tools

On by default. Turn off for a run with `--no-tools`, or permanently:

```sh
thirtycli config set tools false
```

| Tool | Notes |
|---|---|
| `read_file` | numbered lines; absolute paths are fine |
| `write_file` | shows a diff and asks before writing |
| `edit_file` | exact snippet replace; refuses ambiguous matches |
| `list_dir` | file sizes |
| `search_text` | regex search across workspace files (`path:line: match`) |
| `web_fetch` | fetches a URL and strips it to readable text |
| `web_search` | DuckDuckGo search: titles, links, snippets |
| `delegate` | hands a subtask to a read-only sub-agent; several calls run in parallel |
| `git_status` / `git_diff` | read-only git inspection |
| `run_shell` | asks for confirmation unless `--yes` |
| `now` | local date and time |

Relative paths resolve against the folder you launch from. Absolute paths work
too — anything outside the workspace asks first (reads included), so `C:\Users\...`
is fine once you approve it. Context is trimmed to ~24k tokens per request.

## Theme

Colours come from the site's CSS tokens, so the CLI matches the web:

| Token | Value | Used for |
|---|---|---|
| brand | `#FFB547` | mark, prompt, tool calls |
| ink | `#1A1205` | mark on light variants |
| canvas | `#1A1A1A` | panel background assumptions |
| frame | `#0D0D0D` | deepest surface |
| blue | `#42A0FF` | links / secondary |
| green | `#61C985` | success |
| muted | `#979797` | secondary text |

Edit `src/thirtycli/theme.py` to retune; nothing else hard-codes colour.

## Development

```sh
pip install -e ".[dev]"
pytest -q                                  # unit + TUI e2e, no network
ruff check src tests

# architecture
#   docs/architecture/CURRENT_STATE.md
#   docs/architecture/TARGET_ARCHITECTURE.md
#   docs/architecture/IMPLEMENTATION_ROADMAP.md
#   docs/BENCHMARKS.md

# visual QA: renders the real prompt_toolkit screen to docs/*.png (needs pillow)
.venv/Scripts/python tools/tui_snapshot.py

# optional smoke test against a real provider
THIRTYCLI_LIVE=1 DEEPSEEK_API_KEY=sk-... pytest -m live -q
```

CI runs pytest and ruff on Ubuntu and Windows for Python 3.11/3.12
(`.github/workflows/ci.yml`). Live tests skip themselves when no key is set.

Context is trimmed by whole conversation blocks, so an `AIMessage(tool_calls)` is
never separated from its `ToolMessage`; persisted tool output is capped at 4k chars.
# ThirtyTUI
