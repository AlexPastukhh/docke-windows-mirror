# P5 READ v2 — Windows owner evidence update (2026-11-19)

**Snapshot, not a deployment or production GO.** This supplements the
[earlier P5 handoff](P5_OWNER_HANDOFF_EVIDENCE_2026-11-19.md) and the
[source-only isolated restore guide](P5_LEDGER_ISOLATED_RESTORE_PREP_2026-11-19.md).
Windows values below were reported by the owner in the interactive PowerShell
session, **not independently inspected from the Fortis Repo Linux container**.
Do not substitute this dated snapshot for a release-window recheck.

## Accepted source and Windows preflight

- Mirror [PR #53](https://github.com/AlexPastukhh/docke-windows-mirror/pull/53)
  was owner-merged. GitHub and Docker Mirror `main` were confirmed clean at
  `3cf1576bb3306a6894020dd8a1408fb771ca48f5` through the typed Gateway
  and local worktree readback. Windows owner ran the accepted
  `mirror_read_v2_cutover_preflight.py` against that exact SHA.
- Owner preflight result: **10 PASS / 1 STOP**, exit 2;
  `machineGenerationSyntax=OWNER_REVIEWED_MACHINE_GENERATION_REQUIRED`,
  `cutoverAuthorized=false`. The clean Windows source, origin identity, main
  tracking, ledger existence and 16-byte SQLite header passed. A header is
  **not** an SQLite integrity check.
- Windows owner identified Browser/Core process **PID 2720** running
  `mirror_api.server` with neither `--gateway-receipt-db` override nor
  preview mode; standalone READ v1 **PID 3988** running
  `mirror_api.gateway_readonly` with loopback bind `127.0.0.1`.
  Both processes were running under the interactive owner identity. PIDs and
  start times are ephemeral; they are not immutable installed-image/source
  attestations. Running Python modules may predate Windows Git sync.
- TCP owner readback showed port 443 attributed to PID 3988, but no
  `Get-NetTCPConnection -State Listen` rows and an unrecognized/null State.
  **Listener state remains UNKNOWN**, not LISTEN PASS or evidence of outage.
  Successful Gateway READ v1 confirms the *application route*, not this TCP
  diagnostic's socket state.

## SQLite continuity — owner-operated, offline restore

- Existing owner-local production Browser/Core receipt database was observed
  present, size **90,112 bytes**, with neither `-wal` nor `-shm` file **at the
  time of observation**. This does not prove quiescence or absence of new
  writes. The Browser/Core source default without a DB override resolves to
  that existing receipt store; direct live handle/path evidence is still a
  release gate.
- Owner created a separate evidence root with protected NTFS inheritance and
  exactly three allow principals: current owner, SYSTEM and Administrators.
  The root, `backups` and `restores` were inspected before capture. The
  source ledger's ACL remains *inherited* and grants `CodexSandboxUsers`
  **ReadAndExecute**. The explicit source of this rule was found above
  LOCALAPPDATA, at the parent `AppData` level. **Do not change a profile-wide
  ACL**. A narrow owner-reviewed remediation and dependency check are pending.
- First owner inline `py -3 -c` backup attempt failed at Python parsing;
  **no backup was created** by that attempt. The subsequent owner-run
  file-based script used `sqlite3.Connection.backup` to capture a consistent
  single-file copy in the separate evidence root, without a database copy
  via ordinary file operations. The reported output was
  `SQLITE_BACKUP_INTEGRITY_PASS`, `BACKUP_FILE_ACL_PASS`.
- The accepted `tools/mirror_read_v2_ledger_restore_verify.py` then reported
  `BACKUP_INTEGRITY_PASS`, `RESTORE_INTEGRITY_PASS`,
  `SCHEMA_AND_ROW_COMPARISON_PASS`, `isolatedBackupRestore=PASS`,
  `state=MANUAL_REQUIRED`, `cutoverAuthorized=false`, exit **3**.
  One metadata row and **93 pull receipts** matched between the backup and
  isolated restore. This is *backup-copy equivalence*, not a live-database
  rewind/rollback or continuous receipt verification.
- Owner queried **only the isolated restored copy**. It contained 91
  `EXECUTED`, 2 `FAILED`, 0 `UNCERTAIN`, 0 `DISPATCHED`, 0 `PENDING`,
  0 `RESERVED`, 0 `RUNNING`, and 0 other states. No active/uncertain receipt
  existed in that copy **at backup time**. Never discard the two `FAILED`
  records or infer that newer live receipts are absent.
- The backup, restored SQLite, private ACL and any owner record remain on
  Windows outside Git. Do not publish their contents, paths, identifiers,
  token values or hashes of credentials.

## Post-outage container/Gateway readback

The owner ran read-only Docker Engine inventory after a reported outage:
`fortis-repo-test` was **running** (no container healthcheck),
`fortis-gateway` **running/healthy**, and FeedBrain PostgreSQL/API
**running/healthy** with both workers running. Repo/Approvals source
operations and a fresh typed Gateway Mirror READ v1 probe independently
worked after that report: `get_repository` v1 READ/ALLOW/ready,
request `1a43c5b0-ecd1-4229-8ef7-f0cd53b72242` **EXECUTED**.
This is *not* proof that every historical/auxiliary container needs to run.
Do not restart legacy Gateway, inactive backup/restore helpers or an
additional 443 listener merely because their state is `exited`.

## Remaining owner acceptance gates — DO NOT CUT OVER

1. **Installed runtime identity:** new window-specific process and socket
   ownership, source/code loaded into Browser/Core and READ v1 listener,
   actual shared ledger provenance and worker quiescence. No forced restart.
2. **Ledger ACL/rollback:** owner reviews inherited `CodexSandboxUsers`
   access only in the minimum Mirror scope; retain exact existing ACL and
   rollback evidence. Preserve current source, runtime, 93 historical receipts,
   WAL/SHM and later records. Qualified crash/replay and restore/rollback
   rehearsal remain separate even with an intact offline copy.
3. **Credential epoch:** independently verify Gateway READ credential
   generation and the installed Windows READ Bearer credential are the same
   functional epoch, then approve a public canonical UUIDv4
   `readCredentialRevision` with a rotation rule. A successful v1 request
   supports current authentication interoperability, **not** public epoch
   provenance or lifecycle approval. Never derive a revision from a secret.
4. **Public `machine_generation`:** owner populates and verifies the external
   closed-schema manifest with the *current installed source SHA*, public
   leaf/CA DER SHA-256 fingerprints, DNS/HTTPS origin, exact 127.0.0.1 bind,
   one-repo scope and ledger contract. Use the accepted offline generator
   **only after** credential approval. Candidate exit 3 requires owner review;
   it is never cutover authorization.
5. **Gateway/Windows release:** preserve READ v1; separately approve
   rollback, exact UC-GW-012 Gateway manifest/grant/policy and Windows
   listener factory. Independently prove TLS CA/SAN/Host/Bearer and negative
   scope/path/method/WRITE checks from Gateway namespace; fresh typed READ v2
   **and v1 compatibility** only after explicit owner-approved release.
   A second occupied 443 listener, hidden credential rotation and automatic
   Windows restart remain forbidden.
6. **Owner production GO:** a separate explicit signed/authorized decision
   is still required. P4 signed ASK/WRITE is an independent incomplete gate.

**Status: SOURCE / OFFLINE RESTORE PASS, RUNTIME / ACL / CREDENTIAL /
GENERATION / ROLLBACK / LIVE v2 NOT ACCEPTED; NO GO.**
