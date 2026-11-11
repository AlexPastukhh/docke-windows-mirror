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
- The owner selects one specific Windows IPv4 interface reachable over a
  reviewed Docker-to-host route. 127.0.0.1 is safe for host-local checking
  but not reachable from Docker. 0.0.0.0, broadcast and firewall changes
  are denied; no network setting is changed automatically.
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

## Owner-controlled foreground run (after prerequisites)

In normal Windows PowerShell 5.1 (not elevated by default), review the script
from the **accepted, clean Windows main** first, then launch, substituting
only actual owner-reviewed values:

    $repo = Join-Path $HOME 'Repos\docke-windows-mirror'
    & (Join-Path $repo 'Start-MirrorGatewayReadOnly.ps1') -SourceSha '<merged-Mirror-main-SHA>' -Bind '<reviewed-Windows-IPv4>' -CertificatePath '<absolute-public-cert-PEM>' -KeyPath '<absolute-private-key-PEM>' -SecretFile '<absolute-owner-only-secret-file>' -AllowedRepo @('fortis-approvals')

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
