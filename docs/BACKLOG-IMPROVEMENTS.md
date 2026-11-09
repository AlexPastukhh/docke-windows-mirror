# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Mirror UX / readback — source в task (до merge и Windows acceptance)

- Компактная таблица: независимо сворачиваемые группы Docker/Windows,
  сохранение размеров каждого столбца в браузере, поиск и фильтр действий,
  обновление выбранного Gateway-статуса без ложной свежести.
- Исправление Windows Git `core.autocrlf` (системный config не игнорируется при
  чтении), сохранение точного porcelain ` M`, повторные **только read-only**
  проверки после fast-forward, конкретные коды непрошедшего readback и
  однокнопочное получение прежнего receipt без F12/повторного Git.
- Источник и Linux/Node-тесты **не доказывают Windows runtime/UX acceptance**.
  После принятого PR и обновления Windows clone нужно отдельно проверить
  реальный Browser Mirror, размеры колонок, сохранение после перезапуска
  браузера, CRLF, `UNCERTAIN` и отсутствие второго Git-эффекта.

## Не завершённое внедрение и acceptance

- Windows browser/API запуск из принятого `main`, фактическая inventory/PR/CI
  проверка и freshness/error cases пока не подтверждены owner-run результатом.
  Повторяемая процедура — [MANUAL-PREVIEW](MANUAL-PREVIEW.md); результат проверки
  не подменяется наличием кода или CI.
- Source для owner-local **Browser Pull Windows main** с одноразовым Preview,
  CSRF, привязкой полного SHA, `git fetch` + `merge --ff-only` и SQLite receipt
  требует отдельного owner-run Windows acceptance: штатный fast-forward и
  отказы на dirty/ahead/diverged/Gateway unavailable, а также readback/replay.
  Наличие тестов на Linux не подтверждает Windows Git/NTFS/GCM.
- TLS/credential/receipt lifecycle для **отдельного Gateway ↔ Windows listener**,
  реальные fast-forward/reject/replay проверки и AI ASK Pull требуют отдельного acceptance.
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
  подтверждённый Browser контракт (чтение + owner-local Pull Windows main). Выбирать необходимый следующий scope
  отдельно, не объявлять весь API-first specification завершённым.

## Приоритеты

- **Source выполнен:** legacy Docker-helper блокирует прямой GitHub network,
  API-first source использует typed Gateway. Отдельно остаётся Windows
  listener/TLS/WebAuthn и browser acceptance по [API-FIRST](API-FIRST.md).
  Наличие source и зелёного CI не означает установку Windows runtime.
- Проверить на реальной Windows принятый Browser UI: в основной 13-колоночной
  таблице только primary; нижние вкладки «Строка» (копирование полного блока)
  и «Worktrees» (отдельные linked worktrees). Browser Stage/Commit/Push
  отключены; owner-local Pull — отдельная операция с подтверждением,
  Gateway ASK Pull не активирован. При ambiguous status — STOP.
- Принять на реальной Windows owner-run Browser Pull: **read-only Preview → exact-SHA fast-forward Apply** только для GitHub main, принятого владельцем и совпадающего с Docker primary main. Без reset/clean, повторной регистрации или прежнего PAT fallback.
- Добавить проверку свежести результатов и обозначение `UNKNOWN`, `REMOTE_DENIED`, `DIRTY`, `NOT_MAIN`, вместо ложного «актуален» по одному локальному SHA. Учесть GitHub token-scoped repo list: наличие Docker checkout не гарантирует доступ.
- Автоматизировать source/static tests и безопасный Windows compile preflight; не имитировать Windows WinForms и файловые ACL внутри Linux. Host-only команды через общий UC-CONT-025.

Точные источники: [`docs/WORKFLOW.md`](WORKFLOW.md), `Fortis-Windows-Mirror-Status.ps1`, [`README.md`](../README.md). Незавершённые task branches и не принадлежащие задаче изменения сохранять без stash/reset. Дальнейший API-first релиз проверять по отдельным owner-evidence.
