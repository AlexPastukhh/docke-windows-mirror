# Manual Windows browser preview — 2026-11-07

Browser source is accepted in `main`. This page owns the repeatable read-only
Windows check procedure; execution status and unfinished work are kept in the
[backlog](BACKLOG-IMPROVEMENTS.md), not a second checklist of project tasks here.

## Preconditions

Use Windows PowerShell 5.1, Python >=3.9 (`py -3`), Git/Docker CLI and running
`fortis-repo-test`. First deliver the approved merged source to the clean Windows
primary clone by UC-CONT-005; Docker main must include the matching inventory
and Gateway READ exporters. Source delivery is separate from runtime acceptance.
Do not add credentials, owner-Git grants or a Gateway listener for this read-only
browser check. The retained `Open-MirrorPreview.ps1` bootstrap uses an old task
path; ordinary startup uses accepted main and does not depend on that worktree.

## Start

From the reviewed Windows clone:

```powershell
& {
    $ErrorActionPreference = 'Stop'
    $repo = Join-Path $HOME 'Repos\docke-windows-mirror'
    $launcher = Join-Path $repo 'Start-MirrorAPI.ps1'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'MIRROR_ACCEPTED_SOURCE_REQUIRED'
    }
    & $launcher -MirrorRoot (Join-Path $HOME 'Repos')
}
```

The process serves a read-only browser UI on loopback and prints a one-use URL.
Keep the terminal open, use the URL in the owner's Windows browser, stop with
Ctrl+C. It reads Windows clone state without a Git write; source pull/installation
is not performed by this command. The service is temporary, not an installed
Windows service. Do not share the bootstrap URL or browser cookie.

## Checklist to record PASS/FAIL + exact mismatch

- [ ] **Access:** loopback URL opens the browser UI; reusing an expired one-use bootstrap URL is denied; the UI does not require a public network port.
- [ ] **Inventory:** all expected Docker primary repositories appear. In particular inspect `docke-windows-mirror` and `fortis-approvals`. An inaccessible Docker container is an error, **not** zero repositories.
- [ ] **Worktrees:** select `docke-windows-mirror`; primary `main` and task/feature linked worktrees are listed distinctly, with branch, HEAD SHA, clean/dirty, changed files. Compare to Git data from the Docker container without modifying it:

  ```powershell
  docker exec fortis-repo-test git -C /workspace/repo/Repos/docke-windows-mirror worktree list --porcelain
  docker exec fortis-repo-test git -C /workspace/repo/Repos/docke-windows-mirror rev-parse HEAD
  ```

- [ ] **Windows clone:** the selected Windows primary clone shows its real branch (`main` expected), HEAD SHA, origin, local dirty/clean and errors. Compare to Windows:

  ```powershell
  git -C "$HOME\Repos\docke-windows-mirror" branch --show-current
  git -C "$HOME\Repos\docke-windows-mirror" rev-parse HEAD
  git -C "$HOME\Repos\docke-windows-mirror" status --short
  ```

- [ ] **PR/CI:** select `fortis-approvals` and click **Проверить выбранный**. Confirm PR numbers/title, `MERGED/OPEN/DRAFT`, workflow runs and check statuses show for matching task branch/SHA; links open github.com. `requiredCI: NOT_VERIFIED` is expected: visible successful runs are not a certified approval gate.
- [ ] **Scope:** select another owner-owned repo and click **Проверить выбранный**. Dynamic Gateway reads are token-scoped; missing permission must show a Gateway read error, **not** a claim there are no PRs or CI.
- [ ] **Refresh:** **Обновить локально** updates Docker/Windows status (and resets PR/CI to not checked); **Проверить GitHub** runs repo checks with progress and an option to cancel; existing results are retained when individual checks fail.
- [ ] **Actions:** Pull and Git-changing buttons are disabled in the preview. No new PR/CI writes should exist.
- [ ] **HTTP contract (optional):** browser developer tools → Network: `/api/v1/repositories` and `/api/v1/repositories/<repo>?remote=true` return JSON. Direct requests without an authenticated browser session receive `401`.
- [ ] **Shutdown:** Ctrl+C stops the service. Windows main remains on the same branch/HEAD and unchanged status; no Git write was performed.

<a id="known-limits--diagnostics"></a>

## Diagnostics and status ownership

For an error, record the exact code, selected repo/branch and expected result;
do not share URL tokens, cookies, machine grants or provider credentials.
Gateway read failure means UNKNOWN, not zero PRs or empty CI. Visible workflow
runs are observations for the selected SHA, not certified required-CI approval.
Read-only preview does not authorize Pull or provider activation. Actual pending
acceptance and feature gaps are owned only by the [backlog](BACKLOG-IMPROVEMENTS.md).

Detailed source architecture: [API-FIRST](API-FIRST.md).
