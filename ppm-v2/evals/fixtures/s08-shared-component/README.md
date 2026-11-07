# s08-shared-component

## Scenario Description
A shared API gateway component (`DEL-GW-001`, owned by `proj-shared-gw`) is consumed by two distinct consumer applications (`proj-consumer-a` and `proj-consumer-b`). Each consumer references the component through `dependency_refs`.

## Decision Table Row Applied
- **Single-Project Assessment**: `Verifier/CI PASS mapped tests at exact revision, no FAIL` -> `verified`.
- **Rollup Aggregation**: Distinct-deliverable counting rule (contract §14.2 & blueprint §11/§19).

## Rationale
- **Single-Project Assessment (`proj-consumer-a`)**:
  - `AC-A-001`: `verified`, `reason_code: evidence_current_pass`.
  - The shared gateway deliverable `DEL-GW-001` is referenced in `dependency_refs` and is strictly excluded from `proj-consumer-a`'s own criterion list and progress denominator.
- **Rollup Aggregation (`expected_rollup.json`)**:
  - `distinct_deliverables_count` is 3 (`DEL-A-001`, `DEL-B-001`, `DEL-GW-001`).
  - `DEL-GW-001` is counted **exactly once** in the portfolio progress denominator (weight 3.0), despite being consumed by multiple projects. Total weight is 3.0 + 2.0 + 2.0 = 7.0.
