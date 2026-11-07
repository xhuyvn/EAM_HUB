# Acceptance Packet: P1a-WP5 Bootstrap Identity

## 1. Milestone and Scope
- **Work Package**: P1a-WP5 (Bootstrap identity per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P1a-WP5, `docs/02_RUNTIME_AND_SECURITY.md`, and ISS-020).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP5` (branched from `main` at `8151e94`)
- **Code Commit under Review**: `6f8438f`
- **Base Commit**: `8151e94` ("accept P1a-WP2 and PPM-M0-WP4; owner fixture amendment ISS-021 (s08, s10 authorization lists)")
- **Parallel Work Package Isolation**:
  - `src/eap/run/**`, `src/eap/action/**`, `src/eap/adapters/**`, `src/eap/api/routes.py`, `migrations/**` (P1a-WP4): Zero modifications.
  - `ppm-v2/**` (PPM-M0-WP5): Zero modifications.
  - No database migrations added.
- **Scope Deliverables**:
  1. `src/eap/config.py`:
     - Added `ConfiguredPrincipal` model with `tenant_id`, `principal_id`, `token_hash: SecretStr`, and `roles: list[str]`.
     - Added settings fields: `eap_owner_tenant_id`, `eap_owner_principal_id`, `eap_owner_token_hash: SecretStr | None`, `eap_second_tenant_id`, `eap_second_principal_id`, `eap_second_token_hash: SecretStr | None`, `eap_principals_json: SecretStr | None`, and `local_principals: list[ConfiguredPrincipal] | None = None`.
     - Removed `eap_bootstrap_tenant_id` and `eap_bootstrap_principal_id` (eliminated implicit default principal).
     - Added `get_configured_principals() -> list[ConfiguredPrincipal]`.
     - Added loopback bind protection: `InsecureBindError(ValueError)`, `parse_bind_host(bind_str: str) -> str`, `is_loopback_host(host: str) -> bool`, and `validate_loopback_bind(bind_str: str) -> None`.
  2. `src/eap/api/app.py`:
     - `create_app` invokes `validate_loopback_bind(settings.eap_bind)` on startup. Refuses startup if `EAP_BIND` host is not a loopback address (`0.0.0.0`, `192.168.x.x`, `::`, etc.) with `InsecureBindError` and clear message; permits `127.0.0.1`, `::1`, and `localhost`.
  3. `src/eap/api/dependencies.py`:
     - Replaced settings-based bootstrap fallback in `get_principal` with genuine bearer token authentication.
     - Parses `Authorization: Bearer <token>`, calculates SHA-256 hex digest (`hash_token`), and performs constant-time comparison via `secrets.compare_digest` against configured hashes from settings.
     - Missing, malformed, unknown, or wrong token raises HTTP 401 Unauthorized with identical response body `{"detail": "Unauthorized"}` and `WWW-Authenticate: Bearer` header.
     - Preserves strict HTTP 422 rejection of client-supplied tenant or principal headers, queries, and body fields.
     - Tenant identity always comes from the resolved principal record.
  4. Documentation:
     - `.env.example`: Replaced bootstrap variables with owner and second principal configuration using token hashes (`SecretStr`).
     - `docs/DEV_ENVIRONMENT.md`: Documented local identity configuration, SHA-256 token hashing, SecretStr protection (SC-04), and loopback-only bind rules.
  5. Test Suites:
     - `tests/test_loopback.py`: 51 unit tests verifying loopback validation, parsing, and startup refusal without opening sockets.
     - `tests/test_identity.py`: 8 tests verifying end-to-end authentication with PostgreSQL, cross-tenant 404 isolation, missing/malformed/wrong token identical 401s, forged headers 422 with valid token, and SC-04 canary redaction.
     - `tests/test_api_tasks.py`: Updated client fixture with explicit dependency override; updated AST check `allowed_functions` to permit `get_principal`.

---

## 2. Issues Resolution (ISS-020)

### Resolution Summary for ISS-020 (Local Pilot Identity)
- **Problem**: In P1a-WP2, `get_principal` used a single bootstrap principal from settings (`EAP_BOOTSTRAP_TENANT_ID`, `EAP_BOOTSTRAP_PRINCIPAL_ID`), meaning requests with no credentials automatically defaulted to a single tenant.
- **Resolution in P1a-WP5**:
  - Removed single bootstrap principal settings and fallback. No implicit default principal exists.
  - Local authentication operates strictly without database user tables. Local principals (owner principal and second principal in different tenants) are loaded from configuration (`EAP_OWNER_*`, `EAP_SECOND_*`, or `EAP_PRINCIPALS_JSON`).
  - Tokens are never stored in plaintext. Settings and environment store only SHA-256 hex hashes protected as `SecretStr`.
  - Authentication checks `Authorization: Bearer <token>` in constant time (`secrets.compare_digest`). Missing or invalid tokens return HTTP 401 Unauthorized with no detail about the failure mode.
  - Tenant ID is always populated from the principal record; client-declared tenant/principal headers, query parameters, or body fields return HTTP 422 Unprocessable Entity.
  - Cross-tenant queries return HTTP 404 (no existence discovery).
  - Binding to non-loopback interfaces (e.g. `0.0.0.0`, `192.168.x.x`, `::`) is refused on startup with `InsecureBindError`.
- **Note on docs/ISSUES.md**: In accordance with work package constraints (parallel P1a-WP4 owns issue numbering), `docs/ISSUES.md` was not modified directly. The owner will mark ISS-020 resolved at merge.

---

## 3. Conformance and Acceptance Matrix

| Item / Requirement | Scope & Mandate | Status | Real Observed Output & Evidence |
|---|---|---|---|
| **E2E Real Token & Isolation** | Valid token A -> principal A (tenant A); valid token B -> principal B (tenant B); B cannot read A's task or runs (404) through real dependency with TestClient + DB | PASS | `tests/test_identity.py::test_end_to_end_real_tokens_and_cross_tenant_isolation_404` verified against PostgreSQL 18.6: POST /v1/tasks under token A returns 202; GET by token A returns 200; GET /runs by token A returns 200; GET by token B returns 404; GET /runs by token B returns 404. |
| **Identical 401 Responses** | Missing / malformed / unknown / wrong token -> 401; response identical for "unknown token" and "wrong token" | PASS | `tests/test_identity.py::test_missing_malformed_and_wrong_tokens_return_identical_401` verified across 9 test cases (missing, empty, whitespace, basic auth, custom scheme, lowercase scheme, unknown token, tampered token, arbitrary wrong token): all return HTTP 401, `WWW-Authenticate: Bearer`, and identical body `{"detail": "Unauthorized"}`. |
| **Forged Headers Rejected** | Forged tenant/principal headers, query params, or body fields return 422 even with valid token | PASS | `tests/test_identity.py::test_forged_tenant_headers_rejected_422_even_with_valid_token` verified: valid Bearer token combined with `X-Tenant-Id`, `X-Principal-Id`, query `?tenant_id=...`, or body `tenant_id` consistently returns HTTP 422 Unprocessable Entity. |
| **SC-04 Canary Redaction** | Token values and hashes never appear in logs, exceptions, repr(settings) or responses | PASS | `tests/test_identity.py::test_token_values_and_hashes_never_leaked_in_settings_or_repr`, `test_token_values_and_hashes_never_leaked_in_caplog`, and `test_canary_token_in_auth_header_never_leaked_in_response_or_logs`: verified with random UUID canary tokens and SHA-256 hashes. Neither tokens nor hashes appear in `repr(settings)`, `str(settings)`, caplog at DEBUG/INFO, or 401 error response bodies. |
| **Loopback Startup Refusal** | Refuse startup if EAP_BIND host is not loopback (127.0.0.1, ::1, localhost); 0.0.0.0, 192.168.x.x, :: refused; 127.0.0.1 and ::1 allowed. Unit-test without opening sockets. | PASS | `tests/test_loopback.py`: 51 unit tests passed. `create_app` with `0.0.0.0:8000`, `192.168.1.5:8000`, `[::]:8000`, `::` raises `InsecureBindError` with clear message; `127.0.0.1:8000`, `[::1]:8000`, `localhost:8000` allowed. |
| **No Implicit Fallback** | Remove settings-based single bootstrap principal fallback | PASS | `tests/test_identity.py::test_no_implicit_default_principal_fallback` verified; requests without valid credentials return 401. |
| **C30 Prerequisite** | Second principal exists for multi-tenant and revocation evaluation | PASS | Owner principal (`tenant-pilot-alpha` / `principal-pilot-alpha`) and second principal (`tenant-pilot-beta` / `principal-pilot-beta`) configured and tested with cross-tenant isolation. |

---

## 4. Verification Commands and Real Output

### 4.1 Pytest Suite against PostgreSQL 18.6
Command: `pytest`
Real Output:
```
============================= test session starts =============================
platform win32 -- Python 3.12.13, pytest-9.2.0, pluggy-1.7.0
rootdir: C:\Users\xhuy\.gemini\antigravity\worktrees\EAM_HUB\bootstrap_identity_auth
configfile: pyproject.toml
testpaths: tests, ppm-v2/reducer/tests
plugins: anyio-4.16.0, hypothesis-6.170.15, asyncio-1.4.1.post1
collected 385 items

385 passed in 43.84s
```

### 4.2 Package and Contract Validation
Command: `uv run python tools/validate_package.py`
Real Output:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

### 4.3 Negative Cases Runner
Command: `uv run python ppm-v2/tools/run_negative_cases.py`
Real Output:
```
PASS 5 templates, 20 cases
```

### 4.4 Fixtures Check
Command: `uv run python ppm-v2/tools/check_fixtures.py`
Real Output:
```
PASS 19 fixtures, 3 rollup
```

### 4.5 SC-06 Database Migration Reversibility
Command: `uv run python tools/run_sc06.py`
Real Output:
```
SC-06 Cycle 1: Empty database up/down/up -> PASSED
SC-06 Cycle 2: Seeded database down/up -> PASSED
SC-06 Verification: ALL CYCLES PASSED
```

### 4.6 Translation Check
Command: `uv run python tools/check_translation.py`
Real Output:
```
SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
```

### 4.7 Linting and Static Type Checking
Commands:
- `uv run ruff check src tests ppm-v2/reducer` -> `All checks passed!`
- `uv run mypy src tests ppm-v2/reducer` -> `Success: no issues found in 107 source files`

### 4.8 Web HUD Production Build
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
✓ built in 385ms
```

### 4.9 Cryptographic Checksum Integrity
Command: `sha256sum -c SHA256SUMS.txt`
Real Output:
```
SHA256SUMS.txt: OK (all files verified)
```

---

## 5. Security and Operational Invariants
- **No Plaintext Token Storage**: Bearer tokens are never stored in settings, `.env`, or database. Only SHA-256 hex digests are kept as `SecretStr`.
- **Constant-Time Comparison**: Digest matching utilizes `secrets.compare_digest` to prevent timing side channels.
- **Identical 401 Responses**: Missing, malformed, wrong, and unknown tokens produce identical responses (`{"detail": "Unauthorized"}` with `WWW-Authenticate: Bearer`), avoiding existence and validation oracles.
- **Server-Side Tenant Resolution**: The client can never supply tenant identity. All tenant header, query, and body fields are rejected with HTTP 422.
- **Loopback Enforcement**: Refusal to bind to non-loopback addresses blocks accidental exposure to external network interfaces during the local pilot.
