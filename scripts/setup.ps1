<#
.SYNOPSIS
    Bootstrap script for dbrescue development and runtime environment (PowerShell).
.DESCRIPTION
    Creates Python virtual environment (.venv), upgrades pip, installs pinned dependencies,
    and initializes .env from .env.example if not present.
#>

[CmdletBinding()]
param (
    [string]$PythonExe = "py"
)

$ErrorActionPreference = "Stop"

Write-Host "==> [dbrescue] Initializing environment bootstrap..." -ForegroundColor Cyan

# 1. Check Python availability
if (Get-Command $PythonExe -ErrorAction SilentlyContinue) {
    Write-Host "--> Found Python launcher: $PythonExe" -ForegroundColor Green
} else {
    Write-Host "--> '$PythonExe' not found, falling back to 'python'..." -ForegroundColor Yellow
    $PythonExe = "python"
}

# 2. Create virtual environment
if (-not (Test-Path ".venv")) {
    Write-Host "--> Creating virtual environment in .venv..." -ForegroundColor Cyan
    & $PythonExe -m venv .venv
} else {
    Write-Host "--> Existing .venv directory found." -ForegroundColor Green
}

$VenvPython = ".\.venv\Scripts\python.exe"
$VenvPip = ".\.venv\Scripts\pip.exe"

# 3. Upgrade pip and install pinned dependencies
Write-Host "--> Installing pinned dependencies from requirements-dev.txt..." -ForegroundColor Cyan
& $VenvPip install --upgrade pip
& $VenvPip install -r requirements-dev.txt

# 4. Copy .env.example to .env if missing
if (-not (Test-Path ".env")) {
    Write-Host "--> Initializing .env from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "--> IMPORTANT: Edit .env to set your secure database passwords." -ForegroundColor Magenta
} else {
    Write-Host "--> .env already exists. Preserving existing configuration." -ForegroundColor Green
}

# 5. Create required directories
$Directories = @("backups", "logs", "docs\reports", "docs\evidence", "db\sqlite")
foreach ($Dir in $Directories) {
    if (-not (Test-Path $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
        Write-Host "--> Created directory: $Dir" -ForegroundColor DarkGray
    }
}

Write-Host "==> [dbrescue] Environment bootstrap completed successfully!" -ForegroundColor Green
Write-Host "    Activate your environment using: .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
