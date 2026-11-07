# STATUS

Last updated: 2026-11-06 by architecture review (plan rev 1.5). Coding agents update this file at the end of every session (`AGENTS.md`).

Status values: `not_started` · `in_progress` · `ready_for_review` · `changes_requested` · `blocked` · `accepted` (owner only).

## Current position
- Phase: **P0 closed** (DG-1, DG-2 signed by owner 2026-11-07 with conditions DG1-C1…C5, DG2-C1).
- Next WP (platform, critical path): **P1a-WP1 Schema and migrations**.
- Can run in parallel: **PPM-M0-WP3 Findings and progress**, **PPM-M0-WP4 Rollup rules** (after WP3 or in a separate session), spike **P0-WP4 LangGraph replay** (before P1a-WP2).
- Every P1a/P2a WP that touches a DG condition must show that condition's evidence in its packet.

## Work packages to D0-thin-slice

| WP | Title | Prereqs | Status | Branch / commit | Acceptance evidence | Notes |
|---|---|---|---|---|---|---|
| P0-WP0 | English canonical docs | — | accepted | `wp/P0-WP0` (a36d44e) | `check_translation.py` (--compare: 38 pairs 0 diff, --fixture: PASS, --links: 0 broken, --tokens: 20.7% reduction, default: 38 up-to-date), `validate_package.py` PASS, `run_negative_cases.py` PASS, `sha256sum` PASS | ADR-025, ISS-007 resolved; accepted by owner |
| P0-WP1 | Skeleton, CI, environment | P0-WP0 | accepted | `wp/P0-WP1` (code commit `59e1135`; packet `de3b8cc`; merged `a1b6b62`) | R1–R9 resolved; `uv sync --locked` PASS, `pytest -q` PASS (9 tests), `npm run build` PASS, docker compose (pg 18.6 healthy, redis/qdrant optional) PASS, alembic SC-06 PASS, standalone PPM PASS, `validate_package.py` PASS, `run_negative_cases.py` PASS, `check_translation.py` PASS, `sha256sum` PASS, GitHub Actions CI 42318570673 PASS | `docs/DEV_ENVIRONMENT.md` checklist satisfied |
| P0-WP2 | Contract models | P0-WP1 | accepted | `wp/P0-WP2` (code commit `2d5c112`; packet `d1b5934`; merged `3ae4eb8`) | SC-01 PASS, SC-02 PASS (46 tests), 16/16 schema parity PASS, 50/50 negative parity PASS, differential 5003 mutations / 0 violations, template digest 76580e7c...a436 PASS, ruff & mypy clean, `sha256sum` PASS | ISS-009 resolved; W2-1, W2-2 resolved |
| P0-WP7 | Decision record DG-1/DG-2 | P0-WP2 | accepted | `wp/P0-WP7` (code commit `33c09c0`; merged `1e61e8e`) | DG-1 (strict rejection, vectors), DG-2, sequences, draft DDL (W7-3 scope cardinality), test_dg1_vectors PASS (23), test_draft_ddl PASS (8), 77 pytest PASS, SC-01 PASS, ruff & mypy clean, sha256sum PASS | ends at `P0 ready_for_owner_review` |
| DG-1, DG-2 | Owner signature | P0-WP7 | accepted | signed 2026-11-07 by owner | `docs/adr/DG-1.md` §0, `docs/adr/DG-2.md` §0 | conditions DG1-C1…C5, DG2-C1 bind P1a/P2a/PPM-M0/P5 WPs |
| P0-WP3 | Spike: Windows path containment | — | not_started | | | gates DG-3; run before P2a-WP2 (collector `safe_open`) |
| P0-WP4 | Spike: LangGraph replay (pinned) | — | not_started | | | resume gate / D-01; run during DG-1/DG-2 review, before P1a-WP2 |
| P0-WP5 | Spike: vendor lookup/cancel | — | not_started | | | gates DG-4; before P4-WP2; needs owner's target provider |
| P0-WP6 | Spike: Ollama throughput | — | not_started | | | gates DG-3; needs pilot GPU + owner model choice |
| PPM-M0-WP1 | Fixture corpus | P0-WP1 | accepted | `wp/PPM-M0-WP1` (code commit `ec98866`; packet `2b0aced`; merged `bd98352`) | `check_fixtures.py` PASS (19 fixtures, 3 rollup), `validate_package.py` PASS, `run_negative_cases.py` PASS, `check_translation.py` PASS, `sha256sum` PASS, `pytest` PASS (9 passed), `npm run build` PASS | s01, s21, s19 signed by owner 2026-11-07 |
| PPM-M0-WP2 | Criterion reducer | PPM-M0-WP1 | accepted | `wp/PPM-M0-WP2` (code commit `d9c0964`; merged `9a31182`) | 18/18 fixtures match post-JCS, s29 InputRejected, determinism hash identical, audit hook clean (0 events), allowlist probe fails; `pytest` PASS (91 passed: 53 platform + 38 reducer), ruff & mypy clean, `validate_package.py` PASS, `run_negative_cases.py` PASS, `check_translation.py` PASS, `sha256sum` PASS, `npm run build` PASS | Pure reducer: DAG supersession, trust classes, TTL boundary second, waivers, claims conflict; M2-1..M2-3 resolved |
| PPM-M0-WP3 | Findings and progress | PPM-M0-WP2 | not_started | | | |
| PPM-M0-WP4 | Rollup rules | PPM-M0-WP2 | not_started | | | |
| PPM-M0-WP5 | Runner and packaging | PPM-M0-WP3, WP4 | not_started | | | |
| P1a-WP1 | Schema and migrations | DG-1, DG-2 | ready_for_review | `wp/P1a-WP1` (code commit `a5a3c4b`) | P1-1 composite FK tenant isolation (7 FKs, unique parent keys, catalog test + 7 cross-tenant rejection tests), P1-2 CI collection failure enforcement, SC-06 PASS (empty up/down/up + seeded down/up), 38 constraint tests PASS against PostgreSQL 18.6 (DG1-C3, C02, C15, C29, C32/W7-3, C33, P1-1), autogenerate diff empty, draft-vs-migration parity PASS; `pytest -q` 160 passed; ruff & mypy clean; GitHub Actions CI 42353502611 PASS; `validate_package.py` PASS, `run_negative_cases.py` PASS, `check_fixtures.py` PASS, `check_translation.py` PASS, `sha256sum` PASS, HUD build PASS | Conformance: C02, C15, C29, C32, C33, DG1-C3; ISS-017 opened; ISS-001 resolved |
| P1a-WP2 | Task/Run service | P1a-WP1 | not_started | | | |
| P1a-WP3 | Outbox relay, ingestion, projection | P1a-WP1 | not_started | | | |
| P2a-WP1 | Sources and project scopes | P1a-WP1 | not_started | | | |
| P2a-WP2 | Collector (read-only) | P2a-WP1 | not_started | | | |
| P2a-WP3 | Ingest `.ppm` and notes | P2a-WP2 | not_started | | | |
| P2a-WP4 | Reducer integration | P2a-WP3, PPM-M0-WP5 | not_started | | | |
| D0 | Thin slice demo | P1a-WP3, P2a-WP4 | not_started | | | owner reviews the page |

