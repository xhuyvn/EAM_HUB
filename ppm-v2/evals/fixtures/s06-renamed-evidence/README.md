# s06-renamed-evidence

## Scenario Description
A source file or test file was renamed or restructured in revision `rev-delta-002`. As a result, the baseline mapping `test_invoice_pdf` does not resolve at the new commit, even though it passed previously at `rev-delta-001`.

## Decision Table Row Applied
- **Condition**: `Evidence at old revision / TTL expired`
- **Outcome**: `stale`

## Criterion Rationale
- `AC-001`:
  - **Status**: `stale`
  - **Reason Code**: `evidence_stale_revision`
  - **Why**: When tests are renamed or deleted in source code, the reducer cannot assume that old evidence remains valid at the new commit. Because evidence from `rev-delta-001` does not resolve at `rev-delta-002`, the criterion loses its `verified` status and drops to `stale` with `acceptance_readiness: not_ready`.
