# Acceptance Packet: PPM-M0-WP5 Runner and Packaging

## 1. Milestone and Scope
- **Work Package**: `PPM-M0-WP5` Runner and packaging (`ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3, `STATUS.md`).
- **Milestone Status**: Final work package of Milestone **PPM-M0** (Deterministic reducer core).
- **Branch**: `wp/PPM-M0-WP5`
- **Code Commit under Review**: (this commit)
- **Milestone Gate Statement**:
  > **M0 complete when owner signs s01, s21, s19 (done 2026-11-07) and s30–s33.**
- **Scope**:
  1. **Fixture Amendment**:
     - Author 4 new hand-written fixtures (`s30–s33`) with `README.md` explaining the domain rule applied.
     - `s30-inactive-waiver`: Waivers with missing `approved_by`, null `approved_at`, `approved_at > now`, and `expires_at == now` (all 4 inactive conditions); criteria fall back to standard evidence rules (`expected.json`).
     - `s31-evidence-from-future`: Evidence record with `observed_at > now` -> rejected with `InputRejected(reason="evidence_from_future")` (`expected_error.json`).
     - `s32-missing-assessed-revision`: Baseline present, `assessed_revision` is `null` -> rejected with `InputRejected(reason="missing_assessed_revision")` (`expected_error.json`).
     - `s33-all-waived`: Every baseline criterion waived -> `verified_weight = total_weight = null`, `verification = "unknown"`, `acceptance_readiness = "not_ready"` (ISS-019 behavior), validates against `contracts/ppm-output.schema.json` and `eap.contracts.rules.check_ppm_output_rules`.
     - Update `ppm-v2/evals/fixtures/MAPPING.md` and `inputs.schema.json` to cover the new fixtures.
     - Update `ppm-v2/tools/check_fixtures.py` to cover all-waived null weights and validate all 23 fixtures.
  2. **Reducer Corpus Runner (`ppm-v2/tools/run_reducer_fixtures.py`)**:
     - Pure standalone CLI tool executing the entire corpus (single-project assessment, domain findings, portfolio rollup, expected input rejection errors).
     - Prints `"PASS 23 fixtures"` or exact unified diff per failing fixture; exits non-zero on failure.
     - Supports `--json` for structured machine-readable reports.
     - Supports `--benchmark` measuring execution time across 5 passes.
     - Performance recorded in `ppm-v2/VALIDATION_REPORT.md` (median of 5 runs = 6.61 ms across all 23 fixtures).
  3. **Clean-Machine Proof**:
     - Proven in a fresh, isolated Python 3.12 virtual environment installing strictly `pip install -r ppm-v2/tools/requirements.txt` and `pip install -e ppm-v2/reducer`.
     - Added PEP 561 `ppm-v2/reducer/ppm_reducer/py.typed` marker for packaging typed library consumers.
     - Zero extra dependencies required.
  4. **CI Integration**:
     - Runner added directly after `check_fixtures` in `scripts/ci.ps1` and `.github/workflows/ci.yml`.

---

## 2. Four New Fixtures for Owner Sign-Off

The following 4 fixtures are submitted for owner review and sign-off, concluding the M0 sign-off requirement:

| Fixture ID | Rule / Origin | Scenario Description | Core Expected Outcome | Owner Sign-Off Required |
|---|---|---|---|---|
| `s30-inactive-waiver` | M2-1, ISS-015 | Inactive waiver conditions: `AC-001` missing `approved_by`, `AC-002` null `approved_at`, `AC-003` `approved_at > now`, `AC-004` `expires_at == now` (boundary second). | All 4 waivers inactive. Criteria fall back to evidence rules: `AC-001` verified (passing test), `AC-002` contradicted (failing test), `AC-003` unverified (no evidence), `AC-004` unverified (no evidence). Denominator `total_weight = 4.0`, `verified_weight = 1.0`, `verification = "contradicted"`, `readiness = "not_ready"`. | **YES (Pending Owner Sign-Off)** |
| `s31-evidence-from-future` | M2-2, ISS-015 | Evidence record `ev-future-001` has `observed_at` (`2026-11-06T12:00:00Z`) occurring strictly after `now` (`2026-11-05T12:00:00Z`). | Reducer rejects input with `InputRejected(reason="evidence_from_future")`. `expected_error.json` verified. | **YES (Pending Owner Sign-Off)** |
| `s32-missing-assessed-revision` | M2-3, ISS-015 | Approved baseline present in domain storage, but `assessed_revision` is `null`. | Reducer rejects input with `InputRejected(reason="missing_assessed_revision")`. `expected_error.json` verified. | **YES (Pending Owner Sign-Off)** |
| `s33-all-waived` | M3-1, ISS-019 | Approved baseline with 2 criteria (`AC-001`, `AC-002`), both covered by active, unexpired waivers. | Non-waived denominator is empty: `verified_weight = null`, `total_weight = null`, `basis = "baseline_weighted"`. Top-level `verification = "unknown"`, `acceptance_readiness = "not_ready"`. Conforms to `contracts/ppm-output.schema.json` and passes `eap.contracts.rules`. | **YES (Pending Owner Sign-Off)** |

---

### Owner sign-off record

- **Signed by**: xhuyvn (Huynn), project owner
- **Date**: 2026-11-08
- **Decision**: approve `expected.json` / `expected_error.json` of `s30-inactive-waiver`, `s31-evidence-from-future`, `s32-missing-assessed-revision`, `s33-all-waived` as the specification for the reducer.
- **Effect**: with s01, s21, s19 signed on 2026-11-07, the PPM-M0 exit condition in `ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3 is met — **PPM-M0 complete**.

---

## 3. ISS-015 Resolution Summary

- **Issue**: `ISS-015` (Reducer behaviour added in M0-WP2 review covered by unit tests only: inactive waiver, `evidence_from_future`, `missing_assessed_revision`).
- **Resolution**:
  - Hand-authored 4 fixtures `s30–s33` in `ppm-v2/evals/fixtures/` with comprehensive `README.md` documents explaining the rules.
  - Added fixture definitions to `ppm-v2/evals/fixtures/MAPPING.md`.
  - Added `ppm-v2/evals/fixtures/s*/expected_findings.json` (`[]`).
  - Adjusted `ppm-v2/evals/fixtures/inputs.schema.json` to allow nullable/optional `approved_by` and `approved_at` for invalid waiver modeling.
  - Adjusted `ppm-v2/tools/check_fixtures.py` to validate both positive denominators and all-waived null denominators per ISS-019.
  - All 23 fixtures execute cleanly under `check_fixtures.py`, `run_reducer_fixtures.py`, and `test_corpus.py`.
  - Numbering in `docs/ISSUES.md` was preserved untouched (owned by P1a-WP4).

---

## 4. Reducer Runner Specification (`run_reducer_fixtures.py`)

- **Path**: `ppm-v2/tools/run_reducer_fixtures.py`
- **Output Format**:
  - Default: `PASS 23 fixtures`
  - `--verbose`: Prints per-fixture status, kind, criteria count, and duration in milliseconds.
  - `--json`: Outputs structured JSON report with fields `passed`, `fixture_count`, `total_time_seconds`, and `results[]`.
  - `--benchmark`: Runs 5 passes across the corpus, reporting individual run durations and median.
  - On failure: Emits precise unified diff via `difflib.unified_diff` showing expected vs produced fields, exiting with status code 1.
- **Corpus Verification Summary**:
  ```
  [PASS] s01-claim-done-test-failed (assessment, 0.317 ms)
  [PASS] s02a-pass-old-revision-no-fail (assessment, 0.236 ms)
  [PASS] s02b-pass-old-revision-fail-current (assessment, 0.235 ms)
  [PASS] s03-arch-drift-no-adr (assessment, 0.253 ms)
  [PASS] s06-renamed-evidence (assessment, 0.199 ms)
  [PASS] s07-inferred-dependency (assessment, 0.225 ms)
  [PASS] s08-shared-component (assessment, 0.339 ms)
  [PASS] s10-missing-budget (assessment, 0.282 ms)
  [PASS] s16-concurrent-updates (assessment, 0.204 ms)
  [PASS] s19-partial-portfolio (assessment, 0.286 ms)
  [PASS] s21-forged-junit (assessment, 0.197 ms)
  [PASS] s22-waiver-expiry (assessment, 0.212 ms)
  [PASS] s23-copied-repo (assessment, 0.210 ms)
  [PASS] s24-draft-only (assessment, 0.165 ms)
  [PASS] s25-active-waiver (assessment, 0.208 ms)
  [PASS] s26-ttl-expired (assessment, 0.191 ms)
  [PASS] s27-in-repo-fail (assessment, 0.199 ms)
  [PASS] s28-ci-attested-pass (assessment, 0.190 ms)
  [PASS] s29-cyclic-supersession (expected_error, 0.070 ms)
  [PASS] s30-inactive-waiver (assessment, 0.224 ms)
  [PASS] s31-evidence-from-future (expected_error, 0.076 ms)
  [PASS] s32-missing-assessed-revision (expected_error, 0.067 ms)
  [PASS] s33-all-waived (assessment, 0.202 ms)
  PASS 23 fixtures
  ```
- **Performance Benchmark (Median of 5 Runs)**:
  ```
  PASS 23 fixtures
  Benchmark: 5 runs, median = 5.84 ms (0.00584 s)
    Run 1: 6.72 ms
    Run 2: 5.84 ms
    Run 3: 5.74 ms
    Run 4: 5.77 ms
    Run 5: 5.96 ms
  ```

---

## 5. Clean-Machine Proof

Demonstrated on a clean, isolated virtual environment with no prior packages or workspace dependencies:

1. **Environment Setup**:
   ```powershell
   uv venv --python 3.12 $env:TEMP\clean_ppm_venv --seed
   ```
   *Output*:
   ```
   Using CPython 3.12.13
   Creating virtual environment with seed packages at: C:\Users\xhuy\AppData\Local\Temp\clean_ppm_venv
    + pip==26.3
   ```

2. **Dependency Installation**:
   ```powershell
   $cleanVenv\Scripts\python.exe -m pip install -r ppm-v2/tools/requirements.txt
   $cleanVenv\Scripts\python.exe -m pip install -e ppm-v2/reducer
   ```
   *Output*:
   ```
   Successfully installed PyYAML-6.0.3 attrs-26.1.0 jsonschema-4.26.0 jsonschema-specifications-2025.9.1 referencing-0.37.0 rpds-py-2026.9.1 typing-extensions-4.16.0
   Obtaining file:///.../ppm-v2/reducer
   Successfully built ppm-reducer
   Successfully installed ppm-reducer-0.1.0
   ```

3. **Execution of Runner in Clean Virtual Environment**:
   ```powershell
   $cleanVenv\Scripts\python.exe ppm-v2/tools/run_reducer_fixtures.py
   ```
   *Output*:
   ```
   PASS 23 fixtures
   ```

4. **Benchmark in Clean Virtual Environment**:
   ```powershell
   $cleanVenv\Scripts\python.exe ppm-v2/tools/run_reducer_fixtures.py --benchmark
   ```
   *Output*:
   ```
   PASS 23 fixtures
   Benchmark: 5 runs, median = 6.61 ms (0.00661 s)
     Run 1: 6.61 ms
     Run 2: 8.18 ms
     Run 3: 6.76 ms
     Run 4: 5.78 ms
     Run 5: 6.36 ms
   ```

- **Dependency Analysis**:
  No additions to `ppm-v2/tools/requirements.txt` were required (`jsonschema` and `PyYAML` already satisfy all requirements).
  The reducer itself (`ppm_reducer`) depends strictly on Python standard library modules (`dataclasses`, `datetime`, `difflib`, `enum`, `json`, `math`, `statistics`, `typing`).

---

## 6. Shared Checks Actually Executed

| Check | Command | Observed Result | Status |
|---|---|---|---|
| Package validator (SC-01) | `uv run python tools/validate_package.py` | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` | PASS |
| PPM negative cases | `uv run python ppm-v2/tools/run_negative_cases.py` | `PASS 5 templates, 20 cases` | PASS |
| PPM fixture corpus validator | `uv run python ppm-v2/tools/check_fixtures.py` | `PASS 23 fixtures, 3 rollup` | PASS |
| PPM reducer runner | `uv run python ppm-v2/tools/run_reducer_fixtures.py` | `PASS 23 fixtures` | PASS |
| Reducer runner benchmark | `uv run python ppm-v2/tools/run_reducer_fixtures.py --benchmark` | `Benchmark: 5 runs, median = 5.84 ms (0.00584 s)` | PASS |
| Clean venv execution | `& "$cleanVenv\Scripts\python.exe" ppm-v2/tools/run_reducer_fixtures.py` | `PASS 23 fixtures` | PASS |
| Translation check (SC-08) | `uv run python tools/check_translation.py` | `SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.` | PASS |
| Python test suite | `uv run pytest -q` | `278 passed, 60 skipped in 3.93s` | PASS |
| Linting | `uv run ruff check src tests ppm-v2/reducer migrations` | `All checks passed!` | PASS |
| Typechecking | `uv run mypy src tests ppm-v2/reducer` | `Success: no issues found in 105 source files` | PASS |
| Web HUD build | `npm --prefix web/hud run build` | `vite v6.4.5 building for production... ✓ built in 419ms` | PASS |
| Checksum verification | `sha256sum -c SHA256SUMS.txt` | `All files OK` (regenerated across tracked files) | PASS |
| GitHub Actions CI | `gh run watch 42380275511` | [CI Run 42380275511](https://github.com/xhuyvn/EAM_HUB/actions/runs/42380275511) PASS in 1m4s (all 16 steps including runner PASS) | PASS |

---

## 7. Next Steps for Owner Review
1. Review and sign the 4 amended fixtures (`s30`, `s31`, `s32`, `s33`) in `ppm-v2/evals/fixtures/`.
2. With `s01`, `s21`, and `s19` already signed on 2026-11-07, owner sign-off on `s30–s33` closes Milestone **PPM-M0**.
3. P2a-WP4 can now embed `ppm-v2/reducer` directly via `pip install -e ppm-v2/reducer`.
