# ThirtyCLI User Guide

ThirtyCLI is a compact terminal agent: an interactive chat, a one-shot `ask`, and a
multi-agent `run` that plans a task graph, executes it with parallel workers and merges
the result. Everything streams into the terminal in the ThirtyCLI theme.

- [Install](#install)
- [Providers](#providers)
- [Chat](#chat)
- [Prompt keys and the palette](#prompt-keys-and-the-palette)
- [TUI mode](#tui-mode)
- [Sessions](#sessions)
- [One-shot and multi-agent commands](#one-shot-and-multi-agent-commands)
- [Multi-agent runs and resume](#multi-agent-runs-and-resume)
- [Agents and skills](#agents-and-skills)
- [Safety and approvals](#safety-and-approvals)
- [Troubleshooting](#troubleshooting)

## Install

Requires Python 3.10 or newer (CI covers 3.11 and 3.12).

```sh
cd thirtycli-cli
python -m venv .venv
.venv\Scripts\activate          # Windows (cmd/PowerShell)
# source .venv/bin/activate     # macOS / Linux
pip install -e .
```

Optional provider extras, and the dev tooling:

```sh
pip install -e ".[anthropic]"   # Claude models
pip install -e ".[google]"      # Gemini models
pip install -e ".[dev]"         # pytest + ruff
```

Two console scripts are installed: `thirtycli` and `thirty`.

```sh
thirtycli --version
thirtycli doctor            # versions, config path, provider, key presence
thirtycli doctor --ping     # also sends one test message
```

## Providers

The provider is a name plus a model. Anything OpenAI-compatible works out of the box:
`openai`, `openrouter`, `deepseek`, `groq`, `together`, `xai`, `ollama`, `lmstudio`,
`custom`. `anthropic` and `google`/`gemini` use native SDKs and need the extras above.

```sh
thirtycli config set provider deepseek      # also resets model to that provider's default
thirtycli config set model deepseek-chat
thirtycli config show
```

Switch for one run without saving:

```sh
thirtycli --provider groq --model llama-3.3-70b-versatile
```

A key can come from three places, in this order of precedence:

1. an environment variable (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …)
2. a `.env` file in the folder you run from, or in `~/.thirtycli/.env`
3. `thirtycli config set api_key <key>` (stored in `~/.thirtycli/config.toml`)

Local endpoints (`ollama`, `lmstudio`, or `custom` with `base_url` set) need no key.

If the configured provider has no key but another one does, ThirtyCLI switches to that
provider, says so, and saves the change. Keys are never echoed: `config show`, `doctor`,
tool output, diffs and errors show only a hint such as `set (env) · sk-****abcd`.

The full provider table, env variable names and defaults are in
[CONFIGURATION.md](CONFIGURATION.md).

## Chat

`thirtycli` with no subcommand opens the chat, which is the default command.

| Command | What it does |
|---|---|
| `thirtycli` / `thirtycli chat` | interactive session, saved under `~/.thirtycli/sessions` |
| `thirtycli chat -c` | resume the latest session |
| `thirtycli chat --session <id>` | resume a specific session |
| `thirtycli chat --tui` | full-screen TUI |

Useful flags for this run only: `--provider`, `--model`, `--tools/--no-tools`,
`--plan`, `--yes` (`--yolo`).

In-chat commands:

| Command | What it does |
|---|---|
| `/help` | the command list |
| `/model <name>` / `/provider <name>` | switch model or provider |
| `/tools on\|off` | local tools for the session |
| `/plan on\|off` | read-only plan mode |
| `/yolo on\|off` | skip approvals |
| `/diff` | `git diff --stat` plus the diff |
| `/commit <msg>` | stage everything and commit |
| `/compact` | summarise the conversation to free context |
| `/undo` | drop the last user/assistant exchange |
| `/session` | usage, cost estimate and message count |
| `/expand [n]` | re-print the n-th most recent tool output |
| `/clear` | start a fresh session (the old one stays on disk) |
| `/agents` / `/skills` / `/tasks` | list agents, skills and saved runs |
| `/exit`, `/quit`, `/q` | leave |

## Prompt keys and the palette

In the normal prompt:

| Input | What it does |
|---|---|
| `/` | opens the command palette with descriptions |
| `@` | opens a file picker; the chosen file is inlined into the message |
| `!git status` | runs the command now and sends the output to the model |
| `↑` `↓` / `Tab` | move through suggestions |
| `Enter` | pick the highlighted suggestion, or send when the palette is closed |
| `Alt+Enter` | insert a newline |
| `Ctrl+O` | re-open the last tool output (`/expand 2` goes further back) |

Type `/` to see the palette:

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

History is stored in `~/.thirtycli/history`; `↑`/`↓` walk it when the palette is closed.

## TUI mode

```sh
thirtycli --tui
```

A full-screen layout with a scrolling output pane and a pinned input line. The status
bar shows `provider · model · tools` plus a spinner while the agent works. Messages sent
while it is busy are queued.

| Key | Action |
|---|---|
| `Enter` | send (or answer an approval) |
| `Alt+Enter` | newline |
| `Ctrl+P` | command palette: type to filter, arrows, Enter, Esc |
| `Ctrl+D` | task dashboard from the latest run's real events |
| `Ctrl+X` `M` / `P` | model picker / provider picker |
| `Ctrl+X` `L` / `N` | session picker / new session |
| `Ctrl+O` | re-open the last tool output |
| `Ctrl+T` | focus the output pane (arrow keys scroll) |
| `PgUp` / `PgDn` | scroll one page |
| `Home` / `End` | top / bottom of the chat |
| `Esc` | back to the input |
| `Ctrl+C` | stop the current answer; quit when idle |

The palette (`Ctrl+P`) is the no-slash way to drive the app. `Switch model`,
`Switch provider` and `Switch session` open a second picker with search and grouped
sections; choosing a provider goes straight to its model list, and choosing a model
applies and saves it.

## Sessions

Every chat is saved as `~/.thirtycli/sessions/<id>.json` and named from your first
message, so lists stay readable. Starting a new session or running `/clear` keeps the
old chat on disk.

```sh
thirtycli sessions list
thirtycli sessions show <id>
thirtycli sessions rename <id> "new name"
thirtycli sessions rm <id>
```

Reopen one with `chat --session <id>`, `chat -c` for the newest, or `Ctrl+P` →
`Switch session` in the TUI. Nothing is deleted unless you run `sessions rm`.

## One-shot and multi-agent commands

| Command | What it does |
|---|---|
| `thirtycli ask "explain this repo"` | one prompt, one answer; reads stdin when no text |
| `thirtycli team "add a /health endpoint"` | planner → builder → reviewer, streamed |
| `thirtycli phases "audit the CLI" -w 3` | plan → parallel sub-agents → merged answer |
| `thirtycli run "add auth and tests" -w 3` | dependency-aware task graph of agents |
| `thirtycli run "..." --coding --yes` | same, but workers may write files/run commands |

`ask` does not save a session or touch the one you were chatting in. `team` and
`phases` are fixed pipelines. `run` is the general path and is described next.

## Multi-agent runs and resume

```sh
thirtycli run "add authentication and tests" --workers 3 --max-tasks 6
thirtycli run "add authentication" --coding --yes --workers 2
thirtycli tasks                         # list saved runs
thirtycli tasks show <run-id>           # tasks, deps, verification, conflicts
thirtycli trace show <run-id>           # structured events
thirtycli run --resume <run-id>         # continue an interrupted run
```

The planner returns a JSON task graph with `id`, `agent`, `depends_on` and a retry
budget. Independent tasks start together, dependent tasks wait, and a failure retries
and blocks only its dependents — independent work keeps going. `Ctrl+C` cancels
cleanly.

Each task records status, attempts, output, error, start/finish time, verification
and token usage in `~/.thirtycli/tasks/<run-id>.json`. Events are appended to
`<run-id>.events.jsonl` with secrets redacted. Resume skips completed tasks and resets
the rest to pending.

### Read-only vs coding workers

By default workers get read-only tools only: file reads/listing, content and glob
search, web fetch/search, git inspection, project inspection and `now`. No writes, no
deletes, no shell, no nested delegation.

Add `--coding` to let workers write files and run commands through the same
scheduler. Permission profiles:

- `--permission read-only` — inspect only, even with `--coding`.
- `--permission controlled` (default) — each write or command needs approval;
  `--yes` approves non-interactively. Without `--yes` in a non-TTY run, tools fail
  closed.
- `--permission autonomous` — no repeated prompts, but path, dangerous-command and
  sandbox policy checks still run.

Writes are workspace-only. `delete_file` refuses the workspace root, symlinks and
paths outside it. `rm -rf /`, `git reset --hard`, `curl | sh`, privilege escalation
and credential dumps are refused at every level. Independent workers cannot overwrite
a file another unrelated task wrote; the second write is denied and recorded as a
conflict.

`ProcessSandbox` pins cwd, scrubs secret-bearing env vars and enforces timeouts. It is
**not an OS jail**; `run` prints the honest sandbox report before work starts.

### Verification and routing

A worker's claim is not proof. If a code task records no file change, it becomes
`verification_failed` and blocks its dependents. Verified tasks only become `completed`
after the verifier sees real changes (and, with `--verify-checks`, project tests).
`tasks show` displays the verification result and conflicts.

`--route-prefer balanced|cost|latency|quality` asks the router for a model per task
and records `route.decision` events. Routing never moves to another provider unless
`--allow-switch` (or a configured fallback) permits it. `trace show` includes the
reason.

### Project memory

```sh
thirtycli memory add "Auth lives in src/auth" --kind architecture --tags auth
thirtycli memory list
thirtycli memory search auth
thirtycli memory edit <id> "Auth lives in src/auth/session.py"
thirtycli memory rm <id>
```

Memory is project-scoped and only relevant entries are injected into prompts.

## Agents and skills

```sh
thirtycli agents     # built-in plus custom profiles
thirtycli skills     # built-in plus custom playbooks
```

Built-in agent profiles cover engineering, quality and research roles (planner,
architect, coder, backend, frontend, fullstack, database, api, devops, debugger,
reviewer, security, tester, performance, refactor, docs, explorer, dependencies,
analyst). The planner picks one per task based on the task text; you can name one
explicitly in a custom plan.

Custom agents are TOML files in `~/.thirtycli/agents/*.toml`. Custom skills are
Markdown files in `~/.thirtycli/skills/*.md`, loaded only when the task matches their
keywords. Both formats are documented in [CONFIGURATION.md](CONFIGURATION.md).

## Safety and approvals

By default:

- `write_file` / `edit_file` show a diff and wait for approval.
- Anything outside the workspace asks first, reads included.
- `run_shell` always asks.

Skip all of it when you trust the run:

```sh
thirtycli --yolo          # alias for --yes / -y
```

Mid-session, `/yolo on` and `/yolo off` toggle the same thing; the banner shows `· yolo`.

The opposite is plan mode (`thirtycli --plan` or `/plan on`): write tools and shell
commands are refused, and the system prompt asks for a plan instead of changes. Reads,
search, fetch and git inspection still work.

These are UI-level checks. Coding workers also run the centralized permission policy
and `ProcessSandbox` (cwd, scrubbed env, timeout), but there is still **no OS
isolation**: a command you approve executes with your user's permissions. Keep work in
git and review diffs.

## Troubleshooting

Common failures and their fixes are in [TROUBLESHOOTING.md](TROUBLESHOOTING.md):
missing keys, `401`/`403`, `NoConsoleScreenBufferError`, prompt_toolkit issues, Windows
shell differences, a missing `git`, timeouts, resuming runs and failed tasks.
