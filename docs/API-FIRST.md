# Mirror API-first — source candidate (2026-11-07)

This document owns the new service/frontend source usage and its current limits. The accepted target workflow remains [WORKFLOW](WORKFLOW.md); common authorization and release rules are in the container use-case registry (UC-CONT-002/005/007/009/010/012/016/019). This is **not** live Windows/Gateway acceptance.

## Components and available actions

- `mirror_api.operations` reads Docker's **Git-registered** linked worktrees, Windows primary clones, and optional exact GitHub heads without a hidden Git fetch. Noncanonical worktrees remain visible, but are not eligible for native Gateway worktree mutations. An unknown status is never reported as clean/current.
- `mirror_api.server` exposes a local browser UI and JSON API on **127.0.0.1 only**; a one-use console URL establishes a SameSite/HttpOnly cookie. The browser endpoints are read-only; all POST/PUT/PATCH/DELETE requests are denied. No browser permission is inherited by the separate Gateway listener.
- `mirror_api.static` is the browser frontend. It shows primary/worktrees, Windows, branch SHA, dirty files, action blockers, targeted GitHub checks, progress/cancellation. Read-only PR metadata and Actions/Checks observations come from the **existing authenticated GitHub Gateway typed client**, invoked from the Docker Repo worker by `tools/Export-Mirror-GitHub-Read.py`. Per-repo branch SHAs also come from typed Gateway `list_branches`, never local `git ls-remote` or a Windows Git credential. PRs match the exact task branch; closed PRs get a separate typed `get_pull_request` to distinguish **merged** from merely **closed** (or remain `CLOSED_OR_MERGED_UNVERIFIED` if unavailable). Workflow runs require the **PR head SHA**, matching branch and `pull_request` event; the latest attempt per workflow is selected, not the last successful run. `requiredCI` remains `NOT_VERIFIED` (only Gateway's merge policy owns the required workflow gate). Unknown scope, missing permissions, errors and pagination truncation remain explicit rather than implying no PR/green CI.
- Browser Stage/Commit/Push write routes and their owner allowlist are **disabled**. The old WinForms helper remains for local source compatibility, but any network `ls-remote`/fetch/push is blocked (`GATEWAY_ONLY_GITHUB_OPERATION`). GitHub publishing and PRs must instead use the existing native typed Gateway client; the UI does not yet offer a write workflow.
- `mirror_api.pull`: Windows main-only fast-forward worker with exact SHA, trusted Docker origin, fresh Windows/GitHub preflight, fixed Git commands, readback and SQLite receipt. It is reachable only over a separately configured authenticated HTTPS Gateway listener. A request with the same UUID is never executed twice after dispatch. No reset, stash, branch switch, deploy or restart.
- Gateway-side Mirror provider — отдельный **не принятый в Gateway main**
  source candidate `task/mirror-provider-20261107`, путь
  `fortis_approvals/integrations/mirror/extension.py`. Наличие Windows API source
  не включает provider. Точная граница и оставшаяся Gateway работа ведутся в
  [Gateway backlog](https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/BACKLOG-IMPROVEMENTS.md#согласование-нормального-ui-merge-с-последующими-операциями).

<a id="first-manual-test-on-windows-without-merging-into-main"></a>

## Manual Windows browser check

Accepted browser source is already in `main`. Use the single
[manual preview runbook](MANUAL-PREVIEW.md) for prerequisites, startup and the
reusable check procedure. `Open-MirrorPreview.ps1` is a retained task-specific
bootstrap pinned to the old `mirror-gateway-ci-20261107` worktree; it is not the
routine startup path after source merge. Do not make cleanup of an old worktree
a prerequisite for normal delivery.

## Source-only local browser run

The read-only browser process uses reviewed source from the Windows primary
clone and matching accepted exporters in Docker. [MANUAL-PREVIEW](MANUAL-PREVIEW.md)
owns the command and checklist; no second copy is maintained here. PR/CI/branch
reads use the existing Repo typed Gateway client and machine grant, never a
Windows PAT fallback. Observed CI success does not certify the required gate.

## Gateway listener (not deployed)

Optional listener requires a private Windows TLS key/cert, a 32+-character random service secret stored outside repositories with owner-only ACL, a protected durable receipt DB under `%LOCALAPPDATA%`, an explicit repo allowlist, and a Windows certificate chain trusted by the **Gateway container** for `host.docker.internal`. Use `--gateway-listen --gateway-bind <reviewed-interface> --gateway-port 443 --gateway-cert <cert.pem> --gateway-key <key.pem> --gateway-secret-file <owner-protected-path> --gateway-receipt-db <owner-protected-path> --gateway-repo <repoId>`. No listener is exposed by default. Do not bind `0.0.0.0` or open firewall access without an explicit owner/network review. TLS/credential installation is an owner task, not an AI task. The one service token authorizes Gateway↔Windows requests; it is **not** a GitHub provider token. Private Git fetch requires Windows Git's existing authorized credential, or a separately reviewed brokered Git transport; credentials are never copied from Gateway into Mirror.

The shared Gateway Extension `JSONAPI` supports fixed HTTPS on port 443 with system-trusted certificate. No insecure fallback, direct AI-to-Windows bypass, arbitrary origin/port or token in caller payload is supported. Gateway config initially should contain the read operation only; add ASK Pull only after the owner confirms TLS/client identity, durable receipt behavior, exact approved merge evidence, Windows Git auth, and real fast-forward/reject/replay tests. Use the existing `Manage-Integrations.ps1` release controller; never redeploy Production as part of source testing.

## GitHub Actions source checks

`.github/workflows/mirror-source-ci.yml` provides source-only Python offline
regression and Node syntax checks. It does not install Windows or enable the
Gateway Pull listener. Publishing changes beneath `.github/workflows` uses
`github.push_commits` through native Gateway **ASK** and requires owner WebAuthn;
local tests and an open PR do not by themselves satisfy branch-required checks.
The test suite runs independently of the private Fortis Gateway repo; the actual
runtime reader still fails closed if the typed client is unavailable.

<a id="checks--outstanding-acceptance"></a>

## Source checks and acceptance boundary

Run `python3 -B -m unittest -q test_mirror_pr_ci test_mirror_api test_mirror_worktrees test_mirror_docker_actions test_mirror_workflow_source test_mirror_git_history test_mirror_status_source`
from the Mirror task worktree. These offline tests do not prove Windows/TLS or
live Gateway Pull. The read-only browser action map above is the implemented
contract; unfinished development and all pending acceptance are maintained only
in [BACKLOG-IMPROVEMENTS](BACKLOG-IMPROVEMENTS.md). The reusable owner check
procedure belongs to [MANUAL-PREVIEW](MANUAL-PREVIEW.md).
