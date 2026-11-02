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
requires confirmation and never uses force. The shared infrastructure
repositories (`script-control-center`, `docke-windows-mirror`, `security`,
`tunnel`, `rd-graphs`) cannot be mutated through these GUI actions.

## Tests

From the Docker project directory:

```bash
python3 -B -m unittest -q test_mirror_workflow_source test_mirror_docker_actions
```

The Windows compilation test must also pass on an actual Windows system.
Windows PowerShell 5.1 compilation and GUI behavior still require owner
verification on Windows after the two repository updates.
