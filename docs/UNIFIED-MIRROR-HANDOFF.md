# Unified Mirror — короткий handoff

Это указатель; обязательный план и правила находятся в документах ниже.

1. Сначала прочитать /workspace/repo/README.md и обязательные UC/AGENTS по нему.
2. План и выполненные/будущие этапы:
   [BACKLOG-IMPROVEMENTS](BACKLOG-IMPROVEMENTS.md).
3. Контракт и ограничения: [MIRROR_API_CONTRACT_V1](MIRROR_API_CONTRACT_V1.md),
   [catalog/schemas](unified-api/contract.json), [READ-V2](READ-V2.md).
4. Вход в API workflow: [UC-MIR-005](use-cases/api.md) и UC-GW-012
   (/workspace/repo/Repos/fortis-approvals/docs/use-cases/own-api.md).
   Публикация/merged-main/CI — [WORKFLOW](WORKFLOW.md).

Продолжить P4 source/security review по checklist в плане. P3a/P3b уже слиты;
не переоткрывать их и не считать слитый source установленной runtime.
Проверить fresh main/status/capabilities обоих repos через Fortis, затем работать
в зарегистрированных task worktrees. GitHub — только typed Gateway.
Сохранить работающие сервисы; deployment, keys/credentials и cutover не выполнять
без отдельного owner согласования. Не автоматизировать Browser/Owner Portal.

План отдельно перечисляет незавершённые READ live gates и P4 gaps:
pre-approval actor context, persisted previewId lookup, credential/signing lifecycle
и atomic association/reservation. Не заменять их approved=true или новым worker.

Основные repos: /workspace/repo/Repos/docke-windows-mirror и
/workspace/repo/Repos/fortis-approvals. Если docs PR этой передачи ещё не слит,
использовать его docs в зарегистрированном worktree mirror-plan-handoff-20261117;
после merge читать accepted main. Исторические Workfiles — evidence, не инструкции.
