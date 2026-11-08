# Acceptance Packet: P1a-WP7 Ingest Baseline (Measurement)

## 1. Milestone and Scope
- **Work Package**: P1a-WP7 Ingest baseline (measurement) per `STATUS.md`, `docs/IMPLEMENTATION_PLAN.md` §5 SC-07 and §6 P1a-WP7, `docs/adr/DG-1.md` §0.1 DG1-C4, `docs/adr/DG-2.md` §0.1 DG2-C1.
- **Target State**: `ready_for_review` (Blocked on owner architectural decision `ISS-024`)
- **Branch**: `wp/P1a-WP7` (branched from `main` at `06a3015`)
- **Code Commit under Review**: `8b6d29b`
- **Base Commit**: `06a3015` ("accept P1a-WP6: logging and redaction (SC-04)")
- **Parallel Work Packages Respected**:
  - No database migrations added (zero schema changes).
  - No modifications to published schemas (`contracts/*.schema.json`, `examples/`).
  - Reducer purity untouched (`ppm-v2/reducer/**`).
  - Next issue ID owned and recorded in `docs/ISSUES.md`: `ISS-024`.
- **Scope Delivered**:
  - `tools/bench/ingest_baseline.py`: Deterministic benchmark runner driving the real transactional execution path across concurrent producers, execution streams, and outbox relay workers. Records per-event commit timestamps, ingest offset assignment timestamps, and projection checkpoint timestamps.
  - Full environment block: CPU model, cores, RAM, SSD/NVMe disk type, OS, PostgreSQL 18.6 version and key settings (`fsync=on`, `synchronous_commit=on`, `shared_buffers=128MB`, `wal_buffers=4MB`, `max_connections=100`), Python 3.12, git commit.
  - Formal 600 s measurement on local PostgreSQL 18.6 with 120,283 events, achieving sustained >= 200 events/s for the first 100 s, with throughput degrading to a worst 10 s window of 50.00 events/s as the unindexed outbox table grew past 20,000 rows.
  - Headroom ramp across rates 200, 400, 800 events/s for 60 s each (non-gating, for DG-5 capacity planning), detailing capacity limits and bottlenecks.
  - Comprehensive SC-07 outputs: `docs/measurements/P1a-WP7.md` and `artifacts/measurements/P1a-WP7.json`.
  - CI smoke integration: Added 30 s smoke variant to `.github/workflows/ci.yml` and `scripts/ci.ps1` using `--no-throughput-gate` (blocking strictly on correctness, non-blocking on shared runner throughput).
  - Conformance artifacts: `artifacts/conformance/P1a/DG1-C4.json` and `artifacts/conformance/P1a/DG2-C1.json`.

---

## 2. Binding Condition Evaluation (DG1-C4 & DG2-C1)

### 2.1 DG1-C4: Outbox Relay Sustained Throughput (≥ 200 events/s for 10 min, 0 Lost, 0 Duplicated)
- **Condition Definition (`docs/adr/DG-1.md` §0.1)**:
  "Outbox relay sustains at least **200 events/s**. 200 events/s × 10 min: 0 lost, 0 duplicated, offsets strictly increasing in commit order; report p50/p95 ingest→projection (SC-07). Below 200 events/s -> WP7 not accepted."
- **Observed Metrics (Formal 600 s Run)**:
  - **Total Events Ingested**: 120,283 events across 20 execution streams.
  - **Sustained >= 200 evt/s Duration**: Sustained for the first 100 seconds (Window 1: 204.0 evt/s, Window 5: 205.1 evt/s, Window 7: 205.4 evt/s).
  - **Worst 10 s Window**: **50.00 events/s** (observed between 500s–510s, 520s–530s, 540s–550s, 570s–580s).
  - **Overall Throughput**: 66.34 events/s across the full 1813 s execution (600 s producer window + 1213 s backlog drain).
  - **0 Lost**: **PASS** (120,283 outbox rows written == 120,283 outbox rows relayed == 120,283 event_log entries).
  - **0 Duplicated**: **PASS** (120,283 total event_ids == 120,283 distinct event_ids in event_log).
  - **Contiguous Offsets**: **PASS** (all 20 streams verified strictly contiguous 1..N without gaps or regression).
  - **Projection State Hash Parity**: **PASS** (all 20 streams verified: full rebuild snapshot hash == live projection checkpoint hash).
