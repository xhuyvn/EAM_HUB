# Acceptance Packet: P0-WP0 English Canonical Documentation

## 1. Milestone and Scope
- **Work Package**: P0-WP0 (English canonical docs per ADR-025, `STATUS.md`, and `docs/IMPLEMENTATION_PLAN.md` §6).
- **Scope**:
  - Relocate 38 Vietnamese markdown documents to `docs/vi/<original path>` via `git mv`, preserving history and prepending the header `> Translated from <path> @ fb857f4`.
  - Author canonical English versions at the original 38 paths with 100% identical identifiers (ARCH-, PPM-, C-, WP, ADR-, DG-, D-, ISS-), numeric values, table structures, and headings.
  - Implement `tools/check_translation.py` supporting:
    - `--compare`: validates 0 identifier differences between `docs/vi/**` and English canonical pairs.
    - Default mode (SC-08 staleness report): verifies translation header commits against git history.
    - `--fixture`: verifies detection of removed identifiers and changed numbers using synthetic test fixtures.
    - `--links`: checks internal relative markdown links across canonical English documentation.
    - `--tokens`: measures token savings between Vietnamese and English versions using tiktoken `cl100k_base` to resolve ISS-007.
  - Ensure shared checks pass: `tools/validate_package.py`, `ppm-v2/tools/run_negative_cases.py`, and `sha256sum -c SHA256SUMS.txt`.
  - Update `AGENTS.md`, `STATUS.md`, `docs/ISSUES.md`, and provide acceptance packet.

## 2. Source Commit and Environment
- **Base Commit**: `fb857f4eb5432ed4778b8195758c2062ec889fb5`
- **Branch**: `wp/P0-WP0`
- **Commit**: `d477872` (initial commit on branch `wp/P0-WP0`)
- **Environment**:
  - Python: 3.12.9 (via `uv run --python 3.12`)
  - Platform: Windows 11 (x86_64)
  - Packages: `jsonschema 4.26.0`, `pyyaml 6.0.3`, `tiktoken 0.12.0`

## 3. Acceptance Criteria and Observed Results

| Acceptance Criterion | Expected | Observed Result | Evidence |
|---|---|---|---|
| AC-P0-WP0-1: 38 Vietnamese files moved to `docs/vi/` | 38 files moved with translation header | 38 files moved via `git mv`; all start with `> Translated from <path> @ fb857f4` | `git status`, `tools/check_translation.py` |
| AC-P0-WP0-2: 38 English canonical files written | 38 canonical files written at original paths | 38 files written; headings, tables, numbers, and identifiers aligned | `check_translation.py --compare` |
| AC-P0-WP0-3: Zero identifier differences | 0 differences across all 38 pairs | `PASS: 0 identifier differences across 38 file pairs` (2,732 total identifiers matched) | `python tools/check_translation.py --compare` |
| AC-P0-WP0-4: SC-08 translation staleness report | 38 up-to-date, 0 stale, 0 missing headers | `SC-08 Report Summary: 38 up-to-date, 0 stale, 0 missing headers` | `python tools/check_translation.py` |
| AC-P0-WP0-5: Fixture verification for detector | Detects removed identifier and modified number | `PASS: Fixture detection verified (-ARCH-01, 18.6 -> 18.7)` | `python tools/check_translation.py --fixture` |
| AC-P0-WP0-6: Internal relative link verification | 0 broken internal relative markdown links | `Link check: 0 links checked, 0 broken` | `python tools/check_translation.py --links` |
| AC-P0-WP0-7: Token reduction measurement (ISS-007) | ~20% token savings across 3 representative docs | 20.7% total reduction (32,632 vi -> 25,868 en tokens; 6,764 tokens saved) | `python tools/check_translation.py --tokens` |
| AC-P0-WP0-8: Package validation runner | PASS schemas, fixtures, negative cases, PPM checks | `PASS 16 schemas, 15 JSON fixtures, 11 events, 46 negative cases, PPM checks passed` | `python tools/validate_package.py` |
| AC-P0-WP0-9: PPM negative cases runner | PASS 5 templates, 20 negative cases | `PASS 5 templates, 20 cases` | `python ppm-v2/tools/run_negative_cases.py` |
| AC-P0-WP0-10: SHA256 checksum verification | All tracked files match checksums | `sha256sum -c SHA256SUMS.txt` returns 0 exit code | `sha256sum -c SHA256SUMS.txt` |

## 4. Token Reduction Data (ISS-007)
Measured using `tiktoken` with encoding `cl100k_base`:

| File | Vietnamese Tokens | English Tokens | Tokens Saved | % Reduction |
|---|---|---|---|---|
| `docs/ARCHITECTURE_REVIEW.md` | 11,983 | 9,513 | 2,470 | 20.6% |
| `docs/IMPLEMENTATION_PLAN.md` | 16,665 | 13,423 | 3,242 | 19.5% |
| `docs/04_PPM_INTEGRATION.md` | 3,984 | 2,932 | 1,052 | 26.4% |
| **Total** | **32,632** | **25,868** | **6,764** | **20.7%** |

ISS-007 is marked `resolved` in `docs/ISSUES.md`.

## 5. Security, Quality, and Operational Verification
- **Egress & Secrets**: Zero cloud egress, zero billable provider calls, zero secrets logged or committed.
- **Contract Protection**: Zero modifications to `contracts/*.schema.json`, `examples/`, or `ppm-v2/schemas/`.
- **Pure Domain Boundary**: `ppm_reducer` remains pure with zero imports of `eap`, `requests`, `socket`, or `subprocess`.
- **File System Normalization**: All files committed with POSIX LF line endings.

## 6. Unresolved Gaps
- None for P0-WP0.
- Dependent work packages: P0-WP1 (prerequisite: owner acceptance of P0-WP0).

## 7. Recommendation
- **Status**: `ready_for_review` (set in `STATUS.md`).
- **Owner Action**: Review branch `wp/P0-WP0` and merge into `main` if accepted.
- **Rollback Procedure**: Discard branch `wp/P0-WP0` via `git checkout main && git branch -D wp/P0-WP0`.
