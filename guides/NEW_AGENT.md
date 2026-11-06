# New agent onboarding guide
1. Define business owner, task types, input/output, acceptance criteria and the required L0/L1/L2 level.
2. Copy `templates/new-agent/` into a dedicated agent package. Update ID/name/owner/schema/tool refs, keeping contract_version=1.0.
3. Implement tools with typed arguments, service-side authorization and idempotency for mutations. Model input contains only authorized context.
4. Implement the adapter following the protocol and register the verified capability matrix. Native runtimes use the platform workflow entrypoint, without creating a secondary scheduler.
5. Provide domain fixtures and evaluation rubrics; execute conformance cases prior to staging.
6. Build an immutable release bundle, hashing prompt/schema/tools/config. Register in the registry as draft/testing via the API.
7. Admin creates deployment binding from template, resolving secrets/profile/root grants in the environment. Execute permission and budget smoke tests.
8. Shadow/read-only pilot, verifying HUD trace/results/cost/freshness. L1/L2 require corresponding gates.
9. Reviewer approves active release, publishing the version record. Pilot results must reference the exact bundle hash.

The first task should be a bounded read-only fixture. Deliverables include manifest, adapter capability evidence, schema, prompt, eval report, owner decision and rollback plan. Use `prompts/02_ONBOARD_NEW_AGENT.md`.
