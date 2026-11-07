# s02a-pass-old-revision-no-fail

## Scenario Description
The mapped test `test_auth_login` passed at commit `rev-beta-001`. Subsequently, code modifications advanced the commit to `rev-beta-002`. No test execution has occurred at `rev-beta-002`, and no failures were observed.

## Decision Table Row Applied
- **Condition**: `Evidence at old revision / TTL expired`
- **Outcome**: `stale`

## Criterion Rationale
- `AC-001`:
  - **Status**: `stale`
  - **Reason Code**: `evidence_stale_revision`
  - **Why**: Verification evidence does not survive code revision changes without verification mapping proving independence. Because `assessed_revision` is `rev-beta-002` but evidence is pinned to `rev-beta-001`, the criterion transitions from `verified` to `stale`. In the absence of an active failure, it is marked `stale` rather than `contradicted`.
