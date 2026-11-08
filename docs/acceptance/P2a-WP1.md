# Acceptance Packet: P2a-WP1 Sources and project scopes

## 1. Milestone and Scope
- **Work Package**: P2a-WP1 (Sources and project scopes per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P2a-WP1, `docs/04_PPM_INTEGRATION.md`, `docs/adr/DG-1.md` §6, DG1-C5, Amendment A1, and `docs/02_RUNTIME_AND_SECURITY.md`).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P2a-WP1` (branched from `main` at `8056a5b`)
- **Base Commit**: `8056a5b` ("accept P0-WP0b: SC-08 fix")
- **Parallel Work Packages**: Spike `P0-WP3` Windows path containment (`docs/spikes/**`, `spikes/**`).
- **Scope Boundary**: Real filesystem containment (symlinks, junctions, hardlinks, alternate data streams inspection) belongs to P2a-WP2 post-spike P0-WP3. In P2a-WP1, path handling is strictly pure in-memory canonicalization (`canonicalize_root`) without touching the filesystem.
- **Contract and Issue Governance**:
  - Contracts (`contracts/**`) and examples (`examples/**`) were preserved without modification.
  - Logged `ISS-025` in `docs/ISSUES.md` (label `contract`): `contracts/event.schema.json` event_type enum lacks location service event types (`ppm.location.proposed`, `ppm.location.approved`, `ppm.source.registered`, `ppm.identity_conflict.detected`).

## 2. Deliverables
1. **Pure Path Canonicalization and Alias Validation** (`src/eap/ppm/path.py`):
   - `canonicalize_root(path: str, host_os: str, allow_unc: bool = False) -> str`:
     - Rejects relative paths (POSIX without leading `/`; Windows without drive letter and separator).
     - Rejects `..` segments anywhere in the path.
     - Rejects NUL and control characters (ASCII < 32, ASCII 127).
     - Rejects Windows device namespaces (`\\?\`, `\\.\`, `//?/`, `//./`).
     - Rejects UNC paths (`\\server\share`, `//server/share`) unless `allow_unc=True`.
     - Rejects mixed path separators (mixing `/` and `\`).
     - Rejects POSIX paths containing backslashes.
     - Rejects Alternate Data Streams (colons in Windows paths beyond drive letter).
     - Normalizes Windows paths to lowercase with forward slashes (`c:/projects/eam_hub`).
     - Preserves POSIX case and collapses redundant slashes (`/var/log/audit`).
   - `validate_aliases(artifact_aliases, notes_globs)`:
     - Validates aliases data per docs/04; rejects absolute paths, colons, `..`, backslashes, and duplicate aliases within categories.
2. **Persistence and Migration** (`src/eap/db/location_tables.py`, `migrations/versions/0003_locations.py`):
   - Table `location_registrations`: Persisted trusted host location registration records conforming to `location-registration.schema.json` with composite tenant-scoped uniqueness constraints (`UNIQUE (tenant_id, id)`, `UNIQUE (tenant_id, source_id, metadata_root)`).
   - Table `identity_conflicts`: Persisted conflict records for duplicate `project_id` across divergent repo identities (`UNIQUE (tenant_id, id)`).
   - Migration `0003_locations.py` with `down_revision = "0002_task_run"`.
   - Preserves 7-FK catalog invariant (Amendment A1) with zero drift in draft DDL parity or autogenerate comparisons.
   - Outbox event publication on every state transition (`ppm.source.registered`, `ppm.location.proposed`, `ppm.location.approved`, `ppm.identity_conflict.detected`).
3. **Domain Service** (`src/eap/ppm/service.py`):
   - `LocationService`:
     - `register_source`: Enforces 1 directory = 1 source per tenant, and 1 repo_identity = 1 source per tenant (409 on conflict).
     - `register_location`: Enforces unique `(tenant_id, source_id, metadata_root)` (409); detects C42 identity conflicts and quarantines with `state='suspended'` + `identity_conflicts` row; permits multiple scopes per project (W7-3); assigns `registered_by_ref` server-side.
     - `approve_location`: Transitions `proposed -> active`; requires owner/admin role (403 for non-owner); forbids registrant from self-approving (403); enforces tenant isolation (cross-tenant 404); forbids approving quarantined/conflicted locations (409).
     - `get_source`, `list_sources`, `get_location`, `list_locations`, `get_identity_conflicts`.
4. **API Endpoints** (`src/eap/api/ppm_routes.py`, `src/eap/api/app.py`):
   - `POST /v1/ppm/sources`: Registers a source root.
   - `GET /v1/ppm/sources`, `GET /v1/ppm/sources/{id}`: Retrieves sources.
   - `POST /v1/ppm/locations`: Creates location registration in state `proposed` (or `suspended` upon C42).
   - `GET /v1/ppm/locations`, `GET /v1/ppm/locations/{id}`: Retrieves locations.
   - `POST /v1/ppm/locations/{id}/approve` (and alias `/approval`): Approves location registration.
   - Enforces server-side principal resolution and 422 rejection on client-declared tenant/principal injection in headers, query params, or body fields.

---

## 3. Conformance and Acceptance Matrix

| Item / Requirement | Scope & Mandate | Status | Real Observed Output & Evidence |
|---|---|---|---|
| **C32 Monorepo Registration** | `proj-app-a`, `proj-app-b`, `proj-shared-gateway` sharing `src-example-monorepo` succeed with distinct metadata_roots. | PASS | `tests/test_ppm_locations.py::test_c32_monorepo_three_scopes_on_one_source_succeed`: all 3 scopes created with state `proposed`, distinct metadata roots (`apps/a`, `apps/b`, `libs/shared`); 3 rows verified in PostgreSQL `project_scopes`. |
| **C32 Duplicate Detection** | Duplicate (source, metadata_root) -> 409; metadata_root claimed by 2 projects -> 409. | PASS | `tests/test_ppm_locations.py::test_duplicate_source_metadata_root_rejected_409` (409 DUPLICATE_LOCATION) and `test_metadata_root_claimed_by_two_projects_rejected_409` (409 METADATA_ROOT_CONFLICT). |
| **Physical Directory Decoupling** | Same folder under a different source_id -> 409; same repo_identity under a different source_id -> 409. | PASS | `tests/test_ppm_locations.py::test_same_folder_under_different_source_rejected_409` and `test_same_repo_identity_under_different_source_rejected_409` both return HTTP 409 SOURCE_ALREADY_REGISTERED. |
| **W7-3 Scope Cardinality** | A project may have several scopes; each scope belongs to exactly one project. | PASS | `tests/test_ppm_locations.py::test_w7_3_single_project_multiple_scopes_allowed`: single project `proj-multi-scope` successfully registers multiple scopes across components. |
| **C42 Copied Repository Quarantine** | Second location claiming existing project_id with differing repo_identity -> quarantined (state: suspended per contract enum) + identity_conflict record; never merged. | PASS | `tests/test_ppm_locations.py::test_c42_copied_repo_with_same_project_id_quarantined`: second location created with `state='suspended'`; row written to `identity_conflicts`; outbox event `ppm.identity_conflict.detected` emitted; approval rejected with 409. |
| **DG1-C5 Scope Part** | Shared component single ownership; distinct metadata roots for independent scanning. | PASS | Decoupled physical sources from logical scopes; verified via C32 monorepo test. |
| **Amendment A1 Composite FKs** | All foreign keys are tenant-scoped composite keys; catalog FK test must pass. | PASS | `tests/db/test_tenant_isolation_fks.py`: 7 FK catalog invariant passes against live PostgreSQL catalog and metadata; all constraints verified. |
| **Approval Authority** | Owner/admin only; registrant cannot approve own location (403); non-owner principal cannot approve (403); cross-tenant 404. | PASS | `tests/test_ppm_locations.py::test_approval_authority_and_isolation`: self-approval -> 403; viewer/dev -> 403; cross-tenant -> 404; authorized owner -> 200 (state: active, approved_by_ref set). |
| **Pure Canonicalize Root** | Rejects relative, `..`, device namespaces, UNC (unless allowed), NUL/control characters, mixed separators; normalizes case on Windows. | PASS | `tests/test_canonicalize_root.py`: 50 unit tests covering Linux and Windows negatives and positive normalizations pass. |
| **Location Negatives** | Negatives from `examples/` and `validate_package.py` rejected by API with matching reasons. | PASS | `tests/test_ppm_locations.py::test_location_negatives_from_validate_package_rejected_by_api`: follow_symlinks=True (422), `..` alias (422), absolute alias (422), active without approval (422), relative root_path (422), duplicate alias (422). |
| **SC-04 Redaction & Secrets** | No tokens, canary secrets, or paths with secrets in logs or responses. | PASS | `tests/test_ppm_locations.py::test_sc04_canary_redaction_in_ppm_location_flows` verified: canary never appears in logs, exception traces, or response bodies. |
| **SC-06 Migration Cycles** | Upgrade head -> downgrade base -> upgrade head on empty and seeded PostgreSQL databases. | PASS | `python tools/run_sc06.py`: Cycle 1 (empty DB up/down/up) PASS; Cycle 2 (seeded DB down/up) PASS. |

---

## 4. Verification Commands and Real Output

### 4.1 Pytest Suite against PostgreSQL 18.6
Command: `pytest -q`
Real Output:
```
495 passed, 3 warnings in 99.23s (0:01:39)
```

### 4.2 Migration SC-06 Verification
Command: `uv run python tools/run_sc06.py`
Real Output:
```
============================================================
SC-06 Cycle 1: Empty database up/down/up
============================================================
--- [alembic current] after upgrade head (empty DB) ---
0003_locations (head)
--- [alembic current] after downgrade base (empty DB) ---
--- [alembic current] after second upgrade head (empty DB) ---
0003_locations (head)

============================================================
SC-06 Cycle 2: Seeded database down/up
============================================================
Fixture dataset seeded successfully.
--- [alembic current] after downgrade base (seeded DB) ---
--- [alembic current] after upgrade head (post-seeded DB) ---
0003_locations (head)

============================================================
SC-06 Verification: ALL CYCLES PASSED
============================================================
```

### 4.3 Database Parity and Catalog FK Invariant
Command: `uv run pytest -q tests/db/test_autogenerate_diff.py tests/db/test_draft_ddl_parity.py tests/db/test_tenant_isolation_fks.py`
Real Output:
```
14 passed, 1 warning in 0.72s
```

### 4.4 Canonicalize Root and Path Tests
Command: `uv run pytest -q tests/test_canonicalize_root.py`
Real Output:
```
50 passed in 0.15s
```

### 4.5 Location Integration Tests
Command: `uv run pytest -q tests/test_ppm_locations.py`
Real Output:
```
11 passed, 2 warnings in 1.07s
```

### 4.6 Package and Negative Case Checks
Command: `uv run python tools/validate_package.py`
Real Output:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

Command: `uv run python ppm-v2/tools/run_negative_cases.py`
Real Output:
```
PASS 5 templates, 20 cases
```

### 4.7 SC-08 Translation Check
Command: `uv run python tools/check_translation.py`
Real Output:
```
SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.
```

### 4.8 Code Quality Checks
Command: `uv run ruff check src tests ppm-v2/reducer`
Real Output:
```
All checks passed!
```

Command: `uv run mypy src tests ppm-v2/reducer`
Real Output:
```
Success: no issues found in 125 source files
```

### 4.9 HUD Build
Command: `npm --prefix web/hud run build`
Real Output:
```
✓ built in 395ms
```

---

## 5. Security and Operational Invariants
- **SC-04 Credential Redaction**: Verified that bearer tokens, passwords, and secret canary values are never printed, returned, or logged.
- **Tenant Isolation (Amendment A1)**: Validated composite tenant scoping across all live tables and foreign keys. Cross-tenant queries and approvals return HTTP 404 (preventing oracle probing).
- **Separation of Duties**: Registrant cannot approve its own location registration; only owners/admins can approve.
- **Immutability of Historical Evidence**: Conflicting cloned repos are quarantined under `identity_conflicts` with `state='suspended'` and never merged.
- **Transactional Consistency**: Every state modification (source creation, location proposed, location approved, conflict quarantined) atomically emits an event to the `outbox` table.

## 6. Rollback Procedure
- Drop tables introduced in migration `0003_locations.py`:
  ```bash
  alembic downgrade 0002_task_run
  ```
- Revert code commits on branch `wp/P2a-WP1`.
