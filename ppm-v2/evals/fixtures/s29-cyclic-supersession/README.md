# s29-cyclic-supersession

## Scenario Description
The project contains updates with a mutual supersession loop: `upd-cycle-001` specifies `supersedes_update_id: upd-cycle-002`, and `upd-cycle-002` specifies `supersedes_update_id: upd-cycle-001`.

## Decision Table Row Applied
- **Error Condition**: Cyclic supersession rejection (PPM-05, Implementation Plan §3 M0-WP2).
- **Outcome**: `InputRejected`, reason: `supersession_cycle`.

## Criterion Rationale
- Under contract §6 and validator rules, supersession chains must form a directed acyclic graph (DAG). Cyclic correction chains prevent unambiguous resolution of active updates and would cause infinite loops in the reducer's supersession resolver. The reducer rejects the input with `InputRejected`.