- **DG1-C4 Verdict**: **FAIL** on sustained throughput threshold (worst 10s window was 50.00 evt/s < 200.0 evt/s). Per owner rules, not tuned silently; reported with empirical evidence as `ISS-024`.

### 2.2 DG2-C1: Concurrency and CAS Benchmark Prior to Production
- **Condition Definition (`docs/adr/DG-2.md` §0.1)**:
  "Benchmark and test concurrency, lease/fencing and CAS before any production deployment. Concurrency tests with real parallel connections (not mocks of the DB); stale fencing token rejected; CAS loser recorded as history, current pointer never regresses; SC-07 benchmark report. No production deployment while any item lacks evidence."
- **Status in P1a-WP7**: **Evidenced for Ingest Baseline**. Tested across 4 concurrent parallel producer connections, 2 continuous relay workers, and 20 parallel stream advisory locks on real PostgreSQL 18.6. Full production gate P5 remains pending P2a-WP5 (C35) and P2a-WP6 (C34, C39).

---

## 3. Environment and Host Hardware

| Parameter | Value |
|---|---|
| CPU Model | Intel64 Family 6 Model 198 Stepping 2, GenuineIntel |
| Logical Cores | 24 |
| Physical RAM | 63.37 GB |
| Disk Storage | SSD / NVMe |
| Operating System | Windows-11-10.0.26300-SP0 |
| Python Runtime | CPython 3.12.13 |
| Database Engine | PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) |
| PostgreSQL fsync | `on` |
| PostgreSQL synchronous_commit | `on` |
| PostgreSQL shared_buffers | `128MB` |
| PostgreSQL wal_buffers | `4MB` |
| PostgreSQL max_connections | `100` |

---

## 4. Formal SC-07 Measurements (600 s Baseline Run)

### 4.1 Latency Distribution
- **Ingest -> Projection Checkpoint**:
  - p50: **2,220.03 ms**
  - p95: **11,697.46 ms**
  - p99: **14,220.92 ms**
- **Producer Commit -> Projection Checkpoint (End-to-End)**:
  - p50: **124,950.27 ms**
  - p95: **1,157,188.41 ms**
  - p99: **1,300,308.03 ms**
*(High end-to-end latency directly reflects queue buildup in `outbox` as polling scan time degraded).*

### 4.2 Storage Growth and Relation Sizes (ARCH-24 Baseline)
- **WAL LSN Range**: `0/22EBA88` -> `2/C9FAAEC8`
- **Total WAL Written**: **11,941,966,912 bytes** (11,388.75 MB / ~11.4 GB)
- **WAL Growth Per Event**: **99,282.25 bytes/event** (high due to full page writes and checkpoint fsyncs under 120k individual transactions)
- **Total Database Growth**: **836,460,544 bytes** (797.71 MB)

| Table Name | Before (bytes) | After (bytes) | Growth (bytes) | Bytes / Event |
|---|:---:|:---:|:---:|:---:|
| `outbox` | 163,840 | 78,594,048 | 78,430,208 | 652.05 |
| `event_log` | 253,952 | 110,272,512 | 110,018,560 | 914.66 |
| `projection_checkpoints` | 81,920 | 81,920 | 0 | 0.00 |
| `outbox_relay_marks` | 114,688 | 26,959,872 | 26,845,184 | 223.18 |
| `stream_heads` | 81,920 | 621,248,512 | 621,166,592 | 5,164.21 |

---

## 5. Headroom Ramp (Non-gating, for DG-5 Planning)

Tested across `--rate 200`, `--rate 400`, and `--rate 800` for 60 s each:

| Target Rate (evt/s) | Achieved Rate (evt/s) | Worst 10s Window (evt/s) | Target Sustained | Correctness | p95 Commit Latency (ms) | Primary Bottleneck Observed |
|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 200 | 169.0 | 175.8 | NO | PASS | 6,078.8 ms | Producer commit latency under WAL fsync (p95=6,078.8ms) |
| 400 | 274.6 | 200.0 | NO | PASS | 20,445.0 ms | Producer commit latency under WAL fsync (p95=20,445.0ms) |
| 800 | 264.4 | 200.0 | NO | PASS | 22,735.9 ms | Producer commit latency under WAL fsync (p95=22,735.9ms) |

- **Highest Rate with Sustained Worst 10s Window >= Target**: 0 events/s (plateaus between ~260–275 events/s overall under unindexed outbox scans and synchronous fsync).

---

