# ThirtyCLI — Implementation Roadmap

Legenda: **done** = implemented + tested; **partial** = working subset; **pending** = belum.

| Phase | Status | Isi |
|---|---|---|
| 1. Audit & arsitektur | done | `CURRENT_STATE.md`, `TARGET_ARCHITECTURE.md`, roadmap ini |
| 2. Fondasi sub-agent | done | `AgentProfile` 19 profil, custom TOML, `select_agents`, system prompt |
| 3. Orchestrasi paralel | done | `TaskBoard`, DAG, thread pool, retry, cancel, blocked dependents, persist/resume |
| 4. Intelligence | partial | Planner + agent selection + automatic routing integrated; no Laya/decision-model layer |
| 5. Coding tools | done | File/git/project/quality tools + coding workers through the scheduler |
| 6. Memory & persistence | done | Session + judul, archive on clear/new, task board + event JSONL, resume |
| 7. Skills & custom agents | done | 12 skill, seleksi keyword, custom markdown; custom agent TOML |
| 8. Terminal UX | partial | Banner, palette, pickers, Ctrl+D dashboard from real events; agent/skill picker UI pending |
| 9. Security & QA | partial | Permission policy, guarded coding tools, change tracking, audit log, verification; OS sandbox unavailable and reported honestly |
| 10. Release prep | partial | User/config/troubleshooting/release docs and CI; git repo not committed (no user identity configured) |

## Acceptance criteria yang sudah teruji

- Dependency: task dependen tidak jalan sebelum prerequisite completed.
- Parallel: dua task independen terbukti berjalan di dua thread (barrier test).
- Failure: satu task gagal, task independen tetap selesai, dependen menjadi blocked.
- Retry: gagal sekali lalu sukses, `attempts=2`.
- Cancel: tidak ada task yang dilaporkan completed setelah cancel.
- Resume: task completed tidak dijalankan ulang; task failed/pending dilanjutkan.
- Persistence: board + `list_boards` roundtrip.
- Merge: hasil beberapa worker digabung; tanpa model memakai fallback teks.
- Event: JSONL/on_event berisi run_id/task/agent/status; key ter-redact.
- CLI: `run` end-to-end dengan planner + 2 worker + merge memakai model palsu; `agents`, `skills`, `tasks` berjalan.

## Langkah berikutnya (urut prioritas)

1. **Coding worker dengan approval**: runner yang bisa memakai write/edit/shell dengan kebijakan `controlled`, mencatat approval, dan tidak menimpa file tanpa backup. Test konflik edit.
2. **TUI dashboard**: panel task/agent nyata dari event orchestrator; jangan menampilkan progress palsu.
3. **Model routing otomatis**: gunakan `route_model(..., allow_switch=False)` di planner/worker; tampilkan `explain_route`.
4. **Sandbox/security**: batasi shell berbahaya, audit log terstruktur, permission per agent.
5. **Skill & agent picker di TUI**: pilih profil/skill per run; simpan preferensi.
6. **Verifikasi hasil**: task verifikasi yang menjalankan `run_tests` dan membandingkan klaim worker dengan file nyata.
7. **Live tests**: satu skenario `run` dengan provider asli (gated `THIRTYCLI_LIVE=1`).

## Known limitations

- Worker default read-only; belum ada autonomous coding end-to-end.
- Tidak ada LangGraph/Deep Agents; orchestration lokal dan sederhana.
- Planner kualitas bergantung model; fallback hanya satu task.
- Tidak ada sandbox OS.
- Dashboard TUI belum memakai stream event orchestrator.
- Task board disimpan per-run; belum ada garbage collection.
