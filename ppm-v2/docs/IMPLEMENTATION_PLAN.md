# Implementation Plan — PPM v2 Agent

Plan revision: PPM-1.1 (06/11/2026; khớp plan platform rev 1.5: M0 sau P0-WP1, nhúng vào P2a-WP4, có trong mốc D0). Kế hoạch này **không cạnh tranh** với `docs/IMPLEMENTATION_PLAN.md` (platform, rev 1.5). Nó (1) thêm một milestone độc lập **PPM-M0** để làm lõi domain trước khi platform sẵn sàng, (2) map phần việc PPM vào WP của platform, (3) thêm WP cho program/portfolio/benefit mà platform chưa có. Findings tham chiếu `ppm-v2/docs/ARCHITECTURE_REVIEW.md` (PPM-xx) và `docs/ARCHITECTURE_REVIEW.md` (ARCH-xx).

## 1. Ranh giới
| Ai sở hữu | Nội dung |
|---|---|
| PPM v2 (domain) | Contract `.ppm` 2.1, ngữ nghĩa criterion/claim/evidence trust/waiver/baseline, reducer rules, fixture corpus, rollup rules |
| Platform | Location registration, connector/collector, Run Service, scheduler, Model Gateway, verifier sandbox runtime, acceptance API, HUD |
Reducer core (M0) là thư viện Python thuần, không I/O, không network, không model; P2a-WP4 nhúng nó thay vì viết lại.

## 2. Lộ trình và critical path
```
PPM-M0 (WP1 → WP2 → WP3, WP4)   chạy song song với platform P0/P1a
        │
        └─► P2a-WP4 (nhúng reducer) ─► P2a-WP6 ─► D1-fixture (platform)
PPM-C1 (contract program/portfolio/benefit) ─► PPM-P4-WP1 ─► shared pilot
Track S (standalone adoption pilot) — tùy chọn, không chặn gì
```
Critical path PPM tới D1-fixture: M0-WP1 → M0-WP2 → P2a-WP4. M0 không nằm trên critical path platform nếu xong trước P2a (platform P0+P1a ≈ 12–17 ngày).

## 3. Milestone PPM-M0 — Deterministic reducer core (local MVP domain)
Mục tiêu: một hàm `assess(inputs) -> assessment` thuần, deterministic, chạy được trên fixture, output hợp lệ theo `contracts/ppm-output.schema.json`. Effort: **4–6 ngày**, giả định 1 kỹ sư Python, không model. Prerequisite: P0-WP1 (uv workspace có member rỗng `ppm-v2/reducer`, Python 3.12); không cần DB hay service nào khác. Theo plan platform rev 1.5, M0 chạy trong lúc owner review DG-1/DG-2.

### PPM-M0-WP1 Fixture corpus — 1–1.5 ngày
- Prerequisite: không. Paths: `ppm-v2/evals/fixtures/<scenario>/{inputs.json,expected.json,README.md}`, `ppm-v2/evals/fixtures/SCHEMA.md`.
- Bước: định nghĩa `inputs.json` (baseline: criteria + `verification_refs` + weights; claims đã parse từ `.ppm`; evidence list có `trust_class`, `revision`, `observed_at`, `test_id`, `result`; waivers; `assessed_revision`; `now`). Viết fixture theo `SCENARIO_MATRIX.md` cho #1, 2, 3, 6, 7, 8, 10, 16, 19 và s21–s24.
- Acceptance: mỗi fixture có `expected.json` viết tay trước khi có code; `expected.json` (assessment một project) validate theo `contracts/ppm-output.schema.json`. Fixture rollup (s08, s10, s19) có thêm `expected_rollup.json` theo `ppm-v2/evals/fixtures/ROLLUP_SCHEMA.md` (nội bộ M0; contract chính thức ở PPM-C1).
- Evidence: commit hash corpus; danh sách fixture → scenario.
- Rollback: chỉ thêm file.

### PPM-M0-WP2 Criterion reducer — 1.5–2 ngày [PPM-01/02/03/04/05/06]
- Prerequisite: WP1. Paths: `ppm-v2/reducer/ppm_reducer/{__init__.py,criteria.py,trust.py,supersession.py}`, `ppm-v2/reducer/tests/`.
- Bước: resolve supersession (từ chối vòng/treo — dùng lại logic validator); map claim/evidence vào criterion qua `criterion_ids`/`test_ids`/`verification_refs`; áp bảng quyết định (review §2C); trust class (chỉ `verifier_observed`/`ci_attested` verify; FAIL ở mọi class tại đúng revision → contradicted); stale theo revision và TTL tham số hóa (D-03); waiver còn hạn → `waived`; claim trái ngược đồng thời → finding `conflicting_claims` (không phải reason_code; status vẫn theo evidence).
- Interface: `assess_criteria(baseline, claims, evidence, waivers, assessed_revision, now) -> list[CriterionResult]`.
- Failure tests: s21 forged JUnit → `unverified/reported_artifact_only`; s01 → contradicted; s06 evidence path mất → stale; s22 waiver hết hạn → quay lại mẫu số; supersession vòng → `InputRejected`.
- Acceptance: tất cả fixture criterion khớp `expected.json` byte-for-byte sau chuẩn hóa JCS; chạy 2 lần cùng input ra cùng hash; không import `requests`/`socket`/`subprocess` (kiểm bằng test).

### PPM-M0-WP3 Findings và progress — 1 ngày
- Prerequisite: WP2. Paths: `ppm_reducer/progress.py`, `ppm_reducer/findings.py`.
- Bước: progress `verified/total` với `total` loại `waived`; `basis=none` khi chưa có baseline (s24); finding `architecture_drift` (s03), `relationship_proposed` (s07), `identity_conflict` (s23) chỉ là finding, không đổi verdict.
- Acceptance: s24 → `progress.basis=none`, `acceptance_readiness=not_ready`; s03 không đổi criterion nào; denominator luôn hiển thị.

