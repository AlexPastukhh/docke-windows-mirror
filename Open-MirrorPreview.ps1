#requires -Version 5.1
<#
Local temporary browser preview of the unmerged Mirror API source. The script
copies Python + static assets out of the existing Docker task worktree and
starts the read-only loopback server. It never touches Windows Git clones,
Docker Git index, Gateway service configuration, or source main branches.
#>
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port = 8765
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'MIRROR_PREVIEW_WINDOWS_REQUIRED' }
$docker = (Get-Command docker.exe -CommandType Application -ErrorAction Stop).Source
$null = (Get-Command git.exe -CommandType Application -ErrorAction Stop)
$null = (Get-Command py.exe -CommandType Application -ErrorAction Stop)
$container = 'fortis-repo-test'
$source = '/workspace/repo/Worktrees/docke-windows-mirror/mirror-api-workflow-20261107'
$names = @(& $docker ps --filter 'name=^/fortis-repo-test$' --format '{{.Names}}')
if ($LASTEXITCODE -ne 0 -or $names -notcontains $container) {
    throw 'MIRROR_PREVIEW_DOCKER_CONTAINER_NOT_RUNNING'
}
& $docker exec --user '1000:1000' $container test -f ($source + '/mirror_api/server.py')
if ($LASTEXITCODE -ne 0) { throw 'MIRROR_PREVIEW_DOCKER_WORKTREE_NOT_FOUND' }

$previewRoot = Join-Path $env:TEMP ('Fortis-Mirror-Preview-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $previewRoot -ErrorAction Stop | Out-Null
foreach ($item in @('mirror_api', 'Start-MirrorAPI.ps1')) {
    & $docker cp ($container + ':' + $source + '/' + $item) $previewRoot
    if ($LASTEXITCODE -ne 0) { throw ('MIRROR_PREVIEW_COPY_FAILED: ' + $item) }
}
if (-not (Test-Path -LiteralPath (Join-Path $previewRoot 'mirror_api\server.py') -PathType Leaf)) {
    throw 'MIRROR_PREVIEW_SERVER_SOURCE_NOT_COPIED'
}
Write-Host ('Temporary preview directory: ' + $previewRoot)
Write-Host 'This preview is read-only. It does not merge/pull/commit or change the Windows repositories.'
Write-Host 'Check the one-time browser URL printed next; keep this terminal running while using the UI.'
& (Join-Path $previewRoot 'Start-MirrorAPI.ps1') -MirrorRoot (Join-Path $env:USERPROFILE 'Repos') -Port $Port -PreviewTask 'mirror-api-workflow-20261107'
