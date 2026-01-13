# UNCONS Development Environment - Background Process Launcher
# Runs all services as background processes with logs to files
# Logs are accessible to Claude Code for debugging
#
# Usage:
#   .\scripts\dev.ps1           # Start all services
#   .\scripts\stop-dev.ps1      # Stop all services
#
# Logs written to:
#   logs/api.log       - Backend API (uvicorn)
#   logs/worker.log    - LiveKit agent worker
#   logs/frontend.log  - Next.js dev server

$ErrorActionPreference = "Stop"

# Paths
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"

# Use a temp-based log directory to avoid cloud sync/indexer locks
# Fall back to project logs/ if temp isn't available
$TempLogDir = Join-Path $env:LOCALAPPDATA "uncons-dev-logs"
$ProjectLogDir = Join-Path $ProjectRoot "logs"

# Try to use temp directory first (avoids OneDrive/indexer issues)
try {
    if (-not (Test-Path $TempLogDir)) {
        New-Item -ItemType Directory -Path $TempLogDir -Force | Out-Null
    }
    # Test if we can write to it
    $testFile = Join-Path $TempLogDir ".test"
    "test" | Set-Content $testFile -Force
    Remove-Item $testFile -Force
    $LogDir = $TempLogDir
} catch {
    # Fall back to project directory
    $LogDir = $ProjectLogDir
    if (-not (Test-Path $LogDir)) {
        New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    }
}

$PidFile = Join-Path $LogDir "pids.json"

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "   UNCONS Development Environment" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Create logs directory
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "[SETUP] Created logs directory" -ForegroundColor Gray
}

# Auto-stop existing services if running (to release file locks)
$existingServices = $false
if (Test-Path $PidFile) {
    $existingServices = $true
}
# Also check for processes on known ports
$port8000 = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
$port3000 = Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue
if ($port8000 -or $port3000) {
    $existingServices = $true
}

if ($existingServices) {
    Write-Host "[CLEANUP] Stopping existing services..." -ForegroundColor Yellow

    # Kill processes from PID file (these are cmd.exe wrappers that hold log file handles)
    if (Test-Path $PidFile) {
        $oldPids = Get-Content $PidFile | ConvertFrom-Json
        foreach ($service in @("api", "worker", "frontend")) {
            $procId = $oldPids.$service
            if ($procId) {
                # Kill the cmd.exe process and its entire process tree
                try {
                    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
                    if ($proc) {
                        # Kill child processes first
                        Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $procId } | ForEach-Object {
                            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                        }
                        # Then kill the parent
                        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                    }
                } catch { }
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    # Kill any python processes running our code (including multiprocessing.spawn workers)
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "python.exe" -and (
            $_.CommandLine -match "worker.py" -or
            $_.CommandLine -match "uvicorn" -or
            $_.CommandLine -match "multiprocessing.spawn"
        )
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

    # Kill any node processes running Next.js dev server
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "node.exe" -and $_.CommandLine -match "next"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

    # Kill processes on ports
    if ($port8000) {
        $port8000 | ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }
    if ($port3000) {
        $port3000 | ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    }

    # Kill any remaining cmd.exe processes that might be holding log file handles
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -eq "cmd.exe" -and $_.CommandLine -match "uncons-dev-logs"
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

    # Wait for processes to fully terminate and release file handles
    Write-Host "[CLEANUP] Waiting for processes to terminate..." -ForegroundColor Gray
    Start-Sleep -Seconds 4
}

# Store PIDs for cleanup
$pids = @{}

# Setup log files - try main files first, fall back to timestamped if locked
Write-Host "[SETUP] Preparing log files..." -ForegroundColor Gray

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$sessionId = Get-Date -Format "yyyyMMdd-HHmmss"

# Clean up old session logs - keep only 3 most recent per service type
foreach ($service in @("api", "worker", "frontend")) {
    $oldLogs = Get-ChildItem $LogDir -Filter "$service-*.log" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 3
    foreach ($log in $oldLogs) {
        Remove-Item $log.FullName -Force -ErrorAction SilentlyContinue
    }
}

# Also clean up legacy .log and .old files
Get-ChildItem $LogDir -Filter "*.old" -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}
foreach ($service in @("api", "worker", "frontend")) {
    $legacyLog = Join-Path $LogDir "$service.log"
    if (Test-Path $legacyLog) {
        Remove-Item $legacyLog -Force -ErrorAction SilentlyContinue
    }
}

# Always use session-specific logs to avoid file locking issues
# (Claude Code and other tools hold file handles after reading)
function Get-SessionLogPath {
    param (
        [string]$BaseName,
        [string]$LogDir,
        [string]$SessionId
    )

    $sessionPath = Join-Path $LogDir "$BaseName-$SessionId.log"
    "[$timestamp] === Starting ===" | Set-Content $sessionPath -Force
    return $sessionPath
}

$apiLog = Get-SessionLogPath -BaseName "api" -LogDir $LogDir -SessionId $sessionId
$workerLog = Get-SessionLogPath -BaseName "worker" -LogDir $LogDir -SessionId $sessionId
$frontendLog = Get-SessionLogPath -BaseName "frontend" -LogDir $LogDir -SessionId $sessionId

# Start Backend API
Write-Host "[1/3] Starting Backend API Server..." -ForegroundColor Green

$apiProcess = Start-Process -FilePath "cmd.exe" -ArgumentList @(
    "/c",
    "cd /d `"$BackendDir`" && poetry run uvicorn src.app:app --reload --host 0.0.0.0 --port 8000 >> `"$apiLog`" 2>&1"
) -WindowStyle Hidden -PassThru

$pids["api"] = $apiProcess.Id
$pids["apiLog"] = $apiLog
Write-Host "       PID: $($apiProcess.Id) | Log: $(Split-Path $apiLog -Leaf)" -ForegroundColor Gray

# Wait for API to be ready
Write-Host "       Waiting for API to initialize..." -ForegroundColor Gray
$maxAttempts = 15
$attempt = 0
$apiReady = $false

while ($attempt -lt $maxAttempts -and -not $apiReady) {
    Start-Sleep -Seconds 1
    $attempt++
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($health.status -eq "healthy") {
            $apiReady = $true
            Write-Host "       API healthy after $attempt seconds" -ForegroundColor Green
        }
    } catch {
        # Still starting
    }
}

