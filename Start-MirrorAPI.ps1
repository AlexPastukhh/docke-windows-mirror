#requires -Version 5.1
<#
Start the source-candidate Fortis Mirror API/browser on Windows loopback only.
Does not install a service, open a firewall, activate Gateway, or pull Git.
#>
[CmdletBinding()]
param(
    [string]$MirrorRoot = (Join-Path $env:USERPROFILE 'Repos'),
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [string]$PreviewTask = '',
    [switch]$OpenBrowser,
    [switch]$GatewayReadV2,
    [string]$GatewayCertificate = '',
    [string]$GatewayPrivateKey = '',
    [string]$GatewaySecretFile = '',
    [string]$GatewayMachineGeneration = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'MIRROR_WINDOWS_REQUIRED' }
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($file in @('mirror_api\server.py','mirror_api\static\index.html',
        'mirror_api\static\app.js','mirror_api\static\style.css')) {
    if (-not (Test-Path -LiteralPath (Join-Path $project $file) -PathType Leaf)) {
        throw ('MIRROR_API_SOURCE_MISSING: ' + $file)
    }
}
$python = (Get-Command py.exe -CommandType Application -ErrorAction Stop).Source
$null = (Get-Command git.exe -CommandType Application -ErrorAction Stop)
$null = (Get-Command docker.exe -CommandType Application -ErrorAction Stop)
if (-not $GatewayReadV2 -and ($GatewayCertificate -or $GatewayPrivateKey -or
        $GatewaySecretFile -or $GatewayMachineGeneration)) {
    throw 'MIRROR_V2_EXPLICIT_OPT_IN_REQUIRED'
}
if ($GatewayReadV2) {
    # Source-only owner mode: no automatic service replacement or ACL changes.
    if ($PreviewTask -or $Port -eq 443) { throw 'MIRROR_V2_LAUNCH_SCOPE_DENIED' }
    if ($GatewayMachineGeneration -cnotmatch '^[0-9a-f]{64}$') {
        throw 'MIRROR_V2_OWNER_GENERATION_REQUIRED'
    }
    if (-not $GatewayCertificate -or -not $GatewayPrivateKey -or -not $GatewaySecretFile) {
        throw 'MIRROR_V2_PRIVATE_FILES_REQUIRED'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $project 'mirror_api\gateway_v2_launcher.py') -PathType Leaf)) {
        throw 'MIRROR_V2_LAUNCHER_SOURCE_MISSING'
    }
    $argsList = @('-3', '-m', 'mirror_api.gateway_v2_launcher',
        '--windows-root', $MirrorRoot, '--port', [string]$Port,
        '--cert', $GatewayCertificate, '--key', $GatewayPrivateKey,
        '--secret-file', $GatewaySecretFile,
        '--machine-generation', $GatewayMachineGeneration)
} else {
    $argsList = @('-3', '-m', 'mirror_api.server', '--windows-root', $MirrorRoot,
                   '--port', [string]$Port)
}
if ($PreviewTask) {
    if ($PreviewTask -cnotmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$') {
        throw 'MIRROR_PREVIEW_READONLY_REQUIRED'
    }
    $argsList += @('--preview-source-task', $PreviewTask)
}
if ($OpenBrowser) { $argsList += '--open-browser' }
Push-Location -LiteralPath $project
try {
    if ($GatewayReadV2) {
        Write-Host 'Opt-in READ v2 source launcher; not an owner production GO.'
    } else {
        Write-Host 'Mirror browser API: loopback only. Copy the one-time URL printed below.'
    }
    & $python @argsList
    if ($LASTEXITCODE -ne 0) { throw 'MIRROR_API_EXIT_FAILED' }
}
finally { Pop-Location }
