# Phase 2 — Production Completion Progress

Baseline verified: **317 passed, 3 skipped**. Final suite after Wave 1+2 integration:
**447 passed, 6 skipped** (live tests opt-in). `ruff check src tests tools` clean.

| ID | Workstream | Owner | Status | Files | Tests | Review |
|---|---|---|---|---|---|---|
| A1 | Autonomous coding workers | Lead | COMPLETED | orchestrator.py, tools.py, agents.py | test_coding_worker.py, test_cli_run.py | reviewed |
| A2 | Independent verification | S2 + Lead | COMPLETED | verification.py, orchestrator.py | test_verification.py, test_coding_worker.py | reviewed |
| A3 | Conflict detection | S2 + Lead | COMPLETED | changes.py, orchestrator.py | test_changes.py, test_coding_worker.py | reviewed |
| B1 | TUI dashboard | S4 failed + Lead | COMPLETED | dashboard.py, tui.py | test_dashboard.py, test_tui.py, test_tui_e2e.py | reviewed |
| B2 | Agent/skill pickers | — | NOT_STARTED | tui.py | — | — |
| C1 | Sandbox abstraction | S1 | PARTIAL | sandbox.py | test_sandbox.py | honest: no OS isolation |
| C2 | Permission enforcement | S1 + Lead | COMPLETED | permissions.py, orchestrator.py | test_permissions.py, test_coding_worker.py | reviewed |
| C3 | Security audit log | S3 + Lead | COMPLETED | audit.py, orchestrator.py | test_audit.py, test_coding_worker.py | reviewed |
| D1 | Automatic routing | Lead | COMPLETED | orchestrator.py, cli.py | test_coding_worker.py | reviewed |
| D2 | Project memory | S3 + Lead | COMPLETED | memory.py, chat.py, cli.py | test_memory.py, test_cli_run.py | reviewed |
| E1 | Session/trace/task commands | Lead | COMPLETED | cli.py | test_cli_run.py | reviewed |
| E2 | Live tests | S5 | COMPLETED | tests/test_live_orchestrator.py | 3 skipped locally | opt-in |
| E3 | Release/docs/CI | S5 + Lead | PARTIAL | docs/, .github/workflows/ci.yml | — | git initialized on main; no commit (no user identity) |

## Wave summary

- Wave 1: permissions/sandbox, changes/verification, audit/memory, live tests/docs/CI.
- Wave 2: scheduler integration, coding workers, verification gate, conflict denial,
  audit events, routing events, TUI dashboard, CLI commands, project memory prompt.
- S4 (dashboard) hit a context limit; `dashboard.py` and its tests were written by the
  lead. S1/S2/S3/S5 delivered their scopes.

## Remaining blockers

- B2 agent/skill picker UI is not implemented.
- OS-level sandbox is not available; `ProcessSandbox` is honest cwd/env/timeout only.
- Git repository is initialized on `main`, but there is no initial commit because no
  `user.name` / `user.email` is configured globally. No files were committed.
- Live tests were not executed against a real provider in this environment.
- Verification is deterministic (files/diff/tests optional); no model-based verifier.
- Retry after verification failure requires `--resume`; no automatic reassignment.
