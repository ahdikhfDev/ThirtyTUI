# ThirtyCLI Configuration

Everything persistent lives in `~/.thirtycli/` (Windows:
`C:\Users\<you>\.thirtycli\`). Set `THIRTYCLI_HOME` to relocate the whole directory —
useful for tests, portable installs and CI.

```sh
thirtycli config path     # print the config file path
thirtycli config show     # effective values (the key is masked)
```

## Precedence

Highest wins:

1. command-line flags for this run (`--provider`, `--model`, `--tools`, `--yes`, `--plan`)
2. environment variables (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …)
3. `.env` in the current folder, then `.env` in `~/.thirtycli/` (never overrides a real
   environment variable)
4. `~/.thirtycli/config.toml`
5. built-in defaults (`provider = "openai"`, the provider's default model)

Changing `provider` also resets `model` to that provider's default; set `model` after it
if you want something else.

## config.toml

`thirtycli config set <key> <value>` writes the file; `config show` reads it back. A
complete file looks like this:

```toml
[model]
provider = "deepseek"          # openai | openrouter | deepseek | groq | together | xai
                               # ollama | lmstudio | custom | anthropic | google
model = "deepseek-chat"        # empty means the provider default
temperature = 0.2
base_url = ""                  # empty means the provider default
api_key = ""                   # optional; prefer an env var
fallback_model = ""            # retried once if the primary model errors
fallback_provider = ""         # defaults to the primary provider

[chat]
tools = true                   # local tools on/off
auto_yes = false               # same as --yolo for every session
system = "You are ThirtyCLI, ..."   # system prompt, with project rules appended
```

Settable keys (the full list `config set` accepts):

`provider`, `model`, `temperature`, `base_url`, `api_key`, `fallback_model`,
`fallback_provider`, `system`, `tools`, `auto_yes`.

Notes:

- `api_key` is stored **in plain text**. Environment variables or `.env` are safer.
- `auto_yes = true` disables every approval prompt for every session. Leave it off
  unless the machine is disposable.
- Any top-level keys that are not `[model]` or `[chat]` are preserved as `Config.extra`
  and written back unchanged.
- `system` is extended at runtime with `AGENTS.md`, `THIRTYCLI.md` or
  `.thirtycli/rules.md` from the workspace (first 4k characters of each), and with a
  read-only note in plan mode.

## Providers and env keys

| Provider | Base URL | Key variable | Default model | Key required |
|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | `gpt-4o-mini` | yes |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` | yes |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` | `deepseek-chat` | yes |
| `groq` | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` | yes |
| `together` | `https://api.together.xyz/v1` | `TOGETHER_API_KEY` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | yes |
| `xai` | `https://api.x.ai/v1` | `XAI_API_KEY` | `grok-2-latest` | yes |
| `ollama` | `http://localhost:11434/v1` | `OLLAMA_API_KEY` | `llama3.2` | no |
| `lmstudio` | `http://localhost:1234/v1` | `LMSTUDIO_API_KEY` | `local-model` | no |
| `custom` | whatever `base_url` says | `OPENAI_API_KEY` | `gpt-4o-mini` | yes |
| `anthropic` | native SDK | `ANTHROPIC_API_KEY` | `claude-sonnet-4-5` | yes |
| `google` / `gemini` | native SDK | `GOOGLE_API_KEY` | `gemini-2.5-flash` | yes |

`anthropic` and `google` need their extras: `pip install -e ".[anthropic]"` or
`pip install -e ".[google]"`.

A custom OpenAI-compatible endpoint:

```toml
[model]
provider = "custom"
base_url = "https://my-gateway.example.com/v1"
model = "my-model"
```

### Autodetect

If the configured provider needs a key and has none, ThirtyCLI looks for one in this
order: `deepseek`, `openrouter`, `anthropic`, `groq`, `together`, `xai`, `google`,
`openai`. The first provider with a key wins; the switch is saved to `config.toml` and
printed once.

### Fallback

```sh
thirtycli config set fallback_model deepseek-chat
thirtycli config set fallback_provider deepseek     # optional
```

If a streaming request fails, the current answer is retried once on the fallback model
and the UI says so. There is exactly one fallback attempt per answer.

## Environment variables

| Variable | Effect |
|---|---|
| `THIRTYCLI_HOME` | root of config, sessions, tasks, history, agents, skills |
| `THIRTYCLI_PLAIN` | force plain text instead of styled Markdown |
| `NO_COLOR` | also disables styled Markdown |
| `<PROVIDER>_API_KEY` | see the table above |
| `THIRTYCLI_LIVE` | `1` enables the opt-in live tests |
| `THIRTYCLI_LIVE_PROVIDER`, `THIRTYCLI_LIVE_MODEL` | provider/model for `tests/test_live.py` |
| `THIRTYCLI_PROVIDER`, `THIRTYCLI_MODEL` | provider/model for `tests/test_live_orchestrator.py` |

`.env` parsing is deliberately small: `KEY=value` lines, `#` comments, optional
surrounding quotes, no expansion, and an existing environment variable always wins.

## Custom agents

Drop `*.toml` files in `~/.thirtycli/agents/`. Invalid or unreadable files are skipped.
The file name is the fallback id.

```toml
# ~/.thirtycli/agents/api-reviewer.toml
id = "api-reviewer"                # optional, defaults to the file name
name = "API reviewer"
description = "Checks HTTP surface area for compatibility and errors."
instructions = """
Read the handler and its tests first. List breaking changes, missing status codes,
and unvalidated input. Give the smallest concrete fix per item.
"""
permission = "read-only"          # read-only | controlled | autonomous
allowed_tools = ["read_file", "search_text", "list_dir"]  # omit for all read-only tools
model = ""                        # optional per-agent model
context_policy = "shared"         # free-form note carried in the profile
output_contract = "Compact plain-text result under 200 words."
```

`instructions` is required; everything else has a default. Custom profiles appear in
`thirtycli agents`, `/agents`, and are selectable by the planner. A worker's tool list is
read-only regardless of `permission` — `permission` currently describes intent in the
system prompt and does not grant write tools.

## Custom skills

Drop `*.md` files in `~/.thirtycli/skills/`. A file with `---` frontmatter:

```markdown
---
name: release-notes
description: Turn a git log into release notes grouped by area.
keywords: release, changelog, notes, version
tools: git_diff, git_status
---

# Release notes

Group commits by area, write one user-facing line each, and list breaking changes
first. Never invent a change that is not in the log.
```

Without frontmatter the first heading becomes the name and the first paragraph the
description. `keywords` and `tools` are comma-separated. Skills are lazy: only the
best-matching few (up to three) are injected into an agent prompt, and a custom skill
shadows a built-in with the same name. List them with `thirtycli skills` or `/skills`.

## Data paths

| Path | Contents |
|---|---|
| `~/.thirtycli/config.toml` | provider, model, chat settings |
| `~/.thirtycli/.env` | optional key file, lowest precedence |
| `~/.thirtycli/sessions/<id>.json` | chat sessions: messages, title, usage |
| `~/.thirtycli/tasks/<run-id>.json` | task board: goal, tasks, statuses, usage, final answer |
| `~/.thirtycli/tasks/<run-id>.events.jsonl` | one JSON event per line, strings redacted |
| `~/.thirtycli/audit/audit.jsonl` | append-only security audit trail (tool calls, approvals, decisions), redacted |
| `~/.thirtycli/memory/<project-hash>.json` | per-project notes: decisions, architecture, constraints, outcomes |
| `~/.thirtycli/history` | prompt history shared by the REPL |
| `~/.thirtycli/agents/*.toml` | custom agent profiles |
| `~/.thirtycli/skills/*.md` | custom skills |

The audit log is one JSON object per line, written through `AuditLog.record(...)` in
`audit.py` and read back newest-first with `read_audit(limit=..., event=..., task_id=...)`.
Project memory is a small JSON document per workspace, keyed by a hash of the resolved
workspace path and rewritten atomically; `memory.py` owns the schema (kinds: decision,
architecture, constraint, outcome, note) and the keyword search. Both are local plain
files, safe to archive or delete, and nothing is uploaded anywhere. `events.jsonl`
remains the per-run trace, while `audit.jsonl` is the cross-run security record.
