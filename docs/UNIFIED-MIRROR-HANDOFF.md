# Unified Mirror — вход новому чату

**Главный трек (срез 2026-11-19): P5 owner preflight для READ v2.
Не production cutover.** P0–P3 исходники приняты; Mirror
[PR #51](https://github.com/AlexPastukhh/docke-windows-mirror/pull/51)
(source-only public machine_generation candidate) merged и exact CI
PASS. Живой Gateway на момент среза имеет только READ v1 ALLOW;
READ v2/WRITE не активированы.

**Начать с:** /workspace/repo/AGENTS.md, /workspace/repo/README.md,
полностью UC-CONT-002, UC-CONT-003, UC-GW-009; затем
[WORKFLOW](WORKFLOW.md), [READ-V2](READ-V2.md),
[generation](unified-api/READ-V2-GENERATION.md),
[dated P5 owner evidence](unified-api/P5_OWNER_HANDOFF_EVIDENCE_2026-11-19.md),
[backlog](BACKLOG-IMPROVEMENTS.md) и
[UC-GW-012](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md).
Backlog heading/table previously dated **2026-11-17**; P5 evidence
is the newer dated supplement and MUST be read alongside it.
P5 [isolated ledger restore verifier proposal](unified-api/P5_LEDGER_ISOLATED_RESTORE_PREP_2026-11-19.md)
adds offline **backup-copy-only** source and fixture checks. It does not
capture the production DB, certify an owner restore, or change READ v1.
Старый handoff ошибочно начинал с P4; **P4 signed ASK/WRITE —
отдельный незавершённый трек**.

**Fresh state at snapshot:** Mirror source main
bc8d2f6485bd27d7f87f0a58a181319caad8de61,
Approvals GitHub/Docker/Windows main
e349d2aa37ce1bbd5ac86b31107b6e4412cf9607.
Fresh Gateway v1 READ request
73e31b43-fdf3-441c-b2e5-f2a1b40b3490.

**First work:** recheck actual GitHub/Docker/Windows SHA,
listener/process and Gateway catalog. Owner needs to approve public
READ credential revision **and its Windows epoch relation**,
external manifest/candidate, SQLite integrity/isolated restore,
rollback and live v2 negative acceptance. Windows TCP State=0 was
UNKNOWN despite TLS PASS; not LISTEN PASS.

**Do not** restart/replace occupied HTTPS/443 v1 listener,
rotate credentials, read Bearer/private key, mutate ledger,
deploy/reconfigure Gateway, Windows Pull, enable WRITE or claim
GO without separate owner authorization. Source work in registered
task worktrees; remote GitHub effects only typed Gateway; owner
merges PR via GitHub UI.

Source contract [MIRROR_API_CONTRACT_V1](MIRROR_API_CONTRACT_V1.md),
[closed catalog](unified-api/contract.json), [UC-MIR-005](use-cases/api.md).
