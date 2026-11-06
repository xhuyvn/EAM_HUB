# STATUS

Last updated: 2026-11-06 by architecture review (plan rev 1.5). Coding agents update this file at the end of every session (`AGENTS.md`).

Status values: `not_started` · `in_progress` · `ready_for_review` · `changes_requested` · `blocked` · `accepted` (owner only).

## Current position
- Phase: design and review complete; implementation not started.
- Next WP: **P0-WP0 English canonical docs** (prompt: `prompts/START_NEXT_MILESTONE.md`).
- Can run in parallel once P0-WP1 is accepted: **PPM-M0** (prompt: `ppm-v2/prompts/START_NEXT_MILESTONE.md`).
- Waiting on owner: sign DG-1/DG-2 after P0-WP7 (`docs/IMPLEMENTATION_PLAN.md` §3).

## Work packages to D0-thin-slice

| WP | Title | Prereqs | Status | Branch / commit | Acceptance evidence | Notes |
|---|---|---|---|---|---|---|
| P0-WP0 | English canonical docs | — | not_started | | | ADR-025 |
| P0-WP1 | Skeleton, CI, environment | P0-WP0 | not_started | | | `docs/DEV_ENVIRONMENT.md` checklist |
| P0-WP2 | Contract models | P0-WP1 | not_started | | | |
| P0-WP7 | Decision record DG-1/DG-2 | P0-WP2 | not_started | | | ends at `P0 ready_for_owner_review` |
| DG-1, DG-2 | Owner signature | P0-WP7 | not_started | | | owner action; gates P1a-WP1 |
| PPM-M0-WP1 | Fixture corpus | P0-WP1 | not_started | | | |
| PPM-M0-WP2 | Criterion reducer | PPM-M0-WP1 | not_started | | | |
| PPM-M0-WP3 | Findings and progress | PPM-M0-WP2 | not_started | | | |
| PPM-M0-WP4 | Rollup rules | PPM-M0-WP2 | not_started | | | |
| PPM-M0-WP5 | Runner and packaging | PPM-M0-WP3, WP4 | not_started | | | |
| P1a-WP1 | Schema and migrations | DG-1, DG-2 | not_started | | | |
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
| 2026-11-06 | — | architecture review | plan rev 1.5, workflow files created | (owner commit) |
