# s10-missing-budget

## Scenario Description
A portfolio aggregates two projects: `proj-core-engine` and `proj-ui-app`. The project `proj-core-engine` has unrecorded or missing budget and cost figures.

## Decision Table Row Applied
- **Single-Project Assessment**: `Verifier/CI PASS mapped tests at exact revision, no FAIL` -> `verified`.
- **Rollup Aggregation**: Missing budget/cost rule (contract §5 & SCENARIO_MATRIX row 10).

## Rationale
- **Single-Project Assessment (`proj-core-engine`)**:
  - `AC-CORE-001` is verified based on passing test `test_engine_pipeline`. Single-project assessment focuses on deliverable criteria verification.
- **Rollup Aggregation (`expected_rollup.json`)**:
  - Because `proj-core-engine` has undefined cost/budget, the portfolio rollup MUST NOT default the missing cost to 0 (which would falsify financials).
  - The aggregate `cost_state` transitions to `"unknown"`, and `cost_usd` is set to `null`.
  - Deliverable progress is still computed independently (total weight 6.0, verified weight 6.0, 100%).
