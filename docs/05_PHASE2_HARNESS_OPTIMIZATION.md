# Phase 2 — Harness Engineering & Optimization
## Charter
Improve reliability, grounding and cost of domain agents via repeatable experiments. The harness encompasses instructions, context builder, tool contracts, planner/stop conditions, memory/compaction, recovery, verifiers and observability. Policy enforcement and domain source of truth remain owned by platform services.

## Harness Optimization Agent
Inputs include authorized redacted traces, failure labels, domain eval summaries, runtime/profile versions and engineering constraints. Outputs include hypotheses, candidate changes, experiment plans and evidence-backed recommendations. The agent creates candidate branches/configs within a sandbox, invoking the experiment service with authorized datasets. Deployment promotion routes through release workflows with authorized reviewers.

### Workflow
Detect repeated failure → cluster failure taxonomy → inspect authorized evidence → propose minimal candidate → validate schema/security diff → offline eval with frozen baseline → reviewer assessment → shadow read-only → limited canary → compare → approve promotion or rollback.

Failure classes include retrieval miss, context overflow, stale evidence, tool selection/arguments, unauthorized attempts, loop/nontermination, schema invalid, wrong acceptance, provider timeout, resume/replay errors and high-cost redundant calls.

## Optimization backlog
| Experiment | Potential changes | Metrics |
|---|---|---|
| Context selection | Scope filtering, semantic/keyword fusion, evidence ordering | Relevant evidence recall, answer grounding, tokens |
| Compaction | Structured handoff, summary provenance, restore plan state | Long-run continuity, missing requirement rate |
| Tools | Schema/descriptions, bounded output, tool discovery | Tool argument validity, correct action rate |
| Planning | Fixed workflow vs bounded planner, delegation threshold | Completion, tool count, coordination overhead |
| Model routing | Local/cloud profile by task class | Quality by class, cost and latency under policy |
| Verification | Acceptance checks and citation rules | False success, reviewer correction rate |
| Recovery | Retry classification, idempotency, checkpoint granularity | Lost/duplicate work, resume success |
| Reuse | Context/cache keyed by input hash + ACL/policy version | Cost saving without stale/unauthorized access |

All caches enforce tenant/resource ACLs and invalidation upon source/policy/model alterations. Model compatibility is probed; OpenAI-compatible APIs alone do not guarantee tool or structured output correctness. Local-only domains cannot transition to cloud within experiments.

## Experiment protocol
Each experiment declares experiment_id, candidate/base bundle digest, task set version, execution environment, provider/model immutable config, seed if supported, trial count, budgets, gates and evaluator version. Holdouts remain frozen outside optimizer permissions, exposing only aggregated or curated failure summaries. Synthetic or anonymized fixtures precede live data, handling source permissions and outputs comprehensively.

Proposed baseline includes at least 100 tasks stratified by domain/risk, with 5 trials for critical scenarios. Reports provide numerator/denominator, confidence intervals and per-domain failures. Execute paired trials on identical task/environment configurations, recording timestamps to counter provider drift. LLM graders supplement semantic rubrics, alongside deterministic oracles for authorization/state/idempotency. SME reviews govern PPM acceptance and compliance content.

## Promotion gates
Critical policy/security regressions must equal 0 across defined suites. Primary task-quality metrics must not degrade beyond approved tolerance. For instance, a tolerance of 2 percentage points is an experimental configuration requiring justification. Cost targets propose a 15% reduction at equivalent quality or a 5-point completion increase within budget; selecting a single primary objective prior to execution. Finite test sets do not prove zero production risk.

Canaries constrain deployment subsets, task classes and budgets. Shadow mode operates read-only or utilizes simulated side effects. Real writes in canary require explicit grant/approval authorization. Rollback triggers include false acceptance, unauthorized actions, elevated failure rates relative to baseline, and budget runaway. Running runs remain pinned to older releases; new releases apply strictly to new runs. Upon security revocations, halt new calls and reconcile active work. Configuration rollbacks differ from reversing external side effects, necessitating dedicated compensation procedures.

## Phase 2 milestones
H0 trace quality baseline + benchmark report.
H1 context/tool/model routing experiments with at least two candidates and control.
H2 Optimization Agent proposes bounded candidates and experiments, reviewer selects releases.
H3 canary/rollback drills and regression monitoring, accepted by domain owner + platform owner.

## Deliverables
Experiment manifest, dataset lineage, version locks, paired result table, quality/cost scatter plot, redacted failure examples, reviewer decisions, deployment records and rollback verification. HUD Harness Lab displays these alongside candidate/approved/canary/promoted/reverted statuses.

## Revision 1.1 — detecting cheaper candidates with silent quality degradation
Experiment contract 1.1 requires declaring prior to execution: `change_kind` (prompt/tools/retrieval/context/model_routing/...), a single `primary_metric` with `non_inferiority_margin`, at least one `guardrail_metrics` (recommended for PPM: false_verified_rate=0 per stratum, unsupported_claim_acceptance=0, critical_policy_violations=0), `pairing=paired_same_case_same_environment`, `holdout_access=evaluator_only`, `proposer_ref ≠ reviewer_ref`.

Evaluation procedure:
1. Baseline selection: base_release_digest reflects the active release, rerun across identical task sets/environments/model profiles within the same temporal window as candidates (omitting historical metrics) to eliminate provider drift.
2. Paired evaluation: each case executes base and candidate with matching input snapshots, seeds if supported, and `trials_per_case` iterations; reporting paired differences and confidence intervals (bootstrapped per case).
3. Non-inferiority: candidate qualifies solely when the lower bound of the confidence interval for (candidate − base) ≥ −margin for the primary metric, and all guardrails succeed across each stratum (domain × risk). Cost reductions cannot offset guardrail failures.
4. Silent quality loss: additionally inspect output length distributions, evidence ref counts, "unknown/missing evidence" rates and omitted tool call ratios — cheaper candidates frequently economize by performing less work. Deviations exceeding declared thresholds flag experiments as `review_required` with redacted samples.
5. Grader drift: LLM grader pinned to evaluator_version, calibrated against human-labeled datasets; deterministic oracles for authorization/state/idempotency/verified criteria. Holdouts are read exclusively by evaluator service; optimizer receives aggregate feedback.
6. Rollout: shadow (read-only/simulated side effects) → canary constrained by `max_traffic_percent` and task classes → promote. Rollback triggers declared in `rollout.rollback_trigger_refs`; active runs retain older releases.

Phase 1 retains ownership of permissions, recovery, audit and eval evidence capture (baseline dataset, redacted traces); Phase 2 builds optimizer and experiment pipelines on that foundation.
