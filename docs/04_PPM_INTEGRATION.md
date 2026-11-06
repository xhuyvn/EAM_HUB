# PPM v2 as a managed domain agent
## Capabilities
ppm.inventory.scan reads approved location registries. ppm.project.assess reconciles baselines, criteria and evidence. ppm.program.impact traverses the dependency graph. ppm.portfolio.scenarios computes constraints and priorities. ppm.acceptance.propose generates review packets. Acceptance commit is a domain action performed by an authenticated reviewer.

The deterministic collector is a PPM domain worker. The analysis agent receives scoped snapshot refs and can invoke local or cloud models via Model Gateway. Scanning operates on `.ppm` v2 and mapping files (architecture, blueprint, implementation plan). Scan tasks, assessment tasks and project acceptance maintain independent lifecycles.

## Ownership
| Entity | Authority |
|---|---|
| Agent/release/binding, task/run, platform policy, budget | Platform |
| Host location registration, connector root grants | Platform admin, referenced by PPM |
| Project/program/portfolio, criteria, baseline, assessment | PPM domain service |
| Portable `.ppm` updates and notes | Repo producers, reported claims |
| Evidence snapshot and ACL/provenance | Evidence service |

V2 local registries are imported into the Platform Location Service. Project IDs are preserved, adding tenant_id from context; project IDs are unique within tenants. `.ppm` files contain no platform credentials or auth grants. Cloud and access policies in legacy registries map to trusted bindings and admin policies post-review.

## End-to-end
1. Owner registers project location, root and metadata mapping.
2. Scheduler creates scan task per PPM scan policy, deduplicating schedule occurrence IDs.
3. Connector scans files/hashes/Git snapshot, emitting progress x/y and evidence refs.
4. PPM ingests journal, preserving invalid/partial/mixed snapshot status.
5. Assessment task reconciles claims, criteria and tests; raw reports in repos remain reported evidence pending source and scope verification.
6. PPM persists assessment + domain events in outbox transaction.
7. HUD receives run completion and project assessment updates independently.
8. Work packages requiring coding are submitted to Engineering adapter; completion yields artifacts for PPM verification.

## Progress and quality
Verified progress is calculated from baseline weighted criteria backed by sufficient evidence. Reported progress is tracked separately and remains null when unknown. Project delivered or closed status does not dictate whether operations are running or retired. Program benefits are measured via outcome metrics; portfolio constraints track reviewer capacity, GPU hours and dedicated budgets.

## Mapping PPM milestones
PPM M0 → Platform P0; M1 → P2; M2 → P2/P3; M3/M4 → P4; M5 executor → shared adapter P4 and advanced gated capabilities; M6 → P5. Prefixes prevent confusion with Phase 2 H0–H3.

## Migration acceptance
Onboard 5 representative projects: active, completed historical, non-Git, monorepo and shared component. Preserve identity, counts and provenance. Test stale evidence, duplicate update IDs with divergent content, directory traversal/symlinks, watcher disconnects, offline catch-up, multiwriter statuses and scope alterations. Formal acceptance requires criteria and approvals matching baseline.

## Revision 1.1 — location, claim→assessment, shared component
### Location registration and aliases
`POST /v1/ppm/locations` creates `location-registration` 1.1 in state `proposed`; owner/admin approval transitions to `active`. The record persists root_path, metadata_root, scan_scope, `artifact_aliases` for architecture/blueprint/implementation_plan (e.g. `implement_plan.md`, `docs/IMPLEMENTATION_PLAN.md`), notes_globs, deny_globs, classification and egress_policy_ref. Resolution precedence: (1) `.ppm/project.yaml` artifact_mapping if path exists in scope; (2) first alias in registration existing on disk; (3) unmapped fallback with coverage gap. Discovery heuristics (`blueprint*.md`, ARCHITECTURE.md...) generate onboarding proposals, never mutating mappings autonomously. Location–project cardinality: a single repository (source) can be registered by multiple projects (monorepo), each retaining dedicated `metadata_root`, aliases, notes and criteria. Only rejected (409) when matching both source (`canonical_root_fingerprint` or `repo_identity`) and `metadata_root`, or when a single `metadata_root` is claimed by two projects. `scan_scope` overlap is permitted for reading (e.g. `libs/shared`), but progress weighting is governed by deliverable owner. The collector captures one snapshot per source per scan and segments views by project scan_scope; shared files are hashed once (e.g. `examples/locations-monorepo.json`). Clones and worktrees represent distinct locations of the same project and do not aggregate progress independently.

