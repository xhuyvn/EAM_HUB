# Agent Operations HUD
## Surfaces and behavior
Mockups in visuals represent a 1600×1000 desktop sample. Production UI employs responsive layout, keyboard navigation, text labels and icons to ensure status does not rely solely on color. Preferences are stored on user profiles. Reduced motion and auto-scroll off engage when users read history.

| Surface | Content | Actions |
|---|---|---|
| Attention queue | Pending approval, failed/unknown runs, stale connectors, budget alerts | Inspect, approve/deny if authorized, assign operator |
| Tasks | Objective, project, owner, completion policy, current run | Open attempts, submit scoped follow-up |
| Runs | Agent version, stage, elapsed, progress x/y, last observation | Inspect timeline, cancel supported run, reconcile |
| Agent inventory | Owner, release, binding, certified capabilities, health | Register/propose update, suspend by role |
| Run inspector | Trace tree, tool spans, event timeline, artifacts, usage | Open evidence, redacted export, link project |
| PPM view | Reported/verified progress, criteria coverage, baseline, last scan | Review gaps, request scan, open acceptance packet |
| Harness lab | Phase 2 experiments, quality/cost comparison, canary health | Review candidate, release through approval |

## State/metrics semantics
Execution state and connectivity are independent fields. Heartbeat timeout denotes stale; confirmation from provider is required to conclude worker terminated. Wall elapsed is computed from timestamps and clocks with offsets; durations prioritize monotonic sources. Last observed displays in user timezone, persisted in UTC. Denominator null => stage/elapsed, avoiding percent rendering. Overlapping tool span durations must not be summed into total elapsed.

Cost displays estimated/actual/unknown, provider and cost_as_of. PPM acceptance displays pending/accepted/rejected/superseded independently of run outcome. Trace input/output is redacted by default; artifact access verifies permissions upon retrieval.

## Realtime protocol
GET /v1/hud/snapshot returns projection_revision + snapshot_cursor + accessible rows in a consistent snapshot. GET /v1/hud/events?after=<cursor> over SSE delivers typed deltas and heartbeats. Snapshot+cursor are retrieved atomically; stream replays after cursor to prevent event loss between snapshot and subscription. Expired cursor returns resync_required; client fetches fresh snapshot. Cursor is opaque and bound to tenant/user/filter scope. ACL revocation terminates or re-filters subscriptions; clients receive remove rows and purge cached details.
Polling fallback GET snapshot every 10s during pilot if SSE is unavailable. Client reconnects with exponential backoff + jitter, presenting live/offline indicators. Slow consumers are disconnected and resynced; metadata event sizes are bounded. Raw token deltas may be sampled/coalesced; audit and action events must remain durable.

## Operator commands
POST /v1/runs/{id}/commands {kind, expected_revision, idempotency_key}. Command accepted returns 202 and command_id; UI displays cancelling/requested until acknowledgement. Approvals use dedicated endpoints with action hash and expected revision. Backend returns 403/409/422 for unauthorized/stale/unsupported requests. Buttons are disabled per capability + permission, with server enforcement remaining authoritative. Retries generate new runs only following side-effect reconciliation or safe-retry policy.

## Proposed acceptance targets
At 20 active runs, 10 metadata events/s/run, p95 authorized event-to-HUD <2s on documented pilot environment. Client displays stale if no heartbeat is observed for 30s (configurable). Figures require benchmarking and do not constitute committed SLAs.
Gates include duplicate/out-of-order events, SSE reconnect without resubmission, expired cursor resync, auth revoke during stream, unknown cost, missing progress denominator, double cancel click, expired approval, late terminal event, narrow screen/keyboard focus and output redaction.

## Revision 1.1 — projection contract and display semantics
Snapshots conform to `contracts/hud-projection.schema.json` 1.1 (e.g. `examples/hud-projection.json`). Each row maintains independent axes with dedicated text labels:

