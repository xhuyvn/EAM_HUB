# s19-partial-portfolio

## Scenario Description
A viewer requests a portfolio rollup across `port-enterprise`. The viewer has permission to view `proj-public-frontend` and `proj-public-api`, but lacks permission to view `proj-secret-analytics` and `proj-classified-ml`.

## Decision Table Row Applied
- **Single-Project Assessment**: `Verifier/CI PASS mapped tests at exact revision, no FAIL` -> `verified`.
- **Rollup Aggregation**: Confidentiality-preserving partial rollup rule (PPM-10, C43, & Implementation Plan §3 M0-WP4).

## Rationale
- **Zero Information Leakage**:
  - The unauthorized projects (`proj-secret-analytics`, `proj-classified-ml`) are filtered out **prior** to any aggregation computation.
  - The aggregate output is explicitly labeled `partial: true`.
  - The output contains **zero** references to the hidden projects: no project IDs, no project names, no count of hidden projects, and no weights or costs belonging to hidden projects appear anywhere in the output.
  - `distinct_deliverables_count` is 2, `authorized_project_ids` lists only the 2 authorized projects, and progress/financials reflect solely the authorized projects (5.0 / 5.0 weight, $12,000 cost).
