# D0 Thin Slice Demo

The D0 thin slice demonstrates end-to-end deterministic PPM assessment from a fixture Git repository through the collector, ingest, pure reducer, Read API, and a disposable static inspection page (`web/hud/d0.html`).

All evidence in D0 uses `simulated=True` verifier records loaded via the fixtures-only loader (`EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`). Real verifier sandbox execution arrives in P2a-WP9.

## Prerequisites

- `git >= 2.41.0`
- PostgreSQL 18.6 reachable at `DATABASE_URL` (e.g. `docker compose up -d`)
- `EAP_COLLECTOR_FIXTURES_ROOT` set to a temporary/fixtures directory (never a real project folder)
- `EAP_COLLECTOR_ALLOW_REAL_ROOTS=false`

## Running the Demo

### Linux / macOS

```bash
export DATABASE_URL="postgresql+psycopg://eap:<password>@127.0.0.1:5432/eap_dev"
export EAP_COLLECTOR_FIXTURES_ROOT="/tmp/eap_fixtures"
export EAP_COLLECTOR_ALLOW_REAL_ROOTS="false"

bash scripts/demo_d0.sh
```

### Windows (PowerShell)

```powershell
$env:DATABASE_URL = "postgresql+psycopg://eap:<password>@127.0.0.1:5432/eap_dev"
$env:EAP_COLLECTOR_FIXTURES_ROOT = "$env:TEMP\eap_fixtures"
$env:EAP_COLLECTOR_ALLOW_REAL_ROOTS = "false"

pwsh -File scripts/demo_d0.ps1
```

Both scripts are idempotent and safe to rerun.

### Expected Output

```text
D0 thin slice demo complete.
  project_id:   proj-d0-demo
  rev-r1:       <commit-sha-r1>
  rev-r2:       <commit-sha-r2>
  A1_ID:        asmt-...
  A2_ID:        asmt-...
  API base URL: http://127.0.0.1:8000
  Page URL:     http://127.0.0.1:5173/d0.html
```

## Viewing the Page

1. Start the API server (loopback-only, no query-string access logging):
   ```bash
   uv run uvicorn eap.api.app:app --host 127.0.0.1 --port 8000 --no-access-log
   ```
2. Start the HUD dev server (proxies `/v1/*` to `127.0.0.1:8000` with no CORS):
   ```bash
   npm --prefix web/hud run dev
   ```
3. Open `http://127.0.0.1:5173/d0.html`, enter `proj-d0-demo` and your owner bearer token (kept in memory only), and click **Load**.

## Resetting the Demo

To reset the fixture repository and database state:

```bash
rm -rf "$EAP_COLLECTOR_FIXTURES_ROOT/d0_thin_slice_demo"
# Reset or recreate your dev/test database, then re-run:
uv run alembic upgrade head
```

## Owner Inspection Checklist (Three D1 Questions)

Verify you can answer all three questions from `d0.html` alone:

- [ ] **(1) Advancement**: In the diff view (`A1 -> A2`) and `AC-002` Verified column, confirm `AC-002` transitioned `unverified -> verified` with `verifier_observed` evidence at `rev-r2` (labeled `simulated verifier`).
- [ ] **(2) Revision currency**: In `AC-001` Verified column, confirm the `rev-r1` PASS (`ev-r1-verifier-run`) appears under **Excluded Evidence** with reason `revision_mismatch`, while the `rev-r2` FAIL (`ev-r2-verifier-fail`) from the `simulated verifier` is active under **Refuting Evidence**.
- [ ] **(3) Why not accepted**: Confirm overall `acceptance_readiness` is `not_ready` because:
  - `AC-001` is `required` and `contradicted` despite the agent's `claimed_done` claim in `.ppm/updates/upd-0002.yaml`;
  - `AC-003` is `unverified` (`reported_artifact_only`) because it only has claims and the self-written in-repo `reports/ac003-junit.xml` (`ev-r2-ac003-reported` excluded with `trust_class_insufficient`).
- [ ] **Inert text & no approval action**: Confirm `.ppm/notes/note-001.md` renders `<script>...</script>` as inert text and no approve/accept button exists.
