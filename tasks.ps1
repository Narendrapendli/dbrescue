<#
.SYNOPSIS
    dbrescue Automation Tasks Script (PowerShell equivalent of Makefile)
.DESCRIPTION
    Provides automated commands for environment setup, Docker orchestration,
    testing, linting, backup, and disaster recovery execution.
    SAFETY GUARD: Persistent storage volumes are strictly preserved.
#>

[CmdletBinding()]
param (
    [Parameter(Position = 0, Mandatory = $false)]
    [ValidateSet("setup", "up", "seed", "test", "lint", "run-app", "test-persistence", "backup", "down", "help")]
    [string]$Task = "help"
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "dbrescue Management Tasks (PowerShell):" -ForegroundColor Cyan
    Write-Host "  .\tasks.ps1 setup            - Install development dependencies in current environment"
    Write-Host "  .\tasks.ps1 up               - Start MySQL container (preserves volume)"
    Write-Host "  .\tasks.ps1 seed             - Seed database with student and course fixtures"
    Write-Host "  .\tasks.ps1 test             - Run complete automated pytest test suite"
    Write-Host "  .\tasks.ps1 lint             - Run ruff linter and formatting checks"
    Write-Host "  .\tasks.ps1 run-app          - Launch Streamlit operational dashboard"
    Write-Host "  .\tasks.ps1 test-persistence - Execute 18-stage disaster recovery persistence test"
    Write-Host "  .\tasks.ps1 backup           - Generate logical mysqldump compressed backup"
    Write-Host "  .\tasks.ps1 down             - Safely stop compose containers (NEVER with -v)"
}

function Invoke-Setup {
    Write-Host "==> Installing dbrescue dependencies..." -ForegroundColor Green
    python -m pip install --upgrade pip
    pip install -r requirements-dev.txt
}

function Invoke-Up {
    Write-Host "==> Starting MySQL infrastructure container..." -ForegroundColor Green
    docker compose up -d mysql
}

function Invoke-Seed {
    Write-Host "==> Seeding database records..." -ForegroundColor Green
    & ".\scripts\db-seed.ps1"
}

function Invoke-Test {
    Write-Host "==> Executing test suite..." -ForegroundColor Green
    python -m pytest -v
}

function Invoke-Lint {
    Write-Host "==> Checking code quality & formatting with ruff..." -ForegroundColor Green
    ruff check .
    ruff format --check .
}

function Invoke-RunApp {
    Write-Host "==> Launching dbrescue Streamlit dashboard on http://127.0.0.1:8501..." -ForegroundColor Green
    streamlit run app/main.py --server.port 8501 --server.address 127.0.0.1
}

function Invoke-TestPersistence {
    Write-Host "==> Executing 18-stage disaster recovery state machine..." -ForegroundColor Green
    python -c "from dbrescue.recovery import RecoveryOrchestrator; from dbrescue.config import load_config; res = RecoveryOrchestrator(load_config()).run(confirm=True); print('Recovery Result:', res.status)"
}

function Invoke-Backup {
    Write-Host "==> Creating logical MySQL backup..." -ForegroundColor Green
    python -c "from dbrescue.backup import BackupEngine; from dbrescue.config import load_config; meta = BackupEngine(load_config()).create_logical_backup(label='powershell-backup'); print('Created backup:', meta.filename, meta.sha256_checksum)"
}

function Invoke-Down {
    Write-Host "==> Safely stopping compose services without removing volume..." -ForegroundColor Yellow
    docker compose down
}

switch ($Task) {
    "setup"            { Invoke-Setup }
    "up"               { Invoke-Up }
    "seed"             { Invoke-Seed }
    "test"             { Invoke-Test }
    "lint"             { Invoke-Lint }
    "run-app"          { Invoke-RunApp }
    "test-persistence" { Invoke-TestPersistence }
    "backup"           { Invoke-Backup }
    "down"             { Invoke-Down }
    default            { Show-Help }
}
