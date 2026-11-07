# Mirror API-first — source candidate (2026-11-07)

This document owns the new service/frontend source usage and its current limits. The accepted target workflow remains [WORKFLOW](WORKFLOW.md); common authorization and release rules are in the container use-case registry (UC-CONT-002/005/007/009/010/012/016/019). This is **not** live Windows/Gateway acceptance.

## Components and available actions

- `mirror_api.operations` reads Docker's **Git-registered** linked worktrees, Windows primary clones, and optional exact GitHub heads without a hidden Git fetch. Noncanonical worktrees remain visible, but are not eligible for native Gateway worktree mutations. An unknown status is never reported as clean/current.
- `mirror_api.server` exposes a local browser UI and JSON API on **127.0.0.1 only**; a one-use console URL establishes a SameSite/HttpOnly cookie. The browser endpoints are read-only; all POST/PUT/PATCH/DELETE requests are denied. No browser permission is inherited by the separate Gateway listener.
- `mirror_api.static` is the browser frontend. It shows primary/worktrees, Windows, branch SHA, dirty files, action blockers, targeted GitHub checks, progress/cancellation. Read-only PR metadata and Actions/Checks observations come from the **existing authenticated GitHub Gateway typed client**, invoked from the Docker Repo worker by `tools/Export-Mirror-GitHub-Read.py`. Per-repo branch SHAs also come from typed Gateway `list_branches`, never local `git ls-remote` or a Windows Git credential. PRs match the exact task branch; closed PRs get a separate typed `get_pull_request` to distinguish **merged** from merely **closed** (or remain `CLOSED_OR_MERGED_UNVERIFIED` if unavailable). Workflow runs require the **PR head SHA**, matching branch and `pull_request` event; the latest attempt per workflow is selected, not the last successful run. `requiredCI` remains `NOT_VERIFIED` (only Gateway's merge policy owns the required workflow gate). Unknown scope, missing permissions, errors and pagination truncation remain explicit rather than implying no PR/green CI.
- Browser Stage/Commit/Push write routes and their owner allowlist are **disabled**. The old WinForms helper remains for local source compatibility, but any network `ls-remote`/fetch/push is blocked (`GATEWAY_ONLY_GITHUB_OPERATION`). GitHub publishing and PRs must instead use the existing native typed Gateway client; the UI does not yet offer a write workflow.
- `mirror_api.pull`: Windows main-only fast-forward worker with exact SHA, trusted Docker origin, fresh Windows/GitHub preflight, fixed Git commands, readback and SQLite receipt. It is reachable only over a separately configured authenticated HTTPS Gateway listener. A request with the same UUID is never executed twice after dispatch. No reset, stash, branch switch, deploy or restart.
- `fortis-approvals/integrations/mirror/extension.py`: typed `get_repository` (ALLOW) and `pull_main` (ASK). Pull preview binds an executed owner-approved GitHub merge request with CI evidence and the current remote main SHA; Core/WebAuthn authorization precedes effect. A source provider is not activated merely because it exists.

## First manual test on Windows **without merging into main**

For expected results and a repeatable review checklist, see [MANUAL-PREVIEW](MANUAL-PREVIEW.md).

Use the dedicated **read-only preview**, which copies the source out of the
existing Docker task worktree to an isolated `%TEMP%` directory. It does not
modify Windows `%USERPROFILE%\Repos`, Docker primary main, or any Git index.
In **Windows PowerShell 5.1**, with Docker Desktop and `fortis-repo-test` running:

```powershell
$bootstrap = Join-Path $env:TEMP 'Open-MirrorPreview.ps1'
docker cp 'fortis-repo-test:/workspace/repo/Worktrees/docke-windows-mirror/mirror-gateway-ci-20261107/Open-MirrorPreview.ps1' $bootstrap
& $bootstrap
```

The script checks for `docker.exe`, `git.exe` and `py.exe`, copies the candidate
browser assets into a fresh temporary directory, and starts the local service
with `--preview-source-task mirror-gateway-ci-20261107`. This flag selects
read-only exporters from the Docker task worktree, instead of the older Docker
primary scripts. Local Stage/Commit/Push and Gateway listener are **disabled**
in preview mode. Copy the printed one-time URL into a Windows browser. The
terminal must stay open while testing. Use **Ctrl+C** to stop. The temporary
folder may be deleted after exit. Do not run `Start-MirrorAPI.ps1` from an
unmatched Windows main clone or try to update that main to bypass delivery.

Click **Проверить GitHub** to get PR/CI information. The accepted live native Gateway
uses `dynamicRepositories=true` with token-scoped access; check another repo
individually and treat a missing Gateway read as unknown, not empty. Actions/CI observations do not equate to
verified required-CI approval. GitHub metadata is never obtained using a Windows Git credential; do not add a PAT to Mirror.

## Source-only local browser run

Prerequisites on Windows: Python >=3.9 (`py -3`), Git CLI, Docker CLI, running `fortis-repo-test` and mirrored code on both sides. Source in the Docker primary must already include both new exporters (`--inventory-json` and `tools/Export-Mirror-GitHub-Read.py`); the old exporter is incompatible. The read-only PR/CI and SHA view requires active Gateway multi-repo read access and a valid Repo machine grant; unavailable scopes are reported without PAT fallback. Run from the Windows **reviewed main clone**, after approved source delivery:

```powershell
cd "$HOME\Repos\docke-windows-mirror"
./Start-MirrorAPI.ps1 -MirrorRoot "$HOME\Repos"
```

Open the one-time loopback URL printed by the process. It works only while this process runs. All local browser Git write grants are removed: no `-OwnerGitRepo` parameter or browser Stage/Commit/Push route exists. This is not a Windows Service installer and does not persist after the console exits; service packaging/lifecycle is still pending.

## Gateway listener (not deployed)

Optional listener requires a private Windows TLS key/cert, a 32+-character random service secret stored outside repositories with owner-only ACL, a protected durable receipt DB under `%LOCALAPPDATA%`, an explicit repo allowlist, and a Windows certificate chain trusted by the **Gateway container** for `host.docker.internal`. Use `--gateway-listen --gateway-bind <reviewed-interface> --gateway-port 443 --gateway-cert <cert.pem> --gateway-key <key.pem> --gateway-secret-file <owner-protected-path> --gateway-receipt-db <owner-protected-path> --gateway-repo <repoId>`. No listener is exposed by default. Do not bind `0.0.0.0` or open firewall access without an explicit owner/network review. TLS/credential installation is an owner task, not an AI task. The one service token authorizes Gateway↔Windows requests; it is **not** a GitHub provider token. Private Git fetch requires Windows Git's existing authorized credential, or a separately reviewed brokered Git transport; credentials are never copied from Gateway into Mirror.

The shared Gateway Extension `JSONAPI` supports fixed HTTPS on port 443 with system-trusted certificate. No insecure fallback, direct AI-to-Windows bypass, arbitrary origin/port or token in caller payload is supported. Gateway config initially should contain the read operation only; add ASK Pull only after the owner confirms TLS/client identity, durable receipt behavior, exact approved merge evidence, Windows Git auth, and real fast-forward/reject/replay tests. Use the existing `Manage-Integrations.ps1` release controller; never redeploy Production as part of source testing.

## Checks / outstanding acceptance

Run `python3 -B -m unittest -q test_mirror_pr_ci test_mirror_api test_mirror_worktrees test_mirror_docker_actions test_mirror_workflow_source test_mirror_git_history test_mirror_status_source` from the Mirror worktree. The Gateway provider tests are under `fortis_approvals/integrations/mirror/tests`. Offline tests cannot prove Windows Git/NTFS, WinForms retirement, TLS trust, owner WebAuthn or actual Gateway↔Windows execution. Repo-side **read-only** native Gateway→GitHub branches/PR/CI were checked on 2026-11-07 for `docke-windows-mirror`; it is not Windows browser acceptance. Other repositories depend on current token-scoped Gateway read permissions. Manual owner acceptance and source delivery/activation are required. The current Web UI now displays **read-only** PR/CI for Gateway-supported repo scope; it does not provide PR creation, Ready, CI dispatch/rerun/cancel, merge, direct GUI submission of Gateway ASK, a Windows service installer, or full production onboarding. Those are blockers for declaring the complete specification delivered.
