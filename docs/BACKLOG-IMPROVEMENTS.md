# Backlog improvements — Docker–Windows Mirror

**Единый файл будущих улучшений этого репозитория.** Mirror владеет UI, проверкой Docker/GitHub/Windows SHAs и owner-guarded Windows import. Нормативный процесс GitHub → CI → Windows описан только в [`docs/WORKFLOW.md`](WORKFLOW.md) и общем UC-CONT-002; backlog не заменяет runbook и не разрешает mutation.

## Unified Mirror API — текущий план (проверено 2026-11-17)

Это единственный план этапов Mirror. Контракт — [MIRROR_API_CONTRACT_V1](MIRROR_API_CONTRACT_V1.md);
технические READ/activation gates — [READ-V2](READ-V2.md). Общие процедуры не дублируются:
доставка — WORKFLOW/UC-CONT-002/003, подключение API — UC-GW-012.
Исторический [P0 review](unified-api/P0_SOURCE_REVIEW_2026-11-15.md) не описывает текущую runtime.

| Этап | Принятые исходники / результат | Что ещё требуется |
| --- | --- | --- |
| P0 | Контракт и wire v2/schema; модель B (Ed25519), PR #23 | Signing capability и key lifecycle не реализованы принятием контракта |
| P1 | Windows owner-managed gh reader, общий bounded collector, без Gateway/PAT fallback | Полная Windows Git/GCM/gh и PR/CI/error qualification; базовое «Browser работает» не заменяет протокол |
| P2 | Общий Core/worker/ledger, root/process guard, bounded children; PR #27/#28/#29, Linux/Windows CI | Полный owner negative/crash/rollback протокол и lifecycle qualification |
| P3a | 6 bounded observation READ v2; Mirror #30 и Approvals #106 слиты | Установка и live READ v2 с отрицательными probes |
| P3b | Durable principal-bound preview/lookup; Mirror #31 и Approvals #108 слиты, CI зелёный | Нет production writer для machine_bindings до P4; установка/live acceptance отдельно |
| P4 | **Не реализован:** signed ASK dispatch и атомарные binding/reservation | Source/security review, код и отрицательные тесты ниже; WRITE до admission остаётся DENY |
| P5 | **Не выполнен:** owner release/cutover/rollback | Согласованные Windows/Gateway release, сохранение ledger, реальная приёмка; старый listener/launcher сохраняются до неё |
| P6 | Документы/evidence обновляются на каждом этапе | Продолжать фиксировать source, installed runtime и live results отдельно |

