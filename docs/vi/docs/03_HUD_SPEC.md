> Translated from docs/03_HUD_SPEC.md @ a36d44e

# Agent Operations HUD
## Màn hình và hành vi
Mockup trong visuals là mẫu desktop 1600×1000. UI production dùng layout responsive, keyboard navigation, labels và icon để trạng thái không phụ thuộc riêng màu. Preferences lưu trên user profile. Reduced motion và auto-scroll off khi người dùng đang đọc lịch sử.

| Surface | Nội dung | Hành động |
|---|---|---|
| Attention queue | Pending approval, failed/unknown runs, stale connectors, budget alerts | Inspect, approve/deny nếu authorized, assign operator |
| Tasks | Objective, project, owner, completion policy, current run | Open attempts, submit scoped follow-up |
| Runs | Agent version, stage, elapsed, progress x/y, last observation | Inspect timeline, cancel supported run, reconcile |
| Agent inventory | Owner, release, binding, certified capabilities, health | Register/propose update, suspend by role |
| Run inspector | Trace tree, tool spans, event timeline, artifacts, usage | Open evidence, redacted export, link project |
| PPM view | Reported/verified progress, criteria coverage, baseline, last scan | Review gaps, request scan, open acceptance packet |
| Harness lab | Phase 2 experiments, quality/cost comparison, canary health | Review candidate, release through approval |

## State/metrics semantics
Execution state và connectivity hai field độc lập. Heartbeat timeout là stale, confirmation từ provider mới kết luận worker terminated. Wall elapsed tính từ timestamps và clocks có offset, durations ưu tiên monotonic source. Last observed hiển thị timezone user, persist UTC. Denominator null => stage/elapsed, không render percent. Tool span durations overlap không được cộng làm total elapsed.

Cost hiển thị estimated/actual/unknown, provider và cost_as_of. PPM acceptance hiển thị pending/accepted/rejected/superseded riêng run outcome. Trace input/output bị redact mặc định và artifact access kiểm tra quyền lúc mở.

## Realtime protocol
GET /v1/hud/snapshot trả projection_revision + snapshot_cursor + accessible rows trong một consistent snapshot. GET /v1/hud/events?after=<cursor> qua SSE gửi typed deltas và heartbeat. Snapshot+cursor được lấy atomically, stream replay after cursor để tránh mất event giữa snapshot và subscribe. Expired cursor trả resync_required, client lấy snapshot mới. Cursor opaque gắn tenant/user/filter scope. ACL revoke đóng hoặc tái lọc subscription, client nhận remove rows và xóa cached detail.
Polling fallback GET snapshot mỗi 10s trong pilot nếu SSE không có. Client reconnect exponential backoff + jitter, live/offline indicator. Slow consumer bị ngắt và resync, metadata event size bounded. raw token deltas có thể sampled/coalesced; audit/action events phải durable.

## Operator commands
POST /v1/runs/{id}/commands {kind, expected_revision, idempotency_key}. Command accepted trả 202 và command_id, UI hiện cancelling/requested cho đến acknowledgement. Approvals là endpoint riêng với action hash và expected revision. Backend trả 403/409/422 cho unauthorized/stale/unsupported. Disable nút theo capability + permission nhưng server vẫn enforce. Retry tạo new run chỉ sau side-effect reconciliation hoặc safe-retry policy.

## Acceptance targets đề xuất
At 20 active runs, 10 metadata events/s/run, p95 authorized event-to-HUD <2s trên môi trường pilot được ghi rõ. Client hiển thị stale nếu 30s không thấy heartbeat (configurable). Các số cần benchmark, chưa là SLA đạt được.
Gates gồm duplicate/out-of-order events, SSE reconnect without resubmission, expired cursor resync, auth revoke during stream, unknown cost, missing progress denominator, click cancel twice, expired approval, late terminal event, narrow screen/keyboard focus và output redaction.

## Revision 1.1 — projection contract và ngữ nghĩa hiển thị
Snapshot tuân theo `contracts/hud-projection.schema.json` 1.1 (ví dụ `examples/hud-projection.json`). Mỗi row có các trục độc lập, mỗi trục có nhãn chữ riêng:

