# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Приоритеты

- **Source выполнен:** legacy Docker-helper блокирует прямой GitHub network,
  API-first source использует typed Gateway. Отдельно остаётся Windows
  listener/TLS/WebAuthn и browser acceptance по [API-FIRST](API-FIRST.md).
  Наличие source и зелёного CI не означает установку Windows runtime.
- Сверить реальную поддержку linked task worktrees в GUI и read-only API. `Repos/<repo>/main`, конкретные `task/<slug>`, Windows clone и выбранная строка UI должны быть однозначно связаны перед Stage/Commit/Push/Pull; при ambiguous status — STOP.
- Дать owner-run Windows PowerShell 5.1 **read-only Preview → exact-SHA fast-forward Apply** только для заранее принятого GitHub main, без reset/clean, повторной регистрации или прежнего PAT fallback.
- Добавить проверку свежести результатов и обозначение `UNKNOWN`, `REMOTE_DENIED`, `DIRTY`, `NOT_MAIN`, вместо ложного «актуален» по одному локальному SHA. Учесть GitHub token-scoped repo list: наличие Docker checkout не гарантирует доступ.
- Автоматизировать source/static tests и безопасный Windows compile preflight; не имитировать Windows WinForms и файловые ACL внутри Linux. Host-only команды через общий UC-CONT-025.

Точные источники: [`docs/WORKFLOW.md`](WORKFLOW.md), `Fortis-Windows-Mirror-Status.ps1`, [`README.md`](../README.md). Незавершённые task branches и не принадлежащие задаче изменения сохранять без stash/reset. Дальнейший API-first релиз проверять по отдельным owner-evidence.
