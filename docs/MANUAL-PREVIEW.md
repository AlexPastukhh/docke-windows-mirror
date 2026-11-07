# Manual Windows browser preview — 2026-11-07

Status: **MANUAL-PENDING**. Source-only task worktree; not merged, installed as service, or released.

## Preconditions

Windows PowerShell 5.1, Docker CLI/Desktop, running container `fortis-repo-test` with current Mirror task worktree available, Windows `git.exe`, Python launcher `py.exe` running Python >=3.9. No elevated PowerShell, new credentials, Git reset, branch switch, commit, merge, pull or Gateway deployment is required. Do not enable owner-Git grants or the Gateway listener for this preview.

## Start

```powershell
$bootstrap = Join-Path $env:TEMP 'Open-MirrorPreview.ps1'
docker cp 'fortis-repo-test:/workspace/repo/Worktrees/docke-windows-mirror/mirror-gateway-ci-20261107/Open-MirrorPreview.ps1' $bootstrap
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap
```

This script checks tools and Docker container state, then `docker cp`s the *current task-source* `mirror_api` and `Start-MirrorAPI.ps1` into a unique `%TEMP%/Fortis-Mirror-Preview-*` folder; the service reads Docker exporter and Gateway-reader scripts from the Docker **task** worktree. The preview service binds to `127.0.0.1:8765`, offers a one-use URL in the console and reads Windows `%USERPROFILE%/Repos` without Git mutation. Keep terminal running, paste the URL into the Windows browser. Ctrl+C stops the process. A new run creates another temporary folder and link. The displayed directory can be deleted after stopping.

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

## Known limits / diagnostics

- Live Windows execution of the candidate is not yet verified; Python/PowerShell startup, Docker Desktop connectivity and Gateway scope/credentials can fail. Report the **exact error code or screenshot**, the selected repo/branch and what you expected; **do not share** the one-time URL token, cookies, machine grants or any other credentials.
- PR/CI and branch reads use the existing native Primary Gateway with its **active token-scoped dynamic repository support**. The reader never uses a Repo GitHub token fallback. A successful repo read does not prove write authorization. A temporary inability to access Gateway is a valid `GATEWAY_READ_UNAVAILABLE` error, not missing PRs.
- The view shows observed CI runs for an exact PR head and latest attempt per workflow. It does not claim all required checks passed or make PR/CI mutations.
- Pull, Windows API Gateway TLS, full WebAuthn E2E and permanent Windows service installation are separate MANUAL-PENDING tasks. Do not infer they are working from this preview.

Detailed source architecture: [API-FIRST](API-FIRST.md).
