# docke-windows-mirror

Standalone Docker–Windows Mirror application.

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

The Windows API + local browser UI implementation is documented in [API-FIRST](docs/API-FIRST.md). It reads authoritative Docker worktree inventory and Windows primary state, plus read-only GitHub PR/CI through the authenticated native Gateway client in Docker (registered repository scope only). Gateway read/Pull source adapters are in `fortis-approvals/integrations/mirror`; WebAuthn/TLS live installation is **pending**. The existing WinForms launcher is retained until Windows owner acceptance. Do not interpret the source candidate as a released replacement.

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

## Docker-only Stage, Commit, Push

Stage/Commit/Push run through `tools/Docker-Mirror-Git-Actions.py` inside
the Docker container, not through Windows Git. Branch and HEAD are rechecked;
Stage uses a reviewed selection, Commit uses the reviewed index, and Push
requires confirmation and never uses force. There is no special repository
blocklist: every valid Docker repository can use these actions, including
`rd-graphs` and other infrastructure projects. Existing branch, origin,
preview, freshness and owner-confirmation safeguards still apply.

For GitHub HTTPS network operations, the Docker helper uses the persistent
`/workspace/repo/.fortis-auth/git-credential-fortis-shared` instead of the
temporary VS Code credential bridge. A repository must be granted access in
the existing fine-grained PAT. No token is stored in this repository or
passed on the command line. SSH-origin authentication remains separate.

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
