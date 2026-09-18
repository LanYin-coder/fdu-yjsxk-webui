param([switch]$PrepareOnly)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataRoot = if ($env:FDU_WEBUI_DATA_DIR) { $env:FDU_WEBUI_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "FDUCourseHelper" }
$RuntimeRoot = Join-Path $DataRoot "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
$env:FDU_WEBUI_DATA_DIR = $DataRoot
$env:PYTHONUTF8 = "1"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeRoot "python"
$env:UV_CACHE_DIR = Join-Path $RuntimeRoot "cache"

# Reopen an existing instance before doing any dependency work.
$InstanceFile = Join-Path $DataRoot "instance.json"
if (-not $PrepareOnly -and (Test-Path $InstanceFile)) {
    try {
        $Instance = Get-Content -Raw -Encoding UTF8 $InstanceFile | ConvertFrom-Json
        $Port = [int]$Instance.port
        if ($Port -ge 1024 -and $Port -le 65535) {
            Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/app/activate" -Method Post -Body "{}" -ContentType "application/json" -Headers @{ "X-FDU-App-Token" = $Instance.token } -TimeoutSec 3 | Out-Null
            exit 0
        }
    } catch { }
}
$SetupLock = $null
try {
    try {
        $SetupLock = [IO.File]::Open((Join-Path $RuntimeRoot "setup.lock"), "OpenOrCreate", "ReadWrite", "None")
    } catch { throw "Setup is already running. Wait for the first window to finish, then try again." }
    $UvVersion = "0.12.16"
    $UvRoot = Join-Path $RuntimeRoot "uv-$UvVersion"
    $UvExe = Join-Path $UvRoot "uv.exe"
    if (-not (Test-Path $UvExe)) {
        Write-Host "Preparing the official Python runtime manager (first launch only)..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $Archive = Join-Path $RuntimeRoot "uv-$UvVersion.zip"
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $Archive
        $Expected = "f730454bf09019754e5e5abd71a8aa18683cb739cba0d9c720bac2e7c901160f"
        if ((Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant() -ne $Expected) {
            throw "Runtime manager download failed its SHA256 check. Please retry."
        }
        Expand-Archive -Path $Archive -DestinationPath $UvRoot -Force
    }
    $VenvRoot = Join-Path $RuntimeRoot "venv"
    $PythonExe = Join-Path $VenvRoot "Scripts/python.exe"
    if (-not (Test-Path $PythonExe)) {
        Write-Host "Downloading Python 3.12 into this user's local application folder..."
        & $UvExe venv --managed-python --python 3.12 $VenvRoot
        if ($LASTEXITCODE -ne 0) { throw "Could not prepare Python. Check your Internet connection and retry." }
    }
    $Requirements = Join-Path $ProjectRoot "requirements-desktop.txt"
    $BaseRequirements = Join-Path $ProjectRoot "requirements.txt"
    $Stamp = (Get-FileHash -Algorithm SHA256 $Requirements).Hash + (Get-FileHash -Algorithm SHA256 $BaseRequirements).Hash
    $StampFile = Join-Path $RuntimeRoot "requirements.sha256"
    if (-not (Test-Path $StampFile) -or (Get-Content -Raw $StampFile).Trim() -ne $Stamp) {
        Write-Host "Installing application dependencies (first launch or update)..."
        & $UvExe pip install --python $PythonExe -r $Requirements
        if ($LASTEXITCODE -ne 0) { throw "Could not install dependencies. Check your Internet connection and retry." }
        Set-Content -Encoding ASCII -Path $StampFile -Value $Stamp
    }
    if ($PrepareOnly) { Write-Host "Runtime ready: $PythonExe"; exit 0 }
    $Entry = Join-Path $ProjectRoot "webui.py"
    $LogRoot = Join-Path $DataRoot "logs"
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    $Child = Start-Process -FilePath $PythonExe -ArgumentList @("-u", "`"$Entry`"", "--desktop") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LogRoot "launcher-out.log") -RedirectStandardError (Join-Path $LogRoot "launcher-error.log")
    Start-Sleep -Seconds 2
    if ($Child.HasExited -and $Child.ExitCode -ne 0) { throw "Startup failed. See logs in $LogRoot" }
    Write-Host "FDU Course Helper started. Keep this folder for future launches."
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "No administrator rights are needed. Extract the entire ZIP before launching."
    exit 1
} finally {
    if ($null -ne $SetupLock) { $SetupLock.Dispose() }
}