if (-not $apiReady) {
    Write-Host "       API not ready yet (may still be starting)" -ForegroundColor Yellow
}

# Start LiveKit Agent Worker
Write-Host "[2/3] Starting LiveKit Agent Worker..." -ForegroundColor Green

$workerProcess = Start-Process -FilePath "cmd.exe" -ArgumentList @(
    "/c",
    "cd /d `"$BackendDir`" && poetry run python src/agents/worker.py dev >> `"$workerLog`" 2>&1"
) -WindowStyle Hidden -PassThru

$pids["worker"] = $workerProcess.Id
$pids["workerLog"] = $workerLog
Write-Host "       PID: $($pids["worker"]) | Log: $(Split-Path $workerLog -Leaf)" -ForegroundColor Gray

# Start Frontend
Write-Host "[3/3] Starting Frontend (Next.js)..." -ForegroundColor Green

$frontendProcess = Start-Process -FilePath "cmd.exe" -ArgumentList @(
    "/c",
    "cd /d `"$FrontendDir`" && npm run dev >> `"$frontendLog`" 2>&1"
) -WindowStyle Hidden -PassThru

$pids["frontend"] = $frontendProcess.Id
$pids["frontendLog"] = $frontendLog
Write-Host "       PID: $($pids["frontend"]) | Log: $(Split-Path $frontendLog -Leaf)" -ForegroundColor Gray

# Save PIDs and log paths for stop script
$pids | ConvertTo-Json | Set-Content $PidFile

# Get relative log names for display
$apiLogName = Split-Path $apiLog -Leaf
$workerLogName = Split-Path $workerLog -Leaf
$frontendLogName = Split-Path $frontendLog -Leaf

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "   All services started!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "`n  Services:" -ForegroundColor White
Write-Host "    API:      http://localhost:8000 (PID: $($pids["api"]))" -ForegroundColor Gray
Write-Host "    Frontend: http://localhost:3000 (PID: $($pids["frontend"]))" -ForegroundColor Gray
Write-Host "    Worker:   LiveKit Agent (PID: $($pids["worker"]))" -ForegroundColor Gray
Write-Host "`n  Logs:" -ForegroundColor White
Write-Host "    $apiLog" -ForegroundColor Gray
Write-Host "    $workerLog" -ForegroundColor Gray
Write-Host "    $frontendLog" -ForegroundColor Gray
Write-Host "`n  Commands:" -ForegroundColor White
Write-Host "    View logs:  Get-Content `"$workerLog`" -Tail 50 -Wait" -ForegroundColor Gray
Write-Host "    Stop all:   .\scripts\stop-dev.ps1" -ForegroundColor Gray
Write-Host ""
