# Runtime, persistence and action controls
## Request admission
Authenticate → resolve tenant/principal → authorize task type and resource refs → validate input schema → resolve approved agent release and binding → reserve task-tree budget → create task/run + dispatch intent/outbox transaction → worker claim lease → adapter submit → persist external execution ID → events/results → domain verification.
Public request bodies do not accept tenant/user/permission claims. Internal envelopes are constructed by the gateway from credentials. Scopes can only be narrowed upon delegation. Parent cancellation stops child tasks according to policy and waits for confirmation or reconciliation.

## State transitions
This table serves as the authoritative source; `tools/validate_package.py` (ALLOWED) validates fixtures against this exact table. All state transitions are recorded by Run Service within the same transaction emitting the outbox event `run.state_changed`.

| Run state | Event/command | Next | Condition |
|---|---|---|---|
| queued | dispatch claimed | dispatching | lease + fencing token + capability + budget valid |
| queued | cancel | cancelled | no dispatch intent yet |
| dispatching | external ID confirmed | running | provider acceptance persisted with execution_ref |
| dispatching | provider rejects explicitly (validation/auth) before acceptance | failed | deterministic response, no execution_ref |
| dispatching | journal proves submit not yet sent + cancel | cancelled | dispatch intent in `prepared` state |
| dispatching | response ambiguous / ack timeout / worker crash after send | unknown | journal `sent`, no execution_ref |
| running | action proposal | waiting_approval | immutable action digest + expiry |
| waiting_approval | authorized decision | running | reauthorize and consume approval |
| running | output validated | succeeded | result schema pass; domain verification queued |
| running | provider failure confirmed | failed | provider status/terminal event |
| running/waiting_approval | cancel requested | cancelling | adapter certified cancel or operator workflow |
| running/waiting_approval | deadline | timed_out | issue stop request, continue tracking remote outcome |
| cancelling | provider stop confirmed | cancelled | confirmation evidence |
| cancelling | provider completes prior to stop | succeeded/failed | persist actual result, cancel command → completed with note |
| cancelling | cancel ack lost | unknown | cannot determine if provider stopped |
| unknown | verified provider status | running/waiting_approval/succeeded/failed/cancelled | authoritative observation by operation_key/execution_ref |

Terminal states (succeeded, failed, cancelled, timed_out) do not resurrect. Transitions `unknown → queued` do not exist: retrying an unknown run creates a new attempt, permitted only after reconciliation determines remote does not exist or produced no side effects, or operator accepts risk via audited decision. Deadlines while runs are `unknown`/`cancelling` do not transition to timed_out; an attention item is generated because platform cannot assert remote has halted.

### Dispatch journal and lost-ack/restart/cancel sequence
Dispatch journal (`dispatch_intents` table) enforces unique key `(binding_id, run_id, operation='submit')` and states `prepared → sent → acknowledged | ambiguous`. `idempotency_key` delivered to adapter = sha256(binding_id|run_id|'submit'), stable across worker restarts. Leases possess monotonically increasing `fencing_token`; gateway and result ingestion reject stale tokens.

1. Worker A claims lease (token 7), writes intent `prepared`, commits, calls adapter.submit, writes `sent` prior to byte transmission (or upon CLI invocation).
2. Provider receives task; acknowledgement is lost. Worker A crashes.
3. Lease expires; worker B claims (token 8). Discovers intent `sent` lacking execution_ref → run `unknown`, avoids issuing fresh submit.
4. Worker B invokes `adapter.lookup(operation_key)` or `status` by idempotency key. Upon result → persists execution_ref, `unknown → running`. If adapter lacks lookup support → retains `unknown`, creates attention item "remote outcome unknown", recommending operator review.
5. User clicks cancel during unknown state: command `accepted`, records intent; HUD renders "Cancel requested — remote outcome unknown". Following reconciliation: running → cancelling → cancelled (with evidence), or if remote completed records actual succeeded/failed and completes command with note.
6. Delayed results from worker A (token 7) are rejected by fencing; payload stored as diagnostic artifact.

Acceptance for this sequence: exactly one external submission at provider mock (counted by operation_key), attempt history intact, sequential `run.state_changed` events as above, HUD row exhibits `remote_outcome=unknown` during intervals 2–4 (conformance C05, C06, C21).

