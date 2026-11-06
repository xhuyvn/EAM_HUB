# Enterprise Agent Integration Contract 1.0
## Integration levels
| Level | Capabilities | Prerequisites |
|---|---|---|
| L0 Observe | Inventory, status/events/results readable | Data permissions + identity mapping |
| L1 Managed | Submit/status/results, cancel per capability | Idempotency/recovery certification |
| L2 Governed actions | Tool writes via gateway, exact approvals | Enforcement path and sandbox/network boundary tested |
| L3 Optimizable | Versioned harness + eval candidate pipeline | Phase 2 release controls |

Adapters advertise capabilities supported strictly by evidence. Existing agents possessing direct tool access must declare execution_boundary=external_trusted or sandboxed; the platform guarantees only actions physically routing through the gateway. Agents possessing broad credentials must undergo permission narrowing or L0 confinement prior to managed-write onboarding.

## Package
agent.json specifies identity, owner refs, task types, schema refs, runtime kind, desired capabilities, desired tools, eval profile and harness version. Prompts and task schemas reside in the same package with digests. Trusted bindings store endpoint_ref, secret_ref, host/root scope, approved capability set, timeout/cost profile and grant IDs outside the repo.

Owner_ref in packages represents proposed metadata. Registry verifies owner against IdP and approver prior to active release. Versions are immutable; artifact hashes are verified before dispatch. Disable/uninstall executes dependency reporting, credential/grant revocation and reconciliation of active runs prior to retirement.

## Adapter interface
capabilities(binding) returns verified flags and evidence reference.
submit(task, execution_context, idempotency_key) returns execution_ref or ambiguous outcome. idempotency_key represents the stable operation key of the run (docs/02), not a fresh key per attempt.
lookup(idempotency_key) returns found(execution_ref)/not_found/unsupported; `not_found` holds weight only when the provider commits to authoritative lookup. Adapters lacking lookup must not independently retry submits.
status(execution_ref) returns normalized state + provider raw code + observed_at.
events(execution_ref, cursor) returns events if supported; polling adapters execute snapshot reconciliation.
collect_result(execution_ref) returns typed result + artifact refs.
cancel/resume/approve_tool exposed strictly when capability=true and current run state permits.

Unsupported capabilities return typed error `CAPABILITY_UNSUPPORTED`. CLI exit 0 represents transport/process success only; result schemas and domain verifiers dictate completion levels. stdout/stderr omit credentials; stderr is redacted into restricted diagnostic artifacts.

## Adapter types
Native Python domains utilize typed function/tool boundaries. HTTP services utilize service identity, endpoint allowlists, timeouts, webhook signatures + replay protection. CLI processes utilize pinned executable paths, argv, explicit cwd, env allowlists and process tree ownership. Vendor SDKs utilize supported public APIs and pinned versions. Open sessions require explicit registration/handover and exclusive controllers. GUI automation requires user-session bridging, focus/input arbitration and dedicated capability reviews.

Codex/Claude/OpenCode/Antigravity names in manifests represent adapter targets. P0 must verify version/auth/OS/tool approval/cancel/resume with fixtures. The package does not assume any vendor possesses full attach/resume or cost accounting.

Vendor runtimes executing tools server-side require explicit classification. Verified example (Claude Platform docs, Managed Agents permission policies, accessed 06/11/2026): permission policies govern server-side tools (agent toolset, MCP toolset); custom tools execute in the application and bypass vendor permission policies. Consequence: custom tools can route through Action Service (achieving L2); built-in toolsets (bash/file/web) execute in vendor sandboxes outside platform enforcement and represent cloud egress paths. Bindings qualify as `governed` strictly when built-in toolsets are disabled or all server-side tools are set to `always_ask` with confirmations bridged to platform approvals with digests; otherwise, `execution_boundary=external_trusted` and capped at L1. Adapter certification requires tests proving this configuration (C25).

## Conformance
Schema + IDs + output refs; unauthorized submit/action; scope narrowing delegation; duplicate submission; crash before/after external acceptance; out-of-order stream; cancellation late acknowledgement; missing heartbeat; schema drift; redaction; cost unknown; provider failover egress policy; task result vs acceptance separation. `evals/CONFORMANCE_CASES.md` details expected outcomes.
