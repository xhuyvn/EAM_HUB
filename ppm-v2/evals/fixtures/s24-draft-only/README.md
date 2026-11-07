# s24-draft-only

## Scenario Description
The project repository contains an `acceptance.yaml` file that is in draft mode. The project owner has not yet approved or established a formal baseline revision in the PPM domain database.

## Decision Table Row Applied
- **Condition**: `Baseline absent`
- **Outcome**: `provisional, progress.basis=none`

## Criterion Rationale
- **Overall Assessment**:
  - `baseline_revision`: `null`
  - `validity`: `unknown`
  - `verification`: `unknown`
  - `acceptance_readiness`: `not_ready`
- **Progress Object**:
  - `basis`: `none`
  - `verified_weight`: `null`
  - `total_weight`: `null`
  - **Why**: Under contract §14.5 and review §2C/§4 PPM-08, in-repo `acceptance.yaml` is merely a draft proposal. Progress percentage cannot be calculated without an approved baseline weighting. Assessment output reflects provisional status with `progress.basis=none`, and acceptance cannot proceed.
