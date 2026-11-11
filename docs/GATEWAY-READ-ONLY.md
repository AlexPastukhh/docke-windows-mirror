# Windows HTTPS Mirror Gateway READ — owner runbook

**Scope:** isolated _mirror_api.gateway_readonly_ with only authenticated
GET /gateway/v1/repositories/<repoId>; no Pull worker, receipt DB,
cookie session, browser UI, owner Git fetch or mutating HTTP endpoints.
Source acceptance alone does not prove Windows listener, TLS, vault readiness
or LIVE_READ_VERIFIED. This is an owner-only manual startup procedure,
**not** permission for unattended service installation or production deploy.

Canonical API/browser behavior belongs to [API-FIRST](API-FIRST.md). The
general PR/CI/Windows source delivery contract is [WORKFLOW](WORKFLOW.md).
The Gateway read provider is in fortis-approvals,
fortis_approvals/integrations/mirror/extension.py. Its fixed origin is
https://host.docker.internal:443 (default HTTPS 443); no fallback.

## Preconditions (all must hold)

- Source PR merged and CI passed. On Windows, the owner primary
  %USERPROFILE%\Repos\docke-windows-mirror is clean main at the exact
  merged commit SHA. Update source only through guarded
  [UC-CONT-005](use-cases/windows-sync.md) when appropriate;
  do not copy a Docker worktree or force Git.
- Windows py.exe -3, git.exe and docker.exe resolve under the owner account;
  fortis-repo-test is running for observational worktree inventory.
- The owner selects one specific Windows IPv4 bind address, verified
  from the actual Gateway container before persistent startup. On the
  owner-tested Docker Desktop setup (2026-11-11), binding the Windows
  process to 127.0.0.1:443 was reachable via
  https://host.docker.internal (Gateway resolved it to 192.168.65.254).
  This is an observed host-specific behavior, NOT a universal claim
  that Docker reaches Windows loopback on every platform/release.
  Prefer the tested loopback bind if the current probe still succeeds.
  If that route stops working, STOP and request a new owner-reviewed
  bind/network decision; never silently switch to a LAN address,
  wildcard 0.0.0.0 or change firewall rules. Broadcast, wildcard and
  automatic network changes remain denied.
- An owner-controlled TLS server certificate/private PEM key, with SAN
  DNS:host.docker.internal, and its issuer chain trusted by the Gateway
  container exist under %LOCALAPPDATA% outside Git. Do not disable TLS
  hostname or CA validation; never copy the private key into Docker.
- The independent service Bearer secret is random, at least 32 characters,
  stored outside Git under %LOCALAPPDATA% with protected
  owner/System/Administrators file ACL. Its value is never printed, sent
  to the chat, or passed as a CLI argument.
  The same value must later be provisioned as a distinct mirror-link alias
  in the Gateway vault **only after** the separate shared-vault owner has
  resolved permission/compatibility constraints. No credential is created
  by this launcher or runbook.
- The private key has the same protected ACL conditions. Port 443 must be
  free. No Gateway release, Observatory change or PowerShell Executor
  reconfiguration is included in this step.

## Owner-observed isolated HTTPS acceptance (2026-11-11)

These owner-reported test results do NOT imply a persistent production
listener, operational certificate or live Gateway provider:

- Temporary Windows exclusive loopback TCP/443 listener received the probe
  from real fortis-gateway (UID:GID 10001:10001), then stopped.
- A short-lived synthetic CA and server cert with SAN DNS:host.docker.internal
  gave WRONG_TLS_HOSTNAME_REJECTED=YES,
  GATEWAY_TO_WINDOWS_TLS_CA_HOSTNAME=PASS and SYNTHETIC_HTTPS_STATUS_200=YES.
  Temporary test listener and certificate/key files were removed.
- The actual mirror_api.gateway_readonly.create_server was then separately
  bound to Windows 127.0.0.1:443 with another ephemeral TLS identity,
  independent Bearer and exact allowlist fortis-approvals.
  From the same Gateway container: absent Bearer 401, browser-cookie-only
  401, wrong Bearer 401, wrong HTTP Host 403, out-of-scope repository 404,
  query 404, POST 405, authenticated GET 200. The response contained
  schemaVersion=1 and exact repo ID; Windows readback was AVAILABLE
  and GitHub was NOT_CHECKED. The ephemeral listener stopped and the
  ephemeral key/token files were removed. PUT/PATCH/DELETE denials were
  covered by offline unit tests only (not the owner HTTPS probe).

