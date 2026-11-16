# SC-07 HUD Latency Baseline Measurement Report (P3a-WP4)

- **Work Package**: P3a-WP4 HUD latency baseline (SC-07)
- **Commit**: `b6e5290c0774e0fdd8d32b76c3a4e1668983a6cd`
- **Execution Timestamp**: `2026-11-16T14:56:54.030440+00:00`
- **Total Benchmark Duration**: `65.989 s`
- **Exact Command**: `uv run python tools/bench/hud_baseline.py --db-url postgresql+psycopg://eap:postgres_dev_secret@127.0.0.1:5432/eap_hud_bench --reset-db --scales 10000,100000,1000000 --runs 100 --agents 10 --assessments 20 --iterations 50 --sse-samples 25 --revocation-samples 20 --replay-samples 5 --md-output docs/measurements/HUD_BASELINE.md --json-output C:/Users/xhuy/.gemini/antigravity/brain/19977a58-d5ef-4817-9c55-e47c41d2e6f7/scratch/hud_baseline.json`

---

## 1. Environment and PostgreSQL Configuration

| Parameter | Value |
|---|---|
| Operating System | Windows-11-10.0.26300 |
| CPU Model | Intel64 Family 6 Model 198 Stepping 2, GenuineIntel |
| Logical CPU Cores | 24 |
| Physical Memory (RAM) | 63.37 GB |
| Python Version | 3.12.13 |
| PostgreSQL Version | 18.6 (Debian 18.6-1.pgdg13+2) (PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on x86_64-pc-linux-gnu) |
| `shared_buffers` | `128MB` |
| `work_mem` | `4MB` |
| `effective_cache_size` | `4GB` |
| `max_connections` | `100` |
| `synchronous_commit` | `on` |
| `fsync` | `on` |
| `wal_buffers` | `4MB` |
| `max_wal_size` | `1GB` |

---

## 2. Seeded Scales and Database Footprint

| Scale (events) | Runs | Agents | Projects | Seed Duration | DB Size (MB) | `event_log` (MB) | `tasks` (MB) | `runs` (MB) | `projection_checkpoints` (MB) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10,000 | 100 | 10 | 10 | 0.456 s | 18.3 | 7.55 | 0.141 | 0.109 | 0.141 |
| 100,000 | 100 | 10 | 10 | 1.726 s | 106.11 | 94.82 | 0.281 | 0.219 | 0.242 |
| 1,000,000 | 100 | 10 | 10 | 15.875 s | 964.56 | 952.87 | 0.344 | 0.273 | 0.336 |

---

## 3. Measured Latencies by Scale (ms)

All measurements were executed against a live local Uvicorn/FastAPI server over loopback (`127.0.0.1`) backed by PostgreSQL with `DEFAULT_SSE_POLL_INTERVAL_MS=25`.

### Scale: 10,000 events (100 runs, 10 agents)

