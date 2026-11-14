# ThirtyCLI Troubleshooting

Start with the built-in checks:

```sh
thirtycli --version
thirtycli config show
thirtycli doctor          # python, packages, config path, provider, key presence
thirtycli doctor --ping   # sends one real message
```

Most messages name the fix. The table below covers the rest.

## No API key

```
No key for deepseek. Set DEEPSEEK_API_KEY, or run: thirtycli config set api_key <key>
```

1. Check what the shell actually sees:

   ```powershell
   echo $env:DEEPSEEK_API_KEY        # PowerShell
   ```

   ```bat
   echo %DEEPSEEK_API_KEY%           :: cmd
   ```

2. Set it for the session (`$env:DEEPSEEK_API_KEY="sk-..."` in PowerShell,
   `set DEEPSEEK_API_KEY=sk-...` in cmd, `export DEEPSEEK_API_KEY=...` in sh), or put it
   in a `.env` next to where you run `thirtycli`.
3. Or persist it: `thirtycli config set api_key sk-...` (stored in plain text in
   `~/.thirtycli/config.toml`).
4. `ollama` and `lmstudio` need no key; `custom` needs one unless your gateway ignores
   it.

If the configured provider has no key but another does, ThirtyCLI switches automatically
and saves the change. If you did not want that provider, run
`thirtycli config set provider <name>` afterwards.

## 401 / 403

`401` usually means the key is wrong, revoked or belongs to another provider. `403` is
usually a permission or policy problem.

- Confirm you are sending the key to the provider you think you are:
  `thirtycli config show` prints `provider`, `base_url` and key status (never the key).
- A key in an `.env` file does **not** override a real environment variable. A stale
  variable in your shell profile wins; unset it first.
- `403` from `web_fetch` is the remote site blocking automated requests. The tool says so
  inline; there is no retry.
- If you are behind a corporate proxy, point the provider at your gateway:
  `thirtycli config set provider custom` then
  `thirtycli config set base_url https://gateway.example.com/v1`.
- `doctor` prints only a masked hint, so it cannot tell you whether the key is valid —
  `doctor --ping` can.

## NoConsoleScreenBufferError

```
prompt_toolkit.output.win32.NoConsoleScreenBufferError: No Windows console found.
```

`prompt_toolkit` needs a real Windows console handle, and stdin/stdout are not one (old
`conhost` from a GUI launcher, some IDE terminals, output redirected through another
program).

Fixes, in order:

1. Run in Windows Terminal or a normal `cmd`/PowerShell window, not through a wrapper
   that pipes output.
2. Force plain mode for scripts and CI: `set THIRTYCLI_PLAIN=1`. `thirtycli ask`,
   `run`, `phases` and `team` work without a full-screen console; piped output is always
   plain.
3. `--tui` requires an interactive terminal and refuses to start otherwise:
   `The TUI needs an interactive terminal. Run it without pipes.` Use scrollback chat
   instead.
4. Upgrade the stack: `pip install -U prompt_toolkit`. Very old `prompt_toolkit` and
   Python builds handle the Windows console differently.

## prompt_toolkit and input problems

| Symptom | Fix |
|---|---|
| Prompt never appears, or exits immediately | stdin is not a TTY (piped, IDE runner). Use a real terminal, or `ask`/`run` for scripted use. |
| `EOFError` / `KeyboardInterrupt` on start | Same cause: no interactive stdin. |
| Arrow keys print `^[[A` | Non-interactive shell or `THIRTYCLI_PLAIN=1`; the plain reader has no key bindings. |
| Garbled prompt after resizing | Resize once more, or `Ctrl+C` and restart. |
| Palette does not open on `/` | Make sure the palette is closed and the line starts with `/`; type `/` as the first character. |
| `Ctrl+O` does nothing | No tool has run yet in this session; run a prompt that uses a tool first. |

## Windows shell differences

`run_shell`, `!cmd` and the `git` helpers run through the platform shell — `cmd.exe` on
Windows, `/bin/sh` elsewhere.

