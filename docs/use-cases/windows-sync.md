# Windows main sync

## UC-MIR-002

**Когда:** owner получил PR с подтверждённым merge/CI и хочет обновить Windows main.

**Цель:** выполнить только проверенный fast-forward Windows primary checkout.

**Как:** использовать [canonical Mirror WORKFLOW](../WORKFLOW.md) и общие
[UC-CONT-002](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-002)/
[UC-CONT-005](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-005).
Windows на `main`, clean и без unexpected ahead; сверить approved merge SHA,
origin и branch, preflight повторить перед изменением файлов. Не выполнять
скрытый reset/stash. Gateway-backed Pull candidate и Windows acceptance
описаны в [API-FIRST](../API-FIRST.md). Старый WinForms путь с прямым GitHub
fetch не использовать как обход. Если требуемый backend недоступен — STOP,
не PAT fallback. Source update не запускает Windows listener.

**Готово:** только exact approved SHA получен в Windows и readback совпадает,
либо указан проверяемый блокер; наличие кнопки Pull само по себе не PASS.
