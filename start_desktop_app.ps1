param(
    [switch]$Browser,
    [switch]$CheckOnly,
    [switch]$ServiceOnly
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:8765/"
$healthUrl = "${url}api/health"
$dataDirectory = Join-Path $root "data"
$moduleRoot = Join-Path $root "src\quant_news_monitor"
$expectedBuild = $null

function Test-AppReady {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq "ok" -and $response.build_id -eq $expectedBuild
    }
    catch {
        return $false
    }
}

try {
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    if (Test-Path -LiteralPath $moduleRoot) {
        $modules = @((Get-ChildItem -LiteralPath $moduleRoot -File | Where-Object { $_.Extension -in @('.py', '.json') }).Name)
        [Array]::Sort($modules, [StringComparer]::Ordinal)
        # Shortcuts may run without the PowerShell module that provides Get-FileHash.
        $hash = [Security.Cryptography.SHA256]::Create()
        try {
            $manifest = ($modules | ForEach-Object {
                $bytes = [IO.File]::ReadAllBytes((Join-Path $moduleRoot $_))
                $digest = [BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-', '')
                "${_}:${digest}`n"
            }) -join ''
            $expectedBuild = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($manifest))).Replace('-', '')
        }
        finally { $hash.Dispose() }
    }
    if ($CheckOnly -or -not (Test-AppReady)) {
        $pythonExecutable = Join-Path $root ".venv\Scripts\python.exe"
        $pythonPrefix = @()
        if (-not (Test-Path -LiteralPath $pythonExecutable)) {
            $pythonLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
            if ($pythonLauncher) {
                $pythonExecutable = $pythonLauncher.Source
                $pythonPrefix = @("-3")
            }
            else {
                $pythonExecutable = (Get-Command "python.exe" -ErrorAction Stop).Source
            }
        }

        # Validate the exact runtime used by the shortcut before starting monitors.
        $preflightError = Join-Path $dataDirectory "desktop-preflight-error.log"
        $preflight = Start-Process -FilePath $pythonExecutable `
            -ArgumentList ($pythonPrefix + @("-X", "utf8", "app.py", "--help")) `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $dataDirectory "desktop-preflight.log") `
            -RedirectStandardError $preflightError
        # Retain the process handle so Windows PowerShell can read its exit code.
        $null = $preflight.Handle
        if (-not $preflight.WaitForExit(30000)) {
            $preflight.Kill()
            throw "Startup dependency check timed out. See $preflightError"
        }
        $preflight.Refresh()
        if ($preflight.ExitCode -ne 0) {
            $details = Get-Content -LiteralPath $preflightError -Raw
            throw "Python dependency check failed ($pythonExecutable). Install the project dependencies in this runtime with: python -m pip install -e .`n$details"
        }
        if ($CheckOnly) {
            Write-Output "Startup check passed: $pythonExecutable"
            if ($expectedBuild) { Write-Output "Build: $expectedBuild" }
            exit 0
        }

        # Replace only an older server launched from this exact project, including its junction alias.
        $rootItem = Get-Item -LiteralPath $root
        $actualRoot = if ($rootItem.LinkType -eq "Junction") { @($rootItem.Target)[0] } else { $rootItem.FullName }
        $listeners = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        foreach ($serverId in ($listeners.OwningProcess | Select-Object -Unique)) {
            $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverId"
            $match = [regex]::Match($server.CommandLine, '^"([^"]+)" -X utf8 -u app\.py --port 8765 --no-browser\s*$')
            if (-not $match.Success -or -not $match.Groups[1].Value.EndsWith('\.venv\Scripts\python.exe', [StringComparison]::OrdinalIgnoreCase)) {
                throw "Port 8765 belongs to an unrecognized process; it was not stopped."
            }
            $serverRoot = [IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $match.Groups[1].Value) "..\.."))
            $serverRootItem = Get-Item -LiteralPath $serverRoot
            $serverActualRoot = if ($serverRootItem.LinkType -eq "Junction") { @($serverRootItem.Target)[0] } else { $serverRootItem.FullName }
            if ($serverActualRoot.TrimEnd('\') -ne $actualRoot.TrimEnd('\')) {
                throw "Port 8765 belongs to another project; it was not stopped."
            }
            Stop-Process -Id $serverId -ErrorAction Stop
        }

        $standardOutput = Join-Path $dataDirectory "desktop-app.log"
        $standardError = Join-Path $dataDirectory "desktop-app-error.log"
        $appProcess = Start-Process -FilePath $pythonExecutable `
            -ArgumentList ($pythonPrefix + @("-X", "utf8", "-u", "app.py", "--port", "8765", "--no-browser")) `
            -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $standardOutput `
            -RedirectStandardError $standardError

        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        while (-not (Test-AppReady)) {
            $appProcess.Refresh()
            if ($appProcess.HasExited) {
                throw "The service exited with code $($appProcess.ExitCode). See $standardError and $standardOutput"
            }
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "The service did not become ready within 90 seconds. See $standardError and $standardOutput"
            }
            Start-Sleep -Milliseconds 500
        }
    }

    if ($ServiceOnly) {
        Write-Output "STock service ready: $url"
        exit 0
    }

    if ($Browser) {
        Start-Process $url
    }
    else {
        $desktopDirectory = Join-Path $root "desktop"
        $desktopExecutable = Join-Path $desktopDirectory "node_modules\electron\dist\electron.exe"
        if (-not (Test-Path -LiteralPath $desktopExecutable)) {
            throw "The native desktop runtime is missing. Run npm ci in $desktopDirectory, or use -Browser."
        }
        # The user requested this visible application; Electron owns its frameless title bar.
        $env:ELECTRON_RUN_AS_NODE = $null
        Start-Process -FilePath $desktopExecutable -ArgumentList @("`"$desktopDirectory`"", "--enable-logging") `
            -WorkingDirectory $root `
            -RedirectStandardOutput (Join-Path $dataDirectory "desktop-window.log") `
            -RedirectStandardError (Join-Path $dataDirectory "desktop-window-error.log")
    }
}
catch {
    $message = $_.Exception.Message
    $launcherError = Join-Path $dataDirectory "desktop-launcher-error.log"
    if (Test-Path -LiteralPath $dataDirectory) {
        $message | Out-File -LiteralPath $launcherError -Encoding utf8
    }
    if ($CheckOnly -or $ServiceOnly) {
        [Console]::Error.WriteLine($message)
    }
    else {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "STock could not start.`n$message`n`nDetails: $launcherError",
            "STock"
        ) | Out-Null
    }
    exit 1
}
