# ISSUES

Open problems, risks and questions found during implementation or review. GitHub Issues may replace this file later; if so, keep the `ISS-NNN` IDs.

Fields: ID · opened · by · labels (`decision`, `contract`, `risk`, `bug`, `hud-feedback`, `env`) · blocks (WP IDs) · status (`open`, `in_progress`, `resolved`, `wontfix`) · summary · resolution.

| ID | Opened | By | Labels | Blocks | Status | Summary | Resolution |
|---|---|---|---|---|---|---|---|
| ISS-001 | 2026-11-06 | review | decision | P1a-WP1 | resolved | Owner signs DG-1 and DG-2 after P0-WP7 | Owner signed DG-1 and DG-2 on 2026-11-07; P0 closed |
| ISS-002 | 2026-11-06 | review | decision | Local-pilot | open | D-07: sandbox mechanism for the verifier on real repositories | |
| ISS-003 | 2026-11-06 | review | decision | PPM-C1 | open | PPM-D4: program/portfolio/benefit scope for the shared pilot | |
| ISS-004 | 2026-11-06 | review | decision | PPM-M0-WP2 (default exists) | open | D-03 evidence TTL and PPM-D2 waiver approver/max duration; defaults 7 days and owner-only ≤ 30 days | |
| ISS-005 | 2026-11-06 | review | env | P0-WP1 | open | Verify image tags `postgres:18.6-trixie`, `redis:8.10.1-trixie`, `qdrant/qdrant:v1.20.0` with `docker pull`; record digests | |
| ISS-006 | 2026-11-06 | review | risk | private server | open | Redis 8 license (AGPL-3.0/RSAL/SSPL) to be checked against the bank's software license policy | |
| ISS-007 | 2026-11-06 | review | env | P0-WP0 | resolved | Measure token savings of English vs Vietnamese on 3 representative docs before and after translation | Measured via `tools/check_translation.py --tokens` (cl100k_base): 32,632 vi -> 25,868 en tokens (-6,764 tokens, 20.7% reduction across `docs/ARCHITECTURE_REVIEW.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/04_PPM_INTEGRATION.md`) |
| ISS-008 | 2026-11-06 | review | env | review sessions | open | The Cowork folder bridge cannot delete files, so git pull/commit from a review session leaves lock files. Owner runs git on Windows, or grants delete permission for the folder | |
| ISS-009 | 2026-11-07 | P0-WP2 | contract, decision | — | resolved | ECMA-262 lookaround regex `^(?!/)(?!.*\\)...` in `location-registration.schema.json` is rejected by Pydantic's Rust regex engine | Resolved in `eap.contracts.models.base` via `SafePathStr` using Python `re` compiled pattern and `AfterValidator(_validate_safe_path)`; parity and negative tests pass. |
| ISS-010 | 2026-11-07 | PPM-M0-WP1 | contract | — | open | `ppm-output.schema.json` has no structured `findings`; M0 uses internal `findings.schema.json` + `expected_findings.json` (was ISS-NEW-1) | Propose `findings[]` in next contract minor (PPM-C1 or earlier) |
| ISS-011 | 2026-11-07 | PPM-M0-WP1 | contract | — | open | Program/portfolio rollup is internal to M0 (`rollup.schema.json`) (was ISS-NEW-2) | Standardize in PPM-C1 |
| ISS-012 | 2026-11-07 | review | contract | — | open | No reason code for TTL staleness; s26 uses `evidence_stale_revision` although the revision is current | Decide: add `evidence_stale_ttl` in a contract minor, or document TTL as a revision-stale variant |
| ISS-013 | 2026-11-07 | P0-WP7 | contract | — | open | Align `action-proposal.schema.json` description from `intent_key` to `action_slot` in contract 1.2 to match DG-1 | Propose in next contract minor update |
| ISS-014 | 2026-11-07 | P0-WP7 | decision | P1a-WP4 | resolved | Policy for external_trusted adapters lacking client token support | Owner decided both: mandate host-level lease lock to prevent concurrent dispatch, and enforce that unknown outcome is never auto-retried (C38); requires audited operator intervention if lookup is unsupported. |
| ISS-015 | 2026-11-07 | PPM-M0-WP2 | fixtures | PPM-M0-WP5 | open | Reducer behaviour added in M0-WP2 review is covered by unit tests only: inactive waiver (missing/empty `approved_by`, null or future `approved_at`, `expires_at == now`), `evidence_from_future`, `missing_assessed_revision` (was ISS-NEW-3..5) | Author fixtures s30–s32 in an M0-WP1 amendment before M0-WP5 packaging |
| ISS-016 | 2026-11-07 | review | security | P1a-WP2 | open | Key derivation rejects `|`, edge whitespace, non-NFC and empty values, but accepts control characters (newline, NUL) inside identifiers | Define an identifier charset allowlist (e.g. `[A-Za-z0-9._:#/-]`) in `eap.contracts.keys` and API validation in P1a-WP2 |
| ISS-017 | 2026-11-07 | P1a-WP1 | contract | P1a-WP2 | open | Align tasks.state CHECK constraint and draft DDL default ('queued') with contracts/hud-projection.schema.json task_state enum | Supported 'queued' alongside 'draft', 'ready', 'active', 'blocked', 'review_required', 'completed', 'failed', 'cancelled'; unify in contract 1.2 / P1a-WP2 lifecycle |
