# Scenario s32: Missing Assessed Revision with Baseline Present (M2-3, ISS-015)

## Problem & Context
When a project has an approved baseline with acceptance criteria, evaluation must target a specific code snapshot / commit revision (`assessed_revision`). Without an assessed revision, evidence staleness and exact-revision refutation cannot be computed. Therefore, `assessed_revision` cannot be null or empty when `baseline` is present.

Note that when no baseline exists (e.g. `s24-draft-only`), `assessed_revision` may be null or present, returning `progress.basis = "none"`. But once a baseline is approved in domain storage, omitting `assessed_revision` is an invalid invocation.

## Scenario Setup
- Baseline revision: 1 with criterion `AC-001`.
- `assessed_revision`: `null`.
- Evaluation instant `now`: `2026-11-05T12:00:00Z`.

## Expected Outcome
The reducer rejects the input with `InputRejected(reason="missing_assessed_revision")`.
`expected_error.json` records this rejection.
