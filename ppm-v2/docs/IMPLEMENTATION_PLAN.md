# Implementation Plan — PPM v2 Agent

Plan revision: PPM-1.1 (06/11/2026; aligns with platform plan rev 1.5: M0 post P0-WP1, embedded in P2a-WP4, included in D0 milestone). This plan does **not compete** with `docs/IMPLEMENTATION_PLAN.md` (platform, rev 1.5). It (1) introduces an independent milestone **PPM-M0** to build the domain core prior to platform readiness, (2) maps PPM work packages into platform WPs, and (3) adds WPs for program/portfolio/benefit that the platform currently lacks. Findings reference `ppm-v2/docs/ARCHITECTURE_REVIEW.md` (PPM-xx) and `docs/ARCHITECTURE_REVIEW.md` (ARCH-xx).

## 1. Boundaries
| Ownership | Content |
|---|---|
| PPM v2 (domain) | Contract `.ppm` 2.1, criterion/claim/evidence trust/waiver/baseline semantics, reducer rules, fixture corpus, rollup rules |
| Platform | Location registration, connector/collector, Run Service, scheduler, Model Gateway, verifier sandbox runtime, acceptance API, HUD |
The reducer core (M0) is a pure Python library: zero I/O, zero network, zero model dependencies; P2a-WP4 embeds it rather than reimplementing.

## 2. Roadmap and critical path
```
PPM-M0 (WP1 → WP2 → WP3, WP4)   runs parallel to platform P0/P1a
        │
        └─► P2a-WP4 (embeds reducer) ─► P2a-WP6 ─► D1-fixture (platform)
PPM-C1 (program/portfolio/benefit contract) ─► PPM-P4-WP1 ─► shared pilot
Track S (standalone adoption pilot) — optional, non-blocking
```
PPM critical path to D1-fixture: M0-WP1 → M0-WP2 → P2a-WP4. M0 remains off the platform critical path if completed prior to P2a (platform P0+P1a ≈ 12–17 days).

## 3. Milestone PPM-M0 — Deterministic reducer core (local MVP domain)
Objective: a pure, deterministic function `assess(inputs) -> assessment`, executable against fixtures, producing valid output according to `contracts/ppm-output.schema.json`. Effort: **4–6 days**, assuming 1 Python engineer, zero models. Prerequisite: P0-WP1 (uv workspace with empty member `ppm-v2/reducer`, Python 3.12); requires no DB or other services. Under platform plan rev 1.5, M0 executes during owner review of DG-1/DG-2.

### PPM-M0-WP1 Fixture corpus — 1–1.5 days
- Prerequisite: none. Paths: `ppm-v2/evals/fixtures/<scenario>/{inputs.json,expected.json,README.md}`, `ppm-v2/evals/fixtures/SCHEMA.md`.
- Steps: define `inputs.json` (baseline: criteria + `verification_refs` + weights; parsed claims from `.ppm`; evidence list with `trust_class`, `revision`, `observed_at`, `test_id`, `result`; waivers; `assessed_revision`; `now`). Author fixtures following `SCENARIO_MATRIX.md` for #1, 2, 3, 6, 7, 8, 10, 16, 19, and s21–s24.
- Acceptance: each fixture has handwritten `expected.json` prior to code; `expected.json` (single-project assessment) validates against `contracts/ppm-output.schema.json`. Rollup fixtures (s08, s10, s19) additionally include `expected_rollup.json` per `ppm-v2/evals/fixtures/ROLLUP_SCHEMA.md` (internal to M0; official contract at PPM-C1).
- Evidence: corpus commit hash; fixture → scenario mapping list.
- Rollback: additive file additions only.

### PPM-M0-WP2 Criterion reducer — 1.5–2 days [PPM-01/02/03/04/05/06]
- Prerequisite: WP1. Paths: `ppm-v2/reducer/ppm_reducer/{__init__.py,criteria.py,trust.py,supersession.py}`, `ppm-v2/reducer/tests/`.
- Steps: resolve supersession (reject cycles/dangling — reusing validator logic); map claims/evidence to criteria via `criterion_ids`/`test_ids`/`verification_refs`; apply decision table (review §2C); trust classes (only `verifier_observed`/`ci_attested` verify; FAIL in any class at exact revision → contradicted); stale by revision and parameterized TTL (D-03); unexpired waiver → `waived`; concurrent conflicting claims → finding `conflicting_claims` (not a reason_code; status follows evidence).
- Interface: `assess_criteria(baseline, claims, evidence, waivers, assessed_revision, now) -> list[CriterionResult]`.
- Failure tests: s21 forged JUnit → `unverified/reported_artifact_only`; s01 → contradicted; s06 missing evidence path → stale; s22 expired waiver → returns to denominator; cyclic supersession → `InputRejected`.
- Acceptance: all criterion fixtures match `expected.json` byte-for-byte post JCS normalization; 2 runs with identical input produce identical hash; zero imports of `requests`/`socket`/`subprocess` (enforced via tests).

### PPM-M0-WP3 Findings and progress — 1 day
- Prerequisite: WP2. Paths: `ppm_reducer/progress.py`, `ppm_reducer/findings.py`.
- Steps: progress `verified/total` with `total` excluding `waived`; `basis=none` absent a baseline (s24); findings `architecture_drift` (s03), `relationship_proposed` (s07), `identity_conflict` (s23) emitted as findings only, never altering verdicts.
- Acceptance: s24 → `progress.basis=none`, `acceptance_readiness=not_ready`; s03 alters zero criteria; denominator always displayed.

