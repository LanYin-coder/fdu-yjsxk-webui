param([switch]$UseCurrentPython)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
if ($UseCurrentPython) {
    $BuildPython = (Get-Command python -ErrorAction Stop).Source
} else {
    $BuildVenv = Join-Path $ProjectRoot ".venv-build-windows"
    $BuildPython = Join-Path $BuildVenv "Scripts/python.exe"
    if (-not (Test-Path $BuildPython)) {
        & py -3 -m venv $BuildVenv
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.10 or newer, then rerun this script." }
    }
}
& $BuildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $BuildPython -m unittest discover -s tests -p "test_*.py" -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed; build stopped." }
& $BuildPython -m PyInstaller --noconfirm --clean FDUCourseHelper.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
$AppRoot = Join-Path $ProjectRoot "dist/FDUCourseHelper"
& $BuildPython tests/smoke_desktop.py --executable (Join-Path $AppRoot "FDUCourseHelper.exe")
if ($LASTEXITCODE -ne 0) { throw "Packaged application smoke test failed." }
Copy-Item "docs/Windows.md" (Join-Path $AppRoot "README.md") -Force
$ZipPath = Join-Path $ProjectRoot "dist/FDUCourseHelper-Windows-x64.zip"
Compress-Archive -Path $AppRoot -DestinationPath $ZipPath -Force
Write-Host "Built: $ZipPath"
