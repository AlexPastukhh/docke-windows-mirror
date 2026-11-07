# docke-windows-mirror

Standalone Docker–Windows Mirror application.

Начать с [локального реестра Mirror](docs/use-cases/README.md) и короткого входа
[главного реестра Fortis](https://github.com/AlexPastukhh/fortis-container/blob/main/docs/use-cases/README.md).
Каждый новый чат читает полностью UC-CONT-002/003; остальные UC — по задаче,
просматривать весь каталог не требуется.

Canonical source for the independent Windows PowerShell 5.1 / WinForms interface for checking Docker,
GitHub, and Windows Git mirrors. The primary Git repositories live inside
Fortis Docker; Windows is a receiver. All actions are owner-triggered.

## Paths

Docker source: `/workspace/repo/Repos/docke-windows-mirror`

Windows clone: `%USERPROFILE%\Repos\docke-windows-mirror`

GitHub: https://github.com/AlexPastukhh/docke-windows-mirror

## Repository boundaries

All Mirror UI sources, Docker exporters, Git actions, legacy implementation,
tests, and Mirror-specific documentation are maintained **here**.

The stable Script Center launcher at
`%USERPROFILE%\Repos\script-control-center\Fortis-Windows-Mirror-Status.ps1`
is a thin compatibility wrapper which runs the standalone launcher from
`%USERPROFILE%\Repos\docke-windows-mirror`. It does not compile or run
a local copy of the old UI. Keep both Windows clones alongside one another.

[Detailed Mirror workflow and security behavior](docs/WORKFLOW.md).

## API-first browser source candidate (not yet installed)

The Windows API + local browser UI implementation is documented in [API-FIRST](docs/API-FIRST.md). Its primary view now follows the WinForms-style **13-column Docker/GitHub/Windows repository table**, with a full-copyable **«Строка»** lower panel and an optional **«Worktrees»** lower table for the selected primary repository. It reads authoritative Docker worktree inventory and Windows primary state, plus read-only GitHub PR/CI through the authenticated native Gateway client in Docker (live token-scoped multi-repo access). The Gateway-side Mirror `pull_main` provider exists only as untracked source in `fortis-approvals` task `mirror-provider-20261107` (not in Gateway `main`, not registered or deployed) and still requires a Gateway-managed merge receipt incompatible with owner GitHub UI merge; see [API-FIRST](docs/API-FIRST.md). WebAuthn/TLS/Windows Pull acceptance is **pending**. The existing WinForms launcher is retained until Windows owner acceptance. Do not interpret the source candidate as a released replacement.

## Windows

From the Windows clone in PowerShell 5.1:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Test-MirrorWorkflowCompile.ps1
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Fortis-Windows-Mirror-Status.ps1
```

Windows runtime requires `docker.exe`, `git.exe`, a running container called
`fortis-repo-test`, and the matching Docker files at the path above.
The launcher checks the Docker repo list on startup; GitHub and Windows
checks happen only after clicking a check button.

## GitHub publication and legacy compatibility

For this source candidate, **all GitHub network effects and reads run through the
existing typed Fortis Gateway client**, with exact task/head preconditions and
provider credentials confined to Gateway. Browser UI routes are **read-only**;
there is no Stage/Commit/Push write endpoint or owner Git allowlist. The retained
WinForms source and `tools/Docker-Mirror-Git-Actions.py` still support local
stage/commit logic, but the helper fails closed on network GitHub fetch,
`ls-remote` and push. Do not attempt to recover the old PAT helper as a bypass.
See [WORKFLOW](docs/WORKFLOW.md) for the authorized handoff.

## Tests

From the Docker project directory:

```bash
python3 -B -m unittest -q test_mirror_workflow_source test_mirror_docker_actions
```

The Windows compilation test must also pass on an actual Windows system.
Windows PowerShell 5.1 compilation and GUI behavior still require owner
verification on Windows after the two repository updates.


## Будущие улучшения

Единый список задач и технического долга: [BACKLOG-IMPROVEMENTS](docs/BACKLOG-IMPROVEMENTS.md). Он не заменяет действующие source/owner runbooks.
