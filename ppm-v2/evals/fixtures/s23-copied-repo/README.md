# s23-copied-repo

## Scenario Description
A repository is copied or forked to start a new product, but retains the original `project_id: proj-replicated`. The platform collector observes `repo_identity: git@github.com:acme-corp/product-fork.git` differing from the registered repository identity `git@github.com:acme-corp/product-original.git`.

## Decision Table Row Applied
- **Condition**: `Verifier/CI PASS mapped tests at exact revision, no FAIL` -> `verified`.
- **Identity Resolution**: Finding `identity_conflict` (contract §14.4, review §4 PPM-06, C42).

## Criterion Rationale
- `AC-001`:
  - **Status**: `verified`
  - **Reason Code**: `evidence_current_pass`
  - **Why**: Identity conflicts between duplicate repositories are reported as domain findings (`identity_conflict`) and trigger platform quarantine during ingestion, but reducer evaluation of criterion progress for the scoped snapshot remains deterministic and independent. Because `test_data_export` passed at `rev-copy-001`, `AC-001` is verified.