### PPM-M0-WP4 Rollup rules — 1–1.5 days [C24, C43, #10]
- Prerequisite: WP2. Paths: `ppm_reducer/rollup.py`.
- Steps: rollup by distinct deliverable (owner project); filter by authorized projects **prior** to computation; label `partial` without disclosing count/names of hidden projects; missing cost/budget → `cost_state=unknown` in `RollupResult` (internal to M0).
- Acceptance: s08 shared deliverable counted once; s19 partial total, output contains zero hidden project IDs (test grep); s10 `cost_state=unknown`.

### PPM-M0-WP5 Packaging and measurement — 0.5 days
- `ppm-v2/tools/run_reducer_fixtures.py` executes full corpus, printing `PASS n fixtures` or diff. Records execution time and fixture count in `ppm-v2/VALIDATION_REPORT.md`.
- Acceptance: runner executes cleanly on a clean machine with `tools/requirements.txt`; P2a-WP4 can `pip install -e ppm-v2/reducer`.

M0 complete when: WP1–WP5 acceptance criteria achieved; owner reviews and signs fixture `expected.json` for s01, s21, s19 (the three most easily misunderstood cases).

## 4. Mapping PPM into platform WPs
| PPM work | Platform WP | Review addition |
|---|---|---|
| Registry → location-registration import | P2a-WP1 | Contract §14.7 mapping; egress unmapped |
| Collector, identity_conflict | P2a-WP2 | C42 |
| Verifier sandbox | P2a-WP9 | D-07; fixture test opening socket blocked |
| Ingest `.ppm` 2.0/2.1 | P2a-WP3 | Uses `validate_contract.semantic_*`; quarantines unknown versions |
| Reducer | P2a-WP4 | Embeds PPM-M0; no rule reimplementation |
| Analysis step (claims from free text) | P2a-WP7 | Model extracts claims with source span only, zero verdicts |
| Baseline from draft, waiver, acceptance | P2b-WP1/WP2 | C41, C44, C45 (proposed) |
| Program/portfolio | P4-WP1 | Requires PPM-C1 |

## 5. PPM-C1 — Program/portfolio/benefit contract (prior to P4-WP1) — 2–3 design days
- Blocker: PPM-D4 (owner finalizes shared pilot scope).
- Contents: `program`, `portfolio`, `benefit` (single owner program, metric, baseline value, measured_at, `status ∈ {planned, measuring, realized, unknown}`), `capacity` (optional). Additive: contract 2.2, reads 2.0/2.1.
- Acceptance: s09, s20 have fixtures and expected; validator catches benefit with dual owner programs; reviewed by independent PPM practitioner (not author).

### PPM-P4-WP1 Program/portfolio rollup — 2–3 days (under platform P4-WP1)
- Acceptance: s08, s19, s20 at runtime with fixture data; HUD displays denominator and partial label.

## 6. Track S — Standalone adoption pilot (optional) — 1–2 owner work days
- Apply prompt 01 to **one** repo selected by owner, coding agent in read-only mode for prompt 06.
- Collect: duration, clarification questions, validator errors, update count per session. Review output never treated as verified.
- Outputs: prompt/template refinements; empirical data for PPM-15 (notes structuring).

## 7. Finding → WP → evidence
| Finding | WP | Acceptance evidence |
|---|---|---|
| PPM-01 Critical | M0-WP2 (rules), P2a-WP9 (runtime) | s21 PASS; C40 runtime |
| PPM-02, PPM-03 | M0-WP2 | Fixture lacking mapping → `coverage_not_mapped` |
| PPM-04 | M0-WP2, P2b-WP2 | s22; C41; `previous_status` accepts `waived` (schema updated) |
| PPM-05 | Completed (validator) | `run_negative_cases.py` 20 cases |
| PPM-06 | M0-WP3 (finding), P2a-WP2 | s23; C42 |
| PPM-07 | P2a-WP1 | Import test registry → location-registration |
| PPM-08 | M0-WP3, P2b-WP1 | s24; C44 |
| PPM-09 | Completed (blueprint §19) | Documentation review |
| PPM-10 | M0-WP4 | s19; C43 |
| PPM-11 | M0-WP1 | `SCENARIO_MATRIX.md` all 20 rows with assigned owner |
| PPM-14 | PPM-C1, PPM-P4-WP1 | s09, s20 |

## 8. Blocking decisions
| ID | Blocks | Default if unresolved |
|---|---|---|
| D-03 Evidence TTL | M0-WP2 (parameterized) | Parameterized; fixtures use 7 days |
| D-07 Verifier sandbox | P2a-WP9 | Fixture-only, no real repo execution |
| PPM-D1 CI attestation | `ci_attested` at runtime | Exclude class from pilot |
| PPM-D2 Waiver authority | P2b-WP2 | Project owner only, ≤30 days |
| PPM-D4 Portfolio scope | PPM-C1 | Defer program/benefit from local MVP |

## 9. Out of scope for this plan
Real repository data (except owner-executed Track S), billable providers, standalone PPM UI (uses platform HUD), standalone LangGraph orchestration (superseded by Run Service, blueprint §19).
