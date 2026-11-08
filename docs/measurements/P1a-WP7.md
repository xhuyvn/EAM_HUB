# SC-07 Ingestion Baseline Measurement Report (P1a-WP7)

- **Work Package**: P1a-WP7 Ingest baseline (measurement)
- **DG-1 Condition**: DG1-C4 (Relay sustains ≥ 200 events/s for 10 min, 0 lost / 0 duplicated)
- **Commit**: `4b6269a3c5cc46b1df87429c2640219429c9fedb`
- **Execution Timestamp**: `2026-11-08T00:57:36.340083+00:00`
- **DG1-C4 Verdict**: **FAIL** (Worst 10s Window: `50.00` evt/s ≥ 200.0 evt/s)

---

## 1. Environment and Configuration

| Parameter | Value |
|---|---|
| CPU Model | Intel64 Family 6 Model 198 Stepping 2, GenuineIntel |
| Logical CPU Cores | 24 |
| Physical Memory (RAM) | 63.37 GB |
| Storage Disk Type | SSD / NVMe |
| Operating System | Windows-11-10.0.26300-SP0 |
| Python Version | 3.12.13 |
| PostgreSQL Version | 18.6 (Debian 18.6-1.pgdg13+2) |
| fsync | on |
| synchronous_commit | on |
| shared_buffers | 128MB |
| wal_buffers | 4MB |
| max_connections | 100 |

---

## 2. Benchmark Parameters and Execution

| Parameter | Value | Description |
|---|---|---|
| Configured Duration | 600 s | Target test duration |
| Actual Duration | 1813.06 s | Total wall-clock elapsed time |
| Target Rate | 200 events/s | Nominal required arrival rate |
| Producer Pacing Target | 205.0 events/s | Includes 2.5% anti-jitter margin |
| Concurrent Producers | 4 | Worker threads writing outbox transactions |
| Concurrent Streams | 20 | Distinct task/run execution streams |
| Concurrent Relays | 2 | Worker threads polling & ingesting outbox rows |
| Relay Batch Size | 250 | Maximum outbox rows polled per transaction |
| Relay Batch Count | 7716 | Total relay transaction batches executed |
| Deterministic Seed | 42 | PRNG seed for deterministic test payload |
| Process CPU Utilization | 49.7 % | Process CPU time / wall-clock time |

---

## 3. Throughput and Windowed Rates

- **Total Events Ingested**: `120,283` events
- **Overall Throughput**: `66.34` events/s
- **Worst 10s Window Throughput**: `50.00` events/s
- **Requirement (DG1-C4)**: Worst 10s window sustained ≥ 200.0 events/s: **FAIL**

### 10-Second Non-Overlapping Window Summary

| Window Index | Time Window | Event Count | Achieved Rate (events/s) | Target Met (≥ 200) |
|:---:|:---:|:---:|:---:|:---:|
| 1 |   0s -  10s | 2,040 | 204.00 | YES |
| 2 |  10s -  20s | 2,043 | 204.30 | YES |
| 3 |  20s -  30s | 2,044 | 204.40 | YES |
| 4 |  30s -  40s | 2,044 | 204.40 | YES |
| 5 |  40s -  50s | 2,051 | 205.10 | YES |
| 6 |  50s -  60s | 2,022 | 202.20 | YES |
| 7 |  60s -  70s | 2,054 | 205.40 | YES |
| 8 |  70s -  80s | 1,920 | 192.00 | NO |
| 9 |  80s -  90s | 1,983 | 198.30 | NO |
| 10 |  90s - 100s | 1,920 | 192.00 | NO |
| 11 | 100s - 110s | 1,872 | 187.20 | NO |
| 12 | 110s - 120s | 1,803 | 180.30 | NO |
| 13 | 120s - 130s | 1,726 | 172.60 | NO |
| 14 | 130s - 140s | 1,601 | 160.10 | NO |
| 15 | 140s - 150s | 1,571 | 157.10 | NO |
| 16 | 150s - 160s | 1,585 | 158.50 | NO |
| 17 | 160s - 170s | 1,432 | 143.20 | NO |
| 18 | 170s - 180s | 1,200 | 120.00 | NO |
| 19 | 180s - 190s | 1,500 | 150.00 | NO |
| 20 | 190s - 200s | 1,250 | 125.00 | NO |
| 21 | 200s - 210s | 1,250 | 125.00 | NO |
| 22 | 210s - 220s | 1,500 | 150.00 | NO |
| 23 | 220s - 230s | 1,250 | 125.00 | NO |
| 24 | 230s - 240s | 1,000 | 100.00 | NO |
| 25 | 240s - 250s | 1,250 | 125.00 | NO |
| 26 | 250s - 260s | 1,250 | 125.00 | NO |
| 27 | 260s - 270s | 1,250 | 125.00 | NO |
| 28 | 270s - 280s | 1,250 | 125.00 | NO |
| 29 | 280s - 290s | 1,000 | 100.00 | NO |
| 30 | 290s - 300s | 1,250 | 125.00 | NO |
| 31 | 300s - 310s | 1,000 | 100.00 | NO |
| 32 | 310s - 320s | 1,250 | 125.00 | NO |
| 33 | 320s - 330s | 1,000 | 100.00 | NO |
| 34 | 330s - 340s | 1,000 | 100.00 | NO |
| 35 | 340s - 350s | 750 | 75.00 | NO |
| 36 | 350s - 360s | 1,000 | 100.00 | NO |
| 37 | 360s - 370s | 1,000 | 100.00 | NO |
| 38 | 370s - 380s | 1,000 | 100.00 | NO |
| 39 | 380s - 390s | 1,250 | 125.00 | NO |
| 40 | 390s - 400s | 1,000 | 100.00 | NO |
| 41 | 400s - 410s | 750 | 75.00 | NO |
| 42 | 410s - 420s | 1,000 | 100.00 | NO |
| 43 | 420s - 430s | 1,000 | 100.00 | NO |
| 44 | 430s - 440s | 1,000 | 100.00 | NO |
| 45 | 440s - 450s | 1,000 | 100.00 | NO |
| 46 | 450s - 460s | 1,000 | 100.00 | NO |
| 47 | 460s - 470s | 750 | 75.00 | NO |
| 48 | 470s - 480s | 750 | 75.00 | NO |
| 49 | 480s - 490s | 1,000 | 100.00 | NO |
| 50 | 490s - 500s | 1,000 | 100.00 | NO |
| 51 | 500s - 510s | 500 | 50.00 | NO |
| 52 | 510s - 520s | 1,000 | 100.00 | NO |
| 53 | 520s - 530s | 500 | 50.00 | NO |
| 54 | 530s - 540s | 1,000 | 100.00 | NO |
| 55 | 540s - 550s | 500 | 50.00 | NO |
| 56 | 550s - 560s | 1,000 | 100.00 | NO |
| 57 | 560s - 570s | 1,000 | 100.00 | NO |
| 58 | 570s - 580s | 500 | 50.00 | NO |
| 59 | 580s - 590s | 750 | 75.00 | NO |
| 60 | 590s - 600s | 750 | 75.00 | NO |

