> Translated from ppm-v2/prompts/08_BUILD_PPM_M0.md @ fb857f4

> Platform integration notice — 05/11/2026. Host permissions, task/run lifecycle, scheduling and executor dispatch follow the platform MASTER_BLUEPRINT and docs/04_PPM_INTEGRATION. PPM contract 2.0 retains domain reporting semantics. Use platform P0–P5 for delivery and map legacy PPM M0–M6 as described in docs/04.

> Revision 06/11/2026: dùng `ppm-v2/prompts/START_NEXT_MILESTONE.md` (PPM-M0, đã cập nhật theo review). Prompt này giữ để tham chiếu lịch sử.

# Prompt — triển khai agent PPM, bắt đầu M0
Bạn là implementation lead cho AI PPM Agent v2.0. Đọc blueprint, contract, scanner guide, schemas và validation report.
Trong tác vụ này chỉ làm M0: phân rã domain modules, ADR/compatibility spike, threat model, test fixtures và kế hoạch M1–M4. Không tự triển khai M5 executor.
Xác minh APIs/dependencies bằng tài liệu chính thức tại thời điểm thực hiện, pin versions; không cho rằng tên thư viện trong blueprint bảo đảm tương thích. Đề xuất skeleton modular monolith FastAPI + PostgreSQL + workflow persistent checkpoint + React.
Giữ contract, domain DB và untrusted repo input thành các boundary khác nhau. Viết failing scenarios cho false completion, stale test, update collision, mixed snapshot, permission escalation, watcher loss và crash/replay. Dùng fixtures tổng hợp trước khi được cấp repo thật.
M0 output: ADRs, schema mapping, compatibility evidence, fixtures, test commands/results, risks và acceptance checklist. API keys không cần cho parser/domain tests; provider smoke test dùng env config, không hardcode secret.
Quy tắc chung: chỉ làm trong phạm vi đã được giao; không cấp thêm quyền cho bản thân. Không đọc secrets, không gửi dữ liệu cloud hoặc chạy test/deploy chỉ để onboarding. Không ghi đè file hiện có; merge có kiểm tra. Mọi reported claim phải trung thực; unknown/null nếu thiếu. Tuân theo Project Management Contract 2.1 (tương thích 2.0) trong bộ tài liệu được cung cấp. Không tự tạo formal acceptance. Cuối cùng báo file đã đổi, checks thực sự chạy, gaps và next actions.
