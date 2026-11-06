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
