# Fixture to Scenario and Conformance Case Mapping

This document maps each fixture in the `ppm-v2/evals/fixtures/` corpus to its corresponding scenario in `ppm-v2/evals/SCENARIO_MATRIX.md`, the platform conformance case in `evals/CONFORMANCE_CASES.md`, the consuming work package in PPM-M0, and whether explicit owner sign-off is required.

## Fixture Mapping Table

| Fixture ID | Scenario # | Conformance Case | Consuming WP | Owner Sign-Off Required | Scenario Description & Core Outcome |
|---|---|---|---|---|---|
| `s01-claim-done-test-failed` | 1 | C08, C31 | M0-WP2, P2a-WP4 | **YES** | Checklist done in update, but verifier re-run at assessed revision failed. Outcome: `contradicted`, `reason_code: evidence_refuted`. |
| `s02a-pass-old-revision-no-fail` | 2 (split A) | C31 | M0-WP2 | no | Test passed at older commit `rev-r1`, code changed to `rev-r2`, no current test run. Outcome: `stale`, `reason_code: evidence_stale_revision`. |
| `s02b-pass-old-revision-fail-current` | 2 (split B) | C31 | M0-WP2 | no | Test passed at older commit `rev-r1`, code changed to `rev-r2`, verifier re-run at `rev-r2` failed. Outcome: `contradicted`, `reason_code: evidence_refuted`. |
| `s03-arch-drift-no-adr` | 3 | new: M0 only | M0-WP3 | no | Implementation deviates from architectural blueprint without approved ADR. Outcome: finding `architecture_drift`; criterion verdict remains unchanged (`verified`). |
| `s06-renamed-evidence` | 6 | new: M0 only | M0-WP2 | no | Source file / mapped test renamed or deleted, evidence ref does not resolve at current revision. Outcome: `stale`, `reason_code: evidence_stale_revision`. |
| `s07-inferred-dependency` | 7 | C32 | M0-WP3 | no | Code import suggests dependency, but relationship unconfirmed by owner. Outcome: finding `relationship_proposed`; excluded from confirmed `dependency_refs`. |
| `s08-shared-component` | 8 | C24 | M0-WP4, P4-WP1 | no | Shared component deliverable consumed by multiple projects. Rollup aggregates distinct deliverables and counts shared deliverable once. |
| `s10-missing-budget` | 10 | C11 | M0-WP4 | no | Project in portfolio has missing or unmeasured budget/cost. Outcome: `cost_state: "unknown"`, `cost_usd: null` (never defaulted to 0). |
| `s16-concurrent-updates` | 16 | C45 (acceptance CAS not_due: P2b-WP2) | M0-WP2 | no | Two concurrent updates submit divergent progress claims. Outcome: both preserved in `reported_claim_refs`, finding `conflicting_claims`; criterion verdict follows evidence. |
| `s19-partial-portfolio` | 19 | C43 | M0-WP4, P4-WP1 | **YES** | Viewer lacks permission to see one project in portfolio. Outcome: `partial: true`; output contains zero hidden project IDs, names, or weights. |
| `s21-forged-junit` | Review add | C40 | M0-WP2 | **YES** | Producer commits forged JUnit report in repo claiming PASS. Outcome: in-repo report cannot verify -> `unverified`, `reason_code: reported_artifact_only`, `evidence_trust: reported_artifact`. |
| `s22-waiver-expiry` | Review add | C41 | M0-WP2 | no | Previously waived criterion passes its `expires_at` timestamp. Outcome: waiver expired -> criterion returns to denominator, evaluated against evidence (`unverified`, `reason_code: evidence_missing`). |
| `s23-copied-repo` | Review add | C42 | M0-WP3 | no | Cloned/copied repository retains original `project_id`. Outcome: finding `identity_conflict`; single-project criterion evaluation proceeds without mutating verdicts. |
| `s24-draft-only` | Review add | C44 | M0-WP3 | no | Project has `acceptance.yaml` in draft without approved baseline in domain DB. Outcome: `progress.basis: "none"`, `baseline_revision: null`, `acceptance_readiness: "not_ready"`. |

## Notes on Special Cases
- **s02 Split**: Scenario #2 is split into `s02a` (stale without refutation) and `s02b` (contradicted with active failure at current revision) to evaluate both branches deterministically.
- **s16 CAS Boundary**: `s16-concurrent-updates` covers claims preservation and finding emission (`conflicting_claims`). Concurrent acceptance CAS (one wins, one returns 409 conflict) is marked `not_due` as it belongs to P2b-WP2 (Acceptance API).
- **Owner Sign-off**: Scenarios `s01`, `s21`, and `s19` require explicit owner sign-off per Implementation Plan §3 because they establish critical trust boundaries (active refutation, in-repo artifact trust boundary, and confidentiality-preserving rollups).
