<#
.SYNOPSIS
    Reports status of dbrescue MySQL containers, healthchecks, and table counts.
.DESCRIPTION
    Inspects container state, port bindings, volumes, and executes diagnostics.
    SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
#>

[CmdletBinding()]
param ()

$ErrorActionPreference = "Continue"

$ContainerName = if ($env:DOCKER_PRIMARY_CONTAINER) { $env:DOCKER_PRIMARY_CONTAINER } else { "dbrescue-mysql-primary" }
$RootPass = if ($env:MYSQL_ROOT_PASSWORD) { $env:MYSQL_ROOT_PASSWORD } else { "RootRescue2026!Emergency" }

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "                dbrescue Database Status Report                   " -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Check Docker daemon
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[STATUS] Docker CLI is not installed or not in PATH." -ForegroundColor Red
    exit 0
}

# 2. Inspect container
$State = docker inspect --format '{{json .State}}' $ContainerName 2>$null
if (-not $State) {
    Write-Host "[CONTAINER] Container '$ContainerName' does NOT exist." -ForegroundColor Yellow
    exit 0
}

$StateObj = $State | ConvertFrom-Json
$Health = if ($StateObj.Health) { $StateObj.Health.Status } else { "N/A" }

Write-Host "[CONTAINER] Name    : $ContainerName" -ForegroundColor Green
Write-Host "[CONTAINER] Status  : $($StateObj.Status)" -ForegroundColor Green
Write-Host "[CONTAINER] Health  : $Health" -ForegroundColor Green
Write-Host "[CONTAINER] Started : $($StateObj.StartedAt)" -ForegroundColor DarkGray

# 3. Port bindings
$Ports = docker port $ContainerName 3306 2>$null
Write-Host "[NETWORK]   Port 3306 -> $Ports" -ForegroundColor Green

# 4. Mounted volumes
$Mounts = docker inspect --format '{{range .Mounts}}{{.Type}}: {{.Source}} -> {{.Destination}} (Mode: {{.Mode}}){{"\n"}}{{end}}' $ContainerName 2>$null
Write-Host "[STORAGE]   Mounts:" -ForegroundColor Cyan
Write-Host "$Mounts" -ForegroundColor DarkGray

# 5. Database Row Counts if running
if ($StateObj.Status -eq "running") {
    Write-Host "[DATA]      Checking table row counts in student_db..." -ForegroundColor Cyan
    $Query = "SELECT 'students' AS tbl, COUNT(*) AS count FROM student_db.students UNION ALL SELECT 'courses', COUNT(*) FROM student_db.courses UNION ALL SELECT 'enrollments', COUNT(*) FROM student_db.enrollments UNION ALL SELECT 'test_records', COUNT(*) FROM student_db.test_records;"
    docker exec $ContainerName mysql -u root -p"$RootPass" -e "$Query" 2>$null
}

Write-Host "==================================================================" -ForegroundColor Cyan
