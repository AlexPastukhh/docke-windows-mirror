# Owner Portal Windows PowerShell — optional source only

Status: **source implementation, NOT deployed or Windows-accepted**.

Two independent processes are used:

- Owner Portal (`fortis-approvals`): WebAuthn owner session + CSRF validated
  `POST /v1/owner/powershell/execute`, with a dedicated trusted TLS transport.
  Only the authenticated owner may submit a script; AI machine ingress cannot.
- Windows executor (`mirror_api.owner_shell`): separately started, mandatory
  HTTPS **mutual TLS** (client CA/cert) and an independent 32+ character bearer
  service secret. Requires exact request UUID and bounded command text. Executes
  a **one-shot** Windows PowerShell 5.1 process (no profile, noninteractive, no
  stdin); returns limited stdout/stderr/exitCode. No interactive PTY yet.

## Trust boundaries

**Arbitrary PowerShell is arbitrary Windows user-level code.** This is not a
sandbox. Commands can read the user's files, call network services, launch
children, or persist. Running non-elevated prevents easy administrator actions
but does not make it harmless. No script is pre-screened by an Analyzer.
Browser extensions that can read Owner Portal may see commands and output.

Do not create or use credentials in repositories. The owner must separately
provision and review: server certificate (SAN `host.docker.internal`), key,
client CA, Portal client certificate/key, server CA trusted by Portal, private
independent bearer secret files, durable receipt database, Windows network
interface/firewall rules and a non-admin process identity. **Never** bind an
unauthenticated HTTP shell, enable fallback to insecure TLS, or run as admin.
Default Script Center/Mirror launcher does not activate the executor.

Manual owner activation (replace placeholders with privately-provisioned paths
and a reviewed interface that is reachable by the Docker Gateway container):

```powershell
& "$HOME\Repos\docke-windows-mirror\Start-OwnerPowerShellExecutor.ps1" `
  -Bind '<reviewed Windows interface>' -Port 10443 `
  -ServerCert '<private server certificate path>' `
  -ServerKey '<private server key path>' `
  -ClientCA '<Portal client CA certificate path>' `
  -SecretFile '<private owner shell secret path>' `
  -ReceiptDb '<protected local receipts sqlite path>'
```

The Portal release must separately opt in using trusted
`FORTIS_OWNER_SHELL_CONFIG` JSON with **exact keys**
`port,serverCA,clientCert,clientKey,secretFile` (all paths are Gateway-private).
The default address is fixed to `https://host.docker.internal:<port>`; the
caller cannot supply a host, port, token, target machine, or arbitrary URL.
There is no automatic credential provisioning or release action in this PR.

## Execution semantics

- Maximum command length: 8 KiB UTF-8, one-shot PowerShell 5.1, 60-second
  timeout, 64 KiB of each output stream in receipt. Nonzero process exit is
  `FAILED`, **not evidence that no side effects occurred**.
- Every execution reserves a durable SQLite receipt **before** launching
  PowerShell. Repeating the same UUID never reruns the script. Uncertain or
  timed-out actions remain `UNCERTAIN`, block further shell dispatch on this
  receipt store and require manual operator investigation; do not auto-retry.
- Preview/analysis and second-opinion AI buttons remain **disabled**, with no
  routes to inspect commands. This service is not an AI-facing endpoint.

## Acceptance before enabling

Independently verify mTLS rejection without a client cert, invalid bearer
rejection, owner portal session+Origin+CSRF rejection, exact-script UUID replay
behavior, timeout/restart unknown result, and non-admin account privileges.
Start with a harmless script (`Get-Date`) on a test user account. Do not treat
Linux mocks/source tests as evidence of Windows/GCM/PowerShell correctness.
