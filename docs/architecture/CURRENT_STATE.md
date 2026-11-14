# ThirtyCLI — Current State (Phase 0 audit)

Tanggal audit: 2026-11-15. Baseline sebelum pekerjaan multi-agent: **181 passed, 3 skipped**.
Bahasa: Python 3.12 di venv proyek; target `requires-python = ">=3.10"`.

## Entry point

| Hal | Nilai |
|---|---|
| Console scripts | `thirtycli`, `thirty` → `thirtycli.cli:main` |
| Callback root | `thirtycli.cli:root` (tanpa subcommand membuka chat) |
| Perintah | `chat`, `ask`, `team`, `phases`, `run`, `agents`, `skills`, `tasks`, `config`, `sessions`, `tools`, `doctor` |
| TUI | `thirtycli --tui` / `chat --tui` |

## Modul

| Modul | Tanggung jawab |
|---|---|
| `cli.py` | Typer app, perintah, config/session/doctor/tools/run/agents/skills/tasks |
| `chat.py` | Loop percakapan, streaming, tool call, approval, slash command, usage |
| `presenters.py` | `ConsoleEmitter` untuk engine chat |
| `ui.py` | Banner Rich, tool block, diff, footer, daftar command |
| `tui.py` | UI full-screen prompt_toolkit, command palette, picker, banner kotak |
| `render.py` | Render Markdown streaming per blok |
| `repl.py` | prompt_toolkit prompt, palette `/`, file picker `@` |
| `tools.py` | Tool lokal: file, search, web, git, shell, project intelligence, quality |
| `agents.py` | `run_subagent` read-only + `fan_out` |
| `agent_catalog.py` | 19 profil agent builtin + custom TOML |
| `skills.py` | 12 skill builtin + custom markdown, seleksi keyword |
| `orchestrator.py` | Task board, dependency scheduler, retry/cancel, persist/resume, merge |
| `phases.py` | Plan → worker paralel → merge (jalur lama, tetap ada) |
| `team.py` | Tiga agent: Ada planner, Forge builder, Grace reviewer |
| `models.py` | Provider resolution, fallback, capabilities, `route_model` |
| `config.py` | `~/.thirtycli/config.toml`, env `.env` |
| `sessions.py` | Session JSON + judul dari pesan pertama |
| `context.py` | Estimasi token, trim, clamp output tool |
| `pricing.py` | Estimasi biaya |
| `secrets.py` | Redaksi API key di output |
| `theme.py` | Token warna |

## Provider

- OpenAI-compatible: openai, openrouter, deepseek, groq, together, xai, ollama, lmstudio, custom.
- Native: anthropic, google/gemini (extra install).
- Urutan key: env → `.env` → `config.toml`.
- Fallback model/provider lewat `Config.fallback_model` / `fallback_provider`.
- Routing baru: `capabilities()`, `route_model()`, `explain_route()` — default tidak memindahkan provider tanpa izin.
- Tidak ada LangGraph atau Deep Agents SDK terpasang.

## Tool yang tersedia

Filesystem: `read_file`, `write_file`, `edit_file`, `list_dir`, `search_text`, `search_files`, `delete_file`.
Terminal: `run_shell`.
Git: `git_status`, `git_diff`, `git_log`, `git_branch`, `git_commit`.
Project: `detect_framework`, `detect_language`, `inspect_dependencies`, `inspect_project_structure`.
Quality: `run_tests`, `run_linter`, `run_formatter`, `run_build`.
Web: `web_fetch`, `web_search`.
Lain: `delegate`, `now`.

`build_readonly_tools()` memfilter ke tool baca-saja; sub-agent memakai daftar itu.

## Agent, orchestration, session

- `agents.run_subagent` menjalankan tool loop pendek (maks 4 langkah), bisa menerima `system_prompt` dan `cancel`.
- `agent_catalog` menyediakan 19 id: planner, architect, coder, backend, frontend, fullstack, database, api, devops, debugger, reviewer, security, tester, performance, refactor, docs, explorer, dependencies, analyst.
- `skills` menyediakan 12 skill; `select_skills()` hanya memilih yang relevan.
- `orchestrator` membuat task graph dari planner JSON, menjalankan task independen paralel, menghormati dependensi, retry, membatalkan, menyimpan board ke `~/.thirtycli/tasks/<run>.json`, dan bisa `--resume`.
- Worker default memakai tool read-only; menulis file tetap lewat chat interaktif yang punya approval.
- `phases` lama tetap dipertahankan.

## Session dan persistence

- `~/.thirtycli/sessions/<id>.json`; field `title` diambil dari pesan pertama.
- `/clear` dan `New session` mengarsipkan chat lama, bukan menghapus.
- Task board dan event trace: `~/.thirtycli/tasks/<run>.json` dan `<run>.events.jsonl`.
- Session kosong tidak disimpan.

## TUI

- Banner ber-kotak rounded, command palette Ctrl+P, picker model/provider/session, scroll follow, Ctrl+C cancel.
- Slash baru: `/agents`, `/skills`, `/tasks`.
- Dashboard multi-agent real-time di dalam TUI belum ada; status task saat ini ditampilkan oleh `thirtycli run` di scrollback.

## Testing

- Pytest marker `live` untuk provider asli.
- Baseline akhir pekerjaan ini: **317 passed, 3 skipped**; `ruff check` bersih.
- Test orchestrator memakai fake runner: dependency, parallel barrier, failure/block, retry, cancel, resume, event redaction.
- Test CLI `run` memakai `ScriptedModel` tanpa jaringan.

## Gap terhadap directive

1. TUI dashboard agent/task real-time belum dibuat.
2. Worker orchestrator default read-only; coding agent dengan write+approval belum dijalankan oleh scheduler.
3. Belum ada sandbox OS; permission hanya approval chat + filter tool.
4. Custom agent/skill dimuat dari disk tetapi belum bisa dipilih dari picker TUI.
5. Planner bergantung pada model; fallback deterministik hanya satu task.
6. Model routing belum dipakai otomatis oleh orchestrator; masih API yang bisa dipanggil.
7. Observability berupa JSONL event; belum ada viewer/debug command.
