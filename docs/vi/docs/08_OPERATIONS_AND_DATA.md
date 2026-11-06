> Translated from docs/08_OPERATIONS_AND_DATA.md @ fb857f4

# Vận hành và dữ liệu
## Storage model
PostgreSQL schemas platform, ppm, knowledge, governance, editorial có ownership qua service API. Các bảng lõi gồm agents, releases, bindings, tasks, runs, run_attempt_history, dispatch_intents, commands, action_proposals, approvals, action_executions (unique operation_key), grants, leases (fencing_token), events, outbox, artifacts, usage_ledger, reservations và eval_results. Foreign keys/scopes include tenant_id. PPM thêm projects/programs/portfolios/baselines/criteria/dependencies/assessments/location mappings.

Evidence blobs content-addressed, metadata có ACL, classification, source_revision, hash, producer và created_at. Delete/retention phải xem legal hold và derived artifacts/indexes. Trace retention pilot 30 ngày redacted metadata, audit 180 ngày và evidence theo project policy là giá trị đề xuất cần owner xác nhận.

## API surface
POST /v1/agents/proposals; POST /v1/agents/{id}/releases; POST /v1/bindings.
POST /v1/tasks; GET /v1/tasks/{id}; GET /v1/tasks/{id}/runs.
POST /v1/runs/{id}/commands; POST /v1/approvals/{id}/decisions.
GET /v1/hud/snapshot; GET /v1/hud/events; GET /v1/artifacts/{id}.
POST /v1/ppm/locations; POST /v1/ppm/locations/{id}/approval; POST /v1/ppm/scans; GET /v1/ppm/projects/{id}/assessment; GET /v1/ppm/assessments/{id} (per-criterion reasons + evidence refs); POST /v1/ppm/projects/{id}/acceptance (reviewer, assessment_id + baseline_revision + expected_revision).
POST /v1/harness/experiments và promotion APIs ở phase 2.
Mutations nhận expected_revision/If-Match và idempotency_key. Auth failures 401/403, state conflict 409, validation/unsupported capability 422, unavailable runtime 503. Async submit 202 trả task/run refs. Canonical schemas nội bộ trong contracts, public request schema riêng không chứa trusted execution fields.

## Runbooks
Connector offline: giữ last observation, stop new local dispatch, cho reconnect/authenticate và reconcile external IDs trước replay.
Unknown remote submission: lock resource, query by operation key/process identity, nếu unresolved giữ unknown và operator decision, không retry mù.
Cost runaway: chặn new admissions/calls, cancel supported work, reconcile incurred usage, báo uncertainty.
Approval expired: invalidate, giữ action pending hoặc fail theo task deadline, tạo proposal mới nếu user còn nhu cầu.
Credential compromise: revoke bindings/grants, stop new calls, rotate token, reconcile active tasks, review access/audit.
Database loss: stop dispatch, restore DB và evidence snapshot consistent, validate hashes/ACLs/cursors, reconcile provider sessions rồi resume.
Bad release: pin new runs về known-good, quarantine candidate, handle in-flight version theo policy, compensation riêng cho side effects.

## Backups và observability
Backup DB qua consistent snapshot/PITR tùy environment, blobs kèm manifest. Restore drill phải xác nhận artifacts/citations/approvals và pending tasks đúng, secret refs vẫn trỏ credentials hợp lệ. Metrics gồm queue wait, completion, policy denial, tool failures, retry count, orphan/unknown runs, connector lag, late usage và HUD lag. Audit events không sampled, raw model traces sampled theo policy. Logs không giữ chain-of-thought bí mật, sử dụng concise rationale và observable tool results.

## Release hygiene
Version pin dependency, schema migrations expand/contract, agent bundle hash, tool allowlist, provenance cho templates/plugins. Upgrade adapter chạy fixtures trước canary. Human review năng lực mới, permissions không tự mở rộng vì package version tăng.
