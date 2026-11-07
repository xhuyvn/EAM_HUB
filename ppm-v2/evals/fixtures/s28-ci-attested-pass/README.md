# s28-ci-attested-pass

## Scenario Description
The project relies on trusted continuous integration pipeline results. Evidence `ev-ci-attested-001` has `trust_class: ci_attested` and records that mapped test `test_billing_webhook` passed at the assessed revision `rev-ci-001`.

## Decision Table Row Applied
- **Condition**: `Verifier/CI PASS mapped tests at exact revision, no FAIL`
- **Outcome**: `verified`

## Criterion Rationale
- `AC-001`:
  - **Status**: `verified`
  - **Reason Code**: `evidence_current_pass`
  - **Evidence Trust**: `ci_attested`
  - **Why**: Under contract §14.3, both `verifier_observed` and `ci_attested` are authoritative trust classes permitted to verify criteria. Because the CI attestation confirmed that the baseline mapped test passed at `rev-ci-001` with verifiable pipeline provenance, the criterion is verified.
