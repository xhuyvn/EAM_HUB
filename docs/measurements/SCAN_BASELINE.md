# SC-07 PPM Scan Baseline Measurement Report (P2a-WP8)

- **Work Package**: P2a-WP8 Scan baseline (measurement, SC-07)
- **Commit**: `69415fe07c4f7959561fd2f5d6380be6a9e48f50`
- **Execution Timestamp**: `2026-11-16T22:47:15.242259+00:00`
- **Total Benchmark Duration**: `953.819 s`
- **Exact Command**: `uv run python tools/bench/scan_baseline.py --db-url <redacted-db-url> --reset-db --scales 1000,10000,50000 --repeats 5 --md-output docs/measurements/SCAN_BASELINE.md`

---

## 1. Environment and PostgreSQL Configuration

| Parameter | Value |
|---|---|
| Operating System | Windows-11-10.0.26300 |
| CPU Model | Intel64 Family 6 Model 198 Stepping 2, GenuineIntel |
| Logical CPU Cores | 24 |
| Physical Memory (RAM) | 63.37 GB |
| Python Version | 3.12.13 |
| Git Version | git version 2.54.0.windows.1 |
| PostgreSQL Version | PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 14.2.0-19) 14.2.0, 64-bit |
| `shared_buffers` | `128MB` |
| `work_mem` | `4MB` |
| `effective_cache_size` | `4GB` |
| `max_connections` | `100` |
| `synchronous_commit` | `on` |
| `fsync` | `on` |
| `wal_buffers` | `4MB` |
| `max_wal_size` | `1GB` |
| `checkpoint_timeout` | `5min` |

---

## 2. Measured Scales: Initial Full Scan

| Target Files | Measured Files | Total Bytes | Repo Gen (s) | Scan (ms) | Ingest (ms) | Assessment (ms) | Total (ms) | DB Growth | WAL Growth |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 1,000 | 325,791 | 1.881 | 1721.10 | 48.82 | 18.12 | 1788.04 | 425,984 B (0.406 MB) | 99,640 B (0.095 MB) |
| 10,000 | 10,000 | 3,250,792 | 17.429 | 41846.07 | 41.25 | 12.52 | 41899.84 | 827,392 B (0.789 MB) | 846,192 B (0.807 MB) |
| 50,000 | 50,000 | 16,330,784 | 540.642 | 237301.45 | 131.56 | 23.31 | 237456.32 | 4,022,272 B (3.836 MB) | 57,189,512 B (54.540 MB) |

---

## 3. Incremental Scan Durations & Storage Growth (1 modified file + 1 new `.ppm/updates/` commit)

| Scale | Repeats | Scan p50 / p95 (ms) | Ingest p50 / p95 (ms) | Assessment p50 / p95 (ms) | Total p50 / p95 (ms) | DB Growth / scan (p50) | WAL Growth / scan (p50) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 5 | 275.51 / 293.36 | 22.81 / 25.98 | 14.62 / 15.77 | 312.46 / 334.54 | 98,304 B (0.094 MB) | 94,848 B (0.090 MB) |
| 10,000 | 5 | 1822.34 / 1904.98 | 42.03 / 45.83 | 13.41 / 19.72 | 1877.42 / 1956.52 | 819,200 B (0.781 MB) | 836,296 B (0.798 MB) |
| 50,000 | 5 | 8598.20 / 9257.96 | 119.50 / 134.29 | 19.34 / 22.55 | 8736.08 / 9397.14 | 4,022,272 B (3.836 MB) | 4,218,832 B (4.023 MB) |

---

## 4. Unchanged Rescan Durations & Semantic Deduplication (0 file changes)

| Scale | Repeats | Scan p50 / p95 (ms) | Ingest p50 / p95 (ms) | Assessment p50 / p95 (ms) | Total p50 / p95 (ms) | Deduplicated | DB Growth / scan (p50) | WAL Growth / scan (p50) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 5 | 271.60 / 278.27 | 14.36 / 35.09 | 8.85 / 9.87 | 300.21 / 316.55 | 5/5 | 90,112 B (0.086 MB) | 88,064 B (0.084 MB) |
| 10,000 | 5 | 1761.87 / 1813.80 | 31.75 / 38.35 | 10.05 / 10.68 | 1801.13 / 1854.80 | 5/5 | 794,624 B (0.758 MB) | 829,568 B (0.791 MB) |
| 50,000 | 5 | 8387.67 / 8857.01 | 112.99 / 122.57 | 17.06 / 24.31 | 8524.07 / 8996.12 | 5/5 | 4,022,272 B (3.836 MB) | 4,790,192 B (4.568 MB) |

---

## 5. Methodology & Threshold Policy (SC-07 / ARCH-30)

- **Collector behavior**: `PpmCollector.scan_source` walks all files matching `scan_scope` (`["."]`), opens each file via handle-contained `safe_open`, computes SHA-256, and persists a `collector_snapshots` + `project_snapshots` manifest. Consequently, collector scan duration scales linearly with repository file count on both initial and incremental scans.
- **Ingest & assessment deduplication**: `ingest_snapshot` deduplicates unchanged `.ppm/` artifacts, and `create_assessment` (`ISS-034`) deduplicates unchanged semantic `inputs_hash` (`deduplicated=True` on unchanged rescans).
- **No invented thresholds**: Per `docs/IMPLEMENTATION_PLAN.md` (`ARCH-30`, `SC-07`), this report records baseline measurements only. Formal acceptance thresholds belong to **DG-5** (prior to `P5-WP4`).
