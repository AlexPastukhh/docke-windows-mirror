# Manual Windows browser check — accepted main source

Browser UI source is accepted in `main`; owner-local Pull is a separate source
change requiring merge and Windows owner acceptance. This page owns the
repeatable read-only inspection and guarded Pull acceptance procedure; actual
execution status and unfinished work are kept in the
[backlog](BACKLOG-IMPROVEMENTS.md), not a second checklist of project tasks here.

## Preconditions

Use Windows PowerShell 5.1, Python >=3.9 (`py -3`), Git/Docker CLI and running
`fortis-repo-test`. First deliver the approved merged source to the clean Windows
primary clone by UC-CONT-005; Docker main must include the matching inventory
and Gateway READ exporters. Source delivery is separate from runtime acceptance.
Do not add new credentials, owner-Git grants or a Gateway listener for this
browser check; manual Windows Pull uses the already configured Windows Git
credential, if any. Test Pull only with a reviewed safe repo after both Docker
and Windows accepted source updates. The retained `Open-MirrorPreview.ps1` bootstrap uses an old task
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

The process serves a local browser UI on loopback and prints a one-use URL.
Keep the terminal open, use the URL in the owner's Windows browser, stop with
Ctrl+C. Startup and normal inspections never change Git; **only the explicit
Pull Windows click → Preview → Execute** can run guarded Windows fetch/fast-forward. The service is temporary, not an installed
Windows service. Do not share the bootstrap URL or browser cookie.

## Checklist to record PASS/FAIL + exact mismatch

- [ ] **Access:** loopback URL opens the browser UI; reusing an expired one-use bootstrap URL is denied; the UI does not require a public network port.
- [ ] **Inventory:** all expected Docker primary repositories appear. In particular inspect `docke-windows-mirror` and `fortis-approvals`. An inaccessible Docker container is an error, **not** zero repositories.
- [ ] **Main table / lower row:** only Docker **primary** repositories appear in the 13-column main table; choose `docke-windows-mirror` and the lower **«Строка»** tab. Check main Git/Docker/Windows state, full SHA, file changes; **«Копировать весь блок»** must copy all text.
- [ ] **Worktrees:** choose the lower **«Worktrees»** tab for `docke-windows-mirror`; the separate table shows task/feature linked worktrees, not duplicate primary rows. Selecting a worktree shows exact branch, HEAD SHA, clean/dirty, changed files and PR/CI details below. Compare to Docker Git without modifying it:

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
- [ ] **Refresh:** **«Обновить список»** reads Docker/Windows without triggering GitHub; **«Проверить выбранный»** and **«Проверить все»** use Gateway for remote checks and progressively update the table. **«Отмена проверки»** stops the remaining requests; already checked rows remain available.
- [ ] **Actions:** **«Pull Windows»** is a single-click owner action that triggers fresh server Preview and Execute with no second browser confirmation. Reject/dirty/unknown preflight must make **no Git write**. Browser Stage/Commit/Push routes remain absent; Gateway ASK Pull is **not** activated.
- [ ] **Pull reject cases (before any real apply):** on a selected repo with dirty/not-main Windows or mismatching origin/dirty Docker, the preview must report blockers. GitHub read failure must deny; if Windows already equals GitHub main, preview must deny as no-op. Do not deliberately dirty a valuable clone merely to test this.
- [ ] **Controlled Pull (owner-run, safe repo only):** after confirming Docker clean primary `main` and GitHub `main` have the same approved SHA, use an intentionally behind **clean** Windows `main`. Click Pull **once**. Server rechecks the full Windows SHA, GitHub SHA, origin and ancestry (unknown ancestry is verified after fetch). There is no second confirmation dialog. The result must be `EXECUTED` and Windows `main`/origin/clean readback must match; repeat Preview must show `WINDOWS_ALREADY_CURRENT`. A divergent/ahead history must fail without merge. If `UNCERTAIN`, do not retry until the receipt and actual Windows SHA are inspected.
- [ ] **HTTP contract (optional):** browser developer tools → Network: `/api/v1/repositories` and `/api/v1/repositories/<repo>?remote=true` return JSON. Direct requests without an authenticated browser session receive `401`.
- [ ] **Shutdown:** Ctrl+C stops the server. If no Pull was explicitly approved, Windows branch/HEAD and clean status must remain unchanged. A successful approved Pull changes only the selected Windows `main` to its exact target SHA.

<a id="known-limits--diagnostics"></a>

## Diagnostics and status ownership

For an error, record the exact code, selected repo/branch and expected result;
do not share URL tokens, cookies, machine grants or provider credentials.
Gateway read failure means UNKNOWN, not zero PRs or empty CI. Visible workflow
runs are observations for the selected SHA, not certified required-CI approval.
The separate developer preview (`--preview-source-task`) must refuse Pull;
ordinary owner-local Browser Pull is authorized by exact session-bound preview
and the user's initial click, not by Gateway ASK. Browser Pull does not activate the
Gateway provider. Actual pending
acceptance and feature gaps are owned only by the [backlog](BACKLOG-IMPROVEMENTS.md).

Detailed source architecture: [API-FIRST](API-FIRST.md).
