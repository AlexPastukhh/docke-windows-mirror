# MIRROR_API_CONTRACT_V1 — unified Mirror, P0

Status: **PROPOSED / SECURITY REVIEW REQUIRED / NO RUNTIME ACTIVATION**.
This is the logical contract v1; its new machine wire protocol is **v2**.
The accepted `mirror.get_repository` operation version `1` stays unchanged.
Merge of this document alone cannot authorize a machine effect or deployment.

## Ownership and architecture

One Windows owner process hosts Mirror Core, one PullWorker, a single durable
receipt database and two adapters. Browser uses loopback HTTP and its existing
owner session; Gateway uses fixed HTTPS/443, pinned CA and service identity.
Adapters perform transport validation and projection; Core owns inventory,
GitHub reads, preflight, preview, clone serialization and receipts.
The separate GET-only pilot remains available until owner-controlled cutover.

Windows Git/GCM owns Git transport; Windows owner-managed `gh` owns GitHub REST
reads. Git authentication does not prove `gh` authentication. Mirror selects
fixed argv commands from a code allowlist, never caller commands or URLs.
Missing permissions produce UNKNOWN/error without Gateway/PAT fallback.
Docker remains the source of registered Docker primaries and linked worktrees.
GitHub data for Mirror is obtained on Windows, never via a nested Gateway call.

The generic onboarding owner is [UC-GW-012][own-api]. The cross-project delivery
owner is [UC-CONT-002/003][delivery]; this file does not replace either workflow.
Outstanding work and acceptance gates live in [the backlog](BACKLOG-IMPROVEMENTS.md).

## Closed catalog and wire schemas

[contract.json](unified-api/contract.json) maps every operation to its method,
fixed path, target kind, schemas, effect and intended policy. All entries are
`implemented: false`, `defaultPolicy: DENY`: this catalog is a design artifact,
not a registration/config file for the running Gateway.
[schemas.json](unified-api/schemas.json) is JSON Schema draft 2020-12 with local
references only. [validate.py](unified-api/validate.py) checks schema integrity,
catalog consistency and positive/negative examples offline; it calls no API.

| Logical operation | New typed operation | Scope | Intended policy |
| --- | --- | --- | --- |
| F-01 list repositories | `list_repositories_v2` | named inventory | ALLOW |
| F-02 repository snapshot | `get_repository_v2` | exact repo | ALLOW |
| F-03 worktrees | `list_worktrees_v2` | exact repo | ALLOW |
| F-04 GitHub main/PR/CI | `get_github_status_v2` | exact repo | ALLOW |
| F-05 available actions | `get_available_actions_v2` | exact repo | ALLOW |
| F-06 metadata fetch | `check_windows_history_v2` | exact repo | DENY |
| F-07 Pull preview | `preview_windows_pull_v2` | exact repo | ALLOW |
| F-08 Windows Pull | `execute_windows_pull_v2` | exact repo | ASK |
| F-09 Pull lookup | `get_windows_pull_result_v2` | exact repo + UUID | ALLOW |
| F-10 capabilities | `get_capabilities_v2` | named inventory | ALLOW |

Public Gateway calls retain the existing integrations client/envelope:
`service=mirror`, closed operation, configured target, strict params and outer
`request_id`. Request IDs are allocated and journaled before submission.
No target, actor, policy, origin, path, proof, header or URL is supplied in params.
Only the trusted provider can build the upstream dispatch envelope.

The named inventory is a separately configured exact target, resolving to a
finite owner-approved repository set. It must not expand to newly discovered
clones or the entire GitHub token scope. Gateway and upstream scopes must agree;
scope mismatch fails closed. Windows-only discovery is outside this P0 scope.
Repo IDs are catalog keys, never filesystem paths; both layers resolve them.
Unknown repo/operation, wildcard, traversal, encoded separators, suffixes,
duplicate JSON/query keys and undeclared fields are rejected before Core work.

List requests use bounded offset/limit, lexical stable IDs and `nextOffset`.
An inventory revision is returned and must be repeated on subsequent pages;
changed revision returns conflict, never a falsely complete mixed snapshot.
No unbounded scan or silent truncation. Results mark `truncated` explicitly.

Each v2 reply has schemaVersion, operation, target, checkedAt, data and error.
Each source has its own verification/time/error. NOT_CHECKED, UNKNOWN, ERROR
and VERIFIED are distinct; null SHA never means equal/clean/PASS.
PR/run observations do not prove required checks: `requiredCI=NOT_VERIFIED`
unless independently established. All outgoing fields are projections, without
local paths, changed filenames, origin secrets, headers or subprocess output.