## 6. Bottleneck Analysis and ISS-024 Architectural Defect

### Empirical Evidence
PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` on the outbox relay query against the 120,283-row table:
```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT outbox.id, outbox.event_id, outbox.event_type, outbox.payload
FROM outbox
WHERE outbox.tenant_id = 'tenant-bench-42'
  AND outbox.stream_id = 'run:bench-stream-42-00'
  AND NOT EXISTS (
    SELECT 1 FROM outbox_relay_marks
    WHERE outbox_relay_marks.tenant_id = 'tenant-bench-42'
      AND outbox_relay_marks.outbox_id = outbox.id
  )
ORDER BY outbox.id ASC
LIMIT 250;
```
Query plan observed:
```text
-> Parallel Seq Scan on outbox (cost=0.00..10007.89 rows=2520 width=527) (actual time=0.101..6.039 rows=2005.00 loops=3)
     Filter: (((tenant_id)::text = 'tenant-bench-42'::text) AND ((stream_id)::text = 'run:bench-stream-42-00'::text))
     Rows Removed by Filter: 38089 (x 3 workers = 114,267 rows scanned per query!)
     Buffers: shared hit=3081 read=6175
Execution Time: 15.042 ms
```

### Root Causes
1. **Missing Composite Index on `outbox`**:
   The table only has `PRIMARY KEY (id)`. Finding the un-relayed batch for a given `(tenant_id, stream_id)` requires scanning the entire outbox table. Polling across 20 streams takes $20 \times 15\text{ ms} = 300\text{ ms}$ per poll cycle, capping polling throughput below 200 evt/s once outbox exceeds ~20,000 rows.
2. **Single Transaction WAL Commit Amplification**:
   Writing 120k individual single-event transactions with `synchronous_commit = on` generated ~11.9 GB of WAL and caused disk fsync queue contention between producers and relays.

### Proposal (Recorded in `docs/ISSUES.md#ISS-024`)
1. **Option A (Index Migration)**: Add migration `0003_outbox_index.py`:
   `CREATE INDEX ix_outbox_tenant_stream_id ON outbox (tenant_id, stream_id, id);`
   Allows $O(\log N)$ btree seek directly to un-relayed rows for the stream.
2. **Option B (Cursor Polling)**: In `relay.py`, track `last_relayed_outbox_id` in `stream_heads` and poll `WHERE outbox.id > :last_id AND outbox.tenant_id = :tenant AND outbox.stream_id = :stream`, avoiding the `NOT EXISTS` anti-join entirely.
3. **Owner Decision Required**: Owner decides whether to approve schema index migration in P1a or defer to DG-5 tuning.

---

## 7. Shared Verification Commands and Results

| Check | Command | Result |
|---|---|:---:|
| Package Validation | `uv run python tools/validate_package.py` | **PASS** (16 schemas, 15 fixtures, 11 events, 46 negative) |
| PPM Negative Cases | `uv run python ppm-v2/tools/run_negative_cases.py` | **PASS** (5 templates, 20 cases) |
| PPM Fixture Corpus | `uv run python ppm-v2/tools/check_fixtures.py` | **PASS** (23 fixtures, 3 rollup) |
| PPM Reducer Runner | `uv run python ppm-v2/tools/run_reducer_fixtures.py` | **PASS** (23 fixtures) |
| Translation Check | `uv run python tools/check_translation.py` | **PASS** (1 up-to-date, 37 stale, 0 missing headers) |
| Full Python Test Suite | `uv run pytest -q` (against PostgreSQL 18.6) | **PASS** (431 passed in 48.27s) |
| Linting and Typecheck | `uv run ruff check src tests ppm-v2/reducer migrations` | **PASS** (0 errors) |
| Static Typecheck | `uv run mypy src tests ppm-v2/reducer` | **PASS** (0 errors in 118 files) |
| Web HUD Build | `npm --prefix web/hud run build` | **PASS** (built in 396ms) |
| Checksums | `sha256sum -c SHA256SUMS.txt` | **PASS** |

---

## 8. Go / No-Go Decision

- **Recommendation**: `ready_for_review` / `blocked` on `ISS-024`.
- **Actor**: Implementation Lead.
- **Rollback Procedure**: Feature flag `relay_enabled: false` halts all outbox processing while preserving un-relayed outbox rows intact in PostgreSQL.
- **Next Work Package**: P2a-WP1 (Sources and project scopes) after owner decision on `ISS-024`.
