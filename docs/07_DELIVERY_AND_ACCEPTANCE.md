# Delivery plan và gates
Work packages chi tiết, dependency graph, critical path và acceptance evidence nằm trong `docs/IMPLEMENTATION_PLAN.md` (revision 1.1). Local MVP = P0 + P1a + P2a + P3a, kết thúc bằng D1-fixture rồi Local-pilot (plan 1.2). Shared pilot = toàn bộ P1–P5.

| Milestone | Deliverable | Acceptance |
|---|---|---|
| P0 | Contract ADRs, provider/SDK compatibility, mock fixtures, deployment assumptions | schemas/examples pass, capability report, threat model và baseline dataset |
| P1 | Registry/releases/bindings, identity, task/run, policy, outbox/events/budget | one mock task end-to-end, unauthorized denied, crash/retry/idempotency tested |
| P2 | PPM location connector, `.ppm` ingest, evidence, project assessment | 5 representative projects, stale/partial/conflict handled, no false acceptance |
| P3 | HUD snapshot/SSE, attention, inspector, approvals | reconnect/revoke/cancel/unknown tested, keyboard UI and load target measured |
| P4 | PPM program/portfolio, one real adapter, onboarding certification | integration impact + portfolio constraints demo, real bounded task verified |
| P5 | Backup/restore, release rollback, operator runbooks, pilot sign-off | restore drill, isolation tests, SLA/cost report, unresolved risks disposition |
| H0 | Phase 2 benchmark and trace baseline | dataset reviewed, comparison repeatable |
| H1 | Context/tools/routing experiments | paired quality/cost evidence |
| H2 | Harness Optimization Agent | bounded proposal→experiment→review pipeline |
| H3 | Canary/promotion/rollback | regression detection and revert drill |

PPM M0–M4 trước đây được map vào P0/P2/P3/P4. Secretary channel/adapter backlog map vào shared gateways. Knowledge full ingestion, Risk, Research, Editorial/Media triển khai theo domain backlog sau core ổn định; không là điều kiện phải xây hết để nghiệm thu phase 1.

## Gate evidence
Commit/release digest, DB migration revision, schema/policy versions, exact test commands/results, provider/model/SDK versions, measured environment, sample count, failures, security review, acceptance actor và rollback notes. Một check chưa chạy ghi not_run. Mock result không thay live compatibility test.

## Targets đề xuất
- 100% side-effect attempts đi qua enforced boundary cho L2 deployments.
- Zero unauthorized action/false acceptance trong critical suite.
- Source/citation references resolve 100% trong fixtures, semantic grounding do SME sample.
- PPM critical contradiction recall >=90% trên frozen holdout, báo precision và denominator.
- HUD p95 event-to-display <2s tại profile tài liệu HUD.
- Restore pilot RPO 24h và RTO 4h là initial proposal, phải đo và owner chốt trước production.
- Model quality/cost/latency theo domain, không dùng một ngưỡng chung cho research, PPM và media.

## Vertical slices
S1 mock task→event→HUD; S2 scan→evidence→PPM assessment; S3 exact approval→tool→audit; S4 existing-agent wrapper→result→verification; S5 phase 2 candidate→eval→review. Mỗi slice có auth/error/recovery cùng implementation.
