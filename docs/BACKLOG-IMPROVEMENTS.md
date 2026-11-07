# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Не завершённое внедрение и acceptance

- Windows browser/API запуск из принятого `main`, фактическая inventory/PR/CI
  проверка и freshness/error cases пока не подтверждены owner-run результатом.
  Повторяемая процедура — [MANUAL-PREVIEW](MANUAL-PREVIEW.md); результат проверки
  не подменяется наличием кода или CI.
- TLS/credential/receipt lifecycle для Gateway ↔ Windows listener, реальные
  fast-forward/reject/replay проверки и guarded Pull требуют отдельного acceptance.
  Mirror-provider остаётся **untracked source** в `fortis-approvals`
  `task/mirror-provider-20261107`, отсутствует в принятом Gateway `main`,
  не активирован. В нём `pull_main` всё ещё требует `mergeRequestId` от
  Gateway-managed merge, тогда как владелец сливает через GitHub UI.
  Перед внедрением необходимо перейти на Gateway-доказательство конкретного
  owner UI merge/PR HEAD/merge SHA/GitHub main, сохранить CI/review gate,
  ASK/WebAuthn и Windows ff-only preflight + reject/replay tests. Владельцы
  подключения и acceptance —
  [UC-GW-002](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/integrations.md#uc-gw-002)
  и [UC-GW-005](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/releases.md#uc-gw-005).
  **Не активировать** исходную версию provider до переработки.
- Windows service packaging/lifecycle, WinForms retirement и остальные UI actions
  (PR creation/Ready, CI controls, submission of Gateway ASK) не входят в
  подтверждённый read-only browser контракт. Выбирать необходимый следующий scope
  отдельно, не объявлять весь API-first specification завершённым.

## Приоритеты

- **Source выполнен:** legacy Docker-helper блокирует прямой GitHub network,
  API-first source использует typed Gateway. Отдельно остаётся Windows
  listener/TLS/WebAuthn и browser acceptance по [API-FIRST](API-FIRST.md).
  Наличие source и зелёного CI не означает установку Windows runtime.
- Проверить на реальной Windows принятый Browser UI: в основной 13-колоночной
  таблице только primary; нижние вкладки «Строка» (копирование полного блока)
  и «Worktrees» (отдельные linked worktrees). Browser Stage/Commit/Push
  отключены, Windows Pull не активирован. При ambiguous status — STOP.
- Дать owner-run Windows PowerShell 5.1 **read-only Preview → exact-SHA fast-forward Apply** только для заранее принятого GitHub main, без reset/clean, повторной регистрации или прежнего PAT fallback.
- Добавить проверку свежести результатов и обозначение `UNKNOWN`, `REMOTE_DENIED`, `DIRTY`, `NOT_MAIN`, вместо ложного «актуален» по одному локальному SHA. Учесть GitHub token-scoped repo list: наличие Docker checkout не гарантирует доступ.
- Автоматизировать source/static tests и безопасный Windows compile preflight; не имитировать Windows WinForms и файловые ACL внутри Linux. Host-only команды через общий UC-CONT-025.

Точные источники: [`docs/WORKFLOW.md`](WORKFLOW.md), `Fortis-Windows-Mirror-Status.ps1`, [`README.md`](../README.md). Незавершённые task branches и не принадлежащие задаче изменения сохранять без stash/reset. Дальнейший API-first релиз проверять по отдельным owner-evidence.
