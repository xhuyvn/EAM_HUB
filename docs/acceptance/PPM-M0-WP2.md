# Acceptance Packet: PPM-M0-WP2 Criterion Reducer

## 1. Milestone and Scope
- **Work Package**: `PPM-M0-WP2` Criterion reducer (`ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3, `STATUS.md`).
- **Scope**:
  - Implement a pure, deterministic criterion reducer function:
    `assess_criteria(baseline, claims, evidence, waivers, assessed_revision, now, evidence_ttl_days=7.0) -> list[CriterionResult]`.
  - Modules in `ppm-v2/reducer/ppm_reducer/`:
    - `types.py`: `CriterionResult`, `InputRejected`, `TrustClass`, `CriterionStatus`, `ReasonCode`, `ReportedStatus`.
    - `supersession.py`: DAG resolution, cycle and dangling detection raising typed `InputRejected`.
    - `trust.py`: trust class classification and parameterized TTL evaluation with strict inequality boundary.
    - `criteria.py`: pure domain reduction logic, decision table implementation, determinism sorting.
    - `__init__.py`: public export surface.
  - Test suites in `ppm-v2/reducer/tests/`:
    - `test_corpus.py`: corpus comparison against all 19 fixtures in `ppm-v2/evals/fixtures/` post RFC 8785/JCS normalization (excluding `explanation`).
    - `test_determinism.py`: verification of identical SHA-256 digest across runs with shuffled inputs (claims, evidence, waivers, criteria).
    - `test_properties.py`: Hypothesis property-based tests asserting verification provenance and waiver validity.
    - `test_reducer_rules.py`: edge cases covering exact TTL boundary second, dangling supersession, unmapped coverage, concurrent conflicting claims, waiver inactivity conditions (M2-1), evidence from future (M2-2), and missing assessed revision (M2-3).
  - Hardened import boundary in `tests/test_import_boundary.py`:
    - AST static analysis enforcing an ALLOWLIST of pure stdlib modules (`dataclasses`, `datetime`, `enum`, `typing`, `re`, `json`, `hashlib`, `functools`, `itertools`, `collections`, `decimal`, `math`, `ppm_reducer`).
    - Runtime audit hook (`sys.addaudithook`) verifying zero I/O, network, subprocess, or os calls across the entire fixture corpus.
    - Static probe tests verifying that imports of `builtins`, `shutil`, `os`, `subprocess` or `getattr(..., 'open')` fail immediately.

---

## 2. Rule to Code Location to Fixture Mapping

| Rule / Semantic Requirement | Code Location | Fixtures Covered | Observed Behavior |
|---|---|---|---|
| **Supersession Resolution (DAG)**: Resolve supersession chains first; reject cycles or dangling references. | `ppm_reducer/supersession.py` (`resolve_supersession`) | `s29-cyclic-supersession`, `test_dangling_supersession` | Raises `InputRejected(reason="supersession_cycle")` on mutual loop `upd-cycle-001` <-> `upd-cycle-002`. Raises `dangling_supersession` on missing target. |
| **Evidence Temporal Validity (M2-2)**: Evidence with `observed_at > now` is rejected deterministically as future evidence. | `ppm_reducer/criteria.py` lines 86–92 | `test_evidence_from_future` | Raises `InputRejected(reason="evidence_from_future")`. |
| **Revision Presence Check (M2-3)**: Baseline present with criteria requires a non-empty `assessed_revision`. | `ppm_reducer/criteria.py` lines 94–98 | `test_missing_assessed_revision` | Raises `InputRejected(reason="missing_assessed_revision")`. Baseline absent/empty draft returns `[]`. |
| **Active Refutation (FAIL in any class)**: A test failure in ANY trust class at the exact assessed revision refutes the deliverable. | `ppm_reducer/criteria.py` lines 180–205 | `s01`, `s02b`, `s27` | Outcome: `contradicted`, `reason_code: evidence_refuted`. In `s27`, producer-authored `reported_artifact` FAIL refutes deliverable. |
| **Waiver Authority & Temporal Validity (M2-1)**: Waiver is active ONLY if `approved_by` is a non-empty string AND `approved_at` is set AND `approved_at <= now < expires_at`. Inactive waiver is ignored (falls back to evidence rules). | `ppm_reducer/criteria.py` lines 100–120, 207–225; `trust.py` | `s08` (`AC-A-002`), `s25` (`AC-002`), `s22` (`AC-002`), `test_waiver_inactive_conditions` | Outcome: active -> `waived`, `reason_code: waiver_approved`, `evidence_trust: none`. Inactive (e.g. `expires_at == now`, `approved_at > now`, null `approved_at`, missing `approved_by`) ignored; criterion falls back to evidence rules. |
| **Trust Class Verification Authority**: Only `verifier_observed` or `ci_attested` at assessed revision can verify a criterion. | `ppm_reducer/trust.py` (`is_verifying_trust_class`); `criteria.py` | `s03`, `s07`, `s08`, `s10`, `s19`, `s22`, `s23`, `s25`, `s28` | Outcome: `verified`, `reason_code: evidence_current_pass`. `s28` cryptographically verifies via `ci_attested`. |
| **In-Repo Reported Artifacts (PPM-01 / ADR-023)**: Test reports authored by producer inside repository cannot verify deliverables. | `ppm_reducer/criteria.py` lines 270–288 | `s21-forged-junit` | Outcome: `unverified`, `reason_code: reported_artifact_only`, `evidence_trust: reported_artifact`. Forged test report cannot verify. |
| **Stale Revision**: Evidence executed at older commit without current refutation becomes stale. | `ppm_reducer/criteria.py` lines 305–335 | `s02a`, `s06` | Outcome: `stale`, `reason_code: evidence_stale_revision`. Retains prior revision in `evidence_revision`. |
| **Evidence TTL Expiration (D-03 / ISS-012)**: Evidence at assessed revision older than `evidence_ttl_days` relative to `now` is stale. Strict boundary: `age > ttl_days`. | `ppm_reducer/trust.py` (`is_evidence_ttl_expired`); `criteria.py` lines 228–246 | `s26-ttl-expired` | Outcome: `stale`, `reason_code: evidence_stale_revision`. Tested at exact boundary second (fresh at 7d 0s, stale at 7d 1s). |
| **Unmapped Test Coverage**: Test passing outside baseline `verification_refs` cannot verify deliverable. | `ppm_reducer/criteria.py` lines 337–348 | `test_unmapped_pass_coverage_not_mapped` | Outcome: `unverified`, `reason_code: coverage_not_mapped`. |
| **Claims Only & Concurrent Conflict**: Claims never verify; conflicting claims preserved and exposed on `conflicting_claim_refs`. | `ppm_reducer/criteria.py` lines 135–144, 350–360 | `s16-concurrent-updates` | Outcome: `unverified`, `reason_code: claim_only`. Highest priority status chosen (`claimed_done`), conflict exposed on result. |
| **Draft-Only / Baseline Missing**: Absent approved baseline in domain DB, reducer returns empty criteria list. | `ppm_reducer/criteria.py` lines 61–63 | `s24-draft-only` | Outcome: returns `[]`. |

---

## 3. Environment and Pinned Dependencies
- **Base Commit**: `fc2bd1d` (main)
- **Branch**: `wp/PPM-M0-WP2`
- **Code Commit under Review**: `76640fd`
- **Python**: 3.12.13 (CPython via `uv`)
- **Node.js**: 26.7.0 LTS / npm 11.2.0
- **Dev Dependency Addition**:
  - `hypothesis>=6.115.0` (pinned `hypothesis==6.170.15` in `uv.lock`) for property testing.
  - Workspace lock verified via `uv sync --locked`.

---

## 4. Acceptance Evidence and Execution Results

### 4.1 Corpus Match: 18/18 Fixtures + s29 InputRejected
Command:
```bash
uv run pytest -s ppm-v2/reducer/tests/test_corpus.py -k test_corpus_summary_table
```
Real Output:
```text
======================================================================
Fixture ID                               | Criteria   | Status
----------------------------------------------------------------------
s01-claim-done-test-failed               | 1          | PASS (JCS match)
s02a-pass-old-revision-no-fail           | 1          | PASS (JCS match)
s02b-pass-old-revision-fail-current      | 1          | PASS (JCS match)
s03-arch-drift-no-adr                    | 1          | PASS (JCS match)
s06-renamed-evidence                     | 1          | PASS (JCS match)
s07-inferred-dependency                  | 1          | PASS (JCS match)
s08-shared-component                     | 2          | PASS (JCS match)
s10-missing-budget                       | 1          | PASS (JCS match)
s16-concurrent-updates                   | 1          | PASS (JCS match)
s19-partial-portfolio                    | 1          | PASS (JCS match)
s21-forged-junit                         | 1          | PASS (JCS match)
s22-waiver-expiry                        | 2          | PASS (JCS match)
s23-copied-repo                          | 1          | PASS (JCS match)
s24-draft-only                           | 0          | PASS (JCS match)
s25-active-waiver                        | 2          | PASS (JCS match)
s26-ttl-expired                          | 1          | PASS (JCS match)
s27-in-repo-fail                         | 1          | PASS (JCS match)
s28-ci-attested-pass                     | 1          | PASS (JCS match)
s29-cyclic-supersession                  | N/A        | PASS (InputRejected: supersession_cycle)
======================================================================
1 passed, 19 deselected in 0.25s
```

### 4.2 Determinism Verification: Identical SHA-256 Digest
Command:
```bash
uv run pytest ppm-v2/reducer/tests/test_determinism.py
```
Real Output:
```text
ppm-v2/reducer/tests/test_determinism.py ..........                      [100%]
10 passed in 0.24s
```
All runs with randomly shuffled claims, evidence, waivers, and criteria produced byte-for-byte identical RFC 8785 / JCS SHA-256 digests.

### 4.3 Runtime Audit Hook: Zero Forbidden System Events
Command:
```bash
uv run pytest tests/test_import_boundary.py -k test_ppm_reducer_runtime_audit_hook
```
Real Output:
```text
tests/test_import_boundary.py .                                          [100%]
1 passed, 7 deselected in 0.24s
```
Executing `assess_criteria` over the entire fixture corpus under `sys.addaudithook` intercepted 0 open, socket, subprocess, or os.* events.

### 4.4 Static Import Allowlist and Probe Failure Demonstration
Command (probe failure demonstration):
```bash
# In temporary file ppm-v2/reducer/ppm_reducer/probe.py:
# import shutil
# import builtins
# f = getattr(__builtins__, 'open')
uv run pytest tests/test_import_boundary.py -k test_ppm_reducer_allowlist_imports_and_calls
```
Real Output:
```text
FAILED tests/test_import_boundary.py::test_ppm_reducer_allowlist_imports_and_calls
AssertionError: Forbidden imports/calls detected in ppm_reducer:
  probe.py - Line 1: import shutil (not in allowlist)
  probe.py - Line 2: import builtins (not in allowlist)
  probe.py - Line 3: getattr on forbidden 'open'
```
After removing `probe.py`:
```text
tests/test_import_boundary.py .                                          [100%]
1 passed, 7 deselected in 0.22s
```

### 4.5 Standard Shared Check Suite
1. **Package Validation (SC-01)**:
   ```bash
   uv run python tools/validate_package.py
   ```
   Output: `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` (exit code 0)

2. **Negative Cases**:
   ```bash
   uv run python ppm-v2/tools/run_negative_cases.py
   ```
   Output: `PASS 5 templates, 20 cases` (exit code 0)

3. **Fixture Corpus Validation**:
   ```bash
   uv run python ppm-v2/tools/check_fixtures.py
   ```
   Output: `PASS 19 fixtures, 3 rollup` (exit code 0)

4. **Translation Check (SC-08)**:
   ```bash
   uv run python tools/check_translation.py
   ```
   Output: `SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.` (exit code 0)

5. **Pytest Full Suite**:
   ```bash
   uv run pytest -q
   ```
   Output: `91 passed in 1.82s` (53 platform tests in `tests/` + 38 reducer tests in `ppm-v2/reducer/tests/`)

6. **Code Quality and Linter**:
   ```bash
   uv run ruff check src tests ppm-v2/reducer migrations
   ```
   Output: `All checks passed!`

7. **Type Checking**:
   ```bash
   uv run mypy
   ```
   Output: `Success: no issues found in 57 source files`

8. **Web HUD Production Build**:
   ```bash
   npm --prefix web/hud run build
   ```
   Output: `dist/index.html 0.39 kB │ gzip: 0.27 kB`, `built in 541ms` (exit code 0)

9. **Cryptographic Checksums**:
   ```bash
   sha256sum -c SHA256SUMS.txt
   ```
   Output: All listed repository files reported `OK` (exit code 0)

---

## 5. Deviations and Open Issues
- **Deviations**: None. Full specification implemented per signed fixtures and owner review feedback (M2-1, M2-2, M2-3).
- **Open Issues / Fixture Addition Proposals**:
  - `ISS-NEW-1` (registered in M0-WP1): Structured representation of findings in `ppm-output.schema.json` for future contract minor.
  - `ISS-NEW-2` (registered in M0-WP1): Program and portfolio rollup contract standardization (PPM-C1).
  - `ISS-NEW-3` (Proposal for `s30` fixture): Waiver inactive conditions (`approved_at > now`, `expires_at == now`, missing/empty `approved_by`, null `approved_at`) falling back to evidence rules.
  - `ISS-NEW-4` (Proposal for `s31` fixture): Future evidence rejection (`evidence_from_future` when `observed_at > now`).
  - `ISS-NEW-5` (Proposal for `s32` fixture): Missing assessed revision when baseline present (`missing_assessed_revision` when `assessed_revision` is None/empty).

---

## 6. Recommendation and Rollback
- **Status**: `ready_for_review` (updated in `STATUS.md`).
- **Owner Action**: Review criterion reducer implementation and acceptance evidence; approve merge into main.
- **Rollback Procedure**: Revert branch `wp/PPM-M0-WP2` via `git checkout main && git branch -D wp/PPM-M0-WP2`. Reducer core is self-contained within `ppm-v2/reducer` and not yet wired into platform runtime (scheduled for P2a-WP4).
