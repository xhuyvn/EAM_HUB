# Architecture Review — PPM v2 Agent (Project–Program–Portfolio Management)

Review date: 06/11/2026. Scope: `ppm-v2/` package (blueprint, Project Management Contract, schema, templates, prompts, scanner guide, validator) and touchpoints with Enterprise Agent Platform 1.1. Starting revision: `0f91f2d` (repo `EAM_HUB`), along with the working tree modified during the platform review cycle. No real project data used, zero billable provider calls made.

This review does not create a competing blueprint. When executing within the platform, `MASTER_BLUEPRINT.md` and `docs/04_PPM_INTEGRATION.md` serve as canonical sources for runtime; PPM v2 serves as canonical source for domain semantics (`.ppm` contract, criteria, claims, assessments). Superseded PPM blueprint points are enumerated in §19 of `AI_PPM_Agent_Blueprint_v2.0.md`.

## 0. Conclusions and readiness

| Scope | Verdict | Rationale |
|---|---|---|
| Standalone adoption suite (contract 2.1, templates, prompts 01–07, validator) | **Ready for immediate pilot on owner repository — claims level only.** | Contract, templates, and validator re-verified (20 cases pass). In the absence of a verifier, all verdicts are reported/manual review, not verified. |
| Automated PPM agent — Local MVP | **Design adequate for implementation; zero code exists.** | Vulnerabilities causing false verification resolved (PPM-01..03). Depends on platform P0–P2a and PPM-M0 (reducer core). Verifier on real repo requires D-07. |
| Shared pilot | **Not ready.** | Program/portfolio/benefit lack contract (PPM-14); baseline/waiver/acceptance APIs belong to P2b; portfolio rollup with hidden permissions untested (C43). |
| Production | **Not ready.** | No runtime, no evals on real data, domain model unapproved by PPM expert. |

Most critical modifications:
1. **In-repo test reports are no longer treated as evidence** (PPM-01, platform ADR-023). Only a verifier rerunning mapped tests at exact revision, or CI with attestation, verifies a criterion. Rationale: producer can author code, update, and JUnit file within the same repository.
2. **Contract 2.1 compatible with 2.0**: criteria include `verification_refs` (mapped tests/probes), updates/checks include `criterion_ids`/`test_ids`. Rationale: without these linkages, the reducer must rely on LLM guessing to map claims to criteria and tests to coverage.
3. **Validator catches additional semantic errors** (duplicate criteria, cyclic/dangling supersession, nonexistent criterion references, future timestamps, 2.1 fields in 2.0 files).
4. **Project identity upon repo copying, waivers, baseline from draft, registry → location-registration** explicitly defined.

## 1. What exists

| Component | Path | Implemented | Tested |
|---|---|---|---|
| Blueprint | `ppm-v2/AI_PPM_Agent_Blueprint_v2.0.md` | Design | — |
| Normative contract | `ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md` | Design | — |
| Schema | `ppm-v2/schemas/contract.schema.json` | Yes | Templates + 20 cases |
| Templates `.ppm/`, registry | `ppm-v2/templates/` | Yes | Validator |
| Prompts 01–08 | `ppm-v2/prompts/` | Text | Untested with coding agent |
| Validator, runner | `ppm-v2/tools/validate_contract.py`, `run_negative_cases.py` | Yes | Executed (§9) |
| Collector/scanner, reducer, verifier, UI | `guides/SCANNER_IMPLEMENTATION.md`, platform P2a | No | No |
| Program/portfolio/benefit | Blueprint §3, §11 | No contract | No |

Evidence notation: **[V]** command execution/spike; **[D]** inferred from documentation; **[A]** assumption requiring spike.

## 2. Workflow traces

### A. Adopting active project (prompt 01, standalone)
| Step | Caller | Authorization | Source of truth | What is written | Failure | What owner sees |
|---|---|---|---|---|---|---|
| Inventory | Owner's coding agent | Agent repo permissions (outside PPM) | Repo | Nothing written | Agent runs commands from docs → prohibited by prompt, lacks enforcement | Agent report |
| Create `.ppm/` | Coding agent | — | Repo (portable metadata) | project/status/acceptance(draft)/initial update | Copy template retaining `proj-example` → duplicate ID across multiple repos (PPM-06) | Diff `.ppm/` |
| Registry | Owner | Owner | Host registry (standalone) / `location-registration` (platform) | Path, scan policy | Registry lacks fields required by platform (PPM-07) | Registry file |
| Validate | Owner/agent | — | Validator | — | Prior to review: semantic errors leaked (PPM-05) | PASS/FAIL |

