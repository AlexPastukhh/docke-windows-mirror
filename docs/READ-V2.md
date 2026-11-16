# Mirror READ v2 — P3a source candidate

P3a adds six operations from the [closed catalog](unified-api/contract.json):
list repositories, repository snapshot, worktrees, GitHub status, available actions
and capabilities. Their `implemented` flag means source readiness. Every default
policy remains DENY and `runtimeActivation=false`. The [P0 contract](MIRROR_API_CONTRACT_V1.md)
and schemas still own wire shapes; `get_repository` v1 remains unchanged.

The new `mirror_api.gateway_v2.create_server` factory takes the **existing
MirrorCore**, explicit exact `allowed_repos` and a finite `inventories` mapping.
It reuses the existing fixed HTTPS/443 TLS/private-file factory and also serves
v1 through the same Core. No launcher calls it automatically. The standalone
v1 pilot and running Browser remain unchanged by source publication. No new
worker, guard, receipt database, credential, endpoint or deployment is created.

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

Preview allocation, history-check WRITE, signed Pull and initiating-principal
receipt lookup are **not registered** in the P3a provider. P3b must implement
durable principal binding and immutable preview semantics; P4 then admits model B
signed ASK. Existing owner-session receipts cannot become a machine lookup shortcut.

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
