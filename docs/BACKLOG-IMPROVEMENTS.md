# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Unified Mirror API — P0 review (2026-11-15)

[P0 contract/schemas](MIRROR_API_CONTRACT_V1.md) принят owner merge PR #23;
UC-GW-012/UC-CONT-003 приняты PR #100/#25. Это не реализация v2.
[Source/catalog review](unified-api/P0_SOURCE_REVIEW_2026-11-15.md) отделяет
проверенные исходники от неподтверждённой установленной runtime.

- P0 source принят, модель B выбрана; реализация signing/key lifecycle и
  machine activation остаются DENY до своих code/release/live gates.
- P1 source: Windows gh reader без Gateway fallback, общий snapshot collector,
  fixed GET/identity/pipe limits и regression fixtures. Подтвердить реальные
  Git/GCM/gh scopes, browser main/PR/CI и независимость от Gateway на Windows
  по [Windows GitHub reader](WINDOWS-GITHUB-READ.md), без остановки Gateway.
- P2: один Core/worker/receipt и process guard; сохранить существующий общий
  browser check/Pull mutex, проверить crash/UNCERTAIN и все пути второго writer.
- P3: typed v2 READ при сохранённом strict v1; отдельные source CI,
  Windows install, Gateway release и fresh live READ с отрицательными probes.
- P4: signed ASK dispatch, deny legacy POST, principal-bound lookup и один
  exact-SHA ff-only; no-approval/READ credential/replay/timeout/concurrency tests.
- P5: owner cutover/rollback с сохранённой DB; старый listener/launcher не
  удалять до живой приёмки. P6 обновляет docs/evidence на каждом этапе.
- Windows-only inventory из открытого Approvals PR #87 — отдельный scope;
  не расширять зарегистрированные пары и права автоматически.

## Mirror UX / readback — source в task (до merge и Windows acceptance)

- Компактная таблица: независимо сворачиваемые группы Docker/Windows,
  сохранение размеров каждого столбца в браузере, поиск и фильтр действий,
  обновление выбранного Gateway-статуса без ложной свежести.
- Исправление Windows Git `core.autocrlf` (системный config не игнорируется при
  чтении), сохранение точного porcelain ` M`, повторные **только read-only**
  проверки после fast-forward, конкретные коды непрошедшего readback и
  получение прежнего receipt через обычную проверку выбранного репозитория
  без F12/повторного Git.
- Источник и Linux/Node-тесты **не доказывают Windows runtime/UX acceptance**.
  После принятого PR и обновления Windows clone нужно отдельно проверить
  реальный Browser Mirror, размеры колонок, сохранение после перезапуска
  браузера, CRLF, `UNCERTAIN` и отсутствие второго Git-эффекта.

## Pull recovery — source follow-up 2026-11-16

- Обычная проверка обновляет receipt и строку даже при UNCERTAIN; отдельная
  кнопка результата Pull удалена. Сохранены session ownership и запрет replay.
- Checkpoints до fetch/merge/readback, безопасная категория fetch failure,
  read-only разрешение известного pre-merge отказа в FAILED. DISPATCHED и
  оставшийся UNCERTAIN блокируют новый UUID; P2 process guard ещё не принят.
- После restart — внутренняя repo-scoped сверка без доступа новой сессии к
  старой квитанции. Неизвестный исход блокирует Preview/metadata fetch.
- Source/fixture checks не подтверждают установленную Windows runtime.
  Требуется owner acceptance по [runbook](WINDOWS-GITHUB-READ.md#pull-failure-recovery--source-follow-up-2026-11-16).

## Производительность основной проверки — 2026-11-09

- Source fix в отдельной task/PR: отделить свежую проверку GitHub `main` от
  дорогостоящих проверок старых PR и workflow runs. **«Проверить выбранный/все»**
  используют один и тот же быстрый режим, без кэширования SHA и без изменения
  безопасности Pull. Подробная PR/CI проверка остаётся доступной отдельной
  кнопкой нижней вкладки Worktrees, только для выбранного репозитория.
- На живом Gateway измерено: `docke-windows-mirror` старый full-inspection
  ~28.7 с, новый main-only ~2.0 с. Четыре main-only чтения при четырёх
  параллельных запросах ~2.2 с. Локальный Docker exporter: 31 репозиторий,
  118 worktrees ~2.8 с. Эти цифры **не включают Windows Git**, настоящий
  браузер и Docker exec bridge из Windows. Windows owner acceptance и
  финальные wall-clock измерения остаются отдельной задачей.
- В обоих режимах отсутствие Gate/PR/CI подтверждения всегда остаётся UNKNOWN;
  дорогостоящие подробности не появляются автоматически или из кэша.

## Точное направление Windows Git при проверке — source change (2026-11-09)

- При разных Windows и GitHub SHA обычная read-only инвентаризация не может
  доказать направление без целевого Git-объекта. Кнопки «Проверить выбранный» и
  «Проверить все» теперь используют отдельный owner-session POST с CSRF,
  допустимый только в штатном Browser runtime. После Gateway-проверки main
  выполняется Windows `git fetch origin main` без интерактива (тайм-аут 45 с),
  проверяется точный `FETCH_HEAD`, HEAD/branch/origin/clean readback и ancestry.
  Ни checkout, ни merge, ни reset в проверке нет; GET остаются read-only.
- Если Windows не чистая / не main / origin не совпадает / Docker main не
  подтверждён — fetch не выполняется. Если fetch не удался, менялся SHA или
  Windows-клон во время fetch, состояние не объявляется «отстаёт», Pull
  блокируется до новой проверки. На настоящей Windows остаётся acceptance
  скорости массовых проверок и отказов в сетевом Git/GCM.
- Старые числа ~2.0 с на репозиторий измеряли только Gateway, без сетевого
  Windows fetch. Теперь wall-clock зависит также от сети и объёма Git-объектов.

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
- Принятый Gateway Mirror READ v1 и работающий AI ASK Pull — разные стадии.
  Provider READ находится в Gateway main; исторический untracked `pull_main`
  не является принятой реализацией эффекта. TLS/receipt/reject/replay и AI ASK
  gates остаются открыты в Unified Mirror API P0–P5 выше. Владельцы —
  UC-GW-003/005/009/012; source/catalog не доказывает Windows live acceptance.
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
