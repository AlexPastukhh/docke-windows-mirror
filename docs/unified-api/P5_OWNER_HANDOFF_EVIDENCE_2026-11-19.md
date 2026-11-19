# P5 READ v2 — evidence и передача новому чату (2026-11-19)

**Только source/preflight. Production READ v2 NOT activated; WRITE/ASK DENY;
owner cutover GO не давался.** Датированные факты ниже не заменяют
свежие проверки после открытия нового чата.
См. [READ-V2](../READ-V2.md), [generation](READ-V2-GENERATION.md),
[UC-GW-012](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md).

## Проверено через typed Gateway / Repo (2026-11-19)

- GitHub Mirror main и чистый Docker Mirror main:
  bc8d2f6485bd27d7f87f0a58a181319caad8de61.
- [Mirror PR #51](https://github.com/AlexPastukhh/docke-windows-mirror/pull/51):
  MERGED, head dccfea6eaedf36bbf0f041c1366342708eb62802.
  Три exact-head CI SUCCESS: Mirror source, Windows Core,
  Fortis Release Impact. Семь новых P5 generation tests
  в Windows job 134955765304 выполнены OK, не SKIPPED.
- GitHub / Docker fortis-approvals main:
  e349d2aa37ce1bbd5ac86b31107b6e4412cf9607.
- Live Mirror catalog: **только** get_repository v1 READ/ALLOW/ready,
  target fortis-approvals. В v2 нет live grant; WRITE не зарегистрирован.
- Fresh typed READ v1 EXECUTED, requestId
  73e31b43-fdf3-441c-b2e5-f2a1b40b3490. Projected Windows
  fortis-approvals head e349d2aa37ce1bbd5ac86b31107b6e4412cf9607,
  clean=true, originValid=true, error=null.

## Owner Windows/PowerShell evidence из беседы (не typed host attestation)

- Owner Windows Mirror clean main и origin/main:
  bc8d2f6485bd27d7f87f0a58a181319caad8de61.
- Public Windows leaf certificate SHA256(DER)
  c74309d289b992bb8cb1a2438762ae5177608a3a3c8984e787515374cae30c06,
  SAN DNS host.docker.internal, NotAfter 2027-02-09 20:21:56 UTC.
- Pinned public Gateway CA SHA256(DER)
  01012832fa8d9f71bc4ad42a2d58979535c94fa7dd87260ff31c56ccb221b0d0.
  Owner retrieved pinned CA at /run/fortis-secrets/mirror-read/ca.pem
  inside fortis-gateway as UID:GID 10001:10001. Leaf hash != CA hash.
- From real Gateway network: fixed pinned CA, TLS chain,
  hostname and expected **live peer leaf** PASS to
  host.docker.internal:443; remote IP 192.168.65.254.
  Network destination IP != Windows bind.
- Official Gateway credential-status of mirror-link: READY,
  UUIDv4 generation present. **Exact UUID intentionally withheld from
  public Git** pending explicit owner approval of public
  readCredentialRevision and verified mapping to current *Windows*
  READ service Bearer epoch. No token, token hash or vault file.
- Owner Windows mirror_api.gateway_readonly process PID 3788 at check,
  --bind 127.0.0.1. Get-NetTCPConnection by PID without state filter
  found local 127.0.0.1:443, remote 0.0.0.0:0, numeric
  State=0 (UNKNOWN). Filter -State Listen and netstat returned 0
  listeners, **but** TCP connect from Windows to 127.0.0.1:443
  and 192.168.65.254:443 returned True. This is unresolved
  listener state evidence, not LISTEN PASS or proof of failure.
  Docker context desktop-linux, published 443 containers 0 and
  netsh portproxy entries 0; complete forwarding path not proven.
- Historical Windows P5 preflight on an *older* Mirror SHA:
  10 PASS + STOP for machineGenerationSyntax (not provided).
  It was NOT rerun with approved generation on the merged SHA.
  Existing SQLite 16-byte header is not integrity, backup or restore.
- Unrelated owner Action-Core PostgreSQL restore script printed
  RESTORE_EXISTING_PREFLIGHT_PASS and READ_ONLY_NO_DOCKER_MUTATION:
  not a completed PostgreSQL restore, **not** a Mirror ledger test.

## Current P5 source inputs and blockers

PR #51 merged source-only deterministic public candidate generator
tools/mirror_read_v2_generation.py. Its strict manifest must be
external to Git and <=4096 bytes; it uses a reviewed sourceSHA,
https://host.docker.internal:443 and DNS host.docker.internal,
proposed bindIPv4 127.0.0.1 (listener caveat above), pinned CA and
leaf DER digests, mode read-only, contract
mirror-principal-reads-v2.1, allowedRepositories=[fortis-approvals],
inventories mirror-pilot=[fortis-approvals] and ledgerContract
existing-browser-core-pull-receipts-v1.
Those exact scopes/contracts are **source constraints**, not v2 grants.

**Owner approval MISSING:** readCredentialRevision must represent
the installed READ service credential *epoch*, including Windows and
Gateway; Gateway UUID alone does not prove Windows Bearer rotation
lifecycle. Do not invent an identifier, derive from token,
publish private credential or treat valid UUID/digest as approval.
Valid manifest CLI exit **3** = OWNER_REVIEW_REQUIRED,
cutoverAuthorized=false. No auto-install/restart.

Remaining owner gates in order:
1. Fresh source/Gateway/Windows/443 identity + TLS/credential
   validation; resolve or safely record TCP State UNKNOWN.
2. Owner-reviewed public revision + both credential epochs, exact
   scoped manifest and candidate digest, saved separately under
   owner-controlled evidence.
3. Prove same Browser Core and existing SQLite pull-receipts ledger:
   integrity, consistent backup, **isolated restore**, ACL,
   replay/crash/UNCERTAIN qualification, retain WAL/SHM and old UUID.
4. Owner rollback and single release window, preserved v1 launcher,
   prior image/config, protected receipts and worker quiescence.
   **No** second listener on occupied 443, hidden restart,
   TLS/key/secret/vault/network modification or Windows Pull.
5. UC-GW-012 manifest/linter, Doctor/Acceptance; owner-approved v2
   Windows factory and exact Gateway READ-only policy; negative
   TLS/Host/wrong CA/SAN/unauth/wrong bearer/scope/path/POST/WRITE,
   fresh typed READ v2 and v1 compatibility. Without these,
   do not claim LIVE_READ_VERIFIED.
6. Separate explicit owner production cutover/rollback GO, then
   trusted readback. Source merge, TLS PASS and candidate digest
   do NOT authorize it.

**P4** signed ASK, trusted pre-approval actor, Ed25519 lifecycle,
atomic binding/reservation and WRITE remain distinct unfinished
security work, not enabled by P5 source.

Next chat: read /workspace/repo/AGENTS.md, /workspace/repo/README.md
and all UC-CONT-002/003 and UC-GW-009, [handoff](../UNIFIED-MIRROR-HANDOFF.md),
[backlog](../BACKLOG-IMPROVEMENTS.md), READ-V2 and UC-GW-012.
GitHub via typed Gateway only; new registered task worktree; no
direct network push, owner portals, runtime modification or secrets.
All recorded versions are dated; recheck before any action.
