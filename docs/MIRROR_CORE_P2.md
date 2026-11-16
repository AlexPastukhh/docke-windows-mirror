# Mirror Core P2 — accepted source

P2 source was accepted in PR #27, with the clipboard/readback follow-ups in
#28/#29. After updating Windows, the owner reported that Browser works. This
is basic owner-reported acceptance, not the full negative/crash/rollback
qualification and not an enabled machine v2 provider. The logical contract remains
[MIRROR_API_CONTRACT_V1](MIRROR_API_CONTRACT_V1.md); the delivery sequence and
remaining gates remain in [the backlog](BACKLOG-IMPROVEMENTS.md).

## Ownership

| Module | Responsibility |
| --- | --- |
| `core.py` | One domain Core, shared worker/store, preview facts, owner history checks and lifecycle |
| `browser_pull.py` | Browser principal/session, nonce, expiry and one-use confirmation binding |
| `pull.py` | Exact-SHA executor and durable outcome/replay rules |
| `process_guard.py` | Canonical-root writer exclusion and persistent receipt-store binding |
| `commands.py`, `command_child.py` | Bounded argv execution and pre-execution child handshake |
| `windows_job.py` | Narrow Win32 Job Object boundary and process-tree termination evidence |

Browser routes and the optional replacement Gateway listener receive the
same Core. The accepted standalone GET-only pilot stays unchanged until the
reviewed cutover. It remains safe to run as a reader, with GitHub NOT_CHECKED.
The replacement v1 POST and unbound receipt lookup are deny-only; a service
bearer is not ASK. No v2 operation, signing key, provider grant, port/firewall
change or runtime restart is enabled by this source candidate.

## Writer and ledger lifetime

Preview does not acquire a writer claim or resolve an old worker outcome.
An explicit owner check/lookup performs bounded readback reconciliation.
The first effect claims `.fortis-mirror-writer.lock` in the canonical Windows
repositories root; it remains held until controller close. Port and receipt
DB changes cannot select another lock. Root aliases resolve to the same scope;
Windows reparse points in a clone or its `.git` directory are rejected.

The persistent lock record binds the canonical receipt path and an immutable
SQLite `mirror_store_meta.store_id`. Existing receipts are retained. A different
DB, or a newly empty DB at the old path, cannot become a replacement journal.
The record contains a fingerprint, not the DB path or credentials. Never delete
or rewrite the lock record or receipt DB to clear a block. Configuration/store
migration needs a separately reviewed owner operation; it is not an auto-repair.

The same per-clone lock protects history fetch and Pull. Independent clones
can proceed in parallel inside this Core; a second controller cannot write.
A refused new admission has its own FAILED/PRE_EFFECT_REFUSAL receipt, so a
browser UUID cannot get stranded without an outcome. An existing UUID still
returns lookup only. A refusal describes that admission, not an inferred
outcome of a historical operation in a different store.

## Children, bounds and crashes

On Windows a non-inheritable named Job covers writer children; each command
also has its own child Job. Timeout/overflow stops only that command's tree.
A private Python helper waits for GO until both Jobs own its retained process
handle. A controller crash before assignment leaves EOF, never a Git command;
after assignment KILL_ON_JOB_CLOSE covers the helper and descendants. No PID
reopen, taskkill, shell command interpolation or job-handle inheritance is used.
A new controller refuses an existing active/unconfirmed named Job. No success
or new effect is permitted when child termination cannot be established.

Core requests share a 60-second budget; subprocess limits are clamped to the
remaining budget with cleanup reserve. Git fetch/merge are at most 45 seconds.
Captured stdout/stderr are bounded while draining (1,000,000 / 16,384 bytes).
Owner responses are bounded at 1 MiB and the legacy machine response at 256 KiB.
The P1 gh transport keeps its bounded collector and shares the Core deadline.
Future v2 pagination/projection/schema limits still belong to P3.

DISPATCHED remains reserved after a crash. Neither an empty Job, a released
lock nor missing receipt proves that the requested Git effect did not happen.
An unconfirmed child prevents target readback from completing the outcome.
Existing known preflight recovery remains; no automatic replacement UUID,
fetch/merge replay, owner-session access expansion or fabricated receipt exists.

## Qualification and rollout boundary

Portable tests cover real OS exclusion, root aliases, controller death with
an inherited lock, immutable ledger binding, admission races, no replay,
stream overflow/deadline and pre-GO Windows API failure injection. The Windows
CI job runs only controlled Core/guard/command fixtures. Its crash test holds
handles to fixture children and verifies both child and grandchild termination.
It uses temporary roots, no network, no working service stop and no PID-based
kill of an unrelated process. A Linux skip is not Windows qualification.

Receipt transactions explicitly close their SQLite connection after commit or
rollback, including errors. This releases Windows file handles without waiting
for garbage collection; both transaction paths have regression fixtures.

Source validation on 2026-11-16: the complete `test_mirror*.py` suite ran 204
tests successfully on Linux, with two native Windows fixtures skipped; Node
syntax and `git diff --check` passed. Windows CI and owner-host results must
be recorded separately; this source evidence does not close those gates.

After accepted source and owner agreement, run the same fixture modules on
the actual Windows host, then verify browser check/Pull, strict legacy v1 READ,
wrong-DB refusal, restart and rollback with the preserved ledger. Before writer
cutover the owner must quiesce old pre-P2 writers: they do not honor this guard.
The independent opt-in owner PowerShell executor and manual owner Git remain
outside Mirror synchronization; do not use them for competing clone mutations.
This is process coordination, not a sandbox against compromised owner code or
owner-managed Git filters.

P3/v2 READ, P4 signed ASK and P5 launcher/cutover remain separate gates.
No Docker image build, Gateway release, Windows update or deployment was
performed by preparing this source candidate.
