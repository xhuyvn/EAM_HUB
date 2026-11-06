# Hướng dẫn tiếp nhận agent có sẵn
## Discovery
Inventory source/runtime version, API/CLI entrypoint, auth, storage, schedulers, model providers, tools, credentials, data classification, external sessions, output artifacts và owner. Đánh giá side effects nào đang bypass gateway và tác vụ có idempotency/reconcile không.

## Chọn wrapper
- Agent HTTP có status/result endpoints dùng external-service adapter.
- CLI one-shot có output file/schema dùng CLI wrapper với isolated workspace.
- SDK runtime dùng managed worker adapter.
- Ứng dụng chỉ có UI bắt đầu L0/manual evidence, GUI control là capability bổ sung sau test.
- Agent chưa có logs/events dùng coarse status polling, HUD ghi coverage=partial và stage unknown.

## Cutover
Lập mapping legacy job/task/session IDs. Freeze duplicate schedulers cho scope chuyển giao, import read-only history, không tự replay historical commands. Current runs do legacy owner kết thúc hoặc explicit handover có quyền riêng. So sánh state/artifacts trên fixtures, kiểm tra rollback đường điều khiển rồi mới chuyển submit path sang platform.

Một remote agent chỉ được L2 nếu write tools đi qua enforcement hoặc sandbox/service boundary đã chứng minh chặn bypass. Nếu chưa đạt, giảm capabilities hoặc giữ read-only tasks. Ghi rõ observability coverage và control limitations trong registry/HUD.

## Thao tác
Copy templates/existing-agent và dùng prompt 03. Giữ app/source behavior qua wrapper, thêm instrumentation/correlation tại boundary. Credentials bind bằng secret_ref. Chạy duplicate submit/cancel/recovery scenario. Tắt deployment mới nếu gặp unknown remote state, reconcile trước retry. Uninstall tách remove from registry khỏi xóa remote app/data, owner quyết định từng phần.
