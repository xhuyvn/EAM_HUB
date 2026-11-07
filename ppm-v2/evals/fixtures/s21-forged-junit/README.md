# s21-forged-junit

## Scenario Description
A producer (coding agent or developer) commits an update claiming that `AC-001` is done, pointing to an in-repository test report `reports/junit-security.xml` showing a 100% test pass. No verifier sandbox execution or attested CI run exists.

## Decision Table Row Applied
- **Condition**: `In-repo report file only (even if PASS)`
- **Outcome**: `unverified / reported_artifact_only`

## Criterion Rationale
- `AC-001`:
  - **Status**: `unverified`
  - **Reason Code**: `reported_artifact_only`
  - **Evidence Trust**: `reported_artifact`
  - **Why**: Under threat model finding PPM-01 and platform ADR-023, files within the repository are written by the producer and are therefore not independent evidence. Only `verifier_observed` (tests rerun in an isolated sandbox) or `ci_attested` (cryptographically verified CI provenance) can verify a criterion. A test report file in the repo is treated as an unverified claim, preventing forged reports from verifying unexecuted deliverables.
