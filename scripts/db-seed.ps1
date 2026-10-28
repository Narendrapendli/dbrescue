<#
.SYNOPSIS
    Applies synthetic seed data into the running student_db MySQL container.
.DESCRIPTION
    Executes db/seed/seed_students.sql idempotently and queries row counts for verification.
    SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
#>

[CmdletBinding()]
param ()

$ErrorActionPreference = "Stop"

$ContainerName = if ($env:DOCKER_PRIMARY_CONTAINER) { $env:DOCKER_PRIMARY_CONTAINER } else { "dbrescue-mysql-primary" }
$RootPass = if ($env:MYSQL_ROOT_PASSWORD) { $env:MYSQL_ROOT_PASSWORD } else { "RootRescue2026!Emergency" }
$SeedFile = "db\seed\seed_students.sql"

Write-Host "==> [dbrescue] Seeding synthetic student data into '$ContainerName'..." -ForegroundColor Cyan

if (-not (Test-Path $SeedFile)) {
    Write-Host "[ERROR] Seed file '$SeedFile' not found." -ForegroundColor Red
    exit 1
}

# 1. Verify container is running
$Status = docker inspect --format '{{.State.Status}}' $ContainerName 2>$null
if ($Status -ne "running") {
    Write-Host "[ERROR] Container '$ContainerName' is not running (status: $Status). Run .\scripts\db-up.ps1 first." -ForegroundColor Red
    exit 1
}

# 2. Ingest seed SQL file
Write-Host "--> Applying '$SeedFile' via mysql CLI..." -ForegroundColor Cyan
Get-Content $SeedFile | docker exec -i $ContainerName mysql -u root -p"$RootPass"

# 3. Verify counts
Write-Host "--> Verifying seeded counts..." -ForegroundColor Cyan
$Query = "SELECT 'students' AS tbl, COUNT(*) AS count FROM student_db.students UNION ALL SELECT 'courses', COUNT(*) FROM student_db.courses UNION ALL SELECT 'enrollments', COUNT(*) FROM student_db.enrollments UNION ALL SELECT 'test_records', COUNT(*) FROM student_db.test_records;"
docker exec $ContainerName mysql -u root -p"$RootPass" -e "$Query"

Write-Host "==> [dbrescue] Database seeding completed successfully!" -ForegroundColor Green