Limits: request 4 KiB, response 256 KiB, list page 32 (max 128), 128 worktrees,
100 PR/runs per requested page. HTTP budget 60 seconds; child read 25 seconds,
fetch 45 seconds; bounded output 1 MiB before JSON projection. Oversize,
timeout and permission failures produce fixed codes, not raw exception text.
P2 must enforce these limits during execution/streaming, not after allocation.

## Browser and machine boundaries

Existing browser paths/session/bootstrap/Host/Origin/CSRF behavior remain
compatible through an adapter to Core. Bearer does not establish a browser
session. Machine ingress never accepts browser cookies/CSRF as authorization.
Both reject ambiguous auth headers. No AI browser automation is introduced.

Machine profile: fixed `https://host.docker.internal:443`, validated SAN,
pinned owner CA, no redirects/proxy/HTTP fallback. READ and WRITE identities
are separate; READ identity cannot access any effect route. Owner configuration
and credentials remain outside source, argv, env and logs. Binding/firewall
changes require separate owner review. Source presence cannot prove reachability
from the actual Gateway network namespace.

Metadata history check is a WRITE to `.git`, although it does not move HEAD.
For AI it stays DENY until independent review of locking, bounds, readback and
retry semantics. Browser explicit check still requires session and CSRF.
No new machine READ performs fetch. Preview may persist only local preview
metadata; it cannot fetch, merge, acquire a writer claim or execute Git effects.

## Preview, immutable identity and ASK

Preview returns repoId, expectedHead, targetSha, Docker SHA, origin identity,
source evidence, checkedAt, expiresAt, configGeneration, blockers and previewId.
Only verified registered clean main pairs with Docker HEAD at GitHub main
can become executable. Unknown ancestry is reverified after exact fetch;
ahead/diverged, mismatched origin, dirty or unverified GitHub main block Pull.
Default TTL is 120 seconds; no silent target or expiry refresh after ASK.

Execute params bind previewId, expectedHead, targetSha and configGeneration.
Gateway preview reads that exact persisted snapshot and rechecks current
preconditions. Re-reading a preview returns its frozen semantic fields;
volatile timing/checked-at diagnostics cannot cause an artificial fingerprint
change. A changed semantic value makes the request STALE and requires a new ASK.
Gateway policy/grant/credential generations remain governed by existing Core.

Core requestId is the Windows requestId and receipt key. Browser creates a
different requestId tied to its owner session and one-use preview. No UUID is
rebound to another actor/repo/payload. Upstream trusted dispatch binds actor;
result lookup also binds repo and initiating principal. Audit visibility through
UC-GW-009 does not grant another actor execution or result ownership.

## D-04 proposal: model B, fail closed until accepted

Model A cannot independently prove ASK if a reachable WRITE bearer is stolen.
This proposal selects **B**: pinned Gateway signing public key on Windows and a
dedicated private signing credential inside Gateway. A bearer alone is never
sufficient. This is a new reviewed signing capability, absent from today's
Gateway; the existing vault/controller must be assessed before implementing it.
It does not require independent ASK/auth toggles or a replacement Core.

The proposed proof uses Ed25519 with no negotiated algorithm. Sign exactly
`FORTIS-MIRROR-ASK-V1\n` followed by UTF-8 canonical JSON of the closed claims:
sorted ASCII keys, compact separators, ASCII-only claim values, integers for
issuedAt/expiresAt, no floats, duplicate fields or alternate encodings.
The signature is unpadded base64url of 64 bytes. Key ID must resolve to one
owner-pinned key; remote key URLs/discovery and caller-selected keys are denied.
The proof schema closes issuer, audience, service, operation, requestId, actor,
repoId, previewId, expectedHead, targetSha, configGeneration, fingerprint,
issuedAt and expiresAt. There is no `approved: true` field.

Only the trusted operation's execute path after Core approval/revalidation may
sign. Preview, READ, lookup and arbitrary caller payloads cannot invoke signing.
The signed fingerprint is SHA-256 of the canonical immutable dispatch identity
(all claims except fingerprint and issuedAt/expiresAt); it also binds previewId
and configGeneration. Gateway Core's own internal fingerprint is a separate
value governed by Core; do not claim these two algorithms are identical.

Mirror verifies signature and every claim against route, resolved repository,
persisted preview and body, then checks issuedAt <= now + 5 seconds,
now < expiresAt <= issuedAt + 120 seconds and preview.expiresAt >= expiresAt.
Unknown key/generation, stale clocks, wrong actor/repo/SHA/UUID or missing proof
deny before reservation. Key/policy/config rotation invalidates unconsumed
previews/dispatch. Owner security review must accept the signing-key lifecycle
and revocation model before P4; this document does not provision a key.

