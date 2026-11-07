# Acceptance Packet: PPM-M0-WP3 Findings and Progress

## 1. Milestone and Scope
- **Work Package**: `PPM-M0-WP3` Findings and progress (`ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3, `STATUS.md`).
- **Scope**:
  - Implement delivery-progress metrics, validity, top-level verification, acceptance readiness, and self-reported progress in `ppm-v2/reducer/ppm_reducer/progress.py`.
  - Implement domain findings detection in `ppm-v2/reducer/ppm_reducer/findings.py` covering `architecture_drift` (s03), `relationship_proposed` (s07), `identity_conflict` (s23), and `conflicting_claims` (s16).
  - Implement assessment assembly in `ppm-v2/reducer/ppm_reducer/assemble.py` combining criteria results, progress calculation, dependency resolution, evidence references, and findings detection into a full single-project assessment proposal conforming to `contracts/ppm-output.schema.json`.
  - Expose domain models and enums in `ppm-v2/reducer/ppm_reducer/types.py` (`ProgressResult`, `FindingResult`, `AssessmentResult`, `FindingKind`, `FindingSeverity`, `ProgressBasis`, `AcceptanceReadiness`, `Validity`, `Verification`).
  - Public export surface in `ppm-v2/reducer/ppm_reducer/__init__.py` (`__version__ = "0.2.0"`).
  - Test suites in `ppm-v2/reducer/tests/`:
    - `test_corpus.py`: 19/19 fixtures verified for findings post RFC 8785 JCS; 18/18 assessments matched post JCS + s29 InputRejected; schema validation (`contracts/ppm-output.schema.json`) and platform semantic rules (`eap.contracts.rules.semantic_checks`) verified for all produced assessments.
    - `test_determinism.py`: 14 fixtures verified for identical assessment and findings JCS SHA-256 digests across repeated runs with shuffled inputs (claims, evidence, waivers, criteria, dependencies).
    - `test_properties.py`: Hypothesis property tests asserting progress invariants, context decoupling (stripping context leaves criteria and progress 100% identical), and exclusion of relationship proposals from `dependency_refs`.
    - `test_reducer_rules.py`: unit tests for s24 draft-only progress/readiness, s25 active waiver denominator exclusion, and multi-finding emissions.
  - Import purity in `tests/test_import_boundary.py`:
    - AST static analysis enforcing pure stdlib allowlist (`dataclasses`, `datetime`, `enum`, `typing`, `re`, `json`, `hashlib`, `functools`, `itertools`, `collections`, `decimal`, `math`, `ppm_reducer`).
    - Runtime audit hook (`sys.addaudithook`) verifying zero forbidden I/O, network, subprocess, or os calls across the entire fixture corpus.

---

## 2. Rule to Code Location to Fixture Mapping

| Requirement / Rule | Code Location | Fixtures Covered | Observed Behavior |
|---|---|---|---|
| **Baseline-Weighted Progress**: `verified_weight / total_weight` where `total_weight` strictly excludes waived criteria. | `ppm_reducer/progress.py` (`calculate_progress`) | `s01`, `s03`, `s08`, `s10`, `s22`, `s25`, `s28` | Denominator is always present; waived criteria excluded from total and verified weights (e.g. `s25`: `verified_weight=2.0`, `total_weight=2.0`). |
| **Draft-Only Progress**: Absent approved baseline (s24), basis is `none`, weights are `null`, and counts are 0. | `ppm_reducer/progress.py` lines 36–45 | `s24-draft-only` | `progress.basis="none"`, `verified_weight=null`, `total_weight=null`, `criteria_total=0`, `criteria_with_current_evidence=0`. |
| **Acceptance Readiness**: `ready_for_review` only when every required non-waived criterion is verified; otherwise `not_ready`. | `ppm_reducer/progress.py` (`calculate_readiness`) | All 18 fixtures | Returns `ready_for_review` for `s03`, `s07`, `s08`, `s10`, `s19`, `s23`, `s25`, `s28`; all others return `not_ready`. |
| **Current Evidence Count**: Number of criteria with current evidence (`reason_code` in `evidence_current_pass`, `evidence_refuted`). | `ppm_reducer/progress.py` lines 61–63 | All 18 fixtures | Computed deterministically (e.g., `s01`: 1, `s02a`: 0, `s08`: 1, `s22`: 1). |
| **Assessment Validity & Verification**: Top-level validity (`current`, `stale`, `unknown`) and verification (`verified`, `partially_verified`, `claimed`, `contradicted`, `stale`, `unknown`). | `ppm_reducer/progress.py` (`calculate_validity`, `calculate_verification`) | All 18 fixtures | Contradicted evidence surfaces as `contradicted`; stale revision/TTL surfaces as `stale`. |
| **Self-Reported Progress**: Percent of un-superseded claims in `claimed_done` status over total claims. | `ppm_reducer/progress.py` (`calculate_reported_progress_percent`) | All 18 fixtures | `s16`: `50.0%` (1 done, 1 blocked); `s24`: `null`; single-done fixtures: `100.0%`. |
| **Domain Finding: Architecture Drift**: Implementation diverges from architectural blueprint without approved ADR. | `ppm_reducer/findings.py` lines 50–87 | `s03-arch-drift-no-adr` | Emits finding `architecture_drift` (`severity: review_required`, `target_ref: component:payment_service`). Criterion verdict remains `verified`. |
| **Domain Finding: Proposed Relationship**: Inferred component dependency awaiting owner approval. | `ppm_reducer/findings.py` lines 89–118 | `s07-inferred-dependency` | Emits finding `relationship_proposed` (`severity: info`, `target_ref: deliverable:DEL-INV-001`). Excluded from `dependency_refs`. |
| **Domain Finding: Identity Conflict**: Duplicate `project_id` observed across multiple distinct repositories. | `ppm_reducer/findings.py` lines 120–147 | `s23-copied-repo` | Emits finding `identity_conflict` (`severity: blocker`, `target_ref: project:proj-replicated`). |
| **Domain Finding: Conflicting Claims**: Concurrent updates in same snapshot submit divergent progress claims for same criterion. | `ppm_reducer/findings.py` lines 149–180 | `s16-concurrent-updates` | Emits finding `conflicting_claims` (`severity: warning`, `target_ref: criterion:AC-001`) with conflicting update IDs and statuses. |
| **Findings Decoupling**: Domain findings never mutate criterion verdicts or progress numbers. | `ppm_reducer/findings.py`, `assemble.py` | `test_property_findings_decoupled_from_criteria_and_progress` | Removing `context` from inputs leaves criteria and progress 100% byte-for-byte identical across all fixtures. |
| **Dependency Refs Exclusion**: Proposed relationship is excluded from confirmed `dependency_refs`. | `ppm_reducer/assemble.py` lines 112–120 | `s07-inferred-dependency`, `s08-shared-component` | In `s07`, `dependency_refs` is `[]`; confirmed shared component in `s08` is preserved. |

---

## 3. Environment and Pinned Dependencies
- **Base Commit**: `fab6cd3` (main)
- **Branch**: `wp/PPM-M0-WP3`
- **Code Commit under Review**: (this commit)
- **Python**: 3.12.13 (CPython via `uv`)
- **Node.js**: 26.7.0 LTS / npm 11.2.0

---

## 4. Excluded Fields in Corpus Equality Comparison
Per prompt and fixture specifications:
1. `criteria[].explanation`: Human-readable summary string authored in fixture `expected.json`; omitted during byte-for-byte RFC 8785 JCS regression comparison, but included during schema validation (`contracts/ppm-output.schema.json`) and semantic rules validation (`eap.contracts.rules.semantic_checks`).
2. `assessment_id`: Caller/pipeline-supplied identifier; supplied from `expected.json` during fixture regression run so all reducer-produced fields are compared directly.
All other fields (`contract_version`, `project_id`, `baseline_revision`, `snapshot_refs`, `validity`, `verification`, `acceptance_readiness`, `criteria` [verdicts, reason codes, evidence trust, revisions, weights, requirement, waiver refs], `dependency_refs`, `progress`, `reported_progress_percent`, `evidence_refs`, `missing_evidence`, `as_of`, `source_as_of`, `assessed_revision`, `previous_assessment_id`, `snapshot_seq`, `analysis_status`, `basis_seq`, `trigger`) are compared byte-for-byte post RFC 8785 JCS normalization.

---

## 5. Acceptance Evidence and Execution Results

### 5.1 Corpus Match: 19/19 Findings + 18/18 Assessments + s29 InputRejected
Command:
```bash
uv run pytest -s ppm-v2/reducer/tests/test_corpus.py -k test_corpus_summary_table
```
Real Output:
```text
==========================================================================================
Fixture ID                             | Criteria | Assessment   | Findings   | Status
------------------------------------------------------------------------------------------
s01-claim-done-test-failed             | 1        | PASS         | PASS       | PASS
s02a-pass-old-revision-no-fail         | 1        | PASS         | PASS       | PASS
s02b-pass-old-revision-fail-current    | 1        | PASS         | PASS       | PASS
s03-arch-drift-no-adr                  | 1        | PASS         | PASS       | PASS
s06-renamed-evidence                   | 1        | PASS         | PASS       | PASS
s07-inferred-dependency                | 1        | PASS         | PASS       | PASS
s08-shared-component                   | 2        | PASS         | PASS       | PASS
s10-missing-budget                     | 1        | PASS         | PASS       | PASS
s16-concurrent-updates                 | 1        | PASS         | PASS       | PASS
s19-partial-portfolio                  | 1        | PASS         | PASS       | PASS
s21-forged-junit                       | 1        | PASS         | PASS       | PASS
s22-waiver-expiry                      | 2        | PASS         | PASS       | PASS
s23-copied-repo                        | 1        | PASS         | PASS       | PASS
s24-draft-only                         | 0        | PASS         | PASS       | PASS
s25-active-waiver                      | 2        | PASS         | PASS       | PASS
s26-ttl-expired                        | 1        | PASS         | PASS       | PASS
s27-in-repo-fail                       | 1        | PASS         | PASS       | PASS
s28-ci-attested-pass                   | 1        | PASS         | PASS       | PASS
s29-cyclic-supersession                | N/A      | REJECTED     | PASS       | PASS (InputRejected: supersession_cycle)
==========================================================================================
```

### 5.2 Determinism Verification: Identical SHA-256 Digest
Command:
```bash
uv run pytest ppm-v2/reducer/tests/test_determinism.py
```
Real Output:
```text
ppm-v2/reducer/tests/test_determinism.py ..............                  [100%]
14 passed in 0.23s
```
All runs with randomly shuffled claims, evidence, waivers, criteria, and dependency refs produced identical RFC 8785 / JCS SHA-256 digests for both assessments and domain findings.

### 5.3 Schema Validation & EAP Platform Semantic Rules
Validated within `test_fixture_corpus_assessment`:
- Every produced assessment validated against `contracts/ppm-output.schema.json`.
- Every produced assessment passed `eap.contracts.rules.semantic_checks('ppm-output', assessment)`.
- Every produced finding validated against `ppm-v2/evals/fixtures/findings.schema.json`.

### 5.4 Property-Based and Purity Tests
Command:
```bash
uv run pytest ppm-v2/reducer/tests/test_properties.py ppm-v2/reducer/tests/test_reducer_rules.py
```
Real Output:
```text
ppm-v2/reducer/tests/test_properties.py ....                             [ 28%]
ppm-v2/reducer/tests/test_reducer_rules.py ..........                    [100%]
14 passed in 0.72s
```
Verified:
- Progress denominator strictly excludes waived criteria and verified weight <= total weight.
- Removing findings input context produces identical criteria and progress.
- `relationship_proposed` is excluded from confirmed `dependency_refs`.

### 5.5 Import Boundary and AST Purity
Command:
```bash
uv run pytest tests/test_import_boundary.py
```
Real Output:
```text
tests/test_import_boundary.py ........                                   [100%]
8 passed in 0.25s
```

### 5.6 Standard Shared Check Suite
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

4. **Translation Staleness Check (SC-08)**:
   ```bash
   uv run python tools/check_translation.py
   ```
   Output: `SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers.` (exit code 0)

5. **Pytest Full Suite**:
   ```bash
   uv run pytest -q
   ```
   Output: `170 passed in 1.67s` (exit code 0)

6. **Code Quality and Linter**:
   ```bash
   uv run ruff check src tests ppm-v2/reducer
   ```
   Output: `All checks passed!`

7. **Type Checking**:
   ```bash
   uv run mypy src tests ppm-v2/reducer
   ```
   Output: `Success: no issues found in 62 source files`

8. **Web HUD Production Build**:
   ```bash
   npm --prefix web/hud run build
   ```
   Output: `dist/index.html 0.39 kB │ gzip: 0.27 kB`, `built in 563ms` (exit code 0)

9. **Cryptographic Checksums**:
   ```bash
   sha256sum -c SHA256SUMS.txt
   ```
   Output: All 336 listed repository files reported `OK` (exit code 0)

---

## 6. Conformance Cases & Blocking Status
- **Conformance Cases Run**:
  - `s03-arch-drift-no-adr` (new: M0 only) -> finding `architecture_drift`, criteria unchanged (`verified`).
  - `s07-inferred-dependency` (C32) -> finding `relationship_proposed`, excluded from `dependency_refs`.
  - `s16-concurrent-updates` (C45) -> finding `conflicting_claims`, updates and statuses preserved.
  - `s23-copied-repo` (C42) -> finding `identity_conflict` (blocker).
  - `s24-draft-only` (C44) -> progress basis `none`, `acceptance_readiness: not_ready`.
  - `s25-active-waiver` (C41 variant) -> waiver excluded from denominator.
- **Blockers**: None. Work package PPM-M0-WP3 is fully verified and ready for review.
- **Next Work Package**: `PPM-M0-WP4` Rollup rules or `PPM-M0-WP5` Runner and packaging (upon completion of WP4).
