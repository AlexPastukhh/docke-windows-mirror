# Mirror READ v2 — accepted P3 source

P3a (Mirror #30 / Approvals #106) and P3b (Mirror #31 / Approvals #108)
are owner-merged source. Current stages and remaining work: [plan](BACKLOG-IMPROVEMENTS.md).

P3a supplies six observations and P3b adds bound preview/lookup from the [closed catalog](unified-api/contract.json):
list repositories, repository snapshot, worktrees, GitHub status, available actions,
capabilities, preview and result lookup. Their `implemented` flag means source readiness. Every default
policy remains DENY and `runtimeActivation=false`. The [P0 contract](MIRROR_API_CONTRACT_V1.md)
and schemas still own wire shapes; `get_repository` v1 remains unchanged.

The new `mirror_api.gateway_v2.create_server` factory takes the **existing
MirrorCore**, explicit exact `allowed_repos` and a finite `inventories` mapping.
It reuses the existing fixed HTTPS/443 TLS/private-file factory and also serves
v1 through the same Core. No launcher calls it automatically. The standalone
v1 pilot and running Browser remain unchanged by source publication. No second worker, guard or receipt database is created. Basic observations remain
independent of writer initialization; bound reads reuse the existing Core ledger.
No credential or deployment is created.

Repository/worktree/action reads use Core inventory with `remote=False`.
GitHub status explicitly uses the existing bounded Windows gh collector through
Core, with `remote=True, details=True`. It never fetches Git refs. It reports
`requiredCI=NOT_VERIFIED`: observed runs do not prove required checks. Closed PRs
whose merged state was not independently checked are omitted with `truncated=true`.
Other upstream list bounds also set that flag. `nextOffset=null` with
`truncated=true` means the bounded upstream observation is partial, not complete
and not an invitation to invent later pages. Main or required list failures
return typed errors. Repository snapshots keep GitHub NOT_CHECKED and relation
unknown. Absent SHA never establishes equality or cleanliness.

Pages sort by stable IDs and hash the whole bounded projected semantic dataset.
Subsequent pages repeat `inventoryRevision`; changed observations return
INVENTORY_CHANGED. Timestamps and diagnostics are excluded from the revision.
No cache or pagination state can retain stale Git authorization. Worktree lists
allow at most 128 ordinary entries, list pages at most 128, GitHub pages at most
100. Upstream response limit is 256 KiB; Gateway's existing audited-result limit
remains 64 KiB, failing closed without broadening shared infrastructure.

Named inventories are separate exact targets, never all discovered clones or
GitHub credential scope. The proposed Gateway source mapping is
`mirror-pilot -> [fortis-approvals]`; it is **not enabled**. Owner-reviewed Windows
configuration must use exactly that mapping and include the repo in its allowlist.
The provider adds the inventory name and SHA256 of canonical sorted membership
as trusted internal GET query `inventory`/`scope_digest`. These are not public
params. Windows checks that digest before Core work; the provider checks the
returned inventory page against its own finite mapping. Scope disagreement fails
closed. Any expansion needs source/config review on both sides and Gateway grants.

Public params remain exactly the P0 schemas. For internal HTTP, page params map
to `offset`, `limit`, optional `revision`; repo routes have no caller URL/path or
identity override. Duplicate keys/headers, encoded paths, suffixes, browser
identity, GET bodies and unknown fields are denied before observations. Fixed
errors omit paths, origins, changed filenames and subprocess output. Capabilities
and available actions describe source support; Gateway catalog/policy remains
authoritative for the actor. All deferred actions report POLICY_DENIED.

## P3b principal-bound metadata and lookup

The factory additionally requires an explicit owner-reviewed public
`machine_generation` digest and a Core with safe receipt storage. Without both,
preview/lookup stay POLICY_DENIED and basic P3a reads remain available. The digest
must identify the reviewed Windows code/trust configuration; rotate it when that
configuration changes. Code also binds the configured repo/inventory scopes into
the effective generation. No launcher or installed service gains this option
through source merge.

Gateway obtains actor from the persisted, authenticated Core request row only
in execute/reconcile, verifies operation/target/params/UUID, and derives the peer
generation from the trusted definition digest, admitted Core policy version
and READ credential generation.
It supplies `principal`, `request_id`, `gateway_generation` on the internal
preview GET; lookup supplies `principal` only and takes the effect UUID from the
fixed route. These fields are forbidden in public params. Duplicate keys and
noncanonical query encoding are rejected. Colon-containing actor IDs use canonical
URL encoding. Dedicated READ service authentication authenticates this Gateway
assertion; it is **not an independent ASK proof** and does not authorize effects.
No READ invokes the signing capability. Protecting that service credential and
the Gateway runtime remains part of UC-GW-012's trust boundary.

Preview uses the same Core preflight observation as Browser, without its session
binding or a second business preflight. SQLite in the existing Core worker ledger
persists actor, repo, originating READ UUID, peer/config generations, private
origin and the projected immutable snapshot. The SQLite transaction never spans
GitHub observations. TTL is 120 seconds; retries and a reopened Core return the
original fields, even after expiry, without Git reobservation or TTL refresh.
Expired data is historical evidence, never permission to execute. Generation
changes return PREVIEW_STALE; UUID identity changes conflict. A blocked snapshot
has no executable previewId. Metadata initialization creates no writer claim,
fetch/merge, outcome reconciliation or separate store.

Lookup first requires an exact durable machine actor/repo/UUID association and
checks its immutable identity, saved preview and worker payload. Missing, foreign
and Browser-only associations uniformly return PERMISSION_DENIED. Expired
previews do not block authorized lookup. It projects stored receipts only:
NOT_FOUND/DISPATCHED/UNCERTAIN remain UNKNOWN; unconfirmed finish cannot become
success. No worker.status, Git readback, fetch, merge or automatic retry runs.
P4 must add signed atomic association/reservation before effects. **This source
has no production writer for machine_bindings** and cannot adopt legacy receipts;
positive association tests seed isolated fixture ledgers only.

Metadata retention is bounded: 4096 immutable snapshots total, 64 unexpired per
actor, 8 KiB each. Capacity fails LIMIT_EXCEEDED. No automatic deletion/renewal of
UUIDs is introduced; retention/maintenance belongs in owner activation review.
History-check and signed Pull remain unregistered/DENY. P4 must verify model B,
atomically bind/reserve the effect, revalidate expiry/generations/preconditions,
and qualify replay/uncertainty before any WRITE activation.

Before activation follow [UC-GW-012](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md)
and its existing manifest, Doctor and Acceptance tools. Required separate owner
gates: exact-source CI; Windows installation/rollback review retaining the DB;
shared Gateway release window; existing pinned CA and dedicated READ credential;
actual Gateway-namespace HTTPS acceptance; wrong-CA/hostname/scope/no-auth/write
negative probes; fresh typed READ acceptance. Do not run a second listener on the
occupied 443 port or change certificates/secrets just to test source. P5 owns
launcher cutover. No effect is authorized by this document.

Offline checks: normal source unittest discovery includes v2 ingress/domain
regressions; `python docs/unified-api/validate.py` validates schemas, P0 examples
and Mirror-produced `read-v2-fixtures.json`. The Gateway provider tests consume
a byte-identical copy of that public fixture set and use the existing strict
schema helpers. No second onboarding framework or generic JSON validator is added.
