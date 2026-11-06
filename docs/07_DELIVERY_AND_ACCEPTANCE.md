# Delivery plan and gates
Detailed work packages, dependency graphs, critical paths and acceptance evidence reside in `docs/IMPLEMENTATION_PLAN.md` (revision 1.1). Local MVP = P0 + P1a + P2a + P3a, concluding with D1-fixture then Local-pilot (plan 1.2). Shared pilot = all of P1–P5.

| Milestone | Deliverable | Acceptance |
|---|---|---|
| P0 | Contract ADRs, provider/SDK compatibility, mock fixtures, deployment assumptions | schemas/examples pass, capability report, threat model and baseline dataset |
| P1 | Registry/releases/bindings, identity, task/run, policy, outbox/events/budget | one mock task end-to-end, unauthorized denied, crash/retry/idempotency tested |
| P2 | PPM location connector, `.ppm` ingest, evidence, project assessment | 5 representative projects, stale/partial/conflict handled, no false acceptance |
| P3 | HUD snapshot/SSE, attention, inspector, approvals | reconnect/revoke/cancel/unknown tested, keyboard UI and load target measured |
| P4 | PPM program/portfolio, one real adapter, onboarding certification | integration impact + portfolio constraints demo, real bounded task verified |
| P5 | Backup/restore, release rollback, operator runbooks, pilot sign-off | restore drill, isolation tests, SLA/cost report, unresolved risks disposition |
| H0 | Phase 2 benchmark and trace baseline | dataset reviewed, comparison repeatable |
| H1 | Context/tools/routing experiments | paired quality/cost evidence |
| H2 | Harness Optimization Agent | bounded proposal→experiment→review pipeline |
| H3 | Canary/promotion/rollback | regression detection and revert drill |

PPM M0–M4 previously mapped into P0/P2/P3/P4. Secretary channel/adapter backlog maps to shared gateways. Knowledge full ingestion, Risk, Research, Editorial/Media deploy per domain backlogs following core stabilization; full implementation is not a prerequisite for Phase 1 acceptance.

## Gate evidence
Commit/release digest, DB migration revision, schema/policy versions, exact test commands/results, provider/model/SDK versions, measured environment, sample count, failures, security review, acceptance actor and rollback notes. Unexecuted checks record not_run. Mock results do not substitute for live compatibility tests.

## Proposed targets
- 100% side-effect attempts pass through enforced boundaries for L2 deployments.
- Zero unauthorized actions / false acceptances across critical suites.
- Source/citation references resolve 100% in fixtures, semantic grounding sampled by SME.
- PPM critical contradiction recall >=90% on frozen holdouts, reporting precision and denominator.
- HUD p95 event-to-display <2s under HUD documented profile.
- Restore pilot RPO 24h and RTO 4h represent initial proposals, to be measured and approved by owner before production.
- Model quality/cost/latency evaluated per domain, avoiding a monolithic threshold across research, PPM and media.

## Vertical slices
S1 mock task→event→HUD; S2 scan→evidence→PPM assessment; S3 exact approval→tool→audit; S4 existing-agent wrapper→result→verification; S5 phase 2 candidate→eval→review. Each slice incorporates auth, errors and recovery alongside core implementation.
