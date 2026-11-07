# s16-concurrent-updates

## Scenario Description
Two agents working concurrently submit updates in the same snapshot with conflicting claims for criterion `AC-001`: `upd-101` claims `claimed_done`, while `upd-102` claims `claimed_blocked`.

## Decision Table Row Applied
- **Condition**: `Claim only (update/note)`
- **Outcome**: `unverified / claim_only`

## Criterion Rationale
- `AC-001`:
  - **Status**: `unverified`
  - **Reason Code**: `claim_only`
  - **Why**: Under the multiple-writers contract (§10) and review decision table §2C, both concurrent claims are preserved in `reported_claim_refs` without last-write-wins. A domain finding `conflicting_claims` is emitted. Findings do not alter criterion status; because no independent verifier evidence exists, status remains `unverified`.
  - **CAS Boundary**: The concurrent acceptance API CAS logic (one reviewer wins, second receives HTTP 409 conflict) belongs to platform work package P2b-WP2 (marked `not_due` in `MAPPING.md`).