### Dual keys: dispatch key and business-intent key
Following the principle of idempotent APIs with client request tokens (AWS Builders' Library, "Making retries safe with idempotent APIs", accessed 06/11/2026), every business intent requires a caller-issued identifier stable across retries.

| Key | Issuer | Formula | Scope of stability | Purpose |
|---|---|---|---|---|
| `task_intent_key` | Task Service at admission | sha256(tenant_id\|task_id) | All attempts of task | Delivered to provider with client token (e.g. branch/PR marker), preventing attempt from creating secondary resource |
| `idempotency_key` (dispatch) | Run Service | sha256(binding_id\|run_id\|'submit') | Single run; preserved across worker restarts | Submit/lookup at adapter |
| `operation_key` (action) | Action Service when action initially proposed | sha256(tenant_id\|task_id\|intent_key) | All attempts of task | Execution journal; approval consumption |

**`action_slot` is held by the platform, not self-assigned by the agent.** Workflow/Action Service issues slots (or validates agent-proposed slots against task-type step definitions) and **persistently stores in `task_intents` prior to initial tool call**, in the same transaction as execution intent. Upon retry/resume, Action Service recovers slots from `task_intents` by (task_id, tool_id, workflow step), rejecting new agent-proposed slots. Agent proposing new slot (`create_pr#2`) for an existing step is mapped back to persisted slot if same step, or rejected with `ACTION_SLOT_UNDECLARED` if task type forbids secondary steps; valid secondary PRs require fresh user-confirmed intents (audited). Conformance variant C29b: agent proposing `create_pr#2` after restart still results in exactly 1 PR created.

Rules:
- Retries create new `run_id` but represent **identical business intent** when task and action match (tool_id, arguments_sha256, resource_ref). The new action reuses `operation_key`; Action Service returns journaled results rather than invoking tool twice.
- Matching `operation_key` with differing tool_id, arguments_sha256 or resource_ref → `409 OPERATION_KEY_CONFLICT`, preventing execution (validator: "same operation_key with different target repository").
- New business intent (user requesting second PR) requires new task or fresh user-confirmed intent_key with audit.
- `retry_new_attempt` rejected with `REMOTE_OUTCOME_UNKNOWN` when current run is `unknown`. For `external_trusted` agents executing side effects autonomously, platform possesses only `task_intent_key` and reconciliation; lacking Action Service deduplication, runs remain in `unknown` until reconciliation evidence exists.

Acceptance (C29): mock provider creates PR then loses response; worker restarts; user clicks retry. Mandatory outcome: retry rejected while unknown; after reconcile PR found via `task_intent_key`/operation_key; exactly 1 PR at mock; complete attempt history; retry with same key but different target repository yields 409.

Failed runs do not blindly retry ambiguous side effects. Terminal states do not resurrect on late events. Late usage/artifacts can append to result history; conflicting results yield reconciliation records. Pause/resume utilizes checkpoints only when certified. `waiting_approval` does not imply pausing all provider processes; adapter must define that boundary.

## At-least-once and consistency
Unique task submission enforced by tenant + client idempotency key. Unique adapter submission by binding + run ID + operation key. Event keys by tenant + producer + event_id. Matching ID with divergent hash quarantined as conflict. Outbox transaction coincides with mutation. Projections employ revision/CAS and versioned reducers.
Lacking external idempotency, store intent and query status before retrying. Process identity tracks PID and host creation time. Leases employ fencing tokens for gateway writes; stale shells with filesystem access require stopping/quarantining workspace before re-dispatch. Exactly-once remote side effects are not promised.

## Event ordering
event_id unique, run_id, producer_id, producer_seq, occurred_at, received_at. Ingestion assigns monotonic offset per tenant stream. producer_seq is ordered strictly within producer/run, not globally. Deduplication, gap detection, bounded buffers and reconciliation upon seq gaps; replay utilizes ingestion cursor and reducer rules. Heartbeats do not substitute for task progress. SSE Last-Event-ID points to opaque user-scoped cursor, avoiding wall-clock cursors.

### Offsets strictly in commit order (ADR-020)
Sequence/bigserial assigned inside write transactions cannot serve as cursors: transactions can acquire offsets 10 and 11 but commit 11 before 10. If SSE transmits 11, clients with cursor 11 permanently miss 10. Design: business transactions write outbox entries without offsets. A single relay (one worker holding advisory lock per tenant stream) reads **committed** outbox rows, assigns monotonic `ingest_offset` in dedicated transactions, and publishes. Cursor offsets reflect the exact order relay observes commits. HUD cursors point exclusively to relay-assigned offsets. HUD snapshots record `projection_revision` = highest offset applied to projection, within the same read transaction. Single-relay throughput benchmarked at P1a-WP7; partitioning or `pg_snapshot_xmin` watermarks considered only if required.
Test C33: two transactions acquire sequence 10, 11; forced commit 11 before 10. Replay from any cursor produces projection matching fresh snapshot; zero lost or duplicate events.

## Authorization
Effective permissions represent the intersection of user grants, agent deployment grants, task scopes and resource policies. Tool gateway verifies at execution time, including post-approval and resumed runs. RBAC role + ABAC tenant/project/classification/purpose. Approval payload includes arguments hash, target revision, scope, expiration, nonce and approver. `action_digest` = SHA-256 over RFC 8785 JCS across fields binding_id, run_id, task_id, tool_id, arguments_sha256, resource_ref, expected_resource_revision, policy_version, operation_key, nonce, expires_at (`sha256-jcs-v1`). Approval decision (`contracts/approval-decision.schema.json`) is single-use: Action Service in a single transaction verifies digest/nonce/valid_until/role, transitions consumption → consumed and persists execution intent by operation_key; duplicate operation_key executions return journaled results without invoking tools. Agent generated text cannot approve its own actions.

Custom tools route through Action Service even when vendors provide permission policies. Credentials stored in secret vaults; resource handles resolve server-side. SSRF checks for URL tools verify protocol, resolved IP and redirects. File root checks handle symlinks/reparse points and TOCTOU, limiting parser and file sizes. On Windows, connectors must: open files using handles with `FILE_FLAG_OPEN_REPARSE_POINT` to reject reparse points/junctions; resolve final path via `GetFinalPathNameByHandle` and verify normalized case-insensitive containment; reject `\\?\`, UNC, alternate data streams (`:` in names), 8.3 short-name mismatches and link count > 1 (hardlinks undetected by realpath); re-verify size/hash post-read. On Linux, use `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS)` or `O_NOFOLLOW` per component. Validated in spike P0-WP3 on native Windows. Commands require allowlisted argv in isolated workers.

### Untrusted project content and prompt injection
Repository contents (`architecture.md`, blueprint, implementation plan, `.ppm/notes`, `.ppm/updates`) enter models as delimited data with provenance. Enforcement relies on architectural guardrails, not prompts: (1) analysis agents possess tools solely to read scoped snapshots and call `ppm.propose_assessment`; (2) all evidence_refs in proposals must resolve to collector-gathered evidence, otherwise reducer strips them; (3) deterministic reducer marks criteria `verified` solely via observed evidence matching revision/criteria — model text or notes cannot verify; (4) Model Gateway applies egress policy by classification pre-request, closing paths for "upload .env to cloud" injections; (5) deny_globs exclude `.env`, keys and credentials before snapshot capture. Test: fixture note containing forged commands (C22).

### Tenant and producer identity
Ingestion binds tenant from producer binding/credentials; events with `tenant_id` mismatching bindings are quarantined with security alerts (C23). HUD cursors, idempotency keys and project IDs are scoped by tenant.

## Budget
Reserve pre-dispatch across task tree, specifying max tokens, wall time, tool calls, child tasks and cost estimates. Model calls record provider usage IDs to dedup cumulative snapshots. Unknown usage renders as unknown. Reservation settlement reconciles late usage and releases unused capacity. Circuit breakers halt new calls upon limit exhaustion; cancellation latency renders in HUD.

## Network and storage
OIDC service tokens/mTLS or scoped connector tokens with rotation for host connectors. Cloud policy governs provider/classification including traces and embeddings. Audit events default to redacted metadata; raw tool outputs are permission-gated artifacts. PostgreSQL roles/RLS requires testing connection pool tenant resets. Evidence object URIs do not directly grant access; downloads require authenticated APIs or short-lived verified signed URLs.
