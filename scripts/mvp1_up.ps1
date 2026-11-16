param(
    [switch]$WithFixture,
    [switch]$NoBuild,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

# Start EAP MVP1 local deploy package (loopback only).
# Never prints tokens, passwords, or hashes.

$UseFixture = $WithFixture.IsPresent -or ($ExtraArgs -contains "--with-fixture")
$SkipBuild = $NoBuild.IsPresent -or ($ExtraArgs -contains "--no-build")

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Push-Location $RepoRoot
try {
    # 1. Preflight: Docker & Docker Compose
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error "ERROR: docker is required."
        exit 1
    }
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: Docker daemon is not running."
        exit 1
    }
    docker compose version 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "ERROR: docker compose plugin is required."
        exit 1
    }

    # 2. Preflight: .env present or created from .env.example with generated secrets
    function New-RandomHex {
        param([int]$Bytes)
        $buf = New-Object byte[] $Bytes
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($buf)
        return ([System.BitConverter]::ToString($buf) -replace '-', '').ToLowerInvariant()
    }

    function Set-EnvIfEmpty {
        param([string]$Key, [string]$Value, [string]$Path)
        $lines = Get-Content -Path $Path -Encoding UTF8
        $found = $false
        $updated = New-Object System.Collections.Generic.List[string]
        foreach ($line in $lines) {
            if ($line -match "^$([regex]::Escape($Key))=(.*)$") {
                $found = $true
                $rawVal = ($Matches[1] -replace '\s*#.*$', '').Trim()
                if ([string]::IsNullOrEmpty($rawVal)) {
                    $updated.Add("${Key}=${Value}")
                } else {
                    $updated.Add($line)
                }
            } else {
                $updated.Add($line)
            }
        }
        if (-not $found) {
            $updated.Add("${Key}=${Value}")
        }
        [System.IO.File]::WriteAllText($Path, ($updated -join "`n") + "`n", [System.Text.UTF8Encoding]::new($false))
    }

    $EnvPath = Join-Path $RepoRoot ".env"
    $EnvExamplePath = Join-Path $RepoRoot ".env.example"
    if (-not (Test-Path $EnvPath)) {
        if (-not (Test-Path $EnvExamplePath)) {
            Write-Error "ERROR: .env.example not found."
            exit 1
        }
        Copy-Item -Path $EnvExamplePath -Destination $EnvPath
        Write-Host "Created .env from .env.example."
    }

    Set-EnvIfEmpty -Key "POSTGRES_PASSWORD" -Value (New-RandomHex 24) -Path $EnvPath
    Set-EnvIfEmpty -Key "EAP_SECRET_CANARY" -Value ("EAP-CANARY-" + (New-RandomHex 16)) -Path $EnvPath
    Set-EnvIfEmpty -Key "EAP_HUD_CURSOR_SECRET" -Value (New-RandomHex 32) -Path $EnvPath

    # 3. Preflight: check loopback ports if stack is not already running
    $PgPort = if ($env:EAP_POSTGRES_PORT) { [int]$env:EAP_POSTGRES_PORT } else { 5432 }
    $ApiPort = if ($env:EAP_API_PORT) { [int]$env:EAP_API_PORT } else { 8000 }
    $HudPort = if ($env:EAP_HUD_PORT) { [int]$env:EAP_HUD_PORT } else { 5173 }
    $PortMap = @(
        @{ Service = "postgres"; Port = $PgPort },
        @{ Service = "api"; Port = $ApiPort },
        @{ Service = "hud"; Port = $HudPort }
    )
    foreach ($entry in $PortMap) {
        $cid = (docker compose --profile mvp1 ps -q $entry.Service 2>$null)
        if ([string]::IsNullOrWhiteSpace($cid)) {
            $listening = Get-NetTCPConnection -LocalPort $entry.Port -State Listen -ErrorAction SilentlyContinue
            if ($listening) {
                Write-Error "ERROR: Port 127.0.0.1:$($entry.Port) is already in use by another process."
                exit 1
            }
        }
    }

    # 4. Start services
    if ($SkipBuild) {
        docker compose --profile mvp1 up -d
    } else {
        docker compose --profile mvp1 up -d --build
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed."
    }

    # 5. Wait for health
    Write-Host "Waiting for MVP1 services to become healthy..."
    $deadline = (Get-Date).AddSeconds(120)
    $apiOk = $false
    $hudOk = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $r1 = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
            if ($r1.StatusCode -eq 200) { $apiOk = $true }
        } catch {}
        try {
            $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 3
            if ($r2.StatusCode -eq 200) { $hudOk = $true }
        } catch {}
        $workerCid = (docker compose --profile mvp1 ps -q worker 2>$null)
        $workerHealth = if ($workerCid) { (docker inspect -f '{{.State.Health.Status}}' $workerCid 2>$null) } else { "" }
        if ($apiOk -and $hudOk -and ($workerHealth -eq "healthy")) { break }
        Start-Sleep -Seconds 2
    }

    if (-not $apiOk) {
        docker compose --profile mvp1 logs --tail=50 api migrate
        throw "API failed to become healthy on http://127.0.0.1:8000/healthz"
    }
    if (-not $hudOk) {
        docker compose --profile mvp1 logs --tail=50 hud
        throw "HUD failed to become healthy on http://127.0.0.1:5173/"
    }
    if ($workerHealth -ne "healthy") {
        docker compose --profile mvp1 logs --tail=50 worker
        throw "Worker failed to become healthy."
    }

    # 6. Register fixture mock agent + binding (idempotent P3a-WP5 CLI)
    docker compose --profile mvp1 exec -T api `
        python -m eap.agents.cli register `
        --manifest src/eap/agents/fixtures/mock_agent.json `
        --binding src/eap/agents/fixtures/mock_binding.json | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register fixture mock agent and binding."
    }

    # 7. Optional D0/D1 fixture project seed
    if ($UseFixture) {
        docker compose --profile mvp1 exec -T api python -m eap.mvp1_fixture | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to seed D0/D1 fixture project."
        }
        Write-Host "Seeded D0/D1 fixture project (proj-d0-demo) and sample tasks/runs."
    }

    Write-Host ""
    Write-Host "EAP MVP1 is running (loopback only):"
    Write-Host "  HUD URL:    http://127.0.0.1:5173"
    Write-Host "  API Health: http://127.0.0.1:8000/healthz"
    Write-Host ""
    Write-Host "Owner token setup (never stored in plaintext in .env):"
    Write-Host "  1. Generate a local token:  python -c `"import secrets; print(secrets.token_hex(32))`""
    Write-Host "  2. Hash it (SHA-256 hex):   python -c `"import hashlib; print(hashlib.sha256(b'<token>').hexdigest())`""
    Write-Host "  3. Set EAP_OWNER_TOKEN_HASH=<sha256_hex> in .env and run: docker compose --profile mvp1 up -d api"
    Write-Host "  4. Paste <token> into the HUD UI token field."
}
finally {
    Pop-Location
}
