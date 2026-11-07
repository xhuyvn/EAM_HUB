# Acceptance Packet: P1a-WP6 Logging and Redaction

## 1. Milestone and Scope
- **Work Package**: P1a-WP6 (Logging and redaction per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §6 P1a-WP6, `docs/02_RUNTIME_AND_SECURITY.md`, and SC-04).
- **Target State**: `ready_for_review`
- **Branch**: `wp/P1a-WP6` (branched from `main` at `81fc436`)
- **Code Commit under Review**: `706e784`
- **GitHub Actions CI Run**: `https://github.com/xhuyvn/EAM_HUB/actions/runs/42388151035` (Conclusion: `success`, 1m35s)
- **Base Commit**: `81fc436` ("accept P1a-WP4: dispatcher, mock provider, recovery, cancel")
- **Parallel Work Package Isolation**:
  - No parallel platform session this round.
  - Zero database migrations added.
  - `ppm-v2/reducer/**`: Zero modifications.
  - Ownership of `docs/ISSUES.md`: Registered `ISS-023`.
- **Scope Deliverables**:
  1. `src/eap/observability/logging.py`:
     - Structured JSON logging setup used by the application and workers.
     - Standard JSON fields: `timestamp` (UTC ISO-8601), `level`, `logger`, `message`, `tenant_id`, `principal_id`, `task_id`, `run_id`, `request_id`, optional extra fields (`status`, `duration_ms`, etc.), and `exception` / `traceback`.
     - Correlation context variables: `tenant_id_var`, `principal_id_var`, `task_id_var`, `run_id_var`, `request_id_var`, `set_log_context`, `clear_log_context`, and `log_context` context manager.
     - `configure_logging(settings, level, handler)` configured once on startup.
  2. Redaction Filter (`RedactionFilter`):
     - Automatically applied to every logging handler (root handlers, file handlers, pytest caplog, etc.) via `apply_redaction_filter` and dynamic patching of `logging.Logger.addHandler`.
     - Redaction rules:
       - `SecretStr` values from settings and runtime registered secrets.
       - Authorization headers: `(?i)\bAuthorization\s*:\s*([^\r\n]+)` -> `Authorization: ***`.
       - Bearer tokens: `(?i)\bBearer\s+([A-Za-z0-9_\-\.+=/]+)` -> `Bearer ***`.
       - Token hashes: SHA-256 hex digests and registered principal token hashes -> `***`.
       - DSN passwords: `([a-zA-Z0-9_+]+://[^:\s@]*):([^@\s/]+)@` -> `\1:***@` (e.g. `postgresql://user:pass@ -> postgresql://user:***@`).
       - Secret canary: `EAP-CANARY-[A-Za-z0-9_\-]+` and `settings.eap_secret_canary` -> `***`.
       - Sensitive keys matching `(?i)(password|secret|token|api_key|authorization|cookie)` in dicts, JSON strings, URL query parameters, and python repr/kwargs.
       - Exceptions and tracebacks: `record.exc_info`, `record.exc_text`, and `record.stack_info` formatted and redacted before emission.
  3. Request Logging Middleware (`RequestLoggingMiddleware`):
     - Positioned in FastAPI / ASGI pipeline to log every request on completion at `INFO`: method, path, status, duration (ms), request_id.
     - Strictly enforces security invariant: NEVER logs request headers, request bodies, response headers, or response bodies.
     - Attaches `X-Request-Id` correlation header to response.
     - Catches unhandled server exceptions, logs at `ERROR` with exception traceback (redacted), and returns HTTP 500 `{"detail": "Internal Server Error", "code": "INTERNAL_SERVER_ERROR"}` with zero secret leakage.
  4. Telemetry Egress Validation (`validate_telemetry_config`):
     - Enforces `EAP_TELEMETRY=local_only`: forbids any exporter that sends data off-host.
     - Rejects non-loopback endpoints in `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `EAP_TELEMETRY_ENDPOINT`, etc., raising `InsecureTelemetryError`.
     - Rejects remote exporter kinds (`zipkin`, `jaeger`, `remote`, `http`).
     - Loopback local endpoints (`http://127.0.0.1:4318`, `http://localhost:4318`) permitted for local tracing.
  5. Test Suites:
     - `tests/test_logging_sc04.py`: SC-04 end-to-end verification and mutation check.
     - `tests/test_observability.py`: Unit tests for JSON formatting, correlation variables, redaction patterns, middleware invariants, and telemetry egress validation.

---

## 2. Issues Resolution (ISS-023) and Review Findings (W6-1, W6-2)

### Summary for ISS-023 (Alembic fileConfig logger disable pitfall)
- **Problem**: When Alembic migrations ran (`alembic.ini` via `migrations/env.py`), standard library `logging.config.fileConfig(config.config_file_name)` defaulted to `disable_existing_loggers=True`. This disabled existing runtime loggers that were imported prior to migration execution (specifically `eap.api.request`).
- **Resolution**:
  - In `migrations/env.py`, set `fileConfig(config.config_file_name, disable_existing_loggers=False)`.
  - In `src/eap/observability/logging.py`, `configure_logging()` iterates over `logging.root.manager.loggerDict` and re-enables any existing loggers (`l.disabled = False`).
  - Recorded in `docs/ISSUES.md` with status `resolved`.