### B. Working session cycle (prompt 04/05)
Coding agent writes `updates/<UUID>.yaml` — self-reported claim. Corrections via new update with `supersedes_update_id`. Prior to review: claims unlinked from criteria (PPM-03), supersession could form cycles/dangle (PPM-05, [V]). After review: `criterion_ids`, `test_ids`; validator blocks cycles/dangling/unrecognized IDs.

### C. Claim → assessment (platform mode)
Collector snapshots → ingests claims (update, note, status) → verifier runs mapped tests at `assessed_revision` → deterministic reducer → active assessment by `basis_seq` → reviewer accepts. Per-criterion decision table:

| Condition | Outcome |
|---|---|
| Verifier/CI PASS mapped tests at exact revision, no FAIL | `verified` |
| FAIL (verifier, CI, or in-repo report) at exact revision | `contradicted` |
| Claim only (update/note) | `unverified / claim_only` |
| In-repo report file only (even if PASS) | `unverified / reported_artifact_only` |
| Evidence at old revision / TTL expired | `stale` |
| Test PASS but unmapped | `unverified / coverage_not_mapped` |
| Approved waiver unexpired | `waived` (excluded from denominator) |
| Baseline absent | provisional, `progress.basis=none` |

Acceptor: reviewer with authorized role (IdP), via acceptance API, on `current` assessment matching baseline. Inaccessible path → scan failed/partial, prior assessment becomes stale per TTL. Duplicate notes → content hash dedup. Baseline change → reassessment.

### D. Scope change (prompt 07)
Agent authors decision note `proposed` → owner approves new baseline revision in DB → triggers `baseline_changed` → prior assessment archived, prior acceptance `superseded`. `acceptance.yaml` differing from baseline → `baseline_drift` (contract §14.5).

### E. Program/portfolio rollup
Deliverable belongs to single owner project; consumer uses `uses_component`/`dependency_refs`; rollup by distinct deliverables. Viewer lacking permission on one project → total includes authorized projects only, labeled "partial", without disclosing count/name/weight of hidden entries (PPM-10, C43). Program/portfolio/benefit contract absent (PPM-14).

### F. Standalone manual review (prompt 06)
Coding agent reads repo and returns reported/verification table. Absent a verifier, verification column must state "manual — unverified"; agent must not run tests autonomously to "verify" (prohibited by prompt). Residual risk: agent with shell access reads note containing prompt injection; recommended execution of prompt 06 in read-only/no-shell mode.

## 3. Dedicated PPM threat model
| Threat | Enforcement point | Test | Expected | Residual |
|---|---|---|---|---|
| Forged test reports authored by producer | Reducer: only `verifier_observed`/`ci_attested` verify | C40; negative "repo-written test report used to verify" | Not verified | Misconfigured CI attestation |
| Malicious test code during verifier rerun | Verifier sandbox: argv allowlist, no network, no secrets, timeout, ephemeral workspace | Fixture test attempting socket connection/env read | Blocked, inconclusive result | Sandbox escape — D-07 selects mechanism |
| Prompt injection in notes targeting analysis model | Deterministic reducer; model extracts claims only | C22 | Verdict unchanged | Distorted claims still displayed (reported column) |
| Prompt injection targeting standalone coding agent (prompt 06) | Prompt instructions only | — | — | **High**: depends on agent permissions; use read-only mode |
| Repo copy retaining project_id | Ingest: identity_conflict via repo_identity | C42 | Quarantine | Erroneous owner confirmation |
| Repo self-granting permissions/cloud | Schema (`access_policy` const), registry outside repo | Negative "repo grants cloud", "registry enables command execution" | Rejected | — |
| Portfolio total leaking hidden projects | Rollup filters by permission prior to aggregation | C43 | Partial, zero leakage | Inference from total changes over time |
| Secrets committed in `.ppm` | deny_globs, secret scan prior to ingest | SC-04 | Ingest rejected | Incomplete detector patterns |