Ссылки на принятые P3 изменения:
[Mirror #30](https://github.com/AlexPastukhh/docke-windows-mirror/pull/30),
[Approvals #106](https://github.com/AlexPastukhh/fortis-approvals/pull/106),
[Mirror #31](https://github.com/AlexPastukhh/docke-windows-mirror/pull/31),
[Approvals #108](https://github.com/AlexPastukhh/fortis-approvals/pull/108).

P4 source/security review на свежих `main` обоих проектов: [P4_SOURCE_SECURITY_REVIEW_2026-11-17](unified-api/P4_SOURCE_SECURITY_REVIEW_2026-11-17.md). Он фиксирует проверенные code gaps и порядок реализации, **не** означает P4 implementation, WRITE activation или live acceptance.

P4 private lookup source candidate: `mirror_api/machine_reads.py:persisted_preview_for_ask` читает по `previewId` только неизменяемую actor/repo/generation/TTL-bound запись в общей ledger, без новой HTTP route, Git, signer или WRITE. Это лишь внутренний строительный блок: доверенный Gateway ASK actor context, свежая проверка предусловий и атомарный binding/reservation остаются незавершёнными.

### Где остановились

P0–P3 приняты **по исходникам**. В каталоге 8 READ implemented=true,
default DENY и runtimeActivation=false. Strict get_repository v1 сохранён.
P3b хранит immutable preview в общей ledger и читает только связанные machine
receipts; Browser/чужие UUID не открываются. Production association writer
отсутствует, успешные associations в тестах созданы только в изолированных fixtures.
Повтор preview не обновляет TTL; NOT_FOUND/DISPATCHED/UNCERTAIN не доказывают NONE.

Установка/активация unified READ v2 и live acceptance **в этой работе не выполнялись**.
Последнее сообщение владельца подтверждало базовую работу Browser после обновления,
а не полный P3 runtime протокол. Актуальные установленные версии, catalog, CA/bind
и scopes новый исполнитель проверяет заново; исторические SHA не являются current state.
Source P3 checks: Mirror 235 tests (2 Windows-only skips), Gateway после объединения
с другими принятыми изменениями 1030 tests; exact-head CI зелёный; 8 loopback-only
pinned HTTPS fixture calls. Эти результаты не доказывают живую интеграцию.

### Следующая работа: P4 source/security review

1. Сверить D-04 и закрытые schemas с свежими main/capabilities. Сохранить один
   Core/worker/ledger, v1 и default DENY; не создавать альтернативный executor.
2. До реализации оценить существующий Gateway vault/controller: отдельные READ,
   WRITE и signing роли, generations, pinning/rotation/revocation и Windows
   dependency/verification profile. Ed25519 выбран; ключи не provisioned.
   Проверить ограничение Operation.credential/Context на один credential.
3. Решить доверенную передачу actor **до ASK**: нынешний trusted_identity читает
   уже сохранённую Core row в execute/reconcile; Core.submit.prepare вызывается
   до вставки этой row. Не переносить этот helper в pre-approval без изменения
   контракта и не принимать actor/approved из публичных params.
4. Связать executeParams.previewId с точным persisted snapshot и свежими
   preconditions. Текущий preview GET повторяется по исходному READ UUID;
   закрытый способ чтения snapshot по previewId для ASK нужно спроектировать.
   Никакого обновления target/TTL после ASK; stale требует новой заявки.
5. Проверяемая подпись только после Core approval/revalidation. В одной durable
   транзакции связать actor/repo/UUID/immutable identity и зарезервировать эффект.
   Не привязывать старые Browser receipts и не писать binding отдельной
   незащищённой стадией перед reservation. Использовать существующий worker.
6. Покрыть Reject/no approval, READ credential на WRITE, неверные claim/key/actor/
   repo/SHA, TTL/generation/replay/conflict, concurrent writers, crash/timeout/lost
   ack и readback. После неоднозначного эффекта — lookup исходного UUID без replay.

Опорные исходники: mirror_api/core.py, machine_reads.py, read_v2.py, gateway_v2.py,
pull.py; в Approvals — integrations/mirror/read_v2.py и tls.py, extensions/model.py,
runtime.py, vault.py и core.py. Перед изменением проверять текущие реализации.
Пункты 2–4 — проверки/дизайн, а не обещание уже существующих SDK capabilities.

### Admission и внедрение

READ v2 допускается отдельно через существующие UC-GW-012 manifest/linter,
Doctor/Acceptance и согласованное owner window. Проверяются точный installed
source/image/config, фактический Gateway network path, pinned HTTPS/443,
scoped READ credential, отрицательные TLS/auth/scope/write probes.
Owner READ release можно планировать отдельно от разработки P4; ASK допускается
только после security review и live READ gate. Signing/WRITE credential, key
provisioning, установка dependencies, изменение bind/портов, restart/deployment
и retirement старого listener требуют отдельного согласования.
Windows-only inventory и расширение approved scopes — отдельные задачи.

## Mirror UX / readback — source принят, частичная Windows acceptance

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
  оставшийся UNCERTAIN блокируют новый UUID; полная Windows qualification P2 остаётся отдельной.
- После restart — внутренняя repo-scoped сверка без доступа новой сессии к
  старой квитанции. Неизвестный исход блокирует Preview/metadata fetch.
- PR #25/#26 приняты. После обновления Windows clone владелец 2026-11-16
  сообщил «всё работает» для ранее заблокированного Browser Pull. Это базовая
  owner-reported проверка, не полный протокол негативных/PR/CI сценариев по
  [runbook](WINDOWS-GITHUB-READ.md#pull-failure-recovery--source-follow-up-2026-11-16).

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

- Базовый запуск Browser/Pull подтверждён сообщением владельца выше; полный
  протокол inventory/PR/CI, freshness/error cases пока не подтверждён.
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

## Legacy preflight receipt recovery — source follow-up 2026-11-16

- Подтверждённый legacy GITHUB_MAIN_CHANGED без checkpoints: FAILED по известному отказу до fetch и безопасному readback, в том числе после перехода Windows на третий SHA.
- Одна блокировка в итоговом статусе, оценке Preview и кнопке Pull; число незавершённых операций видно в деталях.
- Негативные fixtures сохраняют DISPATCHED/unknown/merge/unsafe readback и session ownership. Owner Windows acceptance остаётся отдельным этапом; P2 process guard не реализован этим исправлением.
