# Operations and data
## Storage model
PostgreSQL schemas platform, ppm, knowledge, governance, editorial maintain ownership via service APIs. Core tables comprise agents, releases, bindings, tasks, runs, run_attempt_history, dispatch_intents, commands, action_proposals, approvals, action_executions (unique operation_key), grants, leases (fencing_token), events, outbox, artifacts, usage_ledger, reservations and eval_results. Foreign keys and scopes include tenant_id. PPM adds projects, programs, portfolios, baselines, criteria, dependencies, assessments and location mappings.

Evidence blobs are content-addressed; metadata maintains ACL, classification, source_revision, hash, producer and created_at. Deletion and retention must accommodate legal holds and derived artifacts/indexes. Trace retention pilot of 30 days redacted metadata, audit 180 days and evidence per project policy represent initial proposals requiring owner confirmation.

## API surface
POST /v1/agents/proposals; POST /v1/agents/{id}/releases; POST /v1/bindings.
POST /v1/tasks; GET /v1/tasks/{id}; GET /v1/tasks/{id}/runs.
POST /v1/runs/{id}/commands; POST /v1/approvals/{id}/decisions.
GET /v1/hud/snapshot; GET /v1/hud/events; GET /v1/artifacts/{id}.
POST /v1/ppm/locations; POST /v1/ppm/locations/{id}/approval; POST /v1/ppm/scans; GET /v1/ppm/projects/{id}/assessment; GET /v1/ppm/assessments/{id} (per-criterion reasons + evidence refs); POST /v1/ppm/projects/{id}/acceptance (reviewer, assessment_id + baseline_revision + expected_revision).
POST /v1/harness/experiments and promotion APIs in Phase 2.
Mutations accept expected_revision/If-Match and idempotency_key. Auth failures 401/403, state conflicts 409, validation/unsupported capabilities 422, unavailable runtimes 503. Async submits return 202 with task/run refs. Canonical internal schemas reside in contracts; public request schemas omit trusted execution fields.

## Runbooks
Connector offline: retain last observation, halt new local dispatch, allow reconnection/authentication and reconcile external IDs before replaying.
Unknown remote submission: lock resources, query by operation key/process identity; if unresolved retain unknown and await operator decision, avoiding blind retry.
Cost runaway: halt new admissions and calls, cancel supported executions, reconcile incurred usage, report uncertainty.
Approval expired: invalidate, hold action pending or fail per task deadline, generate fresh proposal upon renewed user request.
Credential compromise: revoke bindings and grants, halt new calls, rotate tokens, reconcile active tasks, review access and audit logs.
Database loss: halt dispatch, restore DB and evidence snapshot consistently, validate hashes/ACLs/cursors, reconcile provider sessions then resume.
Bad release: pin new runs to known-good release, quarantine candidate, handle in-flight versions per policy, execute dedicated compensation for side effects.

## Backups and observability
Backup DB via consistent snapshots or PITR depending on environment, blobs accompanied by manifests. Restore drills verify artifacts, citations, approvals and pending tasks, ensuring secret refs resolve to valid credentials. Metrics track queue wait, completion, policy denials, tool failures, retry counts, orphan/unknown runs, connector lag, late usage and HUD lag. Audit events are never sampled; raw model traces are sampled per policy. Logs omit secret chain-of-thought, utilizing concise rationale and observable tool results.

## Release hygiene
Pin dependency versions, schema migrations via expand/contract, agent bundle hashes, tool allowlists, provenance for templates and plugins. Adapter upgrades execute fixtures prior to canaries. Human review governs new capabilities; permissions never expand autonomously due to package version increments.