### From claim to verified assessment
1. Collector snapshot: file hash, HEAD/dirty manifest, `.ppm/updates` → ReportedUpdate (claim), notes → Note (observation, deduped by content hash; identical notes copied across two projects represent independent claims without increasing evidence).
2. Evidence originates exclusively from collector/verifier: test reports with revision, runtime probes, artifact inspections. `checks[].result=passed` in updates constitutes a claim; it converts to evidence when collector acquires a report at `evidence_path` matching `tested_revision` with assessed revision.
3. Analysis run (local model via Model Gateway) produces `ppm-output` 1.1 proposal: maps claim/evidence → criterion, providing concise rationale.
4. Deterministic PPM reducer re-verifies each criterion: evidence exists, belongs to project, matches active baseline_revision and environment; `mixed_snapshot` or dirty trees preclude verification; refuting evidence → contradicted. The reducer may downgrade but never upgrades against rules, regardless of proposal text.
5. Assessment persists in PPM DB with baseline_revision, validity and outbox `ppm.assessment.updated`. `acceptance_readiness=ready_for_review` only when verified.
6. Formal acceptance: reviewer possessing authorized role per binding/IdP invokes `POST /v1/ppm/projects/{id}/acceptance` with assessment_id + baseline_revision + expected_revision; server verifies assessment remains current. Run success, in-repo "accepted" notes or commit signatures hold zero authority.

### Reducer evidence rules
A resolvable `evidence_ref` alone does not verify a criterion. A criterion is `verified` only when satisfying concurrently:
1. Producer provenance: only `verifier_observed` (verifier sandbox re-running mapped test/probe at exact revision, P2a-WP9) or `ci_attested` (CI with owner-trusted provenance) verifies. Collector *observing* an in-repo report file does not mean *observing* test execution: such files are `reported_artifact`, treated as claims with files (PASS does not verify; FAIL acts as refuting signal). Notes, `.ppm/updates`, statuses and model text are claims; reducer assigns them to `reported_status`/`reported_claim_refs` and never to `supporting_evidence_refs` (`evidence_wrong_producer` if violated).
2. Revision match: `evidence_revision` = `assessed_revision` of assessed snapshot (non-Git: tree hash). PASS at an older commit does not verify current commit (`evidence_stale_revision`).
3. Baseline currency: criterion belongs to active baseline_revision; baseline modifications render prior assessments stale.
4. Test coverage mapping: baseline persists test mappings per criterion (test IDs approved by owner at baseline lock). Reports must contain those tests with passed results; PASS on unmapped tests yields `coverage_not_mapped`.
5. Non-contradiction: FAIL on a mapped test at the same revision yields `contradicted`, superseding older PASSes and "done" claims.
`previous_status` + `previous_assessment_id` enable HUD to detail criterion and evidence evolutions. Full example: `examples/ppm-output.json` (AC-001: "done" note, PASS at rev-r1, FAIL captured by collector at rev-r2 on identical mapped test → contradicted; because AC-001 is required, acceptance is blocked).

### Waivers and draft baselines
Waivers approved in PPM DB (approver, rationale, scope, expiry) set criterion `status=waived` + `waiver_ref`, excluded from progress denominator and counted as satisfied for acceptance readiness until expiry (triggering reassessment upon expiration). Prior to owner approving a baseline from draft `acceptance.yaml`, assessments remain provisional with `progress.basis=none` (PPM contract §14.5).

### Incremental rescanning
Each scan compares file hashes across scan_scope + metadata_root of each project against previous snapshots. Projects lacking file modifications within their scope bypass new assessment generation (retaining prior assessment, updating `last_successful_scan_at`). Shared file modifications (e.g. `libs/shared`) trigger revalidation for the deliverable owner project and consumers possessing `dependency_refs`.

### Source mutations and outages
- Unreachable path/disconnected drive/host sleep: scan records `failed` or `partial`, retaining prior assessment with validity `stale` post-TTL, marking `connector.status` stale/offline; never generating 0 progress or no_change.
- Stale evidence: test reports where tested_revision ≠ current revision of modified modules mark criteria `stale`; conservative policies treat new commits as stale pending impact mapping.
- Baseline update (new approved revision): all prior baseline assessments transition to `stale`; prior acceptance transitions to `superseded`, creating revalidation scan tasks.
- Duplicate update ID with divergent content: conflict, avoiding last-write-wins; supersedes preserves revision chains.

