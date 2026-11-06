# Scanner implementation design for coders

## 1. Registry contract
Template `registry.example.yaml` is trusted host configuration installed by the owner, not read from repo to grant permissions. This configuration is draft until root/owner verification. Each project may have multiple locations; `metadata_location_id` selects primary metadata. `scan_scope` is relative to root. `metadata_root` is relative to root and contains `.ppm/`.

## 2. Scheduling
On-start runs catch-up for overdue locations. Watcher debounces 5s and coalesces by location; interval 900s, full 86400s in template. Scheduler claims lease via DB; on lease expiration, another run may recover. Limits to one active scan/location, never holding locks across model call durations. Absent an application, real schedules do not exist; this is a specification for implementation.

## 3. Algorithm
1. Authorize registered location; normalize/canonicalize path and verify containment.
2. Acquire scan lease, record attempt; load checkpoint.
3. Freeze HEAD/dirty manifest or non-Git manifest; enumerate includes/excludes via documented rules.
4. Hash/snapshot files, stat before/after; enforce size limit reads. On changing dirty trees, retry bounded then mixed_snapshot.
5. Parse safe YAML (no custom tags), validate schema/version/semantic links. Retry incomplete; quarantine duplicate-key YAML, unsupported_version, invalid_paths.
6. Ingest journal by ID/hash, handle supersedes and collision; always preserve raw provenance.
7. Reconcile claims/status/criteria; execute no commands from docs or YAML.
8. Transaction writes source snapshots, claims, tombstones, outbox, and checkpoint. If partial, checkpoint per-artifact with unresolved set; do not omit errors on subsequent runs.
9. Analysis job receives snapshot IDs, generates assessment via deterministic rules. Scan success may still leave assessment pending/failed.
10. Report delta and freshness; release lease.

## 4. Supplementary APIs
POST /locations; PATCH /locations/{id} with expected_revision.
POST /scans {project_id, location_ids, mode}; GET /scans/{id}.
GET /projects/{id}/freshness; GET /projects/{id}/updates.
GET /projects/{id}/conflicts; POST /conflicts/{id}/resolve per owner policy.
POST /onboarding-proposals (not auto approval).

## 5. Supplementary domain tables
RegisteredLocation, ScanPolicy, ScanRun, ScanCheckpoint, ArtifactVersion, ReportedUpdate, UpdateConflict, Supersession, OnboardingProposal. Separate observed_at/reported_at/ingested_at. Do not use modified time as sole cursor. Unique(project_id, update_id) and content hash collision handling.

## 6. Minimal display
Location/reachability; scan attempt/success; assessment as_of; reported vs verified progress; missing criteria; unreconciled updates; stale evidence; blockers; proposed relations. When worker is down, UI must indicate monitoring offline.

## 7. Loop prevention
Cache/runtime outside repository. Do not allow collector to write updates when scanning itself; assessment in DB. If exporting reports into repo, mark generated and exclude from trigger/claim input to avoid self-validating its own output.

## 8. Gates
Duplicate events do not produce duplicate domain events; partial writes preserve checkpoints; deletes preserve history; sleeping host catches up; repo YAML does not enable cloud; status conflicts do not drop journal; dirty trees do not become verified; owner baseline change invalidates affected assessment; all external side effects route through Action Service as in blueprint.

## 9. Platform mode (06/11/2026)
When running within Enterprise Agent Platform, scanner is P2a-WP2 (collector) + P2a-WP5 (scheduler) + P2a-WP6 (orchestrator) + P2a-WP9 (verifier); registry YAML is imported as `location-registration` 1.1 per contract §14.7. Import mapping table:
| registry.example.yaml | location-registration 1.1 |
|---|---|
| `projects[].project_id` | `project_id` |
| `locations[].location_id` | `location_id` |
| `locations[].path` | `root_path` (canonicalized by connector) |
| `metadata_root`, `scan_scope` | preserved |
| `scan_policy.max_file_bytes`, `deny_file_names` | `max_file_bytes`, `deny_globs` |
| `scan_policy.*` interval/debounce/watch | `ppm_scan_schedules` (P2a-WP5) |
| `access_policy.cloud_processing` | not mapped automatically; owner selects `egress_policy_ref` |
| (absent) | `source_id`, `host_id`, `connector_id`, `artifact_aliases`, `classification`, `identity`, `approved_by_ref` added by owner upon approval |
Test evidence verifies only when verifier reruns at exact revision or CI provides attestation (contract §14.3).
