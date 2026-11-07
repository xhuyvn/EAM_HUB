# s25-active-waiver

## Scenario Description
Criterion `AC-002` has an active, approved waiver `waiver-act-001` with `expires_at: 2026-12-01T00:00:00Z` (future relative to evaluation time `now: 2026-11-05T12:00:00Z`). Criterion `AC-001` has passing verifier evidence at the assessed revision.

## Decision Table Row Applied
- **Condition**: `Approved waiver unexpired`
- **Outcome**: `waived` (excluded from denominator)

## Criterion Rationale
- `AC-001`:
  - **Status**: `verified`, `reason_code: evidence_current_pass`, weight: 2.0.
- `AC-002`:
  - **Status**: `waived`
  - **Reason Code**: `waiver_approved`
  - **Waiver Ref**: `waiver-act-001`
  - **Why**: Under platform ADR-024 and contract §14.6, an active approved waiver excludes the criterion from `total_weight` and `verified_weight`, while counting as satisfied for `acceptance_readiness` (`ready_for_review`). The total weight denominator is 2.0 (excluding AC-002's weight 1.0), yielding 2.0 / 2.0 (100%) progress.
