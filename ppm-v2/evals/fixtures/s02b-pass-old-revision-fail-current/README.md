# s02b-pass-old-revision-fail-current

## Scenario Description
The mapped test `test_auth_login` previously passed at commit `rev-beta-001`. The repository was modified to `rev-beta-002`. The verifier re-executed `test_auth_login` at `rev-beta-002` and the test failed.

## Decision Table Row Applied
- **Condition**: `FAIL (verifier, CI, or in-repo report) at exact revision`
- **Outcome**: `contradicted`

## Criterion Rationale
- `AC-001`:
  - **Status**: `contradicted`
  - **Reason Code**: `evidence_refuted`
  - **Why**: While old passing evidence existed at `rev-beta-001`, active failure evidence `ev-verifier-current` was observed at the exact assessed revision `rev-beta-002`. An active failure at current revision refutes the criterion regardless of historical passes.
