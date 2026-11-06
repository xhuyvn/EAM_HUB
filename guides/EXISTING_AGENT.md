# Existing agent onboarding guide
## Discovery
Inventory source/runtime version, API/CLI entrypoint, auth, storage, schedulers, model providers, tools, credentials, data classification, external sessions, output artifacts and owner. Assess which side effects are bypassing the gateway and whether operations have idempotency/reconciliation. Explicitly state whether the adapter supports `lookup` by idempotency key; if not, any acknowledgement timeout will leave the run at `unknown` and require operator intervention. List vendor server-side tools (built-in shell/file/web) because they execute outside the Action Service.

## Choosing a wrapper
- HTTP agents with status/result endpoints use the external-service adapter.
- One-shot CLIs with output files/schemas use the CLI wrapper with an isolated workspace.
- SDK runtimes use the managed worker adapter.
- UI-only applications start at L0/manual evidence; GUI control is a capability added only after testing.
- Agents lacking logs/events use coarse status polling; the HUD records coverage=partial and stage unknown.

## Cutover
Establish legacy job/task/session ID mappings. Freeze duplicate schedulers for the transitioned scope, import read-only history, and do not replay historical commands. Current runs are terminated by the legacy owner or require explicit handover with dedicated permissions. Compare states/artifacts on fixtures, verify control rollback paths, and only then cut over the submit path to the platform.

A remote agent is granted L2 only if write tools pass through enforcement or a sandbox/service boundary proven to prevent bypass. If not yet achieved, narrow capabilities or restrict to read-only tasks. Document observability coverage and control limitations in the registry and HUD.

## Operations
Copy templates/existing-agent and use prompt 03. Preserve application/source behavior through the wrapper, adding instrumentation and correlation at the boundary. Credentials bind via secret_ref. Execute duplicate submit/cancel/recovery scenarios. Disable new deployments if unknown remote states are encountered, reconciling before retry. Uninstall separates registry removal from remote app/data deletion, with the owner deciding each part.
