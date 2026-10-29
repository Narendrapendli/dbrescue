# Run the automated pytest suite with coverage and JUnit XML report generation.
# Adheres strictly to the safety guard: never touches production mysql_data.

[CmdletBinding()]
param (
    [string]$Marker = "",
    [switch]$Verbose = $false
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  dbrescue Automated Acceptance Test Runner" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

$ReportsDir = Join-Path $ProjectRoot "reports"
if (-not (Test-Path $ReportsDir)) {
    New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null
}

$JunitXml = Join-Path $ReportsDir "junit.xml"
$CovJson = Join-Path $ReportsDir "coverage.json"
$CovHtml = Join-Path $ReportsDir "coverage_html"

$PytestArgs = @(
    "-m", "pytest",
    "--junitxml=$JunitXml",
    "--cov=src",
    "--cov-report=json:$CovJson",
    "--cov-report=html:$CovHtml",
    "--cov-report=term-missing"
)

if ($Marker -ne "") {
    $PytestArgs += @("-m", $Marker)
}

if ($Verbose) {
    $PytestArgs += @("-v")
}

Write-Host "Executing: $PythonExe $($PytestArgs -join ' ')" -ForegroundColor Yellow
& $PythonExe @PytestArgs
$ExitCode = $LASTEXITCODE

Write-Host "Test execution completed with exit code: $ExitCode" -ForegroundColor $(if ($ExitCode -eq 0) { "Green" } else { "Red" })
exit $ExitCode
