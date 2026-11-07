# s27-in-repo-fail

## Scenario Description
An in-repository test report `reports/junit-validation.xml` (`trust_class: reported_artifact`) records that mapped test `test_data_validation` failed at the assessed revision `rev-fail-001`.

## Decision Table Row Applied
- **Condition**: `FAIL (verifier, CI, or in-repo report) at exact revision`
- **Outcome**: `contradicted`

## Criterion Rationale
- `AC-001`:
  - **Status**: `contradicted`
  - **Reason Code**: `evidence_refuted`
  - **Evidence Trust**: `reported_artifact`
  - **Why**: Under contract §14.3, while `reported_artifact` evidence cannot verify a criterion (because the producer authored the report file), a `failed` result in any trust class at the exact assessed revision is conservatively accepted as an active refutation signal. The criterion is therefore refuted (`contradicted`).
