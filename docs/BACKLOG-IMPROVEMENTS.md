# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Не завершённое внедрение и acceptance

- Windows browser/API запуск из принятого `main`, фактическая inventory/PR/CI
  проверка и freshness/error cases пока не подтверждены owner-run результатом.
  Повторяемая процедура — [MANUAL-PREVIEW](MANUAL-PREVIEW.md); результат проверки
  не подменяется наличием кода или CI.
- TLS/credential/receipt lifecycle для Gateway ↔ Windows listener, реальные
  fast-forward/reject/replay проверки и guarded Pull требуют отдельного acceptance.
  Gateway provider ещё находится в отдельной source task, не в принятом Gateway
  main; его перенос и proof совместимость принадлежат
  [Gateway backlog](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/BACKLOG-IMPROVEMENTS.md#согласование-нормального-ui-merge-с-последующими-операциями).
- Windows service packaging/lifecycle, WinForms retirement и остальные UI actions
  (PR creation/Ready, CI controls, submission of Gateway ASK) не входят в
  подтверждённый read-only browser контракт. Выбирать необходимый следующий scope
  отдельно, не объявлять весь API-first specification завершённым.

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