| Trục | Field | Nguồn sự thật | Hiển thị |
|---|---|---|---|
| Task | task_state | Task Service | Badge chữ, không suy từ run |
| Run | run_state, attempt | Run Service journal | "Attempt 2 · running" |
| Remote outcome | remote_outcome | Run Service reconciliation | `unknown` hiển thị "Remote outcome unknown — no retry until reconciled" |
| Connectivity | connectivity, last_observed_at, observation_source | Heartbeat/connector status | "Stale · last observed 09:10:30 (+07:00) via run-service" |
| Project | verification, validity, acceptance, assessment_as_of | PPM domain DB | Ba chip riêng; acceptance chỉ đổi qua reviewer action |
| Cost | status, amount_usd, scope, as_of | Usage ledger | actual/estimated: số; partial: "≥ $0.42 (some calls unknown)"; unknown: "Unknown", không bao giờ "$0" |
| Pending command | kind, state | Command table | "Cancel requested" cho đến acknowledgement |

Mẫu số: percent chỉ khi `progress.kind=units` và total_units ≥ 1. Project progress hiển thị `verified_weight / total_weight` của baseline revision hiện hành, kèm "N/M criteria có evidence hiện hành"; không có baseline thì hiển thị "No baseline — progress not computed". Tổng chi phí nhiều run: cộng các run actual/estimated/partial thành lower bound, đếm riêng số run unknown ("≥ $4.82 · 2 runs unknown"); mockup hiện tại ghi "$4.82 Estimate · 2 runs unknown" phải đổi sang dạng lower bound khi implement. Ô "Verified scope 6 / 10 criteria" trong mockup phải hiển thị weight và baseline revision.

Reconnect và quyền: mỗi lần mở SSE, server xác thực lại session và kiểm tra cursor thuộc cùng tenant/principal/filter và cùng `policy_version` hoặc grant set; nếu grants đổi hoặc cursor quá retention → `resync_required`, client bỏ cache, gọi snapshot mới. Cursor không bao giờ mở rộng quyền đã bị thu hồi. Client hiển thị trạng thái kết nối HUD (live/reconnecting/offline + thời điểm snapshot) tách khỏi connectivity của worker.

Năng lực cần đo trước khi cam kết: 20 active runs × 10 metadata events/s = 200 events/s; với payload bounded ~1 KB, ingestion ~0.2 MB/s và vài trăm insert/s là giả định nằm trong khả năng một PostgreSQL node và một API process SSE. Không thêm Redis/Kafka/WebSocket gateway cho tới khi load test P3 cho thấy p95 event-to-HUD vượt 2 s hoặc DB CPU > 70% ở profile này.

### Thu hồi quyền khi SSE vẫn đang mở
Reconnect không phải nơi duy nhất kiểm quyền. Subscription manager giữ `grant_epoch` của từng subscription. Khi grant/ACL đổi, Policy Service phát `policy.changed` (outbox, cùng transaction với thay đổi grant). Subscription manager tăng epoch và đánh giá lại phạm vi trước khi gửi event tiếp theo. Mỗi event gửi đi được lọc theo phạm vi của epoch hiện hành, không theo phạm vi lúc subscribe. Rows bị mất quyền nhận delta `row.removed` và client xóa cache detail. Evidence/artifact luôn tải qua `GET /v1/artifacts/{id}` có xác thực ở mỗi request; MVP không dùng signed URL. Nếu sau này dùng signed URL, TTL ≤ 60 s và revocation không thu hồi được URL đã cấp trong TTL: đây là residual risk phải ghi rõ.
Mục tiêu đề xuất (cần đo): p95 thời gian từ commit thu hồi quyền tới khi chính sách có hiệu lực trên stream đang mở < 2 s. Test C30: giữ HUD mở, thu hồi quyền đọc project, rồi phát event chứa dữ liệu project đó. Ghi timestamp commit thu hồi, timestamp event cuối client nhận cho project, và kết quả mở evidence bằng đường dẫn cũ (phải 403).

### Ngân sách dung lượng
Throughput đỉnh không đủ để ước lượng dung lượng. Ở 200 events/s và payload 1 KB, nếu chạy liên tục cả ngày: 200 × 1 KB × 86.400 s ≈ 17,3 GB/ngày, chưa tính index, WAL và backup. Trên server 100 GB dùng chung với websites, con số quyết định là tỷ lệ thời gian ở mức đỉnh và số byte thực lưu mỗi event. Thiết kế giảm dung lượng (áp dụng khi số đo cần): heartbeat không lưu thành event bền vững (chỉ cập nhật last_observed_at); progress coalesce; raw token deltas không lưu; audit/action/state events luôn bền vững. Retention theo lớp (HUD metadata events, trace, audit, evidence) chỉ được chốt ở D-05 sau khi có số đo của P5-WP4.
Chính sách evidence đã chốt cho Phase 1: không dùng signed URL. Mỗi lần mở evidence đều qua `GET /v1/artifacts/{id}`, kiểm quyền tại thời điểm request. Sau khi thu hồi quyền, đường dẫn cũ trả 403.