The remaining WPs to D1-fixture (P2a-WP5…WP9, P1a-WP4…WP7, P3a-WP1…WP4, D1) are added here when D0 is accepted.

## Session log (newest first)
| Date | WP | Agent/session | Result | Commit |
|---|---|---|---|---|
| 2026-11-07 | P1a-WP1 | coding agent (implementation lead) | ready_for_review; addressed changes_requested (P1-1: composite FK tenant isolation across 7 FKs, UNIQUE (tenant_id, id/source_id) on parents, catalog test + 7 cross-tenant tests, Amendment A1 in DG-1/docs/01/draft DDL; P1-2: CI collection failure enforcement if TEST_DATABASE_URL unset); SC-06 empty & seeded cycles pass, autogenerate diff empty, draft parity pass; 160 pytest pass, ruff & mypy clean, CI 42353502611 pass | `a5a3c4b` |
| 2026-11-07 | P1a-WP1 | coding agent (implementation lead) | ready_for_review; 0001_core migration + SQLAlchemy Core metadata, 16 tables, state CHECK constraints, SC-06 empty & fixture DB cycles pass, 30 constraint tests pass against PG 18.6 (DG1-C3, C02, C15, C29, C32, C33), autogenerate diff empty, draft parity pass; 152 pytest pass, ruff & mypy clean, CI 42350572193 pass | `6cfe452` |
| 2026-11-07 | DG-1, DG-2 | owner | signed: DG-1.1–1.4 and DG-2.1–2.5 approved with conditions DG1-C1…C5, DG2-C1; **P0 closed**; P1a-WP7 relay threshold ≥ 200 events/s | (this commit) |
| 2026-11-07 | P0-WP7, PPM-M0-WP2 | owner | accepted after re-review (P0-WP7: 9/9 vectors recomputed, strict key rejection probed, DDL parsed independently, W7-1..3 fixed; PPM-M0-WP2: off-corpus decision-table probes, M2-1..3 fixed); ISS-015..016 registered; spike rows P0-WP3..WP6 added | `1e61e8e`, `9a31182` |
| 2026-11-07 | P0-WP7 | coding agent (implementation lead) | ready_for_review; DG-1 & DG-2 decision records, draft DDL, C29/C29b/C33/C34/C39 sequence diagrams, 65 tests pass, DDL parsed via pglast, ruff & mypy clean | `db61cf7` |
| 2026-11-07 | PPM-M0-WP2 | coding agent (implementation lead) | ready_for_review; addressed changes_requested (M2-1 waiver active validation, M2-2 future evidence rejection, M2-3 missing revision rejection); 91 tests pass, ruff & mypy clean | `wp/PPM-M0-WP2` |
| 2026-11-07 | PPM-M0-WP2 | coding agent (implementation lead) | ready_for_review; pure deterministic criterion reducer (`assess_criteria`), 18/18 fixtures match post-JCS, s29 InputRejected, determinism verified, import boundary allowlist hardened + runtime audit hook, 88 tests pass, ruff & mypy clean | `wp/PPM-M0-WP2` (`76640fd`) |
| 2026-11-07 | P0-WP2, PPM-M0-WP1 | owner | accepted after re-review (P0-WP2: differential 5089 mutations / 0 model_looser; PPM-M0-WP1: 19 fixtures, cross-checks, s01/s21/s19 signed); ISS-010..012 registered | `3ae4eb8`, `bd98352` |
| 2026-11-07 | P0-WP2 | coding agent (implementation lead) | ready_for_review; addressed changes_requested (W2-1 strict models, constraint parity, 5003 differential mutations / 0 violations; W2-2 validator negative helpers import); 46 tests pass, ruff & mypy clean | `wp/P0-WP2` (code commit `2d5c112`) |
| 2026-11-07 | P0-WP2 | coding agent (implementation lead) | ready_for_review; 16 Pydantic v2 contract models, rules.py invariant port, RFC 8785 JCS digest, 44 tests pass (16/16 schema parity, 50/50 negative parity, template digest verified), ruff & mypy clean | `wp/P0-WP2` (code commit `c176ba7`) |
| 2026-11-07 | PPM-M0-WP1 | coding agent (implementation lead) | ready_for_review; addressed changes_requested (M1-1..M1-4: findings schema, 5 new fixtures, mixed rollup, cross-checks); check_fixtures PASS (19 fixtures, 3 rollup) | `wp/PPM-M0-WP1` (ec98866) |
| 2026-11-07 | P0-WP1 | owner | accepted after re-review (R1–R9 verified; follow-ups: import-boundary hardening in PPM-M0-WP2, cross-platform checksum tool); merged into main | `a1b6b62` |
| 2026-11-07 | P0-WP1 | coding agent (implementation lead) | ready_for_review; R1–R9 changes_requested fixes applied (SecretStr canary redaction, .env.example ordering, alembic config, raw validation evidence, import boundary hardening, HUD error handling, CI pinning); all checks pass; CI 42318570673 success | `wp/P0-WP1` (59e1135) |
| 2026-11-06 | P0-WP1 | coding agent (implementation lead) | ready_for_review; modular monolith skeleton, uv workspace, Vite+React HUD shell, compose & alembic SC-06 verified, all tests pass | `wp/P0-WP1` (f1a9557) |
| 2026-11-06 | P0-WP0 | owner | accepted; merged wp/P0-WP0 into main | (owner action) |
| 2026-11-06 | P0-WP0 | coding agent (implementation lead) | ready_for_review; 38 docs translated, tools/check_translation.py created, ISS-007 resolved (-20.7% tokens), all shared checks pass | `wp/P0-WP0` (a36d44e) |
| 2026-11-06 | — | architecture review | plan rev 1.5, workflow files created | (owner commit) |
