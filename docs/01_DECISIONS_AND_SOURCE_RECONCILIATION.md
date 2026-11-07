# Consolidated decisions and migration
## Order of authority
Master/platform contracts govern execution/auth/events. PPM contract 2.0 governs repository reporting. Domain specs govern business validation. Source archives record hashes in the manifest for lineage. The following choices represent agreed decisions for the integrated design, verified in P0.

| Prior conflict | Integrated decision | Migration |
|---|---|---|
| Secretary TS/SQLite, enterprise Python/PostgreSQL | Python control plane + PostgreSQL, vendor TS workers permitted | Wrapper retains separate API, transfer task mapping via legacy_id |
| Enterprise harness PydanticAI, PPM LangGraph | LangGraph orchestration owner | PydanticAI adapter/candidate Phase 2, no dual scheduling |
| Enterprise prioritizes Knowledge before PPM | Phase 1 prioritizes PPM v2 first | Knowledge facade for evidence retrieval, full corpus roadmap post-pilot |
| GUI/open-session automation | Dedicated capability, observe-only prior to control certification | Explicit handover, scope, session identity |
| Qdrant/S3 default | Search/object-storage per pilot demand | Retain interface and migration path |
| Project task state and execution run mixed | Task/Run/Connectivity/PPM acceptance separated | State mapping per runtime tables |
| `.ppm` file self-declared permissions | Authority resides in deployment binding | Registry owner-controlled imported via admin API |
| Harness core at v1 and optimization Phase 2 | Core controls Phase 1, improvement experiments Phase 2 | Baseline corpus established from pilot |

The bundled PPM package inherits source contracts/templates. The PPM blueprint and prompt M0 include a platform integration notice instructing coders to use the P0–P5 platform roadmap. Prior PPM M0–M4 constitutes domain backlog to map; PPM M5 executor utilizes shared platform adapters. Adoption and end-session prompts of `.ppm` continue to serve standalone projects.

## ADRs
ADR-001 PostgreSQL authoritative state, connector spool durable without offline self-approval.
ADR-002 Single workflow owner per run, external agents execute via adapter contract.
ADR-003 Agent package proposal and deployment binding represent two distinct trust levels.
ADR-004 Event store + projection transaction serves as HUD source, trace sampling separated from audit.
ADR-005 PPM verification performed by domain reducer, provider success treated as observation.
ADR-006 Baseline harness incorporates Phase 1 controls, optimization releases in Phase 2.
ADR-007 Tool API/MCP boundary enforces distinct policies; MCP transport does not replace business authorization.
ADR-008 Reuse existing agents via wrappers; unknown capabilities display as unavailable.
ADR-009 Chat delivery via durable outbox with user opt-in and verified recipient binding.
ADR-010 Backup and restore covers DB, blobs, and ACLs; RPO/RTO targets finalized via pilot.

## Legacy import
Inventory source versions and schemas → backup → map IDs → dry-run import → compare counts, approvals, and source hashes → owner review → cut over → retain rollback window. Pending legacy approvals expire upon payload/schema modification, requiring fresh proposals. Unknown sessions remain observe-only until identity and ownership are established. Historical sample content is not treated as live project data.

## Revision 1.1 (06/11/2026) — decisions following architecture review
Evidence and finding IDs reside in `docs/ARCHITECTURE_REVIEW.md`.