### Shared components and portfolios
A deliverable has exactly one owner project (e.g. shared gateway is a dedicated project/component). Consumer projects reference via `dependency_refs` and exclude those criteria from their denominator. Program and portfolio rollups aggregate verified weight across distinct deliverable_ids of owner projects; shared evidence is stored once (content-addressed) and referenced across consumers. Rollups render contributions without averaging project percentages. Test: fixture of two projects sharing a gateway; portfolio total matches distinct weight sum (C24).

### Terminology
PPM blueprint references "autonomy L0–L4"; platform references "integration level L0–L3" (docs/06). In platform documentation, PPM autonomy is designated A0–A4 to prevent ambiguity: A0 read/analyze, A1 draft/proposal (MVP), A2 administrative updates, A3 budgeted dispatch, A4 merge/deploy/strategic prioritization.

## Revision 1.1 Round 3 — orchestrating scan → assessment
### Physical sources and project scopes (ADR-019, extended)
Location Service decouples two entities. `source` is a physical directory or repository: host_id, canonical root, repo_identity; each directory has a single `source_id`. `project scope` (`location-registration` referencing `source_id`) encompasses metadata_root, scan_scope, aliases, notes_globs. Multiple scopes can share one source. `(source_id, metadata_root)` is unique and belongs to exactly one project.

### Coordination ownership
PPM Scan Scheduler owns scan intervals. PPM Assessment Orchestrator owns the transition from snapshot → assessment. Both operate as modules in the PPM domain, executing on Task/Run Service like standard tasks, avoiding secondary schedulers.
1. Scheduler (`ppm_scan_schedules` table, lease by location) initiates scan tasks upon interval expiry, debounce trigger, overdue location startup, or manual invocation. Each trigger assigns a unique `occurrence_id`; duplicate occurrences are no-ops. Each source maintains at most one running scan.
2. Scan task commits snapshots and issues monotonically increasing `snapshot_seq` per (source_id, project) in the same transaction, emitting `ppm.snapshot.captured` outbox event.
3. Orchestrator receives events and initiates assessment tasks, deduplicating by `snapshot_id`. If snapshot hashes match the prior snapshot per project scope, new assessments are omitted: updating `last_successful_scan_at` without duplicating notes or progress.
4. Assessment task executes model analysis (optional, extracting claims from free text) followed by the deterministic reducer. Model errors or timeouts allow the reducer to process structured evidence and claims with `analysis_status=model_failed`; HUD renders "claims from notes unprocessed". Reducer errors fail the task, retaining prior assessment while HUD signals "new snapshot unassessed".
5. Persisting assessments utilizes compare-and-set on pointer `project_current_assessment`, advancing solely when `basis_seq` is higher (see "Reassessment" below; `basis_seq` increments with snapshot sequence, preserving ordering). Example: scan A begins first; scan B reads newer revision and finishes first; when A completes later, A persists to history while B remains current (C34).

### Reassessment independent of file scanning (ADR-021 rev. 2)
Unchanged file contents can still necessitate reassessment. Reassessment triggers (each creating an assessment task, deduplicated by `(project_id, trigger, trigger_ref)`):
- `snapshot`: new snapshot with altered scope hash.
- `baseline_changed`: owner approves new baseline revision (trigger_ref = baseline_revision).
- `evidence_ttl_expired`: evidence active in verdict expires per D-03 (trigger_ref = evidence_id + expiry).
- `model_recovered`: current assessment marked `analysis_status=model_failed` and model profile recovers (trigger_ref = failed assessment_id).
- `manual`: reviewer request.
Each assessment task receives a monotonically increasing `basis_seq` per project **at task creation time**, locking input parameters: latest snapshot_id, active baseline_revision, active policy/TTL. Pointer `project_current_assessment` advances strictly when `basis_seq` increases (CAS). Consequence: identical `snapshot_seq` with baseline modification during execution results in the older assessment (lower basis_seq, old baseline) completing into history; HUD displays the new baseline status and "reassessing on baseline r2" until the new task completes. "Bypassing unchanged scope" applies solely to trigger `snapshot`. Test C39.
