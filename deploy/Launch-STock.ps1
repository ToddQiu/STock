[CmdletBinding()]
param(
    [string]$Server,
    [string]$Domain,
    [ValidateRange(1,65535)][int]$Port = 22,
    [string]$IdentityFile,
    [ValidateSet('install','verify','backup','rollback','status')][string]$Action = 'install',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$archive = Join-Path $PSScriptRoot 'STock-release.tar.gz'
$checksumFile = Join-Path $PSScriptRoot 'STock-release.sha256'
foreach ($file in @($archive, $checksumFile)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing release file: $file" }
}
$expected = (Get-Content -LiteralPath $checksumFile -Raw).Trim()
if ($expected -cnotmatch '^[a-f0-9]{64}$' -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
    throw 'Release checksum failed. Nothing was sent to a server.'
}
if ($CheckOnly) {
    Write-Host "Release package verified: $expected"
    Write-Host 'Offline check only. No purchase, SSH connection, upload or installation performed.'
    return
}
if (-not $Server) { $Server = Read-Host 'SSH target (example: root@203.0.113.10)' }
if ($Server -cnotmatch '^([a-z_][a-z0-9_-]{0,31})@([0-9]{1,3}(?:\.[0-9]{1,3}){3})$') {
    throw 'Use username@public-IPv4. No SSH options, spaces or commands are accepted here.'
}
$userName = $Matches[1]
$publicIP = $Matches[2]
$parsedIP = [System.Net.IPAddress]::Parse($publicIP)
if ($parsedIP.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'IPv4 is required.' }
if ($Action -eq 'install') {
    if (-not $Domain) { $Domain = Read-Host 'Website hostname (without https://)' }
    $Domain = $Domain.ToLowerInvariant()
    if ($Domain.Length -gt 253 -or $Domain -notmatch '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$') {
        throw 'Invalid DNS hostname.'
    }
}
Get-Command ssh.exe, scp.exe, tar.exe -ErrorAction Stop | Out-Null
$managerCode = (& tar.exe -xOf $archive deploy/manage.py) -join "`n"
if ($LASTEXITCODE -ne 0 -or -not $managerCode) { throw 'Verified release has no deployment program.' }
$sshOptions = @('-o', 'StrictHostKeyChecking=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15')
if ($IdentityFile) {
    $keyPath = (Resolve-Path -LiteralPath $IdentityFile).Path
    $sshOptions += @('-i', $keyPath, '-o', 'IdentitiesOnly=yes')
}
$remotePython = if ($userName -eq 'root') { 'python3' } else { 'sudo -n python3' }
& ssh.exe @sshOptions -p $Port $Server "$remotePython --version"
if ($LASTEXITCODE -ne 0) {
    throw 'SSH preflight failed. Verify the host fingerprint and configure SSH-key access (and sudo) first. Security checks were NOT bypassed.'
}
$remoteArchive = '/tmp/stock-' + $expected.Substring(0,16) + '-' + [Guid]::NewGuid().ToString('N') + '.tar.gz'
$arguments = "$Action --root /opt/stock"
if ($Action -eq 'install') {
    & scp.exe @sshOptions -P $Port $archive "${Server}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw 'Release upload failed. Deployment was not started.' }
    $arguments += " --domain $Domain --ip $publicIP --bundle $remoteArchive --sha256 $expected"
}
$oldEncoding = $OutputEncoding
try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $managerCode | & ssh.exe @sshOptions -p $Port $Server "$remotePython - $arguments"
    if ($LASTEXITCODE -ne 0) { throw 'Deployment command failed. See the error above; no successful launch is claimed.' }
} finally {
    $OutputEncoding = $oldEncoding
}
if ($Action -eq 'install') {
    try {
        Invoke-WebRequest -Uri "https://$Domain/api/health" -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 30 | Out-Null
        throw 'The external authentication check failed: anonymous request was accepted.'
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) { throw }
    }
    Write-Host "Server checks and external HTTPS authentication check passed: https://$Domain"
    Write-Host 'Login username: stock. Read the access key privately on the server. Shared workspace, not private user accounts.'
}
