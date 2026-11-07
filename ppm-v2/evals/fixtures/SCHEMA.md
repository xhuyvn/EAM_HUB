# Fixture Inputs Schema Specification (M0 Internal)

This document specifies the format, field semantics, and determinism rules for `inputs.json` files within the `ppm-v2/evals/fixtures/` corpus.
The schema is machine-checked by `ppm-v2/evals/fixtures/inputs.schema.json` and validated by `ppm-v2/tools/check_fixtures.py`.

## 1. Purpose and Determinism Rules

The fixture inputs supply all parameters necessary for the deterministic PPM reducer (`ppm-v2/reducer`) to evaluate project delivery status and progress without performing any filesystem I/O, network requests, or model inferences.

### Determinism Rules
1. **No Wall-Clock Time**: The `now` field MUST be an explicit, fixed ISO-8601 timestamp (e.g., `2026-11-05T12:00:00Z`). Reducer rules evaluate waiver expiration and evidence TTL strictly relative to `now`.
2. **Stable Sorting**: All lists (`criteria`, `claims`, `evidence`, `waivers`, `dependency_refs`) MUST be ordered stably by their identifiers.
3. **Pure Execution**: No environment variables, random seeds, or network lookups are permitted.
4. **Fictional Identifiers**: All project IDs, deliverable IDs, test names, and repository paths are fictional.

---

## 2. Top-Level Structure

| Field | Type | Required | Description |
|---|---|---|---|
| `fixture_id` | `string` | Yes | Unique fixture folder name (e.g. `s01-claim-done-test-failed`). |
| `project_id` | `string` | Yes | ID of the project being assessed. |
| `snapshot_refs` | `array[string]` | Yes | Identifiers of snapshot(s) ingested for this assessment. |
| `snapshot_seq` | `integer` | Yes | Monotonic snapshot sequence number. |
| `basis_seq` | `integer` | Yes | Monotonic assessment ordering sequence number. |
| `previous_assessment_id` | `string \| null` | Yes | Prior assessment ID, or null if first assessment. |
| `assessed_revision` | `string \| null` | Yes | Commit SHA or tree hash of the snapshot being assessed. |
| `now` | `string` (date-time) | Yes | Fixed evaluation timestamp. |
| `source_as_of` | `string \| null` (date-time) | No | Timestamp of the source commit/snapshot. |
| `evidence_ttl_days` | `number` | No | Evidence TTL in days (defaults to 7 per decision D-03). |
| `baseline` | `object \| null` | Yes | Approved project baseline containing criteria and verification refs. Null if no baseline exists (`s24-draft-only`). |
| `claims` | `array[object]` | Yes | Parsed claims extracted from `.ppm/updates/`, `.ppm/status.yaml`, or notes. |
| `evidence` | `array[object]` | Yes | Collected test executions and verification artifacts. |
| `waivers` | `array[object]` | Yes | Active or historical waivers approved in the PPM domain DB. |
| `dependency_refs` | `array[object]` | No | Shared component dependencies consumed by this project. |
| `context` | `object` | No | Scenario-specific context for findings (architecture drift, proposed relations, repo identity, portfolio rollup). |

---

## 3. Sub-Object Specifications

### 3.1 `baseline`
When present, represents the owner-approved baseline revision in the PPM domain database:
- `baseline_revision` (`integer >= 1`): Revision number.
- `criteria` (`array[object]`): List of deliverable acceptance criteria:
  - `criterion_id` (`string`): e.g. `AC-001`.
  - `deliverable_id` (`string`): e.g. `DEL-001`.
  - `owner_project_id` (`string`): Owning project ID.
  - `weight` (`number > 0`): Relative weight.
  - `required` (`boolean`): Whether mandatory for acceptance.
  - `verification_refs` (`array[object]`): Mapped verification references:
    - `kind`: `test_id` | `probe_id` | `artifact_path` | `manual_review`.
    - `ref`: Identifier/path of the test/probe.
  - `previous_status` (`string | null`): Prior verdict if reassessing.

### 3.2 `claims`
Parsed claims from repo `.ppm` files:
- `claim_id` (`string`): e.g. `claim-upd-001`.
- `source_file` (`string`): Path in repository (e.g. `.ppm/updates/upd-001.yaml`).
- `criterion_ids` (`array[string]`): Linked criteria.
- `test_ids` (`array[string]`): Linked test IDs.
- `reported_status` (`claimed_done` | `claimed_in_progress` | `claimed_blocked` | `none`).
- `evidence_path` (`string | null`): Self-reported artifact path.
- `working_tree_state` (`clean` | `dirty` | `untracked_only`): State when update was written.
- `recorded_at` (`string` date-time): Timestamp from update.
- `summary` (`string`): Free-text summary.

### 3.3 `evidence`
Evidence collected by the platform verifier sandbox, CI pipelines, or found in repository:
- `evidence_id` (`string`): e.g. `ev-001`.
- `trust_class` (`verifier_observed` | `ci_attested` | `reported_artifact` | `none`):
  - `verifier_observed`: Independent verifier rerun at exact revision.
  - `ci_attested`: Signed CI attestation with trusted pipeline provenance.
  - `reported_artifact`: In-repo test report authored by producer (cannot verify; can refute on FAIL).
  - `none`: No evidence.
- `revision` (`string | null`): Commit/tree hash where test ran.
- `observed_at` (`string` date-time): Timestamp of test run.
- `test_id` (`string | null`) or `covered_test_ids` (`array[string]`): Test identifier(s).
- `result` (`passed` | `failed` | `inconclusive` | `not_run`).
- `artifact_path` (`string | null`): Path to output artifact.

### 3.4 `waivers`
Approved waivers from the PPM domain DB:
- `waiver_id` (`string`): Unique waiver ID.
- `criterion_id` (`string`): Waived criterion.
- `scope` (`string`): Scope description.
- `rationale` (`string`): Reason for waiver.
- `approved_by` (`string`): Authorizing reviewer ID.
- `approved_at` (`string` date-time): Timestamp of approval.
- `expires_at` (`string` date-time): Expiration timestamp. Evaluated against `now`.

### 3.5 `context`
Supplemental context for evaluation rules:
- `architecture_drift`: Describes pattern implementation and whether an approved ADR exists.
- `relationship_proposals`: Inferred or proposed inter-project relations.
- `repo_identity`: `{registered, observed}` for detecting duplicate project IDs across repos.
- `portfolio`: Project list and deliverable data for rollup aggregation.
- `hidden_project_ids`: Projects hidden from viewer due to permissions (for testing zero-leakage).
- `viewer_authorized_project_ids`: Projects the viewer is permitted to see.
