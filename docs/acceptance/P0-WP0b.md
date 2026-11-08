# Acceptance Packet: P0-WP0b SC-08 Tooling Fix and Provenance Re-baseline

## 1. Milestone and Scope
- **Work Package**: P0-WP0b (SC-08 Tooling Fix, Git History Detection, and Provenance Re-alignment).
- **Branch**: `wp/P0-WP0b-sc08` (branched from `main` @ `d7c9e91`).
- **Parallel Work**: P1a-WP7 (`tools/bench/**`, `docs/measurements/**`) untouched. `docs/ISSUES.md` untouched (numbering owned by P1a-WP7).
- **Scope & Deliverables**:
  1. Fix `tools/check_translation.py` (SC-08) git history detection:
     - When run without git history (e.g., an extracted `git archive` export lacking a `.git` entry or HEAD), it reports `SC-08 Report Summary: not_run: no git history.`, exits with code 0 in standard mode and non-zero (code 1) in `--strict` mode, and never falsely claims translations are up-to-date.
     - Add `--strict` flag to fail with non-zero exit code on stale translations, missing headers, or missing git history.
     - In git check loop, non-zero `git log` exit codes are treated as errors/staleness rather than falling through to OK.
  2. Re-baseline 35 `docs/vi/**` translation headers to `> Translated from <path> @ a36d44e` for all files whose English canonicals have remained unchanged since P0-WP0 (`a36d44e`).
  3. Re-translate the 3 modified English canonical files into Vietnamese:
     - `docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md` (ADR-021 rev 2 table entry, P0 closure with DG-1/DG-2 signing, D-01 LangGraph option (b) decision).
     - `docs/DEV_ENVIRONMENT.md` (Local identity & token hash authentication, uvicorn server execution, request logging middleware, access log query string redaction, universal handler filter, P0-WP1 checklist completion digests).
     - `docs/IMPLEMENTATION_PLAN.md` (P1a-WP7 ingest baseline threshold per DG1-C4 condition).
     - Preserve all identifiers, numbers, paths, commands, and table formats; verify `tools/check_translation.py --compare` yields 0 identifier differences.
     - Set their translation headers to `> Translated from <path> @ d7c9e91`.
  4. Author regression tests in `tests/test_check_translation.py`:
     - Test archive-like export directory without `.git` -> reports `not_run: no git history`, exit 0 in default mode, exit non-zero in `--strict` mode, 0 claims of "up-to-date".
     - Test untouched English canonical -> reports up-to-date (`1 up-to-date, 0 stale`).
     - Test touched English canonical -> reports stale (`0 up-to-date, 1 stale`).
  5. Code quality:
     - `uv run ruff check src tests ppm-v2/reducer tools` -> `All checks passed!`.
     - `uv run mypy src tests ppm-v2/reducer tools` -> `Success: no issues found in 122 source files`.
  6. Shared checks & checksums:
     - Re-generate `SHA256SUMS.txt` with format `"<hash> *<path>"` and LF line endings.
     - Verify full test suite and shared verification scripts.

## 2. Environment and Base Commits
- **Base Commit**: `d7c9e91` ("sign PPM-M0 fixtures s30-s33; PPM-M0 complete")
- **Branch**: `wp/P0-WP0b-sc08`
- **Environment**:
  - Python: 3.12.13 (via uv workspace)
  - Node.js: 26.0.0-nightly202501066c6b4cf728
  - OS: Windows 11 (win32, x86_64)

## 3. Acceptance Criteria and Verification Evidence

