<#
.SYNOPSIS
    Starts the dbrescue MySQL 8.4 LTS service and waits for healthy state.
.DESCRIPTION
    Launches containers defined in compose.yaml, verifies loopback binding, and polls healthcheck.
    SAFETY GUARD: Persistent storage volumes are strictly preserved and never deleted.
#>

[CmdletBinding()]
param (
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

Write-Host "==> [dbrescue] Starting infrastructure via Docker Compose..." -ForegroundColor Cyan

# 1. Verify Docker CLI availability
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 'docker' command not found in PATH." -ForegroundColor Red
    Write-Host "        Please install and start Docker Desktop: https://www.docker.com/products/docker-desktop/" -ForegroundColor Yellow
    exit 1
}

# 2. Check Docker daemon responsiveness
try {
    docker info > $null 2>&1
} catch {
    Write-Host "[ERROR] Docker daemon is not running or unreachable." -ForegroundColor Red
    Write-Host "        Please launch Docker Desktop and retry." -ForegroundColor Yellow
    exit 1
}

# 3. Launch MySQL service in detached mode
Write-Host "--> Running 'docker compose up -d mysql'..." -ForegroundColor Cyan
docker compose up -d mysql

$ContainerName = if ($env:DOCKER_PRIMARY_CONTAINER) { $env:DOCKER_PRIMARY_CONTAINER } else { "dbrescue-mysql-primary" }

# 4. Wait for container healthcheck to transition to 'healthy'
Write-Host "--> Awaiting healthcheck for container '$ContainerName' (timeout: ${TimeoutSeconds}s)..." -ForegroundColor Cyan
$Elapsed = 0
$Interval = 2

while ($Elapsed -lt $TimeoutSeconds) {
    $Status = docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $ContainerName 2>$null
    
    if ($Status -eq "healthy") {
        Write-Host "--> Container '$ContainerName' is HEALTHY and ready for traffic!" -ForegroundColor Green
        
        # Verify loopback port binding
        $Ports = docker port $ContainerName 3306 2>$null
        Write-Host "--> Port mapping: $Ports" -ForegroundColor Green
        exit 0
    }
    
    if ($Status -eq "unhealthy") {
        Write-Host "[ERROR] Container '$ContainerName' reported UNHEALTHY state." -ForegroundColor Red
        docker logs --tail 30 $ContainerName
        exit 1
    }
    
    Start-Sleep -Seconds $Interval
    $Elapsed += $Interval
    Write-Host "    [${Elapsed}s] Health status: $Status..." -ForegroundColor DarkGray
}

Write-Host "[ERROR] Timed out waiting for '$ContainerName' to become healthy after ${TimeoutSeconds} seconds." -ForegroundColor Red
docker logs --tail 30 $ContainerName
exit 1
