param(
    [switch]$ConfirmReset,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

$IsConfirmed = $ConfirmReset.IsPresent -or ($ExtraArgs -contains "--confirm-reset") -or ($ExtraArgs -contains "--yes")
if (-not $IsConfirmed) {
    Write-Error "REFUSED: mvp1_reset.ps1 drops all Docker volumes (database data will be lost). Pass -ConfirmReset (or --confirm-reset) to proceed."
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Push-Location $RepoRoot
try {
    docker compose --profile mvp1 down -v --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    Write-Host "MVP1 containers and volumes removed."
}
finally {
    Pop-Location
}
