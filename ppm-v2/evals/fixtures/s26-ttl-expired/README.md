# s26-ttl-expired

## Scenario Description
Evidence `ev-ttl-old-001` was observed on `2026-11-05T10:00:00Z` at the current commit `rev-ttl-001`. However, the current evaluation time `now` is `2026-11-20T12:00:00Z` (15 days later), exceeding the parameterized `evidence_ttl_days` of 7 days (per architecture decision D-03).

## Decision Table Row Applied
- **Condition**: `Evidence at old revision / TTL expired`
- **Outcome**: `stale`

## Criterion Rationale
- `AC-001`:
  - **Status**: `stale`
  - **Reason Code**: `evidence_stale_revision`
  - **Why**: Even when code has not changed (assessed revision matches evidence revision), evidence cannot be trusted indefinitely without revalidation. Once the elapsed time `(now - observed_at)` exceeds `evidence_ttl_days`, the evidence expires and the criterion status drops from `verified` to `stale`.
