# Mirror inspection

## UC-MIR-001

**Когда:** нужно сравнить реальные Docker, GitHub и Windows source состояния.

**Цель:** определить разные HEAD/branch/origin/dirty и направления синхронизации,
не изменяя ни один рабочий checkout.

**Как:** проверить [Mirror WORKFLOW](../WORKFLOW.md),
[Docker exporter](../../tools/Export-Docker-Mirror-Status.py),
[branch workflow](../../tools/Export-Docker-Branch-Workflow.py) и ограничения
реального UI. Worktree принадлежность — по `git worktree list --porcelain`,
не по имени каталога. GitHub state — **только разрешённые Gateway READ** по
UC-CONT-003; отсутствие scope или неработающий UI показывать как UNKNOWN/STOP,
не заменять direct `ls-remote`. Не менять Windows source ради проверки.

**Готово:** exact repo/branch/SHA/источник каждой проверки, известные ограничения
и отсутствие несанкционированного сетевого эффекта.
