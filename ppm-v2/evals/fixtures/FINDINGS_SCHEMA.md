# Findings Schema Specification (M0 Internal)

This document specifies the structure and semantics for `expected_findings.json` files within the `ppm-v2/evals/fixtures/` corpus.
The schema is machine-checked by `ppm-v2/evals/fixtures/findings.schema.json` and validated by `ppm-v2/tools/check_fixtures.py`.

## 1. Principles and Separation of Concerns

Under the PPM domain architecture (PPM-03, PPM-06, ADR-023, and Implementation Plan §3 M0-WP3):
1. **Verdicts Decoupled from Findings**: Structural anomalies, proposed relationships, identity conflicts, and concurrent claims are emitted as **domain findings**. They **never** mutate or overturn deterministic criterion verification verdicts (`verified`, `contradicted`, `stale`, `unverified`, `waived`).
2. **First-Class Inspection**: Findings provide human reviewers and automated governance pipelines with actionable signals without polluting the strict delivery-progress denominator.
3. **Internal M0 Status**: Like `rollup.schema.json`, `findings.schema.json` is internal to Milestone M0. Contract standardization into platform contracts is proposed under `ISS-NEW-1`.

---

## 2. Structure & Field Specifications

The root document is a JSON array of finding objects: `[{kind, severity, target_ref, criterion_ids, details}]`.

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `enum` | Yes | Finding kind: `architecture_drift`, `relationship_proposed`, `identity_conflict`, `conflicting_claims`. |
| `severity` | `enum` | Yes | Finding severity: `info`, `warning`, `review_required`, `blocker`. |
| `target_ref` | `string` | Yes | Target identifier, component name, deliverable ID, or path. |
| `criterion_ids` | `array[string]` | Yes | Criteria linked to or affected by this finding (empty array if project-level). |
| `details` | `object` | Yes | Structured metadata specific to the finding kind. |

### Finding Kinds & Semantics
- `architecture_drift`: Code implementation diverges from architectural blueprint without an approved ADR (severity: `review_required`).
- `relationship_proposed`: Inferred or scanner-suggested component dependency that has not been approved into baseline (severity: `info`).
- `identity_conflict`: Duplicate `project_id` observed across multiple distinct repositories or locations (severity: `blocker`).
- `conflicting_claims`: Concurrent updates within the same snapshot submit divergent progress claims for the same criterion (severity: `warning`).