- Use Windows syntax in tool calls: `dir`, `type`, `%VAR%` (cmd) or `Get-ChildItem`
  (PowerShell). `ls` and `cat` exist in PowerShell as aliases but not in `cmd`.
- Environment variables differ: `set X=1` (cmd), `$env:X="1"` (PowerShell), `export X=1`
  (sh). A key exported in one shell is invisible in the other.
- Quote paths with spaces; prefer `/` separators inside Python snippets, but `cmd` tools
  accept `\`.
- `thirtycli` must be on `PATH`: activate the virtual environment first, or call
  `.venv\Scripts\thirtycli.exe` directly.
- If PowerShell blocks activation: `Set-ExecutionPolicy -Scope Process Bypass`.
- Long output is clipped in the tool summary; use `Ctrl+O` (`/expand`) to see it all.

## git missing

`git` is optional for chat, but these need it on `PATH`: `git_status`, `git_diff`,
`/diff`, `/commit`, and the read-only workers that inspect history.

```
git is not installed or not on PATH
```

Install Git for Windows (or your package manager), reopen the terminal so `PATH`
refreshes, then check with `git --version`. The error above appears inside a tool result,
not as a crash: the rest of the answer still streams.

## Timeouts

- Model calls through the OpenAI-compatible providers use a 120 s timeout with two
  retries inside the client. A slow local model or a very large prompt can still exceed
  it.
- `run_shell` defaults to 60 s and accepts a `timeout` argument in seconds when a command
  legitimately needs longer; an overrun surfaces as a tool error in the answer, not as a
  hung terminal.
- `git` helpers time out at 30 s — a huge repository or a network filesystem is the usual
  cause.
- Context is trimmed to roughly 24k tokens per request. `/compact` summarises the
  conversation when answers start missing earlier detail.
- Long answers switch to plain streaming after 8k characters by design; that is not a
  hang.
- If the primary model keeps timing out, set a fallback:

  ```sh
  thirtycli config set fallback_model deepseek-chat
  thirtycli config set fallback_provider deepseek
  ```

## Resuming

Chats and runs are separate stores.

- Session: `thirtycli sessions list`, then `thirtycli chat --session <id>`, or `chat -c`
  for the newest. In the TUI, `Ctrl+P` → `Switch session`.
- Agent run: `thirtycli tasks` lists saved runs. Resume with
  `thirtycli run --resume <run-id>` (no goal argument needed).

Resume semantics:

- completed tasks are kept as-is and skipped;
- pending, ready, waiting and running tasks are reset to pending, with attempts and
  errors cleared;
- failures block only their dependents, so a resumed run can finish the rest.

If `run --resume` reports `No saved run '<id>'`, the id is wrong or `THIRTYCLI_HOME`
points at a different directory than the run that created it. Use
`thirtycli config path` and check the sibling `tasks/` folder.

## A task failed verification

A run finishes even when some tasks fail; the merged answer lists what was not completed.

1. Read the failure, do not guess:

   ```sh
   thirtycli tasks                                   # status and done/total
   type %USERPROFILE%\.thirtycli\tasks\<id>.json     # status, attempts, error per task
   type %USERPROFILE%\.thirtycli\tasks\<id>.events.jsonl
   ```

   (`cat` instead of `type` on macOS/Linux.)

2. Common causes:
   - **The task needs a write.** Workers only have read-only tools, so an "implement X"
     task cannot edit files. Do the write in chat, or ask for analysis instead.
   - **The planner produced a bad graph.** A vague goal yields overlapping tasks. Retry
     with a narrower goal, or lower `--max-tasks`.
   - **A tool result was empty.** A missing path or an empty search looks like a failed
     verification; check the tool output in `events.jsonl`.
   - **The model never called a tool.** Retries are limited by `max_retries` (0–3); a
     resumed run gives the task a fresh budget.
3. Resume and inspect the new attempt, or split the goal into smaller runs.

Errors and tool output are redacted before they are stored, so a key never lands in
`tasks/` or in the terminal scrollback. If you still cannot see the cause, run
`thirtycli doctor --ping` to separate a provider problem from a task problem.