## 4. Findings
| ID | Sev | Kind | Summary | Status |
|---|---|---|---|---|
| PPM-01 | Critical | [D] | In-repo test reports (`evidence_path`) treated as observed evidence; blueprint §5 examples evidence `source_kind: test_report`, `path: reports/integration.json`, `trust_class: observed`; platform lacks verifier WP | Fixed design: contract §14.3, platform ADR-023, `evidence_trust`, P2a-WP9; validator |
| PPM-02 | High | [V] | Criteria lack test/probe mapping → cannot determine test scope | Fixed: `verification_refs` (2.1) |
| PPM-03 | High | [V] | Updates/checks unlinked from criteria → forces LLM guessing | Fixed: `criterion_ids`, `test_ids` (2.1) |
| PPM-04 | Medium | [D] | Waivers exist in blueprint §6 but assessment cannot represent them | Fixed: ADR-024, `waived` + `waiver_ref` |
| PPM-05 | High | [V] | Validator PASS on duplicate criteria, cyclic/dangling supersession, year 2099 timestamps | Fixed: validator + 9 new cases |
| PPM-06 | High | [V] | Repo copy preserving `project_id` → two products merged | Fixed design: identity_conflict (contract §14.4), C42 |
| PPM-07 | Medium | [V] | Registry YAML lacks 17 fields compared to `location-registration` 1.1; `cloud_processing` has no equivalent | Fixed: registry as import format, mapping table (contract §14.7, scanner guide §9) |
| PPM-08 | Medium | [D] | Baseline creation flow from `acceptance.yaml` draft undefined; D1 depends on it | Fixed design: contract §14.5, C44 |
| PPM-09 | Medium | [D] | PPM blueprint §9 selects LangGraph as primary orchestration, §10 separate executor, §18 MVP through M4 — contradicts platform ADR-011/017 and D1 scope | Fixed: blueprint §19 supersession table |
| PPM-10 | Medium | [D] | Eval scenario #19 (portfolio total with missing permissions) lacks rules | Fixed design: C43 |
| PPM-11 | Medium | [D] | 20 mandatory eval scenarios (blueprint §13) unmapped to executable cases; #3, #6, #9, #10, #16 lack cases | Fixed plan: `ppm-v2/evals/SCENARIO_MATRIX.md`, PPM-M0-WP1 |
| PPM-12 | Low | [D] | `status.yaml` "designated writer" indistinguishable in file | Accepted: status is derived summary, not used for verdicts |
| PPM-13 | Low | [D] | Prompts omit claim-criterion linkage instructions, omit clarifying self-recorded reports are claims only | Fixed: prompts 03, 05 |
| PPM-14 | Medium | [D] | Program/portfolio/benefit/capacity lack contracts; project-level only | Open: PPM-C1 prior to P4-WP1 |
| PPM-15 | Low | [D] | Notes lack structure (kind/timestamp/source) | Accepted for 2.1; re-evaluate in 2.2 after real data |

### PPM-01 — Report in repo treated as evidence (Critical)
- Evidence: contract §6 "collector collects hash/report metadata independently and verifier confirms" (omits verifier actions); blueprint §5 example draws `reports/integration.json` from repo with `trust_class: observed`; platform docs/04 round 2 stated "evidence collected by collector/verifier (`trust_class=observed`)"; platform plan lacked verifier WP.
- Failure: coding agent (or person) writes `reports/ac003-junit.xml` PASS and update points to it; collector "observes" file matching revision → criterion verified → reviewer observes "verified" and accepts an unexecuted capability.
- Correction: four trust classes; only verifier/CI-attested verify; in-repo report file = claim with file (FAIL remains refutation signal). P2a-WP9 Verifier sandbox; contract `ppm-output` `evidence_trust`; fixture AC-003 demonstrates forgery path.
- Verification: negative "repo-written test report used to verify (forged-report path)"; runtime C40.

### PPM-05 — Validator misses semantic errors (High, [V])
- Evidence: spike on template copy (adding duplicate criteria, `upd-b`↔`upd-c` mutual supersession, `upd-d` superseding `upd-missing`, `recorded_at: 2099-01-01`) → `validate_contract.py`: `PASS 8 records; structural checks only`.
- Failure: cyclic correction chains cause reducer infinite loops or wrong active selection; duplicate criteria distort denominator; future timestamps render updates permanently "newest".
- Correction: `semantic_doc_errors`, `semantic_set_errors`; runner executes directory-level cases.
- Verification: `run_negative_cases.py` → `PASS 5 templates, 20 cases`.

### PPM-06 — Copying repo preserves project_id (High, [V])
- Evidence: copy template to another directory, rename product; `project_id` remains `proj-example`, validator on each directory PASS; contract §4 merely recommends "must issue new project ID".
- Failure: two distinct products merge updates and progress under a single project.
- Correction: identity_conflict when identical project_id encountered at distinct `repo_identity` sources; quarantine; owner decides.
- Verification: C42 (P2a-WP2).