| ADR | Decision | Rejected alternative and rationale | Evidence that would alter decision |
|---|---|---|---|
| ADR-011 (ARCH-03) | Run Service/PostgreSQL is sole owner of run state, dispatch journal, lease/fencing, cancel. LangGraph executes strictly within a single attempt of native agent, never invoking external side effects directly. | Retaining LangGraph as orchestration owner across runs: dual sources of state (checkpointer vs runs table), node replay repeats from start causing duplicate adapter submissions. Temporal: robust for durable workflows but adds operational cluster for single-node pilot. | If P1 measures multi-day workflow demands with complex activities beyond bespoke journal capabilities, open ADR migration to Temporal for specific workflows. |
| ADR-012 (ARCH-02) | PPM assessment proposal 1.1: per-criterion status/reason/evidence, progress numerator/denominator, excludes acceptance. Deterministic reducer is sole entity assigning verified. | Retaining ppm-output 1.0: inability to explain rationale, permitting `verified` with missing evidence and `contradicted`+`accepted`. | None; change rectifies contract defect. |
| ADR-013 (ARCH-06/07) | Location registration is platform-trusted record containing artifact aliases; repo merely proposes aliases. Each deliverable has single owner project; consumer projects use `dependency_refs`; portfolio sums distinct deliverable_id. | Automatic discovery of `blueprint*.md` from repo: injected files can alter mapping. Percentage allocation for shared deliverables: lacks distribution basis. | If owner requires benefit attribution, add approved contribution records without double counting. |
| ADR-014 (ARCH-10) | Approval binds SHA-256 digest over RFC 8785 JCS of immutable proposal fields, nonce and operation_key; decision single-use, consumed atomically in transaction recording execution intent. | Unstandardized "arguments hash" digest: services could hash inconsistently or approvals could be applied to altered targets. | None. |
| ADR-015 (ARCH-12) | Experiment 1.1 mandates primary metric + non-inferiority margin + per-stratum guardrails, paired design, evaluator-only holdout, proposer ≠ reviewer. | Solely comparing mean quality/cost: cheaper candidates may degrade quality in small strata without shifting overall means. | If dataset expands sufficiently to estimate effect sizes per stratum, revise margin via power analysis. |
| ADR-016 (ARCH-05/15) | Binding 1.1 enumerates fallback/embedding/telemetry profiles; Model Gateway deny-by-default. Vendor server-side tools classified as `external_trusted`. | Single `model_profile_ref`: fallback/embedding/telemetry bypass policy. | None. |

### Contract 1.1 — compatibility, migration, rollback
- Transitioned to `contract_version: "1.1"`: event, result, task-envelope, action-proposal, binding, experiment, ppm-output (assessment proposal). New contracts: approval-decision, location-registration, hud-projection, run-command. Retained 1.0: agent, task-request, ppm-input, research-input/output.
- Breaking change (new required fields, `acceptance`→`acceptance_readiness`, new enums). Zero deployed consumers existed (repository contained no application code at revision `0f91f2d`), obviating dual-read. From P1, all contract alterations follow expand/contract: introduce optional field first, producers write both, consumers migrate, then deprecate old field; readers reject unrecognized majors and accept higher minors only if no new required fields exist.
- PPM portable contract remains 2.0. Schema fix: `updated_at`/`recorded_at` enforce mandatory timezone patterns — required by contract specification; previously valid files unaffected, previously accepted files lacking timezones were false positives.
- Rollback: revert files in `contracts/`, `examples/`, `templates/`, `tools/validate_package.py` to revision `0f91f2d`; zero runtime data requires migration.

### Round 2 (06/11/2026, owner feedback) — additions to contract 1.1 prior to consumers
| ADR | Decision | Rejected alternative |
|---|---|---|
| ADR-017 (ARCH-20) | Three distinct keys: `task_intent_key` (Task Service, stable across attempts), dispatch `idempotency_key` (per run), `operation_key` (Action Service, per business intent). Identical operation_key with conflicting payload → 409 `OPERATION_KEY_CONFLICT`. | Keys inferred from request parameters: cannot differentiate identical intents nor detect same key with different target. |
| ADR-018 (ARCH-21) | Evidence verifies criteria only with valid producer, assessed revision match, active baseline, report containing mapped tests, and absence of revision FAIL. Claims separated in assessment. | Treating any resolvable evidence_ref as valid. |
| ADR-019 (ARCH-22) | Multiple projects sharing repository with distinct metadata_root; duplicate = identical source + metadata_root. | Single repository ↔ single project (precludes monorepos). |

Contract changes (retained 1.1 prior to consumer release): `ppm-output` adds `assessed_revision`, `previous_assessment_id` and per-criterion fields `reported_status`, `reported_claim_refs`, `evidence_revision`, `covered_test_ids`, `previous_status`; `task-envelope` adds `task_intent_key`; updated descriptions for `action-proposal.operation_key` and `location-registration`. New fixtures: `examples/action-proposal-retry.json`, `examples/locations-monorepo.json`. Rollback: as documented for contract 1.1 above.

