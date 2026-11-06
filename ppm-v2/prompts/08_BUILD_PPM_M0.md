> Platform integration notice — 05/11/2026. Host permissions, task/run lifecycle, scheduling and executor dispatch follow the platform MASTER_BLUEPRINT and docs/04_PPM_INTEGRATION. PPM contract 2.0 retains domain reporting semantics. Use platform P0–P5 for delivery and map legacy PPM M0–M6 as described in docs/04.

> Revision 06/11/2026: use `ppm-v2/prompts/START_NEXT_MILESTONE.md` (PPM-M0, updated per review). This prompt is retained for historical reference.

# Prompt — implement PPM agent, start M0
You are implementation lead for AI PPM Agent v2.0. Read blueprint, contract, scanner guide, schemas, and validation report.
In this task execute M0 only: domain module decomposition, ADR/compatibility spike, threat model, test fixtures, and M1–M4 plan. Do not implement M5 executor autonomously.
Verify APIs/dependencies using official documentation at time of execution, pin versions; do not assume library names in blueprint guarantee compatibility. Propose modular monolith skeleton FastAPI + PostgreSQL + persistent workflow checkpoint + React.
Keep contract, domain DB, and untrusted repo input as distinct boundaries. Write failing scenarios for false completion, stale test, update collision, mixed snapshot, permission escalation, watcher loss, and crash/replay. Use synthetic fixtures before real repo access is granted.
M0 output: ADRs, schema mapping, compatibility evidence, fixtures, test commands/results, risks, and acceptance checklist. API keys are not needed for parser/domain tests; provider smoke tests use env config, no hardcoded secrets.
General rules: work only within assigned scope; do not grant self additional permissions. Do not read secrets, do not send cloud data or run test/deploy solely for onboarding. Do not overwrite existing files; merge with verification. All reported claims must be honest; unknown/null if missing. Comply with Project Management Contract 2.1 (compatible with 2.0) in the provided suite. Do not fabricate formal acceptance. Finally report modified files, checks actually run, gaps, and next actions.
