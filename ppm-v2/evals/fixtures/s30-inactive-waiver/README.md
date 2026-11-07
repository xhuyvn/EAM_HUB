# Scenario s30: Inactive Waiver Conditions (M2-1, ISS-015)

## Problem & Context
A waiver is only legally active when:
1. `approved_by` is present and non-empty.
2. `approved_at` is set and represents an instant at or before `now`.
3. `expires_at` is strictly in the future relative to `now` (`now < expires_at`).

If any of these conditions fails, the waiver is inactive and ignored by the reducer. The corresponding criterion falls back to standard evidence-based evaluation rules.

## Scenario Setup
Four criteria are defined in the baseline:
- `AC-001`: Associated with `waiver-inact-001` lacking `approved_by`. Mapped to `test_alpha` which passed via `verifier_observed` at assessed revision. Result: `verified` (`evidence_current_pass`), waiver ignored.
- `AC-002`: Associated with `waiver-inact-002` having null `approved_at`. Mapped to `test_beta` which failed via `verifier_observed` at assessed revision. Result: `contradicted` (`evidence_refuted`), waiver ignored.
- `AC-003`: Associated with `waiver-inact-003` having `approved_at` in the future (`2026-11-06T00:00:00Z` > `2026-11-05T12:00:00Z`). No evidence exists. Result: `unverified` (`evidence_missing`), waiver ignored.
- `AC-004`: Associated with `waiver-inact-004` where `expires_at == now` (`2026-11-05T12:00:00Z`). Boundary condition second: waiver has expired. No evidence exists. Result: `unverified` (`evidence_missing`), waiver ignored.

## Expected Outcome
All 4 waivers are inactive. All 4 criteria remain in the non-waived denominator (`total_weight = 4.0`). `AC-001` is verified (`verified_weight = 1.0`), `AC-002` is contradicted, making overall verification `contradicted` and readiness `not_ready`.