### PPM-M0-WP4 Rollup rules — 1–1.5 ngày [C24, C43, #10]
- Prerequisite: WP2. Paths: `ppm_reducer/rollup.py`.
- Bước: rollup theo deliverable distinct (owner project); lọc theo tập project được phép **trước** khi tính; nhãn `partial` không kèm số/tên bị ẩn; cost/budget thiếu → `cost_state=unknown` trong `RollupResult` (nội bộ M0).
- Acceptance: s08 deliverable dùng chung đếm 1 lần; s19 tổng partial, output không chứa ID project ẩn (test grep); s10 `cost_state=unknown`.

### PPM-M0-WP5 Đóng gói và đo — 0.5 ngày
- `ppm-v2/tools/run_reducer_fixtures.py` chạy toàn corpus, in `PASS n fixtures` hoặc diff. Ghi thời gian chạy và số fixture vào `ppm-v2/VALIDATION_REPORT.md`.
- Acceptance: runner chạy sạch trên máy sạch với `tools/requirements.txt`; P2a-WP4 có thể `pip install -e ppm-v2/reducer`.

M0 done khi: WP1–WP5 acceptance đạt; owner review fixture `expected.json` của s01, s21, s19 (ba case dễ hiểu sai nhất) và ký.

## 4. Map PPM vào platform WP
| Phần việc PPM | Platform WP | Bổ sung từ review PPM |
|---|---|---|
| Registry → location-registration import | P2a-WP1 | Mapping contract §14.7; egress không map |
| Collector, identity_conflict | P2a-WP2 | C42 |
| Verifier sandbox | P2a-WP9 | D-07; fixture test mở socket bị chặn |
| Ingest `.ppm` 2.0/2.1 | P2a-WP3 | Dùng `validate_contract.semantic_*`; quarantine version lạ |
| Reducer | P2a-WP4 | Nhúng PPM-M0; không viết lại rules |
| Analysis step (claim từ text tự do) | P2a-WP7 | Model chỉ trích claim kèm source span, không verdict |
| Baseline từ draft, waiver, acceptance | P2b-WP1/WP2 | C41, C44, C45 (đề xuất) |
| Program/portfolio | P4-WP1 | Cần PPM-C1 |

## 5. PPM-C1 — Contract program/portfolio/benefit (trước P4-WP1) — 2–3 ngày thiết kế
- Blocker: PPM-D4 (owner chốt phạm vi shared pilot).
- Nội dung: `program`, `portfolio`, `benefit` (owner program duy nhất, metric, baseline value, measured_at, `status ∈ {planned, measuring, realized, unknown}`), `capacity` (tùy chọn). Additive: contract 2.2, đọc 2.0/2.1.
- Acceptance: s09, s20 có fixture và expected; validator bắt benefit có hai owner program; review bởi một người làm PPM (không phải tác giả).

### PPM-P4-WP1 Rollup program/portfolio — 2–3 ngày (thuộc platform P4-WP1)
- Acceptance: s08, s19, s20 ở runtime với dữ liệu fixture; HUD hiển thị denominator và nhãn partial.

## 6. Track S — Standalone adoption pilot (tùy chọn) — 1–2 ngày công owner
- Áp dụng prompt 01 trên **một** repo owner chọn, coding agent ở chế độ read-only cho prompt 06.
- Thu: thời gian, câu hỏi agent phải hỏi, lỗi validator, số update/phiên. Không coi kết quả review là verified.
- Đầu ra: sửa prompt/template; dữ liệu cho PPM-15 (cấu trúc notes).

## 7. Finding → WP → evidence
| Finding | WP | Evidence nghiệm thu |
|---|---|---|
| PPM-01 Critical | M0-WP2 (rules), P2a-WP9 (runtime) | s21 PASS; C40 runtime |
| PPM-02, PPM-03 | M0-WP2 | Fixture không mapping → `coverage_not_mapped` |
| PPM-04 | M0-WP2, P2b-WP2 | s22; C41; `previous_status` nhận `waived` (đã sửa schema) |
| PPM-05 | Đã làm (validator) | `run_negative_cases.py` 20 cases |
| PPM-06 | M0-WP3 (finding), P2a-WP2 | s23; C42 |
| PPM-07 | P2a-WP1 | Import test registry → location-registration |
| PPM-08 | M0-WP3, P2b-WP1 | s24; C44 |
| PPM-09 | Đã làm (blueprint §19) | Review tài liệu |
| PPM-10 | M0-WP4 | s19; C43 |
| PPM-11 | M0-WP1 | `SCENARIO_MATRIX.md` đủ 20 dòng có owner |
| PPM-14 | PPM-C1, PPM-P4-WP1 | s09, s20 |

## 8. Quyết định chặn
| ID | Chặn | Mặc định nếu chưa chốt |
|---|---|---|
| D-03 TTL evidence | M0-WP2 (tham số) | Tham số; fixture dùng 7 ngày |
| D-07 sandbox verifier | P2a-WP9 | Fixture-only, không chạy repo thật |
| PPM-D1 CI attestation | `ci_attested` ở runtime | Không có class này ở pilot |
| PPM-D2 quyền waiver | P2b-WP2 | Chỉ owner project, ≤30 ngày |
| PPM-D4 phạm vi portfolio | PPM-C1 | Không làm program/benefit ở local MVP |

## 9. Ngoài phạm vi kế hoạch này
Dữ liệu repo thật (trừ Track S do owner tự chạy), provider tính phí, UI riêng của PPM (dùng HUD platform), LangGraph orchestration riêng (bị thay bằng Run Service, blueprint §19).