| Axis | Field | Source of truth | Display |
|---|---|---|---|
| Task | task_state | Task Service | Text badge, not inferred from run |
| Run | run_state, attempt | Run Service journal | "Attempt 2 · running" |
| Remote outcome | remote_outcome | Run Service reconciliation | `unknown` displays "Remote outcome unknown — no retry until reconciled" |
| Connectivity | connectivity, last_observed_at, observation_source | Heartbeat/connector status | "Stale · last observed 09:10:30 (+07:00) via run-service" |
| Project | verification, validity, acceptance, assessment_as_of | PPM domain DB | Three distinct chips; acceptance mutates solely via reviewer action |
| Cost | status, amount_usd, scope, as_of | Usage ledger | actual/estimated: numeric; partial: "≥ $0.42 (some calls unknown)"; unknown: "Unknown", never "$0" |
| Pending command | kind, state | Command table | "Cancel requested" until acknowledgement |

Denominators: percent rendered solely when `progress.kind=units` and total_units ≥ 1. Project progress displays `verified_weight / total_weight` of current baseline revision, accompanied by "N/M criteria with current evidence"; absence of baseline renders "No baseline — progress not computed". Multi-run aggregate cost: sums actual/estimated/partial runs into a lower bound, counting unknown runs separately ("≥ $4.82 · 2 runs unknown"); current mockup displaying "$4.82 Estimate · 2 runs unknown" must transition to lower bound syntax during implementation. Cell "Verified scope 6 / 10 criteria" in mockup must render weight and baseline revision.

Reconnection and permissions: upon opening SSE, server re-authenticates session and verifies cursor belongs to matching tenant/principal/filter and identical `policy_version` or grant set; if grants mutate or cursor exceeds retention → `resync_required`, client drops cache and fetches fresh snapshot. Cursors never expand revoked permissions. Client renders HUD connection health (live/reconnecting/offline + snapshot timestamp) decoupled from worker connectivity.

Capacity benchmarking required prior to commitments: 20 active runs × 10 metadata events/s = 200 events/s; with bounded payload ~1 KB, ingestion ~0.2 MB/s and several hundred inserts/s represents a plausible baseline for a single PostgreSQL node and single API process. Avoid introducing Redis/Kafka/WebSocket gateways until P3 load tests demonstrate p95 event-to-HUD exceeds 2s or DB CPU > 70% under this profile.

### Permission revocation with active SSE streams
Reconnection is not the sole permission check. Subscription manager maintains `grant_epoch` per subscription. When grants/ACLs mutate, Policy Service emits `policy.changed` (outbox, within transaction altering grant). Subscription manager increments epoch and re-evaluates scope prior to dispatching subsequent events. Dispatched events are filtered against current epoch scope, not subscription-time scope. Rows losing permission receive `row.removed` deltas; client clears cached detail. Evidence and artifacts always download via `GET /v1/artifacts/{id}` with per-request authentication; MVP avoids signed URLs. If signed URLs are introduced subsequently, TTL ≤ 60s and revocation cannot recall issued URLs within TTL: documented as residual risk.
Proposed target (to measure): p95 interval from revocation commit to policy enforcement on active stream < 2s. Test C30: maintain active HUD, revoke project read permission, emit events containing project data. Record revocation commit timestamp, final project event timestamp received by client, and attempt to fetch evidence via old path (yielding 403).

### Storage budget
Peak throughput alone cannot estimate storage. At 200 events/s and 1 KB payload, continuous daily execution yields: 200 × 1 KB × 86,400 s ≈ 17.3 GB/day, excluding indexes, WAL, and backups. On a 100 GB server shared with websites, decisive factors are peak duration ratio and actual bytes persisted per event. Storage reduction designs (applied when measurements warrant): heartbeats not persisted as durable events (updating last_observed_at only); progress coalescing; raw token deltas omitted; audit/action/state events remain durable. Tiered retention (HUD metadata events, trace, audit, evidence) is finalized in D-05 following P5-WP4 measurements.
Evidence policy finalized for Phase 1: no signed URLs. Every artifact access routes through `GET /v1/artifacts/{id}`, validating permissions at request time. Following revocation, legacy URLs return 403.
