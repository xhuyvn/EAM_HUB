# Acceptance Packet: PPM-M0-WP1 Fixture Corpus

## 1. Milestone and Scope
- **Work Package**: `PPM-M0-WP1` Fixture corpus (`ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3).
- **Scope**:
  - Hand-written, deterministic fixture corpus defining the PPM reducer's correct behavior across all mandatory scenarios prior to any reducer implementation.
  - Exactly 19 scenario fixtures under `ppm-v2/evals/fixtures/<id>/`:
    1. `s01-claim-done-test-failed`
    2. `s02a-pass-old-revision-no-fail` (split: stale)
    3. `s02b-pass-old-revision-fail-current` (split: contradicted)
    4. `s03-arch-drift-no-adr` (with `expected_findings.json`)
    5. `s06-renamed-evidence`
    6. `s07-inferred-dependency` (with `expected_findings.json`)
    7. `s08-shared-component` (includes mixed `expected_rollup.json`: 62.5% progress, 8.0 denominator)
    8. `s10-missing-budget` (includes `expected_rollup.json`: `cost_state: unknown`, `cost_usd: null`)
    9. `s16-concurrent-updates` (with `expected_findings.json`; acceptance CAS marked `not_due`)
    10. `s19-partial-portfolio` (includes `expected_rollup.json`: `partial: true`, zero leakage)
    11. `s21-forged-junit`
    12. `s22-waiver-expiry`
    13. `s23-copied-repo` (with `expected_findings.json`)
    14. `s24-draft-only`
    15. `s25-active-waiver` (unexpired approved waiver -> `waived`, excluded from `total_weight`)
    16. `s26-ttl-expired` (evidence older than `evidence_ttl_days` relative to `now` -> `stale`)
    17. `s27-in-repo-fail` (`reported_artifact` FAIL at exact revision -> `contradicted`)
    18. `s28-ci-attested-pass` (`ci_attested` PASS at revision -> `verified`)
    19. `s29-cyclic-supersession` (`expected_error.json`: `{"error": "InputRejected", "reason": "supersession_cycle"}`)
  - Internal M0 schemas and documentation:
    - `ppm-v2/evals/fixtures/SCHEMA.md` and `inputs.schema.json`.
    - `ppm-v2/evals/fixtures/ROLLUP_SCHEMA.md` and `rollup.schema.json`.
    - `ppm-v2/evals/fixtures/FINDINGS_SCHEMA.md` and `findings.schema.json`.
    - `ppm-v2/evals/fixtures/MAPPING.md` (including M0-WP2 import-boundary hardening note).
  - Fixture validation tool:
    - `ppm-v2/tools/check_fixtures.py` validating inputs, single-project expected outputs, expected errors, expected findings, rollup expected outputs, cross-checks (`evidence_trust` consistency, `verified` provenance, waiver expiration), cross-references with `MAPPING.md`, and confidentiality checks.
    - Integrated into `scripts/ci.ps1` and `.github/workflows/ci.yml`.

## 2. Environment and Dependencies
- **Base Commit**: `70ea3bd9a106fc00c6e83d918a0f25bba39ff2a4` (main)
- **Branch**: `wp/PPM-M0-WP1`
- **Corpus Code Commit**: `ec98866` (updates `5fa4eca` with M1-1..M1-4 changes_requested resolutions)
- **Python**: 3.12.13 (CPython via `uv`)
- **Node.js**: 26 / npm 11.2.0
- **Dependencies**: `jsonschema>=4.23,<5`, `PyYAML>=6.0.2,<7` (pinned via `ppm-v2/tools/requirements.txt`)

## 3. Acceptance Criteria and Observed Evidence

| Criterion | Expected | Observed Result | Evidence |
|---|---|---|---|
| AC-M0-WP1-1: Fixture validation tool | All 19 fixtures and 3 rollups pass validation | `PASS 19 fixtures, 3 rollup` | `uv run python ppm-v2/tools/check_fixtures.py` |
| AC-M0-WP1-2: Minimal clean venv execution | Runs with only `ppm-v2/tools/requirements.txt` | `PASS 19 fixtures, 3 rollup` | Isolated clean venv test |
| AC-M0-WP1-3: Broken copy error detection | Detects dropped required fields and leaked project IDs | Detected schema error on dropped `project_id` and leaked `proj-secret-analytics` (exit code 1) | Synthetic broken copy test |
| AC-M0-WP1-4: Cross-check failure on forged verification | Mutating `s21` to `verified` fails validation | Detected `evidence_trust` mismatch and missing `verifier_observed`/`ci_attested` passing evidence (exit code 1) | `check_fixtures.py` cross-check |
| AC-M0-WP1-5: Mixed rollup demonstration | Observable denominator and non-100% progress | `s08` rollup shows `percent: 62.5%` with explicit `total_weight: 8.0` (excluding 1.0 waived deliverable) | `s08-shared-component/expected_rollup.json` |
| AC-M0-WP1-6: Package validation (SC-01) | PASS all schemas and fixtures | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` | `uv run python tools/validate_package.py` |
| AC-M0-WP1-7: PPM negative cases | PASS templates and negative cases | `PASS 5 templates, 20 cases` | `uv run python ppm-v2/tools/run_negative_cases.py` |
| AC-M0-WP1-8: Translation check (SC-08) | PASS staleness report | `SC-08 Report Summary: 1 up-to-date, 37 stale, 0 missing headers` (exit code 0) | `uv run python tools/check_translation.py` |
| AC-M0-WP1-9: Python test suite (SC-02) | All 9 tests pass | `9 passed in 0.38s` | `uv run pytest -q` |
| AC-M0-WP1-10: Code linting & typechecking | Zero errors across platform & tooling | `All checks passed!`, `Success: no issues found in 21 source files` | `uv run ruff check`, `uv run mypy` |
| AC-M0-WP1-11: Web HUD build | Vite build succeeds | `dist/index.html 0.39 kB`, `built in 525ms` | `npm --prefix web/hud run build` |
| AC-M0-WP1-12: SHA256 checksums | All files match regenerated checksums in `<hash> *<path>` format | All listed files `OK` | `sha256sum -c SHA256SUMS.txt` |

## 4. Fixtures Requiring Owner Sign-Off (§3)

Per `ppm-v2/docs/IMPLEMENTATION_PLAN.md` §3, the owner must review and sign off on three critical fixtures:

1. **`s01-claim-done-test-failed`**:
   - *Rationale*: Establishes that active independent verifier refutation strictly overrides self-reported completion claims, transitioning criterion status to `contradicted` and blocking acceptance readiness.
2. **`s21-forged-junit`**:
   - *Rationale*: Enforces the PPM threat model (PPM-01 / ADR-023) ensuring that producer-authored in-repo JUnit reports cannot verify deliverables, yielding `unverified / reported_artifact_only`.
3. **`s19-partial-portfolio`**:
   - *Rationale*: Verifies confidentiality protection in multi-project rollups (C43), guaranteeing zero information leakage (zero IDs, names, or weights) of unauthorized projects in partial aggregations.

## 5. Open Questions and Contract Proposals

- **ISS-NEW-1: Representation of Findings in Assessment Contract**:
  - *Context*: `contracts/ppm-output.schema.json` defines `additionalProperties: false` and currently lacks a top-level `findings` list. Structural findings (`architecture_drift`, `relationship_proposed`, `identity_conflict`, `conflicting_claims`) are recorded within criterion `explanation` strings, structured in M0 internal `expected_findings.json` (`findings.schema.json`), and emitted as platform events.
  - *Proposal*: In a future contract minor (e.g. 1.2 or 2.2), introduce a structured `findings: [{kind, severity, target_ref, criterion_ids, details}]` property directly to `ppm-output.schema.json` so the HUD can render findings natively without string parsing.
- **ISS-NEW-2: Program and Portfolio Rollup Contract Standardization (PPM-C1)**:
  - *Context*: Rollup fixtures `s08`, `s10`, and `s19` use `rollup.schema.json` internal to M0.
  - *Proposal*: Standardize the portfolio contract during PPM-C1 prior to platform work package P4-WP1.

## 6. Unresolved Gaps
- None within scope of PPM-M0-WP1.

## 7. Recommendation and Rollback
- **Status**: `ready_for_review` (recorded in `STATUS.md`).
- **Owner Action**: Review fixture corpus and sign off on `s01`, `s21`, and `s19` `expected.json`.
- **Rollback Procedure**: Discard branch `wp/PPM-M0-WP1` via `git checkout main && git branch -D wp/PPM-M0-WP1` (additive file additions only; zero modifications to platform contracts or core source files).
