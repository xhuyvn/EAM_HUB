# Scenario s33: All Criteria Waived (M3-1, ISS-019)

## Problem & Context
When every criterion in an approved baseline is covered by an active, unexpired waiver:
1. Waived criteria are excluded from the progress denominator.
2. Because no non-waived criteria remain, the denominator is empty: `verified_weight = null` and `total_weight = null` under `basis = "baseline_weighted"`.
3. Because zero criteria are verified, top-level verification is `"unknown"` (never `"verified"` or `"partially_verified"`).
4. Because verification is not `"verified"`, acceptance readiness is `"not_ready"`.

This behavior complies with contract `ppm-output.schema.json` and platform semantic checks in `eap.contracts.rules` (ISS-019 resolution).

## Scenario Setup
- Baseline revision: 1 with criteria `AC-001` (weight 2.0) and `AC-002` (weight 3.0).
- Both criteria have active, unexpired waivers (`waiver-all-001` and `waiver-all-002`) expiring after `now`.
- Evaluation instant `now`: `2026-11-05T12:00:00Z`.

## Expected Outcome
- `criteria[0].status = "waived"`, `reason_code = "waiver_approved"`, `waiver_ref = "waiver-all-001"`.
- `criteria[1].status = "waived"`, `reason_code = "waiver_approved"`, `waiver_ref = "waiver-all-002"`.
- `progress.basis = "baseline_weighted"`, `progress.verified_weight = null`, `progress.total_weight = null`.
- `progress.criteria_total = 2`, `progress.criteria_with_current_evidence = 0`.
- `verification = "unknown"`, `acceptance_readiness = "not_ready"`.
- Output passes `contracts/ppm-output.schema.json` and `eap.contracts.rules.check_ppm_output_rules`.
