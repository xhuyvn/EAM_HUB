# SC-07 Ingestion Baseline Measurement Report (P1a-WP7)

- **Work Package**: P1a-WP7 Ingest baseline (measurement)
- **DG-1 Condition**: DG1-C4 (Relay sustains ≥ 200 events/s for 10 min, 0 lost / 0 duplicated)
- **Commit**: `fd3b7a1f516f4732b0dc66228bef01d146be3c82`
- **Execution Timestamp**: `2026-11-08T03:44:19.182879+00:00`
- **DG1-C4 Verdict**: **PASS** (Worst 10s Window: `202.40` evt/s ≥ 200.0 evt/s)

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
| Actual Duration | 600.86 s | Total wall-clock elapsed time |
| Target Rate | 200 events/s | Nominal required arrival rate |
| Producer Pacing Target | 205.0 events/s | Includes 2.5% anti-jitter margin |
| Concurrent Producers | 4 | Worker threads writing outbox transactions |
| Concurrent Streams | 20 | Distinct task/run execution streams |
| Concurrent Relays | 2 | Worker threads polling & ingesting outbox rows |
| Relay Batch Size | 250 | Maximum outbox rows polled per transaction |
| Relay Batch Count | 35031 | Total relay transaction batches executed |
| Deterministic Seed | 42 | PRNG seed for deterministic test payload |
| Process CPU Utilization | 73.6 % | Process CPU time / wall-clock time |

---

## 3. Throughput and Windowed Rates

- **Total Events Ingested**: `123,016` events
- **Overall Throughput**: `204.73` events/s
- **Worst 10s Window Throughput**: `202.40` events/s
- **Requirement (DG1-C4)**: Worst 10s window sustained ≥ 200.0 events/s: **PASS**

### 10-Second Non-Overlapping Window Summary

| Window Index | Time Window | Event Count | Achieved Rate (events/s) | Target Met (≥ 200) |
|:---:|:---:|:---:|:---:|:---:|
| 1 |   0s -  10s | 2,034 | 203.40 | YES |
| 2 |  10s -  20s | 2,050 | 205.00 | YES |
| 3 |  20s -  30s | 2,049 | 204.90 | YES |
| 4 |  30s -  40s | 2,048 | 204.80 | YES |
| 5 |  40s -  50s | 2,048 | 204.80 | YES |
| 6 |  50s -  60s | 2,049 | 204.90 | YES |
| 7 |  60s -  70s | 2,050 | 205.00 | YES |
| 8 |  70s -  80s | 2,046 | 204.60 | YES |
| 9 |  80s -  90s | 2,055 | 205.50 | YES |
| 10 |  90s - 100s | 2,052 | 205.20 | YES |
| 11 | 100s - 110s | 2,045 | 204.50 | YES |
| 12 | 110s - 120s | 2,050 | 205.00 | YES |
| 13 | 120s - 130s | 2,046 | 204.60 | YES |
| 14 | 130s - 140s | 2,053 | 205.30 | YES |
| 15 | 140s - 150s | 2,042 | 204.20 | YES |
| 16 | 150s - 160s | 2,053 | 205.30 | YES |
| 17 | 160s - 170s | 2,052 | 205.20 | YES |
| 18 | 170s - 180s | 2,047 | 204.70 | YES |
| 19 | 180s - 190s | 2,044 | 204.40 | YES |
| 20 | 190s - 200s | 2,045 | 204.50 | YES |
| 21 | 200s - 210s | 2,053 | 205.30 | YES |
| 22 | 210s - 220s | 2,055 | 205.50 | YES |
| 23 | 220s - 230s | 2,024 | 202.40 | YES |
| 24 | 230s - 240s | 2,070 | 207.00 | YES |
| 25 | 240s - 250s | 2,042 | 204.20 | YES |
| 26 | 250s - 260s | 2,048 | 204.80 | YES |
| 27 | 260s - 270s | 2,040 | 204.00 | YES |
| 28 | 270s - 280s | 2,064 | 206.40 | YES |
| 29 | 280s - 290s | 2,032 | 203.20 | YES |
| 30 | 290s - 300s | 2,058 | 205.80 | YES |
| 31 | 300s - 310s | 2,058 | 205.80 | YES |
| 32 | 310s - 320s | 2,041 | 204.10 | YES |
| 33 | 320s - 330s | 2,044 | 204.40 | YES |
| 34 | 330s - 340s | 2,041 | 204.10 | YES |
| 35 | 340s - 350s | 2,048 | 204.80 | YES |
| 36 | 350s - 360s | 2,043 | 204.30 | YES |
| 37 | 360s - 370s | 2,061 | 206.10 | YES |
| 38 | 370s - 380s | 2,041 | 204.10 | YES |
| 39 | 380s - 390s | 2,048 | 204.80 | YES |
| 40 | 390s - 400s | 2,054 | 205.40 | YES |
| 41 | 400s - 410s | 2,046 | 204.60 | YES |
| 42 | 410s - 420s | 2,042 | 204.20 | YES |
| 43 | 420s - 430s | 2,059 | 205.90 | YES |
| 44 | 430s - 440s | 2,051 | 205.10 | YES |
| 45 | 440s - 450s | 2,051 | 205.10 | YES |
| 46 | 450s - 460s | 2,045 | 204.50 | YES |
| 47 | 460s - 470s | 2,056 | 205.60 | YES |
| 48 | 470s - 480s | 2,036 | 203.60 | YES |
| 49 | 480s - 490s | 2,044 | 204.40 | YES |
| 50 | 490s - 500s | 2,065 | 206.50 | YES |
| 51 | 500s - 510s | 2,047 | 204.70 | YES |
| 52 | 510s - 520s | 2,045 | 204.50 | YES |
| 53 | 520s - 530s | 2,056 | 205.60 | YES |
| 54 | 530s - 540s | 2,046 | 204.60 | YES |
| 55 | 540s - 550s | 2,048 | 204.80 | YES |
| 56 | 550s - 560s | 2,048 | 204.80 | YES |
| 57 | 560s - 570s | 2,062 | 206.20 | YES |
| 58 | 570s - 580s | 2,049 | 204.90 | YES |
| 59 | 580s - 590s | 2,052 | 205.20 | YES |
| 60 | 590s - 600s | 2,042 | 204.20 | YES |

