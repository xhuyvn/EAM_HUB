# s08-shared-component

## Scenario Description
A shared API gateway component (`DEL-GW-001`, owned by `proj-shared-gw`, weight 3.0, verified) is consumed by two distinct consumer applications:
- `proj-consumer-a` owns `DEL-A-001` (weight 2.0, verified) and `DEL-A-002` (weight 1.0, waived under unexpired waiver `waiver-cons-a-001`).
- `proj-consumer-b` owns `DEL-B-001` (weight 2.0, contradicted by test failure) and `DEL-B-002` (weight 1.0, unverified claim).
Both consumers reference `DEL-GW-001` through `dependency_refs`.

## Decision Table Row Applied
- **Single-Project Assessment**:
  - `DEL-A-001`: `Verifier PASS mapped tests at exact revision` -> `verified`.
  - `DEL-A-002`: `Approved waiver unexpired` -> `waived` (excluded from denominator).
- **Rollup Aggregation**: Distinct-deliverable counting and mixed progress rule (M1-3).

## Rationale
- **Single-Project Assessment (`proj-consumer-a`)**:
  - `AC-A-001`: `verified`, weight 2.0.
  - `AC-A-002`: `waived`, weight 1.0. Excluded from `total_weight` and `verified_weight`.
  - `dependency_refs`: `DEL-GW-001` is observed verified and excluded from the consumer's own criteria.
  - Progress: 2.0 / 2.0 (100%).
- **Rollup Aggregation (`expected_rollup.json`)**:
  - `distinct_deliverables_count` is 5 (`DEL-A-001`, `DEL-A-002`, `DEL-B-001`, `DEL-B-002`, `DEL-GW-001`).
  - Shared deliverable `DEL-GW-001` is counted **exactly once** in the portfolio denominator (weight 3.0), despite being consumed by multiple projects.
  - The portfolio rollup is **mixed**: contains verified (`DEL-GW-001`, `DEL-A-001`), waived (`DEL-A-002`), contradicted (`DEL-B-001`), and unverified (`DEL-B-002`) deliverables.
  - Waived deliverable `DEL-A-002` (weight 1.0) is excluded from the progress denominator.
  - Denominator: `total_weight` is `3.0 + 2.0 + 2.0 + 1.0 = 8.0`.
  - Numerator: `verified_weight` is `3.0 + 2.0 = 5.0`.
  - `percent` is `5.0 / 8.0 * 100 = 62.5%` (demonstrating observable non-100% progress and denominator exclusion).
