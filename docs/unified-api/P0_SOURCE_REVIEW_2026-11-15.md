# P0 source review — 2026-11-15

Scope: source/contracts and read-only live Gateway capabilities/catalog.
This record does not assert installed Windows source or current image digest.

| Source owner | Local clean main and independent Gateway GitHub main READ |
| --- | --- |
| Mirror | `c0f5db887f29996ee3464cf991674c31d0a22eaf` |
| Approvals | `59f7875cdf0813a57854ca6e135d4173145a3894` |
| Container | `8a6fbddc44b8ea400d734f8eb220fd8d1c9221fc` |
| Script Center | local main `88bf3cd048b4a031226f332a5731aebf23b0eaa4`; remote not needed for P0 |

Live catalog has exactly `mirror.get_repository`, version `1`, READ/ALLOW,
target `fortis-approvals`, ready=true, providerPermissions=UNVERIFIED.
Configuration digest:
`4d3fc38eef0efb868242efd0b9dce089b34d2a6ec46f4e377baf102aa2367206`.
No typed Mirror Pull is catalogued. Catalog readiness is not an upstream probe.
Native capabilities admit task creation/publication/PR and dynamic repositories;
GitHub READ verified the exact repository identities and main SHAs above.

Source findings:

- `fortis_approvals/integrations/mirror/extension.py` strictly checks v1
  NOT_CHECKED/Pull unavailable and emits a smaller closed projection.
- `mirror_api/server.py:GatewayHandler.do_POST` checks Bearer/UUID then calls
  the worker; no independent ASK proof. P0 does not activate or alter it.
- `mirror_api/gateway_readonly.py` remains GET-only without a PullWorker.
- `mirror_api/operations.py:github_main/gateway_pr_ci` uses Docker/Gateway;
  changing the Windows reader is P1, not a fact already achieved.
- `mirror_api/browser_pull.py` has owner-session one-use preview and lazily
  constructs the existing worker; integrated server can share that instance.
- `mirror_api/pull.py` has durable UUID reservation and an in-process repo
  lock plus a DISPATCHED database guard. Browser check takes that same lock
  when the integrated listener shares the worker. Second-process exclusion
  and safe handling of a new UUID after UNCERTAIN still require P2 review.
- Generic extension write already requires preview and can provide lookup;
  Core policy and machine grant remain in force. No signing API is documented
  or enabled by the existing extension catalog. Model B needs separate work.

Existing open Approvals PR #87 modifies the same UC-GW-012 and backlog.
Its READ-all/Windows-only inventory proposal and Gateway GitHub-sourced Pull
are not treated as accepted policy. This P0 preserves the registered-pair scope,
adds a separate shared-client subsection and records the overlap for review.
Do not overwrite/close/merge that PR automatically; reconcile before owner merge.
PR #70 and #50 are older planning proposals, not runtime evidence.

Archive integrity: the three Markdown SHA-256 values matched the supplied
SHA256SUMS_MIRROR_HANDOFF.txt. HANDOFF was read before the specification/proposal.
The attached design remains unapproved until owner security review of P0.

Windows installed HEAD, Git/GCM/gh auth, listener/CA/bind/ACL, actual Gateway
image/config identity, live upstream READ and ASK effect: **NOT_TESTED**.
No source sync, runtime stop/start, certificate/vault edit or deploy occurred.

## Source validation

- Mirror: `python3 -B -m unittest discover -q -p 'test_mirror*.py'`: 133 OK.
- Browser JavaScript: `node --check mirror_api/static/app.js`: PASS.
- Gateway `test_own_api_onboarding`: 20 OK; Mirror provider tests: 15 OK.
- Existing WIN-HTTPS and DOCKER-HTTPS READ manifest lint: SOURCE_MANIFEST_VALID;
  liveVerified=false, readyToDeploy=false as expected.
- Gateway source manifest regeneration/check: PASS.
- Changed-document inline local links and `git diff --check`: PASS.
- P0 `docs/unified-api/validate.py`: 10 operations / 20 shape examples PASS
  in a separate validation environment (jsonschema 4.26.0); exact schema,
  catalog, example and validator SHA-256 bytes matched the Fortis task copies.
  This is schema validation, not a cryptographic/worker/TLS test.
- Full local Gateway suite: NOT RUN TO COMPLETION; system Python lacks
  cryptography and isolated venv setup lacks ensurepip. No system package
  installation or service change was performed. Exact-head Source CI installs
  the repository-pinned dependencies and is the remaining full-suite check.
