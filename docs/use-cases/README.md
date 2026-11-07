# Mirror — локальный реестр Use Cases

Общие правила: [главный реестр Fortis](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/README.md),
обязательные UC-CONT-002 (Docker → GitHub → CI → Windows/Mirror) и UC-CONT-003
(GitHub **только** через typed Gateway client). **Все UC реестра читать не нужно**.
Локальные сценарии ниже описывают цели Mirror/UI и ссылаются на единственный
[технический WORKFLOW](../WORKFLOW.md) и исходники.

**Граница source/runtime:** API-first candidate и запрет прямых GitHub network
операций legacy Docker helper уже приняты в `main`. Публикация/чтение через
Gateway проверены для этого repo; доступность конкретной операции всё равно
проверяется по live capabilities и policy. Windows API/listener, TLS/WebAuthn
и реальный browser UI требуют отдельного owner acceptance по
[API-FIRST](../API-FIRST.md). Наличие source не означает установку.

| ID | Когда | Результат | Owner |
| --- | --- | --- | --- |
| UC-MIR-001 | Сверить Docker/GitHub/Windows состояния | Источники/SHA и неполные статусы разделены | [Inspection](inspection.md#uc-mir-001) |
| UC-MIR-002 | Получить approved GitHub main в Windows | Guarded Windows fast-forward / STOP | [Windows](windows-sync.md#uc-mir-002) |
| UC-MIR-003 | Выбрать Docker task worktree в Mirror | Точная ветка/working-tree binding либо ограничение | [Worktree UI](worktree-ui.md#uc-mir-003) |
| UC-MIR-004 | Использовать UI/проверки Mirror | Owner action с подтверждённым scope | [Worktree UI](worktree-ui.md#uc-mir-004) |
| UC-MIR-005 | Подключить API-first Mirror | SOURCE CANDIDATE; Windows acceptance отдельно | [API](api.md#uc-mir-005) |

Процедуры Гитхаба не дублировать: [UC-CONT-003](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-003).
Регистрация нового repo в Gateway — UC-CONT-018, не восстановление PAT.