Replay handling is ordered: authenticate principal and validate closed identity;
if UUID already has a matching durable receipt/reservation, return lookup only
(even if its proof has since expired); changed payload conflicts. An unknown
UUID requires a currently valid proof before any reservation/effect. Expiry
never blocks authorized lookup and never authorizes a fresh dispatch.
This protects against stolen service bearer; compromise of the trusted Gateway
signer/runtime remains outside the guarantee. No design proves safety against a
compromised Windows owner process or malicious owner-managed Git filters.

## Shared worker, result and uncertainty

One writer coordinator serializes browser fetch/check/Pull and machine effects
per clone; one process-instance guard prevents an independent second writer.
Persist request identity before Git. SQLite receipt reservation alone is not
proof that a process is quiescent. Existing in-process locks plus the current
DISPATCHED guard require further crash/UNCERTAIN review in P2.

Recheck owner-registered paths/origin, clean Windows main, expected HEAD, current
GitHub main and Docker main. Then bounded exact main fetch; FETCH_HEAD must equal
targetSha, prove ancestry, `merge --ff-only <targetSha>`, read back HEAD/branch/
clean/origin and persist outcome. No reset/stash/switch/force/restart/deploy.
If verified already at target before any fetch, record ALREADY_CURRENT with
`effect=NONE`. No second fetch/merge for a repeated requestId, including crashes.

The result schema exposes **Windows** workerState, effect, observedHead,
expectedTarget and errorCode. Gateway PENDING/APPROVED/DISPATCHED/EXECUTED/
REJECTED/EXPIRED/STALE/UNCERTAIN/REVOKED remain **Core states**, outside the
upstream result. Core EXECUTED can describe a completed provider call whose
Windows result is UNCERTAIN; clients must inspect both before claiming success.
NOT_FOUND is not proof that an in-flight network dispatch cannot arrive later.
FAILED means a known pre-effect refusal; ambiguous post-reservation errors are
UNCERTAIN. ALREADY_CURRENT is a v2 outcome, not a current worker state.

After timeout or uncertain dispatch only lookup on the original UUID is allowed.
Lookup may read Git and durably record a resolved outcome, but cannot fetch or
merge. Target HEAD readback proves convergence, not exclusive causation by that
UUID; response records `evidence=TARGET_READBACK` rather than invented worker
completion. An unresolved clone remains writer-blocked, including new UUIDs,
until a reviewed resolution. No automatic replacement request or proof renewal.

## Legacy migration and release

Keep `GET /gateway/v1/repositories/{repo}` and `get_repository/version=1`:
upstream keys schemaVersion/repository; row keys repoId/docker/windows/github/
actions/readTimingsMs; github.error=NOT_CHECKED, checkedAt=null and
actions.pull_main.available=false. Gateway projection remains schemaVersion=1,
repository(repoId/docker/windows/github), github={verification:NOT_CHECKED}.
Do not add v2 fields, GitHub reads or available Pull to the old route.

The existing optional v1 POST `/pull-main` must be retired/deny-only in the
replacement adapter before any machine effect is enabled. Old Bearer/UUID
dispatch is not ASK. Preserve the old GET-only listener/launcher until live v2
and rollback are accepted; never run two listeners on the same address:443.
Owner cutover preserves source/config/CA identity and durable receipts, ensures
writers are quiescent and activates exactly one writer. Owner controls stop/
start/rollback; no working process changes in these source PRs.

P0 review/owner merge precedes P1 Windows reader and P2 shared Core. P3 releases
new v2 READ beside v1, then P4 separately admits signed ASK after security and
live READ gates. P5 changes launchers/cutover only after owner evidence. Each
source, Windows installation and Gateway image release is separately recorded.
Compatible upstream source changes do not require a provider/image SHA repin;
schema/transport/policy changes do. No new Core toggle work is implied.

## Required refusal and acceptance evidence

Offline contract tests: old v1 shape, all new closed schemas, unknown/extra
fields, oversized lists, wrong types/SHA/UUID, proof claim mismatch, unsigned
bearer, READ credential on WRITE, TTL/replay/conflict and no-fetch READ.
P1–P4 must add executable transport/worker/signature tests; a schema fixture
cannot prove signature validity, ASK or Git idempotency.

Live gates: owner browser main/PR/CI with Gateway unavailable; Git/gh permission
failures without fallback; actual pinned HTTPS Gateway namespace READ; owner
Reject and Approve on a safe exact Windows target; one shared-worker effect;
cross-session/actor failures; timeout lookup; negative direct old POST;
port ownership, receipt persistence and rollback. Record each NOT_TESTED
explicitly. A second real Docker API is required only to claim generic
onboarding reproducibility, not to validate this Mirror installation.

[own-api]: https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md#uc-gw-012
[delivery]: https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/delivery.md
