# Rollup Schema Specification (M0 Internal)

This document specifies the structure, aggregation rules, and confidentiality constraints for `expected_rollup.json` files within the `ppm-v2/evals/fixtures/` corpus.
The schema is machine-checked by `ppm-v2/evals/fixtures/rollup.schema.json` and validated by `ppm-v2/tools/check_fixtures.py`.

## 1. Principles and Aggregation Rules

The portfolio/program rollup computes overall progress across multiple projects while enforcing strict confidentiality and single-ownership accounting:

1. **Distinct Deliverable Counting**:
   - Each deliverable belongs to exactly one owner project.
   - Consumer projects reference shared components via `dependency_refs` (or `uses_component`) and exclude them from their own progress denominator.
   - In a portfolio rollup, each shared deliverable is counted **exactly once** in `distinct_deliverables_count` and in `progress.total_weight`.
2. **Partial Visibility Without Information Leakage (C43, #19)**:
   - When a viewer lacks authorization to view certain projects in a portfolio, the aggregation filters out unauthorized projects **before** computation.
   - The result sets `partial: true`.
   - The result MUST NOT disclose:
     - The count of hidden projects.
     - The names or IDs of hidden projects.
     - The weights or costs of hidden projects.
   - `authorized_project_ids` contains only the project IDs the viewer is permitted to see.
3. **Cost and Budget State Handling (#10)**:
   - If any authorized project has missing or unmeasured cost data, `cost_state` is set to `"unknown"`.
   - When `cost_state` is `"unknown"`, `cost_usd` MUST be `null` (never defaulted to `0`).
   - When all authorized projects have known costs, `cost_state` is `"known"` and `cost_usd` reflects the arithmetic sum.
4. **Explicit Percentage Denominators**:
   - Progress percentage is always backed by explicit `verified_weight` and `total_weight` values.
   - `percent = round((verified_weight / total_weight) * 100, 2)`.

---

## 2. Field Specifications

| Field | Type | Required | Description |
|---|---|---|---|
| `portfolio_id` | `string` | Yes | Identifier of the portfolio or program being aggregated. |
| `as_of` | `string` (date-time) | Yes | ISO-8601 evaluation timestamp. |
| `partial` | `boolean` | Yes | `true` if viewer authorization excludes one or more projects; `false` if all projects included. |
| `authorized_project_ids` | `array[string]` | Yes | List of project IDs included in the rollup. |
| `distinct_deliverables_count` | `integer` | Yes | Count of distinct deliverables aggregated across owner projects. |
| `distinct_deliverable_ids` | `array[string]` | Yes | List of unique deliverable IDs included. |
| `progress` | `object` | Yes | Aggregate progress object. |
| `cost_state` | `enum["known", "unknown"]` | Yes | Cost state indicator. |
| `cost_usd` | `number \| null` | Yes | Summed cost, or `null` if `cost_state=unknown`. |
| `budget_usd` | `number \| null` | No | Summed budget, or `null` if unmeasured. |

### `progress` Sub-Object
- `basis`: `baseline_weighted` | `none`.
- `verified_weight`: Total weight of verified deliverables.
- `total_weight`: Total weight of all applicable deliverables (excluding waived criteria).
- `percent`: `(verified_weight / total_weight) * 100` rounded, or `null` if `basis=none`.
- `criteria_total`: Count of all criteria across authorized projects.
- `criteria_verified`: Count of verified criteria across authorized projects.
