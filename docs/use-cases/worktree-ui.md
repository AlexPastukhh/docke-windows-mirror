# Mirror worktree / UI

## UC-MIR-003

**Когда:** нужно увидеть или выбрать Docker task linked worktree в Mirror.

**Цель:** гарантировать выбранную identity/path/branch и корректные ограничения UI.

**Как:** сверить [WORKFLOW](../WORKFLOW.md),
[Docker exporter](../../tools/Export-Docker-Branch-Workflow.py),
`git worktree list --porcelain` и [API-FIRST](../API-FIRST.md). Сохранённый
WinForms UI и API-first source candidate имеют разные ограничения; наличие
inventory в source не доказывает установку Windows API. Не создавать Windows task worktrees для обычных проектов.

**Готово:** идентичность цели доказана, либо UI явно не поддерживает действие.

## UC-MIR-004

**Когда:** владелец использует Mirror UI и просит выполнить поддержанную операцию.

**Цель:** не смешивать read-only inspection, local Git stage/commit,
GitHub network, Windows Pull и deployment.

**Как:** проверить [README](../../README.md), [WORKFLOW](../WORKFLOW.md) и
[Docker Git actions](../../tools/Docker-Mirror-Git-Actions.py). Local Git без
сети — отдельная операция; GitHub push/PR/merge — только typed Gateway client.
Legacy Docker helper уже блокирует прямые GitHub network операции.
Сохранённые WinForms-механизмы не являются разрешённым fallback.
Owner confirmation не заменяет Gateway scope/WebAuthn. После PR и проверок
владелец выполняет merge через GitHub UI по UC-CONT-002; AI не создаёт merge ASK.

**Готово:** READ/локальный effect/внешний GitHub effect классифицирован, результат
проверен по соответствующему trusted source либо STOP.
