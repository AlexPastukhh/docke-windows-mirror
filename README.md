# Docker–Windows Mirror

Independent Windows PowerShell 5.1 / WinForms interface for checking Docker,
GitHub, and Windows Git mirrors. The primary Git repositories live inside
Fortis Docker; Windows is a receiver. All actions are owner-triggered.

## Paths

Docker source: `/workspace/repo/Repos/docke-windows-mirror`

Windows clone: `%USERPROFILE%\Repos\docke-windows-mirror`

GitHub: https://github.com/AlexPastukhh/docke-windows-mirror

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
Do not delete the original Mirror files in `script-control-center` until
Windows compilation and the new standalone launcher are verified.
