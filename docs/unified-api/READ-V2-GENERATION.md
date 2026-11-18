# READ v2 — proposed public machine_generation candidate (P5)

**Status: SOURCE CANDIDATE, not owner-approved, installed or activated.** The
accepted [READ-V2 contract](../READ-V2.md) requires an owner-reviewed 64-hex
machine_generation identifying Windows code/trust configuration. It does not
specify a canonical source digest procedure. This file and the
[public-generation helper](../../tools/mirror_read_v2_generation.py)
propose **one bounded v1 input format for review**; CI passing does not adopt
the proposal or authorize filling the existing Windows P5 preflight flag.

The candidate is SHA256(ASCII("fortis-mirror/p5/read-v2/machine-generation/v1")
+ NUL + canonical_JSON), where canonical JSON is the validated exact public
mapping, UTF-8/ASCII with sorted keys, no whitespace, no NaN and JSON ASCII
escaping. Keys and arrays are closed; duplicate JSON keys and unknown schema
versions are rejected. Domain separation avoids reusing the digest as a
different kind of attestation.

## Public manifest, proposed schema v1

The owner must independently populate and review an external public JSON
manifest, outside the Git source tree and without passwords, Bearer values,
private keys, private Windows paths, source secrets, raw SQLite content or
private certificate material. The example deliberately contains **unresolved
placeholders** and is NOT a usable or approved manifest:

~~~json
{
  "schemaVersion": 1,
  "profile": "mirror-win-https",
  "sourceSha": "<exact-merged-Mirror-main-SHA>",
  "readContract": "mirror-principal-reads-v2.1",
  "mode": "read-only",
  "tls": {
    "origin": "https://host.docker.internal:443",
    "dnsName": "host.docker.internal",
    "bindIPv4": "<reviewed-exact-Windows-IPv4>",
    "caCertificateDerSha256": "<SHA256-of-public-CA-certificate-DER>",
    "leafCertificateDerSha256": "<SHA256-of-public-server-certificate-DER>"
  },
  "scope": {
    "allowedRepositories": ["fortis-approvals"],
    "inventories": {"mirror-pilot": ["fortis-approvals"]}
  },
  "ledgerContract": "existing-browser-core-pull-receipts-v1",
  "readCredentialRevision": "<owner-managed-public-UUIDv4-NOT-token-hash>"
}
~~~

sourceSha must be the **actual accepted installed source commit**, not the
commit of this proposal or a stale example. The command below requires an
independent exact expected SHA and rejects mismatch. TLS fingerprints mean
SHA-256 of each **certificate's DER encoding**, *not* a hash of its PEM text.
Obtain the fingerprints from the reviewed **public certificates** and compare
with the Gateway's protected pinned CA and owner Windows listener certificate.
The helper does **not** open cert files, verify signatures, inspect Gateway
mounts, probe TLS or prove SAN. The DNS name and HTTPS origin are fixed to the
existing contract. IPv4 bind rejects wildcard, multicast, reserved,
link-local and noncanonical values. Loopback is allowed only as exact
127.0.0.1. Actual Docker reachability needs separate owner/Gateway acceptance.

readCredentialRevision must be an owner-managed, public canonical UUIDv4
representing the installed READ service credential epoch; it must be
**independent of the credential secret** and rotate whenever that credential
changes. A syntactically valid UUID does **not** prove owner review or that
the installed credential matches. If no reviewed revision exists, STOP
instead of inventing one. Gateway derives its separate peer generation
from trusted definition/policy/credential state; this proposal does not
replace that lifecycle. ledgerContract identifies the *intended* same
Browser Core/ledger model, not proof of storage integrity or process identity.

The pilot inventory is exactly one fortis-approvals repo in one mirror-pilot
inventory. Scope widening, new contract/ledger profile, changed TLS identity
or extra JSON fields are STOP and require separately reviewed code/schema.

## Candidate generation and owner review

After populating an owner-reviewed public manifest **outside** the Git clone,
reviewing actual installed SHA and code, a candidate can be computed offline.
This command only reads a small public file. It never contacts Windows
services or GitHub, installs credentials, modifies ledger, starts a listener,
or approves cutover:

~~~powershell
$repo = Join-Path $HOME 'Repos\docke-windows-mirror'
& py -3 (Join-Path $repo 'tools\mirror_read_v2_generation.py') `
    --manifest '<absolute-external-public-manifest.json>' `
    --expected-source-sha '<exact-merged-installed-Mirror-main-SHA>'
~~~

Output with valid *source input*: state OWNER_REVIEW_REQUIRED,
cutoverAuthorized false, candidateMachineGeneration 64-hex, exit code **3**.
Invalid or unreadable, symlinked, oversized or noncanonical input: fixed STOP,
exit code **2**, no raw content. Neither result is owner GO. There is no
automatic transfer of a candidate into the listener, preflight, Gateway
configuration or Windows secret store. Never supply a test candidate to the
Windows P5 preflight just to turn its syntax check green.

Owner review must record the **exact manifest fields, selected SHA and
candidate** in an owner-controlled release evidence record, verify public
certificate fingerprints against actual installed assets, confirm the READ
credential revision and shared ledger, and approve that exact digest before
any use. Changes to source SHA, leaf/CA, bind, scope, credential revision,
contract or ledger profile need new review and generation. Matching a digest
alone is never approval. Separately satisfy Windows source/process, ledger
integrity/isolated restore, rollback, Gateway policy/catalog and live HTTPS
negative probes under [UC-GW-012](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/use-cases/own-api.md).

If activated in a separately approved release later, CoreReadV2 further hashes
the reviewed machine_generation with its configured repository/inventory
scopes and contract. This helper computes only the **input** candidate, not
that derived generation, Gateway policy generation, signing key or dispatch
authorization.

**Production remains unchanged:** port 443 keeps READ v1 until separate owner
P5 cutover/rollback GO. READ v2 and WRITE remain denied/unregistered pending
live acceptance. P4 signed ASK is a different security gate.
