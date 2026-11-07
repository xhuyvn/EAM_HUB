# Acceptance Packet: PPM-M0-WP4 Rollup Rules

## 1. Milestone and Scope
- **Work Package**: `PPM-M0-WP4` Rollup rules (`ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3, `STATUS.md`).
- **Scope**:
  - Implement confidentiality-preserving portfolio rollup in `ppm-v2/reducer/ppm_reducer/rollup.py`.
  - Expose domain models in `ppm-v2/reducer/ppm_reducer/types.py` and `ppm-v2/reducer/ppm_reducer/rollup.py` (`RollupProgress`, `RollupResult`, `rollup_portfolio`).
  - Public export surface in `ppm-v2/reducer/ppm_reducer/__init__.py` (`__version__ = "0.3.0"`).
  - Enforce fail-closed authorization (M4-1):
    - `context.viewer_authorized_project_ids` is required; absent or not a list raises `InputRejected(reason="missing_authorization")`.
    - Empty authorization list raises `InputRejected(reason="no_authorized_projects")`.
    - Visibility is strictly `listed in portfolio AND in viewer_authorized_project_ids`.
    - `hidden_project_ids` is test metadata only and does not grant or deny anything by itself.
    - Adding or removing hidden projects leaves output byte-identical except `partial` (which transitions `False` -> `True` when unviewable projects exist).
  - Enforce single deliverable ownership (DG1-C5 / C24): count each deliverable once, attributed to its owner project; consumer projects reference shared components via `dependency_refs` and exclude them from their own progress denominator.
  - Reject duplicate ownership: if multiple visible projects claim the same deliverable ID, reject with `InputRejected(reason="deliverable_multiple_owners")`.
  - Enforce pre-computation authorization filter (C43 / s19): filter to authorized projects *before* any validation or aggregation computation; label `partial: true`; zero hidden project IDs, names, deliverable IDs, counts, or weights leaked in output or error messages.
  - Enforce cost/budget state handling (C11 / s10): any project with missing or unmeasured cost data sets `cost_state="unknown"` and `cost_usd=null` (never defaulted to 0).
  - Enforce percentage denominator rule: progress percentage is only emitted when backed by a positive `total_weight`; absent denominator -> `verified_weight=null`, `total_weight=null`, `percent=null`.
  - Test suites in `ppm-v2/reducer/tests/test_rollup.py`:
    - M4-1 fail-closed authorization tests (absent list, only hidden given, empty list, non-list, differential byte-invariance except partial).
    - Fixture corpus match: `s19-partial-portfolio` PASS post RFC 8785 JCS; `s08-shared-component` and `s10-missing-budget` strict XFAIL (`missing_authorization`) because their raw `inputs.json` lack `context.viewer_authorized_project_ids` (ISS-NEW-1); with remedy supplied, 3/3 match byte-for-byte post-JCS; outputs validate against `ppm-v2/evals/fixtures/rollup.schema.json`.
    - DG1-C5 / C24 verification: moving shared component criteria into consumer's baseline leaves consumer `total_weight` completely unchanged.
    - s19 / C43 confidentiality verification: grep every hidden identifier from serialized outputs (0 hits); differential testing showing adding/removing arbitrary hidden projects leaves output byte-identical.
    - Hypothesis property tests: random portfolio aggregation invariant (`total_weight` equals exact sum of distinct owner deliverable weights), schema validity, and order independence.
    - Determinism tests across random shuffles of inputs producing identical RFC 8785 JCS SHA-256 digests.
    - Runtime audit hook (`sys.addaudithook`) verifying zero forbidden I/O, network, subprocess, or os calls during rollup computation.
  - Import purity in `tests/test_import_boundary.py`: AST static analysis enforcing stdlib allowlist.

---

## 2. Rule to Code Location to Fixture Mapping

| Requirement / Rule | Code Location | Fixtures Covered | Observed Behavior |
|---|---|---|---|
| **M4-1 Fail-Closed Authorization**: `viewer_authorized_project_ids` required; absent or not a list -> `InputRejected(reason="missing_authorization")`. | `ppm_reducer/rollup.py` (`_filter_authorized`) lines 135–147 | `test_m4_1_absent_viewer_authorized_project_ids_rejected`, `test_m4_1_only_hidden_project_ids_given_rejected` | Rejects unauthenticated/missing authorization lists before examining portfolio projects. |
| **M4-1 Empty Authorization Rejection**: Empty `viewer_authorized_project_ids` -> `InputRejected(reason="no_authorized_projects")`. | `ppm_reducer/rollup.py` lines 149–153 | `test_m4_1_empty_viewer_authorized_project_ids_rejected` | Rejects empty authorization with `no_authorized_projects`. |
| **M4-1 Metadata Decoupling**: `hidden_project_ids` is test metadata only and does not grant or deny anything by itself. | `ppm_reducer/rollup.py` lines 156–166 | `test_m4_1_hidden_project_ids_does_not_grant_or_deny_by_itself` | Visibility is strictly `project_id in allow`. |
| **M4-1 Differential Invariance**: Adding/removing hidden projects leaves serialized output byte-identical except `partial`. | `ppm_reducer/rollup.py`, `test_rollup.py` | `test_m4_1_differential_hidden_projects_byte_identical_except_partial`, `test_property_adding_hidden_projects_is_invisible` | When unviewable projects are added, `partial` transitions `False` -> `True` while progress, weights, deliverables, and costs remain 100% byte-identical. |
| **Single-Ownership Counting (DG1-C5 / C24)**: Each deliverable counted once across owner project only; consumer projects referencing shared component do not inflate denominator. | `ppm_reducer/rollup.py` (`_criteria_units`, `_context_units`, `rollup_portfolio`) | `s08-shared-component` | Shared deliverable `DEL-GW-001` (weight 3.0) counted once in portfolio denominator (8.0 total weight, 62.5% progress); consumer A alone has denominator 2.0. |
| **DG1-C5 Invariance**: Moving shared component criteria into a consumer's baseline input leaves consumer `total_weight` unchanged. | `ppm_reducer/rollup.py` lines 169–177 | `test_dg1_c5_moving_shared_criteria_into_consumer_keeps_consumer_total_weight` | When consumer input includes copied criteria for `DEL-GW-001`, consumer rollup total_weight remains exactly 2.0; portfolio total_weight remains 8.0. |
| **Multiple Owner Rejection**: Two visible projects claiming same deliverable ID rejected before computation. | `ppm_reducer/rollup.py` lines 265–272 | `test_deliverable_claimed_by_two_visible_projects_is_rejected` | Raises `InputRejected(reason="deliverable_multiple_owners")`. |
| **Pre-Computation Authorization Filter (C43 / s19)**: Unauthorized projects excluded before computation; `partial: true`; zero leakage of hidden IDs, names, or counts. | `ppm_reducer/rollup.py` (`_filter_authorized`) | `s19-partial-portfolio` | Output contains `partial: true`, 2 authorized projects, 2 deliverables; 0 occurrences of hidden project IDs, names, or deliverables. |
| **Missing Cost State (C11 / s10)**: Missing or unmeasured cost data -> `cost_state="unknown"`, `cost_usd=null` (never defaulted to 0). | `ppm_reducer/rollup.py` (`_aggregate_cost`) lines 205–225 | `s10-missing-budget` | `cost_state="unknown"`, `cost_usd=null`, `budget_usd=null`. |
| **Explicit Denominator Rule**: Progress percentage always accompanied by explicit denominator; no denominator -> no percent. | `ppm_reducer/rollup.py` (`_aggregate_progress`) lines 179–202 | `test_all_waived_has_no_denominator_and_no_percent`, `test_no_deliverables_basis_none` | When all deliverables are waived or empty, `verified_weight=null`, `total_weight=null`, `percent=null`. |
| **Determinism & Order Independence**: Rollup output is completely independent of input order. | `ppm_reducer/rollup.py` (`math.fsum`, sorted keys) | `test_determinism_shuffled_inputs_identical_jcs_sha256`, `test_property_order_independent` | Shuffled criteria, projects, deliverables, and claims yield identical RFC 8785 JCS SHA-256 hashes across repeated seeds. |
| **Reducer Purity**: Zero I/O, zero network, zero subprocess, pure stdlib allowlist. | `ppm_reducer/rollup.py` | `test_rollup_runtime_audit_hook_clean`, `test_ppm_reducer_allowlist_imports_and_calls` | Audit hook intercepts 0 events across corpus; AST allowlist passes cleanly. |

---

## 3. Environment and Pinned Dependencies
- **Base Commit**: `02a9f74` (main)
- **Branch**: `wp/PPM-M0-WP4`
- **Code Commit under Review**: (this commit)
- **Python**: 3.12.13 (CPython via `uv`)
- **Node.js**: 26.7.0 LTS / npm 11.2.0

---

## 4. Rollup Fixture Corpus Summary Table

```text
================================================================================
Fixture ID                   | Raw (M4-1)     | With Remedy  | sha256(JCS) prefix
--------------------------------------------------------------------------------
s08-shared-component         | XFAIL(missing_authorization) | PASS         | 911538063d5dad02
s10-missing-budget           | XFAIL(missing_authorization) | PASS         | 0038745f2b608e91
s19-partial-portfolio        | PASS           | PASS         | d052357d7361ec57
================================================================================
```

---

## 5. Acceptance Evidence and Execution Results

### 5.1 Rollup Test Suite (61 passed, 2 xfailed)
Command:
```bash
uv run pytest -v ppm-v2/reducer/tests/test_rollup.py
```
Real Output:
```text
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs[s08-shared-component] XFAIL [  1%]
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs[s10-missing-budget] XFAIL [  3%]
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs[s19-partial-portfolio] PASSED [  4%]
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs_with_remedy[s08-shared-component] PASSED [  6%]
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs_with_remedy[s10-missing-budget] PASSED [  7%]
ppm-v2/reducer/tests/test_rollup.py::test_expected_rollup_matches_post_jcs_with_remedy[s19-partial-portfolio] PASSED [  9%]
ppm-v2/reducer/tests/test_rollup.py::test_rollup_corpus_summary_table PASSED [ 11%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_absent_viewer_authorized_project_ids_rejected PASSED [ 12%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_only_hidden_project_ids_given_rejected PASSED [ 14%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_empty_viewer_authorized_project_ids_rejected PASSED [ 15%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_non_list_viewer_authorized_project_ids_rejected PASSED [ 17%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_hidden_project_ids_does_not_grant_or_deny_by_itself PASSED [ 19%]
ppm-v2/reducer/tests/test_rollup.py::test_m4_1_differential_hidden_projects_byte_identical_except_partial PASSED [ 20%]
ppm-v2/reducer/tests/test_rollup.py::test_dg1_c5_moving_shared_criteria_into_consumer_keeps_consumer_total_weight PASSED [ 22%]
ppm-v2/reducer/tests/test_rollup.py::test_dg1_c5_dependency_refs_never_add_weight PASSED [ 23%]
ppm-v2/reducer/tests/test_rollup.py::test_dg1_c5_shared_owner_hidden_means_not_counted_and_not_leaked PASSED [ 25%]
ppm-v2/reducer/tests/test_rollup.py::test_deliverable_claimed_by_two_visible_projects_is_rejected PASSED [ 26%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_hidden_identifiers_absent_from_serialized_output PASSED [ 28%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[0-drop0] PASSED [ 30%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[0-drop1] PASSED [ 31%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[1-drop2] PASSED [ 33%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[1-drop3] PASSED [ 34%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[5-drop4] PASSED [ 36%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_differential_hidden_projects_byte_identical[25-drop5] PASSED [ 38%]
ppm-v2/reducer/tests/test_rollup.py::test_s19_hidden_projects_cannot_influence_output_or_errors PASSED [ 39%]
ppm-v2/reducer/tests/test_rollup.py::test_partial_flag_semantics PASSED  [ 41%]
ppm-v2/reducer/tests/test_rollup.py::test_no_authorized_project_rejected_without_detail PASSED [ 42%]
ppm-v2/reducer/tests/test_rollup.py::test_assessed_project_not_visible_uses_no_criteria PASSED [ 44%]
ppm-v2/reducer/tests/test_rollup.py::test_s10_missing_cost_is_unknown_never_zero PASSED [ 46%]
ppm-v2/reducer/tests/test_rollup.py::test_cost_state_matrix[patch0-known-4600.0-5200.0] PASSED [ 47%]
ppm-v2/reducer/tests/test_rollup.py::test_cost_state_matrix[patch1-unknown-None-5200.0] PASSED [ 49%]
ppm-v2/reducer/tests/test_rollup.py::test_cost_state_matrix[patch2-unknown-None-None] PASSED [ 50%]
ppm-v2/reducer/tests/test_rollup.py::test_cost_state_matrix[patch3-unknown-None-5200.0] PASSED [ 52%]
ppm-v2/reducer/tests/test_rollup.py::test_cost_state_matrix[patch4-known-4500.0-5000.0] PASSED [ 53%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_cost_rejected[-1.0] PASSED [ 55%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_cost_rejected[inf] PASSED [ 57%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_cost_rejected[100] PASSED [ 58%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_cost_rejected[True] PASSED [ 60%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[0] PASSED [ 61%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[-2.0] PASSED [ 63%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[None] PASSED [ 65%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[3] PASSED [ 66%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[nan] PASSED [ 68%]
ppm-v2/reducer/tests/test_rollup.py::test_invalid_visible_weight_rejected[False] PASSED [ 69%]
ppm-v2/reducer/tests/test_rollup.py::test_missing_portfolio_context_rejected PASSED [ 71%]
ppm-v2/reducer/tests/test_rollup.py::test_all_waived_has_no_denominator_and_no_percent PASSED [ 73%]
ppm-v2/reducer/tests/test_rollup.py::test_no_deliverables_basis_none PASSED [ 74%]
ppm-v2/reducer/tests/test_rollup.py::test_property_total_weight_is_sum_of_distinct_owner_deliverables PASSED [ 76%]
ppm-v2/reducer/tests/test_rollup.py::test_property_order_independent PASSED [ 77%]
ppm-v2/reducer/tests/test_rollup.py::test_property_adding_hidden_projects_is_invisible PASSED [ 79%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[1-s08-shared-component] PASSED [ 80%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[1-s10-missing-budget] PASSED [ 82%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[1-s19-partial-portfolio] PASSED [ 84%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[7-s08-shared-component] PASSED [ 85%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[7-s10-missing-budget] PASSED [ 87%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[7-s19-partial-portfolio] PASSED [ 88%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[42-s08-shared-component] PASSED [ 90%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[42-s10-missing-budget] PASSED [ 92%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[42-s19-partial-portfolio] PASSED [ 93%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[2026-s08-shared-component] PASSED [ 95%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[2026-s10-missing-budget] PASSED [ 96%]
ppm-v2/reducer/tests/test_rollup.py::test_determinism_shuffled_inputs_identical_jcs_sha256[2026-s19-partial-portfolio] PASSED [ 98%]
ppm-v2/reducer/tests/test_rollup.py::test_rollup_runtime_audit_hook_clean PASSED [100%]
======================== 61 passed, 2 xfailed in 1.11s ========================
```

### 5.2 Import Boundary and AST Purity
Command:
```bash
uv run pytest -v tests/test_import_boundary.py
```
Real Output:
```text
tests/test_import_boundary.py::test_ppm_reducer_allowlist_imports_and_calls PASSED [ 12%]
tests/test_import_boundary.py::test_allowlist_probe_fails[import shutil\nx = shutil.disk_usage('/')-shutil] PASSED [ 25%]
tests/test_import_boundary.py::test_allowlist_probe_fails[import builtins\nx = builtins.open('file.txt')-builtins] PASSED [ 37%]
tests/test_import_boundary.py::test_allowlist_probe_fails[import os\npid = os.getpid()-os] PASSED [ 50%]
tests/test_import_boundary.py::test_allowlist_probe_fails[import subprocess\nsubprocess.run(['ls'])-subprocess] PASSED [ 62%]
tests/test_import_boundary.py::test_allowlist_probe_fails[f = getattr(__builtins__, 'open')\nf('secret.txt')-open] PASSED [ 75%]
tests/test_import_boundary.py::test_allowlist_probe_fails[open('foo.txt', 'w')-open] PASSED [ 87%]
tests/test_import_boundary.py::test_ppm_reducer_runtime_audit_hook PASSED [100%]
============================== 8 passed in 0.15s ==============================
```

### 5.3 Shared Check Suite Execution
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
   Output: `239 passed, 33 skipped, 2 xfailed in 2.51s` (exit code 0) (155 reducer tests + 84 platform tests)

6. **Code Quality and Linter**:
   ```bash
   uv run ruff check src tests ppm-v2/reducer migrations
   ```
   Output: `All checks passed!` (exit code 0)

7. **Type Checking**:
   ```bash
   uv run mypy src tests ppm-v2/reducer
   ```
   Output: `Success: no issues found in 75 source files` (exit code 0)

8. **Web HUD Production Build**:
   ```bash
   npm --prefix web/hud run build
   ```
   Output: `dist/index.html 0.39 kB │ gzip: 0.27 kB`, `built in 393ms` (exit code 0)

9. **Cryptographic Checksums**:
   ```bash
   sha256sum -c SHA256SUMS.txt
   ```
   Output: All listed repository files reported `OK` (exit code 0)

---

## 6. Conformance Cases & Blocking Status
- **Conformance Cases Run**:
  - `DG1-C5`: Shared component deliverable counted once; consumer project does not inflate denominator.
  - `C11`: Missing cost/budget data produces `cost_state: "unknown"` and `cost_usd: null` (never 0).
  - `C24`: Two projects sharing component; portfolio verified weight counts deliverable once with mixed progress (62.5%).
  - `C43`: Viewer lacks permission for one project; total includes only authorized projects, labeled `partial: true`, zero leakage of hidden projects.
- **Blockers**: `ISS-NEW-1` (fixture defect in `s08` and `s10` inputs lacking `context.viewer_authorized_project_ids`). Requires owner fixture update.

---

## 7. Fixture Defect Proposal: ISS-NEW-1

### Description
Under review change M4-1, portfolio rollup authorization must fail closed:
`context.viewer_authorized_project_ids` is required; if absent, `rollup_portfolio` raises `InputRejected(reason="missing_authorization")`.

However, inspection of the existing signed fixture corpus reveals:
1. `ppm-v2/evals/fixtures/s08-shared-component/inputs.json` line 97:
   ```json
   "context": {
     "portfolio": { ... }
   }
   ```
   `viewer_authorized_project_ids` is absent.
2. `ppm-v2/evals/fixtures/s10-missing-budget/inputs.json` line 66:
   ```json
   "context": {
     "portfolio": { ... }
   }
   ```
   `viewer_authorized_project_ids` is absent.

Only `s19-partial-portfolio/inputs.json` includes `viewer_authorized_project_ids`.

Per instructions ("Do not edit fixtures; if a fixture lacks viewer_authorized_project_ids, stop and report it as ISS-NEW-n"), the fixtures were not modified. As a direct consequence, running `rollup_portfolio` on raw `s08/inputs.json` and `s10/inputs.json` raises `InputRejected(reason="missing_authorization")` and is marked `xfail` in `test_expected_rollup_matches_post_jcs`.

### Verification of Proposed Remedy
When `viewer_authorized_project_ids` is provided matching the expected authorized projects (`s08`: `["proj-consumer-a", "proj-consumer-b", "proj-shared-gw"]`; `s10`: `["proj-core-engine", "proj-ui-app"]`), `test_expected_rollup_matches_post_jcs_with_remedy` demonstrates that all 3/3 fixtures match `expected_rollup.json` 100% byte-for-byte post RFC 8785 JCS.

### Proposed Fix
Update `ppm-v2/evals/fixtures/s08-shared-component/inputs.json` and `s10-missing-budget/inputs.json` to include `"viewer_authorized_project_ids"` under `"context"`.
