# CI script for EAM_HUB local and automated CI runs
$ErrorActionPreference = "Stop"

Write-Host "==> 0. Syncing Python workspace (--locked)..."
uv sync --locked

Write-Host "==> 1. Validating package contracts and schemas (SC-01)..."
uv run python tools/validate_package.py

Write-Host "==> 2. Running PPM v2 negative cases..."
uv run python ppm-v2/tools/run_negative_cases.py

Write-Host "==> 2b. Checking PPM v2 fixture corpus..."
uv run python ppm-v2/tools/check_fixtures.py

Write-Host "==> 3. Checking translation provenance and staleness (SC-08)..."
uv run python tools/check_translation.py

Write-Host "==> 4. Verifying SHA256 file checksums..."
$sha256 = Get-Command sha256sum -ErrorAction SilentlyContinue
if (-not $sha256) {
    if (Test-Path "C:\Program Files\Git\usr\bin\sha256sum.exe") {
        $sha256 = "C:\Program Files\Git\usr\bin\sha256sum.exe"
    }
}
if ($sha256) {
    & $sha256 -c SHA256SUMS.txt
} else {
    uv run python -c "
import hashlib, pathlib, sys
failed = 0
for line in pathlib.Path('SHA256SUMS.txt').read_text().splitlines():
    if not line.strip(): continue
    expected, path = line.strip().split('  ', 1)
    p = pathlib.Path(path)
    if not p.exists():
        print(f'MISSING: {path}')
        failed += 1
        continue
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != expected:
        print(f'MISMATCH: {path}')
        failed += 1
if failed:
    sys.exit(1)
print('SHA256SUMS: OK')
"
}

Write-Host "==> 4b. Running database migration up/down/up cycles (SC-06)..."
if (-not $env:TEST_DATABASE_URL) {
    $pgPass = if ($env:POSTGRES_PASSWORD) { $env:POSTGRES_PASSWORD } else { "postgres_dev_secret" }
    $env:TEST_DATABASE_URL = "postgresql+psycopg://eap:${pgPass}@127.0.0.1:5432/eap_test"
}
uv run python tools/run_sc06.py

Write-Host "==> 5. Running Python test suite (SC-02)..."
uv run pytest -q

Write-Host "==> 6. Linting and type checking platform code..."
uv run ruff check src tests ppm-v2/reducer migrations
uv run mypy src tests ppm-v2/reducer

Write-Host "==> 7. Building web HUD..."
npm --prefix web/hud run build

Write-Host "==> All CI checks PASSED!"