| Endpoint / Flow | Samples (n) | p50 (ms) | p95 (ms) | p99 (ms) | min (ms) | max (ms) | mean (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| GET /v1/hud/snapshot | 50 | 19.10 | 21.61 | 34.69 | 17.11 | 44.03 | 19.85 |
| SSE event delivery (commit -> client receive) | 25 | 46.87 | 54.91 | 55.69 | 21.17 | 55.85 | 46.42 |
| SSE reconnect replay (1,000 missed events) | 5 | 356.45 | 384.01 | 389.03 | 343.75 | 390.28 | 360.64 |
| Live revocation propagation (revoke -> row.removed) | 20 | 68.08 | 83.22 | 85.94 | 24.30 | 86.62 | 58.27 |
| GET /v1/agents | 50 | 11.17 | 13.44 | 16.10 | 9.75 | 16.53 | 11.59 |
| GET /v1/agents/{agent_id} | 50 | 11.22 | 17.88 | 23.48 | 9.76 | 26.56 | 12.13 |
| GET /v1/ppm/assessments/{assessment_id} | 50 | 8.77 | 10.56 | 11.06 | 8.09 | 11.06 | 9.00 |
| GET /v1/ppm/assessments/{id}/diff | 50 | 5.25 | 6.45 | 8.43 | 4.71 | 9.59 | 5.43 |
| GET /v1/ppm/projects/{id}/assessments | 50 | 5.41 | 8.69 | 9.46 | 4.63 | 9.66 | 5.86 |
| GET /v1/hud/runs/{run_id}/inspector | 50 | 14.74 | 19.06 | 26.43 | 12.18 | 30.62 | 15.42 |

### Scale: 100,000 events (100 runs, 10 agents)

| Endpoint / Flow | Samples (n) | p50 (ms) | p95 (ms) | p99 (ms) | min (ms) | max (ms) | mean (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| GET /v1/hud/snapshot | 50 | 21.50 | 25.64 | 45.49 | 19.88 | 63.41 | 22.85 |
| SSE event delivery (commit -> client receive) | 25 | 47.02 | 54.96 | 62.08 | 10.16 | 63.98 | 44.03 |
| SSE reconnect replay (1,000 missed events) | 5 | 369.10 | 375.68 | 376.12 | 356.90 | 376.23 | 368.44 |
| Live revocation propagation (revoke -> row.removed) | 20 | 42.84 | 82.72 | 83.30 | 38.37 | 83.44 | 55.99 |
| GET /v1/agents | 50 | 11.46 | 14.77 | 15.98 | 10.50 | 16.05 | 11.89 |
| GET /v1/agents/{agent_id} | 50 | 11.53 | 13.37 | 15.03 | 10.19 | 15.99 | 11.69 |
| GET /v1/ppm/assessments/{assessment_id} | 50 | 9.78 | 12.76 | 13.36 | 8.80 | 13.66 | 10.17 |
| GET /v1/ppm/assessments/{id}/diff | 50 | 5.88 | 8.32 | 9.84 | 4.87 | 10.70 | 6.09 |
| GET /v1/ppm/projects/{id}/assessments | 50 | 5.42 | 7.82 | 8.90 | 4.66 | 9.74 | 5.76 |
| GET /v1/hud/runs/{run_id}/inspector | 50 | 19.09 | 25.59 | 30.92 | 16.51 | 32.81 | 19.79 |

### Scale: 1,000,000 events (100 runs, 10 agents)

| Endpoint / Flow | Samples (n) | p50 (ms) | p95 (ms) | p99 (ms) | min (ms) | max (ms) | mean (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| GET /v1/hud/snapshot | 50 | 22.96 | 27.83 | 46.95 | 20.26 | 59.80 | 24.05 |
| SSE event delivery (commit -> client receive) | 25 | 46.72 | 50.81 | 62.99 | 10.77 | 66.81 | 45.92 |
| SSE reconnect replay (1,000 missed events) | 5 | 380.60 | 398.22 | 400.08 | 362.68 | 400.54 | 381.58 |
| Live revocation propagation (revoke -> row.removed) | 20 | 200.44 | 252.45 | 252.96 | 186.21 | 253.08 | 207.29 |
| GET /v1/agents | 50 | 12.40 | 15.43 | 17.21 | 10.67 | 17.95 | 12.59 |
| GET /v1/agents/{agent_id} | 50 | 11.35 | 15.68 | 17.19 | 10.32 | 17.62 | 11.97 |
| GET /v1/ppm/assessments/{assessment_id} | 50 | 9.36 | 13.57 | 13.86 | 8.16 | 13.91 | 9.85 |
| GET /v1/ppm/assessments/{id}/diff | 50 | 5.55 | 7.12 | 8.36 | 4.76 | 9.28 | 5.71 |
| GET /v1/ppm/projects/{id}/assessments | 50 | 5.26 | 7.15 | 8.75 | 4.56 | 9.91 | 5.58 |
| GET /v1/hud/runs/{run_id}/inspector | 50 | 20.20 | 28.13 | 30.13 | 16.23 | 31.56 | 21.29 |

---

## 4. Comparison with Existing Documented Targets & Candidate DG-5 Proposals

Per `docs/IMPLEMENTATION_PLAN.md` (`ARCH-30`, `SC-07`), baseline measurements do **not** invent acceptance thresholds; formal acceptance thresholds belong to **DG-5** (before `P5-WP4`).

### Existing Documented Targets (Non-Binding Proposals in Spec)
- `docs/03_HUD_SPEC.md` & `docs/07_DELIVERY_AND_ACCEPTANCE.md`: `p95 event-to-HUD < 2,000 ms` (2 s) under pilot profile (`<= 20` active runs, `<= 10` events/s/run, `<= 10` concurrent SSE clients).
- `docs/03_HUD_SPEC.md`: `p95 revocation propagation < 2,000 ms` (2 s) (`C30` test harness uses `< 500 ms`).
- Point-read endpoints (`/v1/hud/snapshot`, `/v1/agents`, `/v1/ppm/assessments/*`, `/v1/hud/runs/*/inspector`) have **no** thresholds defined in existing docs.

### Candidate Thresholds for DG-5 (Proposals Only — Not Binding)
| Metric | Proposed DG-5 Candidate Threshold | Basis |
|---|---|---|
| `GET /v1/hud/snapshot` p95 | `< 100 ms` | Bounded checkpoint+tail reads (`ISS-040`) stay O(active runs), independent of `event_log` size |
| SSE event delivery p95 | `< 250 ms` (well below 2,000 ms spec target) | `DEFAULT_SSE_POLL_INTERVAL_MS=25` + single-row indexed poll |
| SSE 1k-event reconnect replay p95 | `< 1,000 ms` | Sequential cursor-paged read over `ix_event_log_tenant_id_pk` |
| Live revocation propagation p95 | `< 500 ms` (well below 2,000 ms spec target) | Matches `C30` test assertion and `policy.changed` epoch check |
| `GET /v1/agents` & `/v1/agents/{id}` p95 | `< 100 ms` | Indexed per-tenant agent/binding + run aggregation |
| Assessment detail / diff / list p95 | `< 100 ms` | Primary-key / indexed `(tenant_id, project_id)` lookups |
| `GET /v1/hud/runs/{id}/inspector` p95 | `< 100 ms` | Indexed single-run attempts/commands/artifacts/events |
