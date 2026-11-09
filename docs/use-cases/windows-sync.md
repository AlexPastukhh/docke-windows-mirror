# Windows main sync

## UC-MIR-002

**Когда:** owner получил PR с подтверждённым merge/CI и **Windows-clone нужен
для работы/проверки/установки**, либо сам запросил Windows main sync. Один
только факт merge не требует запускать Mirror или Windows PowerShell.

**Цель:** выполнить только проверенный fast-forward Windows primary checkout.

**Как:** использовать [canonical Mirror WORKFLOW](../WORKFLOW.md) и общие
[UC-CONT-002](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-002)/
[UC-CONT-005](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md#uc-cont-005).
Windows на `main`, clean и без unexpected ahead; сверить approved merge SHA,
origin и branch, preflight повторить перед изменением файлов. Не выполнять
скрытый reset/stash. Owner-local Browser Pull работает через session-bound Preview/confirm и
имеющийся Windows `PullWorker` без Gateway ASK. Gateway-backed AI Pull остаётся
отдельным незапущенным маршрутом. Windows acceptance обоих режимов
описаны в [API-FIRST](../API-FIRST.md). Старый WinForms путь с прямым GitHub
fetch не использовать как обход. Если требуемый backend недоступен — STOP,
не PAT fallback. Source update не запускает Windows listener.

**Готово:** Mirror показывает `EXECUTED`/receipt для сделанного Pull
и Windows `main` = verified GitHub `main` SHA при clean/approved origin,
либо указывает конкретную ошибку или `UNKNOWN`. **Mirror выполняет сравнение SHA
сам**: после успешного readback не нужно запускать отдельную PowerShell-команду,
просить владельца переписать SHA в чат или повторять Pull «для проверки».
При неизвестном результате использовать в том же Mirror `Проверить результат`
и receipt, а не второй Pull. Наличие одной кнопки Pull без результата — не PASS.