### Round 3 (06/11/2026)
| ADR | Decision | Rejected alternative |
|---|---|---|
| ADR-020 | HUD/event offsets assigned by single outbox relay post-commit (commit order), not within write transaction. | bigserial in write transaction: sequence 10/11 committed out of order drops events during replay. |
| ADR-021 (rev 2) | Current assessment pointer advances strictly via `basis_seq` (CAS) issued at assessment-task creation across triggers (`snapshot`, `baseline_changed`, `evidence_ttl_expired`, `model_recovered`, `manual`), locking inputs at creation; delayed assessment of older `basis_seq` stored as history (C34, C39). Model analysis optional; reducer runs on model failure (`analysis_status`). *(Revision note: updated from initial snapshot_seq CAS to rev 2 basis_seq CAS to preserve deterministic ordering during reassessments on identical snapshots; contracts, examples, and `eap.contracts.rules.apply_assessment` agree).* | "Last-writer-wins overwrite": fails when scans complete out of order. Halting full assessment on model failure: loses deterministic verdicts. CAS strictly on `snapshot_seq`: cannot order reassessments on identical snapshot (C39). |
| ADR-022 | Decouple physical `source` (`source_id`) from project scope; Scan Scheduler and Assessment Orchestrator run as PPM modules on Task/Run Service. | Conflating location = project (blocks monorepo); OS cron (secondary scheduler, lacks audit). |
Contract (1.1, no consumers): `ppm-output` adds `snapshot_seq`, `analysis_status`; `location-registration` adds `source_id`. Fixture `examples/ppm-output.json` tests AC-001 with "done" note + PASS rev-r1 + FAIL rev-r2.

### Round 4 (06/11/2026)
- ADR-017 rev. 2: `action_slot` issued or validated by workflow/Action Service, persisted in `task_intents` prior to initial tool call, recovered on retry; agent-proposed slots after restart do not spawn new keys (C29b). Rejected alternative: trusting agent-declared slots (agent renaming `create_pr#1`→`#2` creates second PR).
- ADR-021 rev. 2: current assessment pointer advances by `basis_seq` (issued at assessment task creation across triggers: snapshot, baseline_changed, evidence_ttl_expired, model_recovered, manual), not solely by `snapshot_seq`. Contract `ppm-output` adds `basis_seq`, `trigger` (1.1, no consumers). Rejected alternative: CAS strictly on `snapshot_seq` (cannot handle reassessments on identical snapshot).

### From PPM v2 review (06/11/2026, `ppm-v2/docs/ARCHITECTURE_REVIEW.md`)
- ADR-023: only `verifier_observed` or `ci_attested` verifies criteria; in-repo reports treated as `reported_artifact`. Contract `ppm-output` adds `evidence_trust` (1.1, no consumers). Added P2a-WP9 Verifier. Rejected alternative: trusting in-repo report files matching revision (producers can forge files).
- ADR-024: waivers stored in PPM DB, `status=waived` + `waiver_ref`, excluded from denominator. Rejected alternative: representing waivers as `verified` (masks skipped criteria) or keeping in denominator (progress never satisfies despite approval).
- PPM contract 2.1 (compatible with 2.0): `verification_refs`, `criterion_ids`, `test_ids`, `uses_component`; registry YAML serves as import format for `location-registration`.

### Implementation preparation (06/11/2026, plan rev 1.5)
- ADR-025 Repository language: English is canonical from P0-WP0; agents read and edit English. Vietnamese copies stored at `docs/vi/<original path>` with header `Translated from <path> @ <sha>`, solely for owner review, agents do not read (`AGENTS.md`). SC-08 reports stale translations. Rejected alternative: dual canonical languages (divergence, recreating "two competing blueprints" issue); keeping Vietnamese canonical and translating prompts only (agents consume extra tokens reading review/plan/docs in Vietnamese — savings measured in P0-WP0).
- ADR-026 Dev environment: Python 3.12, PostgreSQL 18.6, Node 26 LTS; Redis 8.10.1 and Qdrant 1.20.0 in compose profile `optional`, storing derived data only (`docs/DEV_ENVIRONMENT.md`). PostgreSQL `SKIP LOCKED` work queue and FTS retained; making Redis/Qdrant mandatory requires new ADR with measurements. Rejected alternative: Redis as run/dispatch queue (journal and fencing must reside in same transaction as run state in PostgreSQL).
