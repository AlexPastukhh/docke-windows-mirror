#requires -Version 5.1
<#
Owner-reviewed Windows launcher for the isolated READ-only HTTPS Mirror API.
Never creates credentials, changes ACLs, opens Windows Firewall, fetches Git,
registers a service, or activates a Gateway provider.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[0-9a-f]{40}$')][string]$SourceSha,
    [Parameter(Mandatory=$true)][string]$Bind,
    [Parameter(Mandatory=$true)][string]$CertificatePath,
    [Parameter(Mandatory=$true)][string]$KeyPath,
    [Parameter(Mandatory=$true)][string]$SecretFile,
    [Parameter(Mandatory=$true)][string[]]$AllowedRepo
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($env:OS -cne 'Windows_NT') { throw 'MIRROR_WINDOWS_REQUIRED' }
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $project 'mirror_api\gateway_readonly.py'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw 'MIRROR_GATEWAY_READ_SOURCE_MISSING'
}
$py = (Get-Command 'py.exe' -CommandType Application -ErrorAction Stop).Source
$git = (Get-Command 'git.exe' -CommandType Application -ErrorAction Stop).Source
$null = Get-Command 'docker.exe' -CommandType Application -ErrorAction Stop
$branch = (& $git -C $project branch --show-current | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -cne 'main') { throw 'MIRROR_SOURCE_NOT_MAIN' }
$head = (& $git -C $project rev-parse HEAD | Out-String).Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $head -cne $SourceSha) { throw 'MIRROR_SOURCE_SHA_MISMATCH' }
$status = @(& $git -C $project status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $status.Count -ne 0) { throw 'MIRROR_SOURCE_NOT_CLEAN' }
$address = $null
if (-not [Net.IPAddress]::TryParse($Bind, [ref]$address) -or
    $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
    $address.Equals([Net.IPAddress]::Any) -or
    ([Net.IPAddress]::IsLoopback($address) -and $Bind -cne '127.0.0.1') -or
    $address.Equals([Net.IPAddress]::Broadcast)) {
    throw 'MIRROR_GATEWAY_BIND_INVALID'
}
if ($AllowedRepo.Count -lt 1 -or
    @($AllowedRepo | Select-Object -Unique).Count -ne $AllowedRepo.Count) {
    throw 'MIRROR_GATEWAY_ALLOWLIST_INVALID'
}
foreach ($name in $AllowedRepo) {
    if ($name -cnotmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$') {
        throw 'MIRROR_GATEWAY_ALLOWLIST_INVALID'
    }
}
function Assert-LocalFile {
    param([string]$Value, [bool]$OwnerOnly)
    if (-not [IO.Path]::IsPathRooted($Value)) { throw 'MIRROR_TLS_ABSOLUTE_PATH_REQUIRED' }
    $full = [IO.Path]::GetFullPath($Value)
    $allowedRoot = [IO.Path]::GetFullPath($env:LOCALAPPDATA) + '\'
    if (-not $full.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'MIRROR_TLS_OWNER_LOCAL_PATH_REQUIRED'
    }
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if (-not ($item -is [IO.FileInfo]) -or
        (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw 'MIRROR_TLS_FILE_UNSAFE'
    }
    if ($OwnerOnly) {
        $acl = Get-Acl -LiteralPath $full
        $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
                     'S-1-5-18', 'S-1-5-32-544')
        if (-not $acl.AreAccessRulesProtected -or
            $allowed -cnotcontains $acl.GetOwner(
                [Security.Principal.SecurityIdentifier]).Value) {
            throw 'MIRROR_TLS_PRIVATE_ACL_INVALID'
        }
        foreach ($rule in $acl.GetAccessRules($true, $true,
                                              [Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and
                $allowed -cnotcontains $rule.IdentityReference.Value) {
                throw 'MIRROR_TLS_PRIVATE_ACL_INVALID'
            }
        }
    }
    return $full
}
$cert = Assert-LocalFile $CertificatePath $false
$key = Assert-LocalFile $KeyPath $true
$secret = Assert-LocalFile $SecretFile $true
$listen = @(Get-NetTCPConnection -LocalPort 443 -State Listen -ErrorAction SilentlyContinue)
if ($listen.Count -gt 0) { throw 'MIRROR_HTTPS_PORT_443_IN_USE' }
$arguments = @('-3', '-m', 'mirror_api.gateway_readonly',
    '--windows-root', (Join-Path $HOME 'Repos'), '--bind', $Bind,
    '--cert', $cert, '--key', $key, '--secret-file', $secret)
foreach ($name in $AllowedRepo) { $arguments += @('--repo', $name) }
Write-Host 'MIRROR_GATEWAY_READ_PREFLIGHT_PASS'
Write-Host 'HTTPS READ listener: manual owner session only; stop using Ctrl+C.'
Push-Location -LiteralPath $project
try {
    & $py @arguments
    if ($LASTEXITCODE -ne 0) { throw 'MIRROR_GATEWAY_READ_EXIT_FAILED' }
} finally {
    Pop-Location
}