---

## 4. Latency Distribution

| Latency Stage | p50 (ms) | p95 (ms) | p99 (ms) |
|---|:---:|:---:|:---:|
| Ingest -> Projection Checkpoint | 8.59 ms | 31.85 ms | 42.02 ms |
| Producer Commit -> Projection Checkpoint | 212.89 ms | 662.25 ms | 772.74 ms |

---

## 5. Storage Growth and Bytes Per Event (ARCH-24 Baseline)

- **WAL LSN Range**: `3/D391218` -> `3/24B93AD8`
- **Total WAL Written**: `394,275,008` bytes (376.01 MB)
- **WAL Growth Per Event**: `3205.07` bytes/event
- **Total Database Growth**: `227,115,008` bytes (216.59 MB)

### Relation Sizes (pg_total_relation_size)

| Table Name | Before (bytes) | After (bytes) | Growth (bytes) | Bytes / Event |
|---|:---:|:---:|:---:|:---:|
| `outbox` | 16,384 | 80,388,096 | 80,371,712 | 653.34 |
| `event_log` | 32,768 | 120,512,512 | 120,479,744 | 979.38 |
| `projection_checkpoints` | 16,384 | 90,112 | 73,728 | 0.60 |
| `outbox_relay_marks` | 16,384 | 26,050,560 | 26,034,176 | 211.63 |
| `stream_heads` | 16,384 | 172,032 | 155,648 | 1.27 |
| `security_events` | 32,768 | 32,768 | 0 | 0.00 |

### Storage & WAL Rate Stability: First 60s vs Last 60s (R7-3)

| Interval | Ingested Events | WAL Bytes | WAL Bytes / Event | Table Growth | Table Bytes / Event |
|---|:---:|:---:|:---:|:---:|:---:|
| First 60s | 12,280 | 42,978,736 | 3499.90 B/evt | 22,585,344 B | 1839.20 B/evt |
| Last 60s | 12,389 | 38,270,968 | 3089.11 B/evt | 22,806,528 B | 1840.87 B/evt |
| Full Run (600.86s) | 123,016 | 394,275,008 | 3205.07 B/evt | 227,115,008 B | 1846.22 B/evt |

---

## 6. Correctness Under Load

| Check | Requirement | Result | Status |
|---|---|---|:---:|
| 0 Lost | Every outbox row relayed to event_log & outbox_relay_marks | 123016/123016 | PASS |
| 0 Duplicated | 0 duplicate event_id in event_log | 0 duplicates | PASS |
| Contiguous Offsets | Offsets per stream strictly contiguous 1..N | Contiguous | PASS |
| Projection Hash Match | Full stream rebuild hash == Live checkpoint hash | Hash identical across all streams | PASS |
| 0 Gaps (R7-2) | Well-behaved producer: gap_count == 0 | 0 gaps | PASS |

---

## 7. DG1-C4 Verdict and Conclusion

- **Sustained Throughput Condition**: Sustained ≥ 200 events/s for full duration with worst 10s window ≥ 200.0 events/s.
- **Observed Worst 10s Window**: `202.40` events/s.
- **Correctness Checks**: All passed (0 lost, 0 duplicated, contiguous offsets, hash parity, 0 gaps).
- **Final Verdict**: **PASS**.

---

## 8. Headroom Ramp (Non-gating, for DG-5)

- **Tested Rates**: `200, 400, 800` events/s (for 60 s each)
- **Highest Rate with Sustained Worst 10s Window >= Target & Correctness PASS**: **0 events/s**
- **Observed Bottleneck at Capacity Limit**: `Producer transaction commit latency under WAL fsync (p95=1541.5ms)`

### Headroom Step Details

| Target Rate (evt/s) | Achieved Rate (evt/s) | Worst 10s Window (evt/s) | Target Sustained | Correctness | p95 Latency (ms) | Bottleneck Observed |
|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 200 | 191.6 | 197.2 | NO | PASS | 2095.6 ms | Producer transaction commit latency under WAL fsync (p95=2095.6ms) |
| 400 | 374.4 | 314.1 | NO | PASS | 1430.0 ms | Producer transaction commit latency under WAL fsync (p95=1430.0ms) |
| 800 | 390.7 | 367.0 | NO | PASS | 1541.5 ms | Producer transaction commit latency under WAL fsync (p95=1541.5ms) |
