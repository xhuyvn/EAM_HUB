# s22-waiver-expiry

## Scenario Description
Criterion `AC-002` was granted temporary waiver `waiver-wv-001` expiring on `2026-11-01T00:00:00Z`. The assessment is executed as of `now = 2026-11-05T12:00:00Z`.

## Decision Table Row Applied
- **Condition**: `Waiver expired` -> returns to denominator; evaluated under `Claim only / missing evidence` -> `unverified`.
- **Preceding Status**: `waived` (under unexpired waiver).

## Criterion Rationale
- `AC-001`:
  - **Status**: `verified`, weight 2.0.
- `AC-002`:
  - **Status**: `unverified`
  - **Reason Code**: `evidence_missing`
  - **Previous Status**: `waived`
  - **Waiver Ref**: `null`
  - **Why**: An unexpired waiver temporarily excludes a criterion from the total progress denominator (`total_weight`). However, once `now > expires_at`, the waiver becomes invalid. The criterion returns to the denominator (total weight increases from 2.0 to 3.0). Because no passing test evidence exists, `AC-002` transitions from `waived` to `unverified`, blocking acceptance readiness (`not_ready`).
