# Acceptance Packet: P2a-WP1 Sources and project scopes

## 1. Milestone and Scope
- **Work Package**: P2a-WP1 (Sources and project scopes per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P2a-WP1, `docs/04_PPM_INTEGRATION.md`, `docs/adr/DG-1.md` §6, DG1-C5, Amendment A1, and `docs/02_RUNTIME_AND_SECURITY.md`).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P2a-WP1` (rebased on `origin/main` at `4082f02`)
- **Base Commit**: `4082f02` ("accept P0-WP3 spike; register ISS-026..028")
- **Parallel Work Packages**: Spike `P0-WP3` Windows path containment (`docs/spikes/**`, `spikes/**`).
- **Scope Boundary**: Real filesystem containment (symlinks, junctions, hardlinks, alternate data streams inspection) belongs to P2a-WP2 post-spike P0-WP3. In P2a-WP1, path handling is strictly pure in-memory canonicalization (`canonicalize_root`) without touching the filesystem. String rules in `canonicalize_root` are defence in depth; physical identity resolution from filesystem handles belongs to P2a-WP2 (`ISS-028`).
- **Contract and Issue Governance**:
  - Contracts (`contracts/**`) and examples (`examples/**`) were preserved without modification.
  - Logged `ISS-025` in `docs/ISSUES.md` (label `contract`): `contracts/event.schema.json` event_type enum lacks location service event types (`ppm.location.proposed`, `ppm.location.approved`, `ppm.source.registered`, `ppm.identity_conflict.detected`, `ppm.source_share.requested`, `ppm.source_share.approved`, `ppm.source_share.rejected`, `ppm.source_share.revoked`).
  - Next free issue ID is `ISS-029`.

---

## 2. Changes Requested (Review 2026-11-08) Resolution

### L1: Path Normalization Hardening (`src/eap/ppm/path.py`)
- **Defects Addressed**:
  - `C:\Repo\App.` could bypass deduplication against `C:\Repo\App`.
  - NFC vs NFD (`C:\Répo` vs `C:\Re\u0301po`) could be registered as two distinct sources.
  - Trailing control characters (`\n`, `\r`, `\t`) were silently stripped on Linux before validation.
- **Implementation**:
  - `unicodedata.normalize("NFC", path)` applied first to all inputs.
  - Casefolded on Windows via `.casefold()` for Unicode-aware case insensitivity.
  - Unstripped input checked for control characters: `ord(ch) < 32 or ord(ch) == 127` immediately raises `PathValidationError` without silently stripping `\n`, `\r`, or `\t`.
  - Windows path segments ending in `.` or `' '` are rejected (never stripped).
  - Windows reserved device names rejected as any segment (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, with or without extension, case-insensitive).
  - Defence in depth: Documented that string rules are defence in depth; physical identity is P2a-WP2 (`ISS-028`).
- **Evidence**: `tests/test_canonicalize_root.py` (74 passed).

### L2: Server-Side Host OS Derivation (`src/eap/config.py`, `src/eap/api/dependencies.py`, `src/eap/api/ppm_routes.py`)
- **Requirement**: `host_os` must never come from the client. Derived server-side from registered host (`EAP_HOST_ID` / host OS of local connector). Any client-supplied `host_os` -> 422.
- **Implementation**:
  - Added `get_server_host_os()` on `Settings` in `src/eap/config.py` defaulting to `"windows" if sys.platform == "win32" else "linux"` (or `EAP_HOST_OS` env).
  - Removed `host_os` from client request schemas (`SourceRegistrationRequest`).
  - Added `host_os` / `host-os` to `is_forbidden_client_field` in `src/eap/api/dependencies.py`. Any presence in headers, query parameters, or JSON body returns HTTP 422 Unprocessable Content.
- **Evidence**: `tests/test_ppm_locations.py::test_l2_host_os_client_supplied_rejected_422`.

### L3: Cross-Tenant Folder Sharing & Ownership (`src/eap/db/location_tables.py`, `src/eap/ppm/service.py`, `src/eap/api/ppm_routes.py`)
- **Owner Decision Quoted**:
  > "Owner decision 2026-11-08 — cross-tenant use of the same folder: ALLOWED ONLY WITH APPROVAL by the owning tenant's owner."
- **Host-Scoped Physical Resource Table (`host_root_ownership`)**:
  - Columns: `host_id`, `canonical_root_key`, `canonical_root`, `owner_tenant_id`, `owner_source_id`, `created_at`.
  - Primary key: `(host_id, canonical_root_key)`.
  - Architectural Note: Intentionally NOT tenant-scoped in its primary key because it models a shared physical filesystem resource on a single host machine across tenants. It contains NO foreign keys to tenant data, preserving DG-1 Amendment A1 tenant isolation invariants.
- **Workflow & Enforcements**:
  - The first tenant to register a canonical root on a host owns it (`state="active"`, HTTP 201).
  - Another tenant registering the same root gets the source in state `pending_share` (HTTP 202) plus a share request (`source_shares`). Response never reveals the owning tenant's ID or name.
  - Until approved, requesting tenant's locations on that source cannot be activated (HTTP 409).
  - Only a principal with role `owner` in the OWNING tenant may approve or reject the share (`POST /v1/ppm/shares/{id}/approve` | `/reject`).
  - Requesting tenant cannot approve its own request (HTTP 403). Non-owner principal in owning tenant cannot approve (HTTP 403). Cross-tenant / stranger returns HTTP 404.
  - Owning tenant's owner can revoke the share (`POST /v1/ppm/shares/{id}/revoke`), which immediately moves the requesting tenant's active locations on that source to `state="suspended"`.
  - Every request, approve, reject, and revoke writes an outbox event and an audit log line in the owning tenant's stream.
- **Evidence**: `tests/test_ppm_locations.py::test_l3_cross_tenant_source_sharing_workflow`.

### L4: Request Log Authentication Correlation (`src/eap/observability/middleware.py`, `src/eap/api/dependencies.py`)
- **Issue**: `tenant_id` and `principal_id` were null in request completion logs due to `BaseHTTPMiddleware` coroutine context boundary resetting `contextvars.ContextVar`.
- **Implementation**:
  - Set `request.state.tenant_id = matched_principal.tenant_id` and `request.state.principal_id = matched_principal.principal_id` in `get_principal`.
  - `RequestLoggingMiddleware.dispatch` extracts authenticated identities directly from `request.state` upon request completion and passes them to `logger.info(..., extra={"tenant_id": ..., "principal_id": ...})`.
- **Evidence**: `tests/test_logging_sc04.py::test_l4_request_logs_carry_tenant_and_principal_after_auth`.

---

## 3. Conformance and Acceptance Matrix

| Item / Requirement | Scope & Mandate | Status | Real Observed Output & Evidence |
|---|---|---|---|
| **L1 Path Normalization** | Unicode NFC, casefold on Windows, reject `.`/` ` segment suffix (never stripped), reject reserved device names, reject `\n\r\t` without stripping. | PASS | `tests/test_canonicalize_root.py` (74 passed). Verified NFC/NFD equivalence (`c:/répo/app`), device names (`CON`, `aux.json`, `PRN`), and trailing dots. |
| **L2 Server Host OS** | `host_os` derived server-side from configuration/platform; client-supplied `host_os` rejected. | PASS | `tests/test_ppm_locations.py::test_l2_host_os_client_supplied_rejected_422`: body, header, and query param client injection return HTTP 422. |
| **L3 Cross-Tenant Sharing** | Owner decision 2026-11-08: cross-tenant folder use requires owning tenant's owner approval; no identity leakage; revocation suspends locations. | PASS | `tests/test_ppm_locations.py::test_l3_cross_tenant_source_sharing_workflow`: 202 pending_share; 409 on activation before approval; 403 on self-approval; 403 on non-owner; 404 cross-tenant; 200 on owner approval; activation succeeds; revocation suspends location; outbox written. |
| **L4 Request Logging Correlation** | Request logs carry `tenant_id` and `principal_id` after authentication (not null). | PASS | `tests/test_logging_sc04.py::test_l4_request_logs_carry_tenant_and_principal_after_auth`: LogRecord and serialized JSON contain non-null `tenant_id` and `principal_id`. |
| **C32 Monorepo Registration** | `proj-app-a`, `proj-app-b`, `proj-shared-gateway` sharing `src-example-monorepo` succeed with distinct metadata_roots. | PASS | `tests/test_ppm_locations.py::test_c32_monorepo_three_scopes_on_one_source_succeed`: 3 scopes created with state `proposed`, distinct metadata roots; 3 rows in PostgreSQL `project_scopes`. |
| **C32 Duplicate Detection** | Duplicate (source, metadata_root) -> 409; metadata_root claimed by 2 projects -> 409. | PASS | `tests/test_ppm_locations.py::test_duplicate_source_metadata_root_rejected_409` (409 DUPLICATE_LOCATION) and `test_metadata_root_claimed_by_two_projects_rejected_409` (409 METADATA_ROOT_CONFLICT). |
| **Physical Directory Decoupling** | Same folder under a different source_id -> 409; same repo_identity under a different source_id -> 409. | PASS | `tests/test_ppm_locations.py::test_same_folder_under_different_source_rejected_409` and `test_same_repo_identity_under_different_source_rejected_409` return HTTP 409 SOURCE_ALREADY_REGISTERED. |
| **W7-3 Scope Cardinality** | A project may have several scopes; each scope belongs to exactly one project. | PASS | `tests/test_ppm_locations.py::test_w7_3_single_project_multiple_scopes_allowed`: single project `proj-multi-scope` successfully registers multiple scopes across components. |
| **C42 Copied Repository Quarantine** | Second location claiming existing project_id with differing repo_identity -> quarantined (state: suspended per contract enum) + identity_conflict record; never merged. | PASS | `tests/test_ppm_locations.py::test_c42_copied_repo_with_same_project_id_quarantined`: second location created with `state='suspended'`; row written to `identity_conflicts`; outbox event `ppm.identity_conflict.detected` emitted; approval rejected with 409. |
| **DG1-C5 Scope Part** | Shared component single ownership; distinct metadata roots for independent scanning. | PASS | Decoupled physical sources from logical scopes; verified via C32 monorepo test. |
| **Amendment A1 Composite FKs** | All foreign keys are tenant-scoped composite keys; catalog FK test must pass. | PASS | `tests/db/test_tenant_isolation_fks.py`: 7 FK catalog invariant passes against live PostgreSQL catalog and metadata; all constraints verified. |
| **SC-04 Redaction & Secrets** | No tokens, canary secrets, or paths with secrets in logs or responses. | PASS | `tests/test_ppm_locations.py::test_sc04_canary_redaction_in_ppm_location_flows` & `test_logging_sc04.py`: canary never appears in logs, exception traces, or response bodies. |
| **SC-06 Migration Cycles** | Upgrade head -> downgrade base -> upgrade head on empty and seeded PostgreSQL databases. | PASS | `python tools/run_sc06.py`: Cycle 1 (empty DB up/down/up) PASS; Cycle 2 (seeded DB down/up) PASS with `0003_locations (head)`. |

---

## 4. Verification Commands and Real Output

### 4.1 Pytest Full Suite against PostgreSQL 18.6
Command: `$env:TEST_DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run pytest -q`
Real Output:
```
522 passed, 3 warnings in 109.38s (0:01:49)
```

### 4.2 Migration SC-06 Verification
Command: `$env:DATABASE_URL="postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"; uv run python tools/run_sc06.py`
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

### 4.3 Database Parity and Invariant Tests
Command: `uv run pytest -q tests/db/`
Real Output:
```
38 passed, 1 warning in 1.13s
```

### 4.4 Contract Package Validation
Command: `uv run python tools/validate_package.py`
Real Output:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

### 4.5 PPM Negative Cases
Command: `uv run python ppm-v2/tools/run_negative_cases.py`
Real Output:
```
template templates/project/.ppm/project.yaml: PASS
template templates/project/.ppm/status.yaml: PASS
template templates/project/.ppm/updates/upd-example-001.yaml: PASS
template templates/project/.ppm/acceptance.yaml: PASS
template templates/registry.example.yaml: PASS
case absolute artifact path: correctly rejected
case parent traversal: correctly rejected
case unknown contract: correctly rejected
case repo grants cloud: correctly rejected
case invalid progress: correctly rejected
case timezone absent: correctly rejected
case recorded_at not a date: correctly rejected
case status updated_at without timezone: correctly rejected
case duplicate YAML keys: correctly rejected
case acceptance file claims approved state: correctly rejected
case registry enables command execution: correctly rejected
case 2.0 document using 2.1 field: correctly rejected
case recorded_at far in the future: correctly rejected
case verification ref with unknown kind: correctly rejected
case duplicate criterion_id: correctly rejected
case supersession cycle: correctly rejected
case supersedes unknown update: correctly rejected
case update references unknown criterion: correctly rejected
case two project_ids in one metadata root: correctly rejected
case valid correction chain accepted: correctly accepted
PASS 5 templates, 20 cases
```

### 4.6 Translation Freshness (SC-08)
Command: `uv run python tools/check_translation.py`
Real Output:
```
SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.
```

### 4.7 Web HUD Build
Command: `npm --prefix web/hud run build`
Real Output:
```
> eap-hud@0.1.0 build
> tsc -b && vite build

vite v6.4.5 building for production...
transforming...
✓ 29 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.39 kB │ gzip:  0.27 kB
dist/assets/index-a8RAso0v.css    0.71 kB │ gzip:  0.39 kB
dist/assets/index-DLYonLHq.js   225.08 kB │ gzip: 70.02 kB
✓ built in 618ms
```

### 4.8 Lint and Type Checkers
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

---

## 5. Security & Isolation Summary
1. **Defence in Depth String Normalization (L1, ISS-028)**:
   - NFC normalization + Windows casefold prevents duplicate registrations through representation differences.
   - Rejection of trailing dots, spaces, device names, and control characters prevents canonical path spoofing.
2. **Server-Side Host Resolution (L2)**:
   - Client is strictly forbidden from declaring `host_os` or tenant/principal identity.
3. **Owner-Approved Cross-Tenant Folder Sharing (L3)**:
   - First tenant to register owns the physical root in host-scoped `host_root_ownership`.
   - Cross-tenant registration creates a pending share request (`source_shares`) and returns HTTP 202 without leaking owner identity.
   - Activations on pending shared sources fail closed (HTTP 409).
   - Only owning tenant's owner may approve/reject/revoke (403 for non-owner or self-approval). Revocation suspends active locations.
4. **Log Identity Correlation (L4)**:
   - Request completion logs reliably capture `tenant_id` and `principal_id` without credential leakage (SC-04).