### Summary for W6-1 (Pre-existing handlers and uvicorn access logging query strings)
- **Problem**: When uvicorn starts via `uvicorn eap.api.app:app`, `dictConfig(uvicorn.config.LOGGING_CONFIG)` executes before the app is imported. This attaches handlers directly to `uvicorn` and `uvicorn.access` (both configured with `propagate=False`). Pre-existing handlers on non-root loggers were bypassed because `configure_logging()` only inspected `root.handlers`. Furthermore, uvicorn's access logger printed URL query strings carrying credentials or canaries (e.g. `?token=<canary>`).
- **Resolution**:
  - In `src/eap/observability/logging.py`, `configure_logging()` iterates over `logging.root.manager.loggerDict.values()`, re-enables any disabled loggers, and invokes `apply_redaction_filter` on every logger and on every existing handler on each logger (including `propagate=False` loggers).
  - Maintained `logging.Logger.addHandler` patching so any handlers attached subsequently automatically receive `apply_redaction_filter`.
  - Patched `uvicorn.logging.AccessFormatter.formatMessage` and added special-case logic in `RedactionFilter.filter()` for `uvicorn.access` to drop query strings (`?` and all following characters) from `record.args[2]` (`full_path`), preventing token/credential leakage in access logs while preserving the 5-element tuple expected by `AccessFormatter`.
  - Documented the recommended server command in `docs/DEV_ENVIRONMENT.md`: `uv run uvicorn eap.api.app:app --host 127.0.0.1 --port 8000 --no-access-log` (relying on `RequestLoggingMiddleware` for structured JSON request logs without query strings or headers/bodies).

### Summary for W6-2 (Orphan logger fallback to logging.lastResort)
- **Problem**: Unhandled log records on orphan loggers (loggers with no handlers and `propagate=False`) fall back to `logging.lastResort` in Python standard library, emitting directly to `sys.stderr` without redaction.
- **Resolution**:
  - Created `RedactedLastResortHandler(logging.StreamHandler)` with `apply_redaction_filter(self)` attached.
  - In `configure_logging()`, `apply_redaction_filter(logging.lastResort)` is called if `lastResort` exists; otherwise `logging.lastResort` is set to `RedactedLastResortHandler()`.
  - Any orphan logger record falling back to `lastResort` has all canaries, bearer tokens, passwords, and secrets sanitized before emission to stderr.

---

## 3. SC-04 Evidence and Conformance Matrix

