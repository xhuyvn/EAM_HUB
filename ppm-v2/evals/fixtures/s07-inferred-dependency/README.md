# s07-inferred-dependency

## Scenario Description
Static code analysis or scanner discovery infers a potential component dependency between `proj-epsilon` and `proj-shared-inventory` (`DEL-INV-001`). However, this relationship has not been confirmed or approved by the project owner.

## Decision Table Row Applied
- **Condition**: `Verifier/CI PASS mapped tests at exact revision, no FAIL`
- **Outcome**: `verified`

## Criterion Rationale
- `AC-001`:
  - **Status**: `verified`
  - **Reason Code**: `evidence_current_pass`
  - **Why**: Inferred dependencies are emitted as proposals (`relationship_proposed`). Unconfirmed proposals MUST NOT be added to confirmed `dependency_refs` and MUST NOT alter internal criterion verdicts. Because `test_order_creation` passed at `rev-epsilon-001`, `AC-001` is verified.
