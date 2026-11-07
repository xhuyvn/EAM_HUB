# s01-claim-done-test-failed

## Scenario Description
A producer session update claims that criterion `AC-001` is completed (`claimed_done`), but the platform verifier sandbox executes the mapped integration test `test_export_csv` at the exact assessed revision (`rev-alpha-001`) and the test fails.

## Decision Table Row Applied
- **Condition**: `FAIL (verifier, CI, or in-repo report) at exact revision`
- **Outcome**: `contradicted`

## Criterion Rationale
- `AC-001`:
  - **Status**: `contradicted`
  - **Reason Code**: `evidence_refuted`
  - **Why**: Self-reported claims cannot override observed test failures. The test `test_export_csv` was mapped in baseline revision 1 to criterion `AC-001`. The verifier executed this test at `rev-alpha-001` and recorded `result: failed`. Therefore, the claim is refuted, the criterion is contradicted, and `acceptance_readiness` is `not_ready`.
