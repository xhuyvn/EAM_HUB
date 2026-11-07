# Scenario s31: Evidence from Future (M2-2, ISS-015)

## Problem & Context
Deterministic evaluation requires that all observed evidence timestamps belong to the past or the present relative to the evaluation instant `now`. An evidence artifact whose `observed_at` timestamp is strictly after `now` violates causality and indicates bad telemetry, clock skew, or data corruption.

## Scenario Setup
- Evaluation instant `now`: `2026-11-05T12:00:00Z`
- Target revision: `rev-fut-001`
- Evidence record `ev-future-001`: `observed_at` is `2026-11-06T12:00:00Z` (24 hours after `now`).

## Expected Outcome
The reducer rejects the input during initial verification with `InputRejected(reason="evidence_from_future")`.
No criteria are assessed, and `expected_error.json` reflects the rejection.