### PPM-07 — Registry differs from platform contract (Medium, [V])
- Evidence: comparing fields in `templates/registry.example.yaml` against `contracts/location-registration.schema.json`: missing `source_id, host_id, connector_id, artifact_aliases, identity, classification, egress_policy_ref, follow_symlinks, allow_hardlinked_files, approved_by_ref, …` (17 fields).
- Failure: two locations configuring location, owner modifies one, other desyncs; `cloud_processing: approved_only` misinterpreted as egress authority.
- Correction: registry treated as standalone/import format; mapping table; egress never mapped automatically.

## 5. Design choices
| Decision | Selected | Alternative | Trade-off | Reversible | Evidence prompting change |
|---|---|---|---|---|---|
| Contract 2.1 additive | Add optional fields, read both 2.0 and 2.1 | Jump to 3.0 forcing migration | Does not break adopted repos; validator requires version checking | Easy | If existing field semantics must change |
| Verifier reruns tests | Local sandbox | Trust CI attestation exclusively | Local incurs test rerun cost; CI requires trusted pipeline | Both coexist | Repos unable to execute tests locally |
| Owner-approved test mapping | `verification_refs` in baseline | LLM autonomously maps tests↔criteria | Owner overhead; zero guessing | Can allow LLM proposal, owner approval | Evals demonstrating LLM proposals sufficiently accurate for approval only |
| Deterministic reducer | Pure Python rules (PPM-M0) | LLM judge | Less flexible; fully verifiable | — | No |

## 6. External claims
Zero new vendor claims added; reuses §8 table from platform review. ISO/TC 258 used as conceptual framework only (blueprint §2); compliance unasserted.

## 7. Changes applied
- `ppm-v2/schemas/contract.schema.json` → 2.1 (reads 2.0): `verification_refs`, `criterion_ids`, `test_ids`, `uses_component`.
- `ppm-v2/standards/PROJECT_MANAGEMENT_CONTRACT.md` §14; blueprint §19; `guides/SCANNER_IMPLEMENTATION.md` §9; prompts 01–08 (referencing 2.1, criterion linkage, self-recorded reports are claims; prompt 08 pointing to new prompt).
- `ppm-v2/tools/validate_contract.py` (semantic checks), `ppm-v2/tools/run_negative_cases.py` (directory-level cases), `ppm-v2/evals/negative_cases.json` (+9), templates → 2.1, `ppm-v2/evals/SCENARIO_MATRIX.md` (new).
- Platform: `contracts/ppm-output.schema.json` (`evidence_trust`, `waived`, `waiver_ref`, reason codes, `previous_status` accepts `waived`), `examples/ppm-output.json`, `tools/validate_package.py` (+3 negative, 1 positive waiver), `docs/01` (ADR-023/024), `docs/04`, `evals/CONFORMANCE_CASES.md` (C40–C44), `docs/IMPLEMENTATION_PLAN.md` (plan 1.4: P2a-WP9, effort), `docs/ARCHITECTURE_REVIEW.md` (ARCH-34/35, D-07).

## 8. Validation
| Command | Actual output |
|---|---|
| Spike: `validate_contract.py` on metadata root with duplicate criteria, cyclic supersession, dangling supersession, year 2099 `recorded_at` (pre-fix) | `PASS 8 records; structural checks only` (errors leaked) |
| Spike: copy template preserving `project_id` | Both directories PASS, identical `proj-example` |
| Spike: compare registry against `location-registration` | Missing 17 fields |
| `python tools/run_negative_cases.py` (post-fix) | `PASS 5 templates, 20 cases` |
| `python tools/validate_contract.py templates/project/.ppm --schema schemas/contract.schema.json` | `PASS 4 records; structural and contract-semantic checks only` |
| `python tools/validate_contract.py <acceptance.yaml 2.0 original at 0f91f2d>` | `PASS 1 records` (2.0 compatible) |
| `python tools/validate_package.py` (repo root) | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` |
Untested: prompts with real coding agents, collector/verifier/reducer (code absent), 20 scenario evals, all runtime C40–C44 cases.

## 9. Unsettled decisions (owner)
- D-07 (shared with platform): verifier sandbox mechanism on real repositories.
- PPM-D1: which CI attestation policies qualify as `ci_attested` (pipelines, signatures, configurator authority).
- PPM-D2: authorized approvers and maximum duration for waivers.
- PPM-D3: evidence TTL by kind (overlaps platform D-03).
- PPM-D4: program/portfolio scope required for shared pilot (benefit register, capacity, scenarios) prior to authoring PPM-C1.
