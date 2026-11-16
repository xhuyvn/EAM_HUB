$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Push-Location $RepoRoot
try {
    docker compose --profile mvp1 ps
    Write-Host ""
    try {
        $r1 = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
        Write-Host "API /healthz: OK ($($r1.StatusCode)) http://127.0.0.1:8000/healthz"
    } catch {
        Write-Host "API /healthz: UNREACHABLE"
    }
    try {
        $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 3
        Write-Host "HUD /:        OK ($($r2.StatusCode)) http://127.0.0.1:5173/"
    } catch {
        Write-Host "HUD /:        UNREACHABLE"
    }
}
finally {
    Pop-Location
}
