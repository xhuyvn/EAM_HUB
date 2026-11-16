param(
    [switch]$Reset,
    [string]$ExportDir = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

# D1-fixture sign-off demo script (fixtures-only, idempotent, resettable).
# Never prints bearer tokens or token hashes.

$UseReset = $Reset.IsPresent -or ($ExtraArgs -contains "--reset")

$TenantId = if ($env:EAP_OWNER_TENANT_ID) { $env:EAP_OWNER_TENANT_ID } else { "tenant-owner" }
$ProjectId = if ($env:D1_PROJECT_ID) { $env:D1_PROJECT_ID } else { "proj-d1-demo" }
$SourceId = if ($env:D1_SOURCE_ID) { $env:D1_SOURCE_ID } else { "src-d1-demo" }
$ApiBaseUrl = if ($env:D1_API_BASE_URL) { $env:D1_API_BASE_URL } else { "http://127.0.0.1:8000" }
$HudUrl = if ($env:D1_HUD_URL) { $env:D1_HUD_URL } else { "http://127.0.0.1:5173/" }

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Error "ERROR: git is required (>= 2.41.0)."
    exit 1
}

$gitVerRaw = (git --version)
if ($gitVerRaw -match '(\d+)\.(\d+)\.(\d+)') {
    $gitMajor = [int]$Matches[1]
    $gitMinor = [int]$Matches[2]
    if ($gitMajor -lt 2 -or ($gitMajor -eq 2 -and $gitMinor -lt 41)) {
        Write-Error "ERROR: git >= 2.41.0 required, found $gitVerRaw."
        exit 1
    }
} else {
    Write-Error "ERROR: could not parse git version from '$gitVerRaw'."
    exit 1
}

if (-not $env:EAP_COLLECTOR_FIXTURES_ROOT) {
    Write-Error "ERROR: EAP_COLLECTOR_FIXTURES_ROOT must be set to a fixtures directory."
    exit 1
}

$allowReal = if ($env:EAP_COLLECTOR_ALLOW_REAL_ROOTS) { $env:EAP_COLLECTOR_ALLOW_REAL_ROOTS } else { "false" }
if ($allowReal -ne "false") {
    Write-Error "ERROR: D1 demo requires EAP_COLLECTOR_ALLOW_REAL_ROOTS=false (fixtures-only)."
    exit 1
}

if (-not $env:DATABASE_URL) {
    Write-Error "ERROR: DATABASE_URL must be set and point to a reachable PostgreSQL database."
    exit 1
}

uv run python -c "import os, sqlalchemy as sa; e = sa.create_engine(os.environ['DATABASE_URL']); c = e.connect(); c.execute(sa.text('SELECT 1')); c.close()" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "ERROR: PostgreSQL is not reachable at DATABASE_URL."
    exit 1
}

New-Item -ItemType Directory -Force -Path $env:EAP_COLLECTOR_FIXTURES_ROOT | Out-Null
$FixturesRootAbs = (Resolve-Path $env:EAP_COLLECTOR_FIXTURES_ROOT).Path

$pyArgs = @(
    "run", "python", "-m", "eap.ppm.d1_demo",
    "--tenant", $TenantId,
    "--project", $ProjectId,
    "--source", $SourceId,
    "--fixtures-root", $FixturesRootAbs,
    "--db-url", $env:DATABASE_URL,
    "--api-base-url", $ApiBaseUrl,
    "--hud-url", $HudUrl
)
if ($UseReset) {
    $pyArgs += "--reset"
}
if ($ExportDir) {
    $pyArgs += "--export-dir"
    $pyArgs += $ExportDir
}

& uv @pyArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
