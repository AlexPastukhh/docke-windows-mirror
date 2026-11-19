# P5 READ v2 — isolated SQLite restore verifier (source-only, 2026-11-19)

**Not a production acceptance record.** This is an offline, owner-run verifier
for a **previously captured, demonstrably consistent** copy of the existing
Mirror Browser/Core ledger. It does not take a backup from the live database,
inspect the installed Windows listener, approve machine_generation, change
the running Gateway or authorize cutover.

See [dated P5 handoff](P5_OWNER_HANDOFF_EVIDENCE_2026-11-19.md),
[READ v2](../READ-V2.md), [generation](READ-V2-GENERATION.md) and
[UC-GW-012](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md).

## Trust boundary and prerequisite owner evidence

1. Independently verify which **running owner Windows process** owns the
   Browser Core and existing `%LOCALAPPDATA%\FortisMirror\pull-receipts.sqlite3`.
   Do not trust a historic PID. Record source SHA, current process image/
   command line, DB identity and ACL privately. Do not restart listener 443.
2. In a separate owner-controlled, quiescent and approved backup procedure,
   produce a **consistent SQLite backup** using SQLite's online backup API
   (or equivalent proven transaction-consistent method). Do **not** copy only
   the DB file from a live WAL store and call it consistent. Preserve
   original WAL/SHM, unreadable/UNCERTAIN receipts and UUIDs unchanged.
   This verifier **never performs the live backup step**.
3. In a private Windows owner location, provision the directory
   `%LOCALAPPDATA%\FortisMirrorP5Evidence\` with independently verified
   owner-only NTFS ACL. Create its `backups\` and `restores\` subfolders.
   Place the captured *single-file consistent* copy named, for example,
   `backups\mirror-p5-consistent.sqlite3`; verify provenance/permissions
   separately. Do not place it inside Git, a shared Workfiles folder,
   Windows Downloads or chat. Do not include private DB bytes in PR/evidence.
4. Use a **new unused restore name**, with no existing directory. The tool
   refuses symlinks/reparse paths, hard-linked backup files, WAL/SHM sidecars,
   unknown application tables or schema and destinations that already exist.
   It never accepts a caller-specified live ledger path: all inputs are
   confined to the private sibling evidence root.

## Owner-only invocation after source and backup review

From the **accepted** Windows clone in PowerShell 5.1, after verifying the
script's exact reviewed SHA and the private backup:

```powershell
$repo = Join-Path $HOME 'Repos\docke-windows-mirror'
& py -3 (Join-Path $repo 'tools\mirror_read_v2_ledger_restore_verify.py') `
    --backup-name 'mirror-p5-consistent.sqlite3' `
    --restore-name 'p5-check-001'
$verifyExitCode = $LASTEXITCODE
```

Exit **3** with `state=MANUAL_REQUIRED` and
`isolatedBackupRestore=PASS` means only that the provided backup and the
isolated SQLite restore both passed `PRAGMA integrity_check`, the supported
schema and row-by-row comparisons. The tool leaves a newly restored database
under the private `restores\p5-check-001\` directory for owner inspection.
It does not delete or overwrite any evidence or set `cutoverAuthorized`.
Exit **2** indicates STOP; use a new destination name only after independent
root-cause investigation, not automatic retry or destructive cleanup.
All tool stdout is bounded generic evidence and table row counts; no payload,
Windows path, store_id or owner credential is printed.

The verifier never checks actual Windows production health, whether the
backup was transaction-consistent relative to the live ledger, source/ACL
provenance, worker quiescence, replay of UNCERTAIN, exact historical receipt
preservation versus live, TLS/443, or a rollback by swapping deployed assets.
**Those remain separate owner gates.** The private backup and restored DB must
be protected and retired under owner procedure; no automatic deletion.

## Remaining production gate after this source-only tool

- Public `readCredentialRevision`: owner-approved value matched to *both*
  Gateway READ credential and Windows service Bearer epochs. No secret,
  token hash or invented UUID can substitute for that review.
- `machine_generation`: owner-controlled external public manifest with
  current installed Mirror source SHA, reviewed leaf/CA DER fingerprints,
  exact bind/port and one-repo inventory; candidate exit 3 is not GO.
- Windows listener 443: independently identify live process, socket/bind and
  Gateway-namespace pinned TLS path. Prior Windows TCP State=0 is UNKNOWN.
- Rollback: verified old launcher/image/config, preserved Core and receipts,
  restoration procedure and worker quiescence. No second occupied 443 bind.
- UC-GW-012: exact READ-only Gateway catalog/admission, negative TLS/Host/
  bearer/scope/path/POST/WRITE checks, fresh typed READ v2 and v1
  compatibility, then **separate explicit owner cutover/rollback GO**.

**P4 signed ASK/WRITE remains separate and disabled.**