---

## 4. Latency Distribution

| Latency Stage | p50 (ms) | p95 (ms) | p99 (ms) |
|---|:---:|:---:|:---:|
| Ingest -> Projection Checkpoint | 2220.03 ms | 11697.46 ms | 14220.92 ms |
| Producer Commit -> Projection Checkpoint | 124950.27 ms | 1157188.41 ms | 1300308.03 ms |

---

## 5. Storage Growth and Bytes Per Event (ARCH-24 Baseline)

- **WAL LSN Range**: `0/22EBA88` -> `2/C9FAAEC8`
- **Total WAL Written**: `11,941,966,912` bytes (11388.75 MB)
- **WAL Growth Per Event**: `99282.25` bytes/event
- **Total Database Growth**: `836,460,544` bytes (797.71 MB)

### Relation Sizes (pg_total_relation_size)

| Table Name | Before (bytes) | After (bytes) | Growth (bytes) | Bytes / Event |
|---|:---:|:---:|:---:|:---:|
| `outbox` | 163,840 | 78,594,048 | 78,430,208 | 652.05 |
| `event_log` | 253,952 | 110,272,512 | 110,018,560 | 914.66 |
| `projection_checkpoints` | 81,920 | 81,920 | 0 | 0.00 |
| `outbox_relay_marks` | 114,688 | 26,959,872 | 26,845,184 | 223.18 |
| `stream_heads` | 81,920 | 621,248,512 | 621,166,592 | 5164.21 |

---

## 6. Correctness Under Load

| Check | Requirement | Result | Status |
|---|---|---|:---:|
| 0 Lost | Every outbox row relayed to event_log & outbox_relay_marks | 120283/120283 | PASS |
| 0 Duplicated | 0 duplicate event_id in event_log | 0 duplicates | PASS |
| Contiguous Offsets | Offsets per stream strictly contiguous 1..N | Contiguous | PASS |
| Projection Hash Match | Full stream rebuild hash == Live checkpoint hash | Hash identical across all streams | PASS |

---

## 7. DG1-C4 Verdict and Conclusion

- **Sustained Throughput Condition**: Sustained ≥ 200 events/s for full duration with worst 10s window ≥ 200.0 events/s.
- **Observed Worst 10s Window**: `50.00` events/s.
- **Correctness Checks**: All passed (0 lost, 0 duplicated, contiguous offsets, hash parity).
- **Final Verdict**: **FAIL**.

---

## 8. Headroom Ramp (Non-gating, for DG-5)

- **Tested Rates**: `200, 400, 800` events/s (for 60 s each)
- **Highest Rate with Sustained Worst 10s Window >= Target & Correctness PASS**: **0 events/s**
- **Observed Bottleneck at Capacity Limit**: `Producer transaction commit latency under WAL fsync (p95=22735.9ms)`

### Headroom Step Details

| Target Rate (evt/s) | Achieved Rate (evt/s) | Worst 10s Window (evt/s) | Target Sustained | Correctness | p95 Latency (ms) | Bottleneck Observed |
|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 200 | 169.0 | 175.8 | NO | PASS | 6078.8 ms | Producer transaction commit latency under WAL fsync (p95=6078.8ms) |
| 400 | 274.6 | 200.0 | NO | PASS | 20445.0 ms | Producer transaction commit latency under WAL fsync (p95=20445.0ms) |
| 800 | 264.4 | 200.0 | NO | PASS | 22735.9 ms | Producer transaction commit latency under WAL fsync (p95=22735.9ms) |