| Check / Requirement | Mandate | Status | Real Observed Output & Evidence |
|---|---|---|---|
| **SC-04 Flow 1: Create Task** | POST /v1/tasks with valid bearer token returns 202; canary, token, hash, DB password absent from logs, file handler, responses, and outbox | PASS | `tests/test_logging_sc04.py::test_sc04_canary_and_credential_redaction_across_all_flows`: POST /v1/tasks returned 202; outbox event `task.created` inserted in DB; asserted secrets absent from caplog, `sc04_audit.log`, response body, and DB outbox payload. |
| **SC-04 Flow 2: Failed Auth** | GET /v1/tasks with invalid canary token returns 401; canary, token, hash absent from logs, file handler, response | PASS | `test_sc04_canary_and_credential_redaction_across_all_flows`: GET with invalid token returned 401 `{"detail": "Unauthorized"}`; asserted canary token absent from caplog, file log, and response body. |
| **SC-04 Flow 3: DB Error DSN** | Route raising DB OperationalError containing full DSN with password returns 500; DB password absent from logs, file handler, response | PASS | `test_sc04_canary_and_credential_redaction_across_all_flows`: GET /_test/db-error returned 500 `{"detail": "Internal Server Error"}`; DB password `secret-pg-pass-...` redacted to `***` in caplog and `sc04_audit.log`; absent from response body. |
| **SC-04 Flow 4: Token in Exception** | Route raising exception carrying token and canary returns 500; token and canary absent from logs, file handler, response | PASS | `test_sc04_canary_and_credential_redaction_across_all_flows`: GET /_test/token-error returned 500; token and canary strings redacted to `***` in caplog and `sc04_audit.log`; absent from response body. |
| **Mutation Check** | Temporarily disable redaction filter and show that secrets leak and SC-04 fails | PASS | `tests/test_logging_sc04.py::test_mutation_sc04_fails_without_redaction`: With redaction enabled, token, canary, and DSN password are sanitized (`***`). Under `with disabled_redaction():`, the unredacted secrets leak directly into caplog: `secret_token in caplog.text` (True), `canary in caplog.text` (True), `secret_db_pass in caplog.text` (True). |
| **Structured JSON Format** | Standard keys: timestamp, level, logger, message, tenant_id, principal_id, run_id, task_id, request_id | PASS | `tests/test_observability.py::test_json_formatter_keys_and_correlation`: Formatter produces valid JSON containing all correlation fields; `test_log_context_manager` verifies scoping and context restoration. |
| **Request Logging Middleware** | Logs method, path, status, duration, request_id; never bodies or headers | PASS | `tests/test_observability.py::test_request_logging_middleware_invariants`: GET /healthz logs `record.method="GET"`, `record.path="/healthz"`, `record.status=200`, `duration_ms`, `request_id`; headers and response body absent from logs. |
| **Telemetry local_only** | Local loopback permitted; any off-host exporter raises InsecureTelemetryError | PASS | `tests/test_observability.py::test_telemetry_validation_local_only`: `http://127.0.0.1:4318` passes; `https://api.honeycomb.io` and exporter `zipkin` raise `InsecureTelemetryError`. |
| **W6-1: Pre-existing uvicorn handlers & access redaction** | `dictConfig(uvicorn.config.LOGGING_CONFIG)` before `create_app()`; log canary, bearer token, DSN password through uvicorn.error and uvicorn.access (?token=<canary>); assert none appears in output | PASS | `tests/test_logging_sc04.py::test_w6_1_uvicorn_dictconfig_preexisting_handlers_and_access_redaction`: Canary, bearer token, token hash, DSN pass completely absent from `uvicorn.error` and `uvicorn.access` streams; query string dropped from path. |
| **W6-1: Pre-existing handler on propagate=False child** | Pre-existing StreamHandler attached to child logger before `configure_logging()` receives redaction filter | PASS | `tests/test_logging_sc04.py::test_w6_1_preexisting_handler_on_child_logger_with_propagate_false`: Pre-existing handler output sanitized (`canary=***`, `secret=***`, `postgresql://user:***@localhost/db`). |
| **W6-2: Orphan logger lastResort redaction** | Orphan logger (no handlers, propagate=False) emits warning to stderr with secrets redacted via lastResort | PASS | `tests/test_logging_sc04.py::test_w6_2_orphan_logger_fallback_to_last_resort_redacted`: `logging.lastResort` filters canary, token, and DSN password (`***`). |
| **W6-1: Uvicorn subprocess E2E** | Subprocess running uvicorn on 127.0.0.1 handles error request carrying ?token=<canary>; output clean of secrets | PASS | `tests/test_logging_sc04.py::test_w6_1_uvicorn_subprocess_e2e_redaction`: Process started on loopback, handled `/trigger-error?token=...`; canary and password absent from stdout and stderr; path logged without query string. |

---

## 4. Verification Commands and Real Output

### 4.1 Pytest Suite against PostgreSQL 18.6
Command:
```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"
uv run pytest -q
```
Real Output:
```
430 passed, 1 warning in 46.72s
```

### 4.2 Ruff Check
Command:
```powershell
uv run ruff check src tests ppm-v2/reducer
```
Real Output:
```
All checks passed!
```

### 4.3 Mypy Static Type Analysis
Command:
```powershell
uv run mypy src tests ppm-v2/reducer
```
Real Output:
```
Success: no issues found in 117 source files
```

### 4.4 Package and Contract Validation
Command:
```powershell
uv run python tools/validate_package.py
```
Real Output:
```
PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed
```

### 4.5 PPM Negative Cases Runner
Command:
```powershell
uv run python ppm-v2/tools/run_negative_cases.py
```
Real Output:
```
PASS 5 templates, 20 cases
```

### 4.6 PPM Reducer Fixture Corpus Runner
Command:
```powershell
uv run python ppm-v2/tools/run_reducer_fixtures.py
```
Real Output:
```
PASS 23 fixtures
```

### 4.7 PPM Fixture Integrity Check
Command:
```powershell
uv run python ppm-v2/tools/check_fixtures.py
```
Real Output:
```
PASS 23 fixtures, 3 rollup
```

### 4.8 Translation Provenance Check
Command:
```powershell
uv run python tools/check_translation.py
```
Real Output:
```
SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.
```

### 4.9 Database Migration Cycles (SC-06)
Command:
```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_test"
uv run python tools/run_sc06.py
```
Real Output:
```
SC-06 Verification: ALL CYCLES PASSED
```

### 4.10 HUD Frontend Build
Command:
```powershell
npm --prefix web/hud run build
```
Real Output:
```
vite v6.4.5 building for production...
transforming...
✓ 29 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                   0.39 kB │ gzip:  0.27 kB
dist/assets/index-a8RAso0v.css    0.71 kB │ gzip:  0.39 kB
dist/assets/index-DLYonLHq.js   225.08 kB │ gzip: 70.02 kB
✓ built in 414ms
```

---

## 5. Rollback Procedure
If logging or redaction regressions occur:
1. Revert to base commit `81fc436`.
2. Middleware removal: Remove `app.add_middleware(RequestLoggingMiddleware)` in `src/eap/api/app.py`.
3. Logging configuration: `configure_logging()` can be bypassed or set to standard logging.
4. Telemetry: Set `EAP_TELEMETRY=local_only` (default) and verify no remote exporters are set in environment.