| Acceptance Criterion | Target Requirement | Real Observed Output | Result |
|---|---|---|---|
| AC-P0-WP0b-1: SC-08 Default Staleness Report | 38 up-to-date, 0 stale, 0 missing headers | `SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers.` | PASS |
| AC-P0-WP0b-2: SC-08 Strict Mode | Exit code 0 when clean; exit 1 on stale or no git | Exit code 0 when clean; exits 1 with `not_run` on no git history | PASS |
| AC-P0-WP0b-3: Identifier Parity (--compare) | 0 identifier differences across all 38 pairs | `PASS: 0 identifier differences across 38 file pairs.` | PASS |
| AC-P0-WP0b-4: Fixture Verification (--fixture) | Catch removed ARCH-01 and number 18.6->18.7 | `PASS: Fixture detection verified (-ARCH-01, 18.6 -> 18.7)` | PASS |
| AC-P0-WP0b-5: Internal Relative Links (--links) | 0 broken relative markdown links | `Link check: 5 links checked, 0 broken.` | PASS |
| AC-P0-WP0b-6: Token Savings (--tokens) | ~20% token savings across representative docs | `TOTAL: 32710 vi -> 25914 en (6796 saved, 20.8% reduction)` | PASS |
| AC-P0-WP0b-7: Archive No-Git Detection | Archive copy without .git reports not_run | `SC-08 Report Summary: not_run: no git history.` (exit 1 with --strict; 0 occurrences of up-to-date) | PASS |
| AC-P0-WP0b-8: Package Contracts Validation | PASS 16 schemas, 15 fixtures, 46 negative cases | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` | PASS |
| AC-P0-WP0b-9: PPM Negative Cases | PASS 5 templates, 20 cases | `PASS 5 templates, 20 cases` | PASS |
| AC-P0-WP0b-10: PPM Fixture Corpus | PASS 23 fixtures, 3 rollup | `PASS 23 fixtures, 3 rollup` | PASS |
| AC-P0-WP0b-11: PPM Reducer Fixtures | PASS 23 fixtures | `PASS 23 fixtures` | PASS |
| AC-P0-WP0b-12: Full Python Test Suite | All tests pass | `349 passed, 83 skipped in 6.48s` (`uv run pytest -q`) | PASS |
| AC-P0-WP0b-13: HUD Web Build | Zero TypeScript / Vite compilation errors | `dist/assets/index-DLYonLHq.js 225.08 kB; built in 397ms` | PASS |
| AC-P0-WP0b-14: Linting (Ruff) | 0 errors across src, tests, reducer, tools | `All checks passed!` (`uv run ruff check src tests ppm-v2/reducer tools`) | PASS |
| AC-P0-WP0b-15: Type Checking (Mypy) | 0 errors across src, tests, reducer, tools | `Success: no issues found in 122 source files` (`uv run mypy src tests ppm-v2/reducer tools`) | PASS |
| AC-P0-WP0b-16: SHA-256 Checksums | All tracked files match checksums | `SHA256SUMS: OK` / `sha256sum -c SHA256SUMS.txt` exit code 0 | PASS |

## 4. Re-translated Documents Detail

1. **`docs/vi/docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md`**:
   - Header set to `> Translated from docs/01_DECISIONS_AND_SOURCE_RECONCILIATION.md @ d7c9e91`.
   - ADR-021 rev 2 table row translated into Vietnamese with CAS `basis_seq`, input locking, and deterministic ordering explanation.
   - P0 closure section translated: signing of DG-1 / DG-2 on 07/11/2026, binding conditions DG1-C1…C5 / DG2-C1, and Amendment A1 tenant composite foreign keys.
   - D-01 section translated: selection of option (b) for single-attempt LangGraph with side-effect replay disabled and DG-1 wrapping.
   - 186 identifiers matched (0 differences).

2. **`docs/vi/docs/DEV_ENVIRONMENT.md`**:
   - Header set to `> Translated from docs/DEV_ENVIRONMENT.md @ d7c9e91`.
   - `.env.example` local authentication variables and comments translated (`EAP_OWNER_TENANT_ID`, `EAP_OWNER_TOKEN_HASH`, etc.).
   - Local identity & token hash authentication section translated (P1a-WP5 / ISS-020).
   - Server run command and access logging section translated (P1a-WP6 / SC-04: `RequestLoggingMiddleware`, `--no-access-log`, query string redaction, universal handler filter).
   - Checklist updated with completed marks and image digests (`postgres:18.6-trixie`, `redis:8.10.1-trixie`, `qdrant/qdrant:v1.20.0`).
   - 151 identifiers matched (0 differences).

3. **`docs/vi/docs/IMPLEMENTATION_PLAN.md`**:
   - Header set to `> Translated from docs/IMPLEMENTATION_PLAN.md @ d7c9e91`.
   - P1a-WP7 ingest baseline description updated to reflect owner condition DG1-C4 (sustained ≥ 200 events/s with 0 lost/duplicated events).
   - 463 identifiers matched (0 differences).

## 5. Security and Safety Rules
- **No egress / no billable calls**: Purely local testing; mock environments only.
- **Pure reducer preserved**: No I/O or banned imports introduced.
- **Contract protection**: No modifications to `contracts/` or `ppm-v2/schemas/`.
- **Parallel isolation**: Did not touch `tools/bench/**` or `docs/measurements/**`.
- **Issues numbering**: `docs/ISSUES.md` was not modified.

## 6. Issues and Gaps
- **ISS-NEW-1**: None. All acceptance criteria met cleanly. No blocker or contract defect identified.