These tests do NOT activate the production Windows listener, owner TLS
material and private ACLs, Gateway vault alias mirror-link, trusted CA
inside the Gateway RO mount, provider registration or live typed Gateway
operation. The shared Gateway vault has an unresolved root ownership/mode
compatibility constraint: do not alter its ACL or create production secrets
based on these isolated tests. The owner deferred off-host backup.

Source coordination: the accepted Script Center
Fortis-Owner-PowerShell-Executor.ps1 adapter currently pins Mirror main
SHA a5cb3e9f61e68331c11337eb5da4af056d27660c. Any newer Mirror
main commit, even docs-only, causes this separate Script Center adapter
to fail closed. Before merging this Mirror PR, coordinate a reviewed
Script Center pin update and Windows rollout. Do not weaken the SHA
check or switch Windows clone branches to bypass it.

## Owner-controlled foreground run (after prerequisites)

In normal Windows PowerShell 5.1 (not elevated by default), review the script
from the **accepted, clean Windows main** first, then launch, substituting
only actual owner-reviewed values:

    $repo = Join-Path $HOME 'Repos\docke-windows-mirror'
    & (Join-Path $repo 'Start-MirrorGatewayReadOnly.ps1') -SourceSha '<merged-Mirror-main-SHA>' -Bind '127.0.0.1' -CertificatePath '<absolute-public-cert-PEM>' -KeyPath '<absolute-private-key-PEM>' -SecretFile '<absolute-owner-only-secret-file>' -AllowedRepo @('fortis-approvals')

The launcher enforces exact source branch/SHA/clean, owner-local TLS paths,
protected private ACLs, non-wildcard IPv4 bind, fixed port 443 and exact
repository allowlist. On success it emits
MIRROR_GATEWAY_READ_PREFLIGHT_PASS, then the Python module emits
MIRROR_GATEWAY_READ_ONLY_HTTPS_READY. Keep that terminal open.
It does **not** register Windows services, open firewall ports, create
secrets, perform Git writes or deploy Gateway. Stop explicitly with Ctrl+C.

The pre-existing browser server module mirror_api.server with
--gateway-listen exposes a different surface that includes POST Pull.
It MUST NOT be used for READ P0 acceptance. The isolated
mirror_api.gateway_readonly module does not import a Pull worker.

## Acceptance (source CI is insufficient)

1. Confirm TLS server name and CA chain for host.docker.internal from the
   actual Gateway container, and reach the reviewed Windows interface.
   A certificate failure is STOP, never an insecure TLS fallback.
2. Unauthenticated and browser-cookie-only GET return 401. Correct bearer
   with wrong HTTP Host returns 403. Authenticated GET outside the repo
   allowlist, with query, or to /pull-results/<id> returns 404. Every
   POST/PUT/PATCH/DELETE, including /pull-main, returns 405 without parsing
   a request body or constructing a Pull worker. Never send an actual
   Pull payload as a negative test.
3. Authenticated GET returns schemaVersion 1 and exactly one repository.
   Windows checkedAt/head/clean/originValid reflect fresh Windows readback;
   github.verification in the Gateway projection is NOT_CHECKED.
   Neither GitHub reads nor Windows git fetch occur in this operation.
4. After shared-vault resolution, stage and release the
   mirror.get_repository READ-only ALLOW operation for exact target through
   the existing owner Manage-Integrations.ps1 controller. Preserve incident
   inventory and unresolved protected requests; verify worker quiescence
   before any release. Do not cancel, clear, or replay those requests.
   Confirm operation in the live catalog, then call it using the normal
   typed fortis_approvals.extensions.client with a unique UUID.
   Verify negative auth, scope, TLS and unsupported write operations.
5. Only after the authenticated Gateway-to-Windows round-trip and negative
   tests, exact runtime source identity and unchanged Git readbacks may the
   evidence be called LIVE_READ_VERIFIED.

**Never** provision tokens, publish a sensitive config, open firewall,
enable ASK Pull, deploy a new Gateway image, or perform Windows Pull as
an implicit part of this read-only runbook. Unmet prerequisites remain
UNKNOWN / MANUAL-PENDING.
